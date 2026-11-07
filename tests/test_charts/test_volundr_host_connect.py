"""The volundr chart's host connect endpoint: the sshd sidecar for reverse-SSH hosts."""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from volundr.adapters.inbound.host_connect_keys import HostConnectKeyServer
from volundr.domain.compute_hosts import EndpointKey

CHART_DIR = Path(__file__).parent.parent.parent / "charts" / "volundr"
BASE = ("--set", "bifrost.url=http://bifrost.test")
ON = (
    "--set",
    "hostConnect.enabled=true",
    "--set",
    "hostConnect.hostKeySecret.name=forge-connect-host-key",
    "--set",
    "strategy.type=Recreate",
)
LINUX = sys.platform.startswith("linux")

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")


def _documents(*args: str) -> list[dict]:
    output = subprocess.check_output(["helm", "template", "test", str(CHART_DIR), *BASE, *args])
    return [doc for doc in yaml.safe_load_all(output) if doc]


def _find(docs: list[dict], kind: str, name: str) -> dict | None:
    return next(
        (d for d in docs if d["kind"] == kind and d["metadata"]["name"] == name),
        None,
    )


def _pod(docs: list[dict]) -> dict:
    return _find(docs, "Deployment", "test-volundr")["spec"]["template"]["spec"]


def _refusal(*args: str) -> str:
    result = subprocess.run(
        ["helm", "template", "test", str(CHART_DIR), *BASE, *args],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, "the chart rendered a configuration it must refuse"
    return result.stderr


def _scripts(*args: str) -> dict[str, str]:
    return _find(_documents(*ON, *args), "ConfigMap", "test-volundr-host-connect")["data"]


def test_off_by_default():
    docs = _documents()
    pod = _pod(docs)
    assert all(c["name"] != "host-connect" for c in pod["containers"])
    assert all(not v["name"].startswith("host-connect") for v in pod["volumes"])
    assert _find(docs, "Service", "test-volundr-host-connect") is None
    assert _find(docs, "ConfigMap", "test-volundr-host-connect") is None


class TestSidecar:
    def test_runs_sshd_beside_forge_sharing_only_the_runtime_volume(self):
        docs = _documents(*ON)
        pod = _pod(docs)
        containers = {c["name"]: c for c in pod["containers"]}
        sidecar = containers["host-connect"]
        assert sidecar["command"] == ["sh", "/etc/niuu-connect/entrypoint.sh"]
        assert sidecar["image"] == containers["volundr"]["image"]
        assert sidecar["env"] == [{"name": "FORGE_UID", "value": "1000"}]
        assert sidecar["ports"] == [
            {"name": "host-connect", "containerPort": 2222, "protocol": "TCP"}
        ]
        shared = {"name": "host-connect", "mountPath": "/run/niuu-connect"}
        assert shared in sidecar["volumeMounts"]
        assert shared in containers["volundr"]["volumeMounts"]
        for name, container in containers.items():
            if name not in {"volundr", "host-connect"}:
                assert shared not in container.get("volumeMounts", [])
        volumes = {v["name"]: v for v in pod["volumes"]}
        assert volumes["host-connect"] == {"name": "host-connect", "emptyDir": {"medium": "Memory"}}
        assert volumes["host-connect-key"]["secret"] == {
            "secretName": "forge-connect-host-key",
            "defaultMode": 0o400,
            "items": [{"key": "ssh_host_ed25519_key", "path": "ssh_host_ed25519_key"}],
        }

    def test_sshd_gets_only_the_privileges_it_separates_connections_with(self):
        sidecar = next(
            c for c in _pod(_documents(*ON))["containers"] if c["name"] == "host-connect"
        )
        context = sidecar["securityContext"]
        assert context["runAsUser"] == 0
        assert context["allowPrivilegeEscalation"] is False
        assert context["readOnlyRootFilesystem"] is True
        assert context["capabilities"] == {
            "drop": ["ALL"],
            "add": ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID", "SYS_CHROOT"],
        }

    def test_probes_read_the_listener_instead_of_connecting(self):
        sidecar = next(
            c
            for c in _pod(_documents(*ON, "--set", "hostConnect.port=2200"))["containers"]
            if c["name"] == "host-connect"
        )
        probe = ["grep", "-q", " 00000000:0898 00000000:0000 0A", "/proc/net/tcp"]
        assert sidecar["readinessProbe"]["exec"]["command"] == probe
        assert sidecar["livenessProbe"]["exec"]["command"] == probe

    def test_one_recreated_pod_and_a_rollout_on_config_change(self):
        deployment = _find(_documents(*ON), "Deployment", "test-volundr")
        assert deployment["spec"]["strategy"] == {"type": "Recreate"}
        annotations = deployment["spec"]["template"]["metadata"]["annotations"]
        assert "checksum/host-connect" in annotations


class TestSshdConfig:
    def test_admits_only_forwards_to_the_keys_own_port(self):
        config = _scripts()["sshd_config"].splitlines()
        for line in (
            "Port 2222",
            "AllowUsers niuu-connect",
            "AuthenticationMethods publickey",
            "PasswordAuthentication no",
            "AuthorizedKeysFile none",
            "AuthorizedKeysCommand /run/niuu-connect-private/niuu-connect-keys %u %t %k",
            "AuthorizedKeysCommandUser niuu-connect",
            "AllowTcpForwarding remote",
            "PermitOpen none",
            "GatewayPorts no",
            "PermitTTY no",
            "X11Forwarding no",
            "AllowAgentForwarding no",
            "AllowStreamLocalForwarding no",
            "PerSourcePenalties no",
            "ClientAliveInterval 15",
            "ClientAliveCountMax 3",
        ):
            assert line in config

    def test_the_key_command_talks_to_forges_socket_with_a_bound(self):
        keys = _scripts("--set", "hostConnect.keyLookupTimeoutSeconds=3")["niuu-connect-keys"]
        assert keys.startswith("#!/bin/sh\n")
        assert '"$1" "$2" "$3" "$PPID" "/run/niuu-connect" "3"' in keys

    @pytest.mark.parametrize("name", ["niuu-connect-keys", "niuu-connect-reaper", "entrypoint.sh"])
    def test_scripts_are_posix_sh(self, name, tmp_path):
        script = tmp_path / name
        script.write_text(_scripts()[name])
        subprocess.run(["sh", "-n", str(script)], check=True)
        if shutil.which("shellcheck"):
            subprocess.run(["shellcheck", "-s", "sh", str(script)], check=True)


class TestExposure:
    def test_a_load_balancer_by_default(self):
        service = _find(
            _documents(
                *ON,
                "--set",
                "hostConnect.service.loadBalancerIP=192.0.2.10",
                "--set",
                "hostConnect.service.loadBalancerSourceRanges[0]=198.51.100.0/24",
                "--set",
                "hostConnect.service.externalTrafficPolicy=Local",
            ),
            "Service",
            "test-volundr-host-connect",
        )
        assert service["spec"]["type"] == "LoadBalancer"
        assert service["spec"]["loadBalancerIP"] == "192.0.2.10"
        assert service["spec"]["loadBalancerSourceRanges"] == ["198.51.100.0/24"]
        assert service["spec"]["externalTrafficPolicy"] == "Local"
        assert service["spec"]["ports"] == [
            {"name": "host-connect", "port": 2222, "targetPort": "host-connect", "protocol": "TCP"}
        ]

    def test_or_a_gateway_tcp_route_to_a_cluster_ip(self):
        docs = _documents(
            *ON,
            "--set",
            "hostConnect.tcpRoute.enabled=true",
            "--set",
            "hostConnect.tcpRoute.parentRefs[0].name=edge",
            "--set",
            "hostConnect.tcpRoute.parentRefs[0].sectionName=host-connect",
            "--set",
            "hostConnect.service.loadBalancerIP=192.0.2.10",
        )
        service = _find(docs, "Service", "test-volundr-host-connect")
        assert service["spec"]["type"] == "ClusterIP"
        assert "loadBalancerIP" not in service["spec"]
        route = _find(docs, "TCPRoute", "test-volundr-host-connect")
        assert route["apiVersion"] == "gateway.networking.k8s.io/v1alpha2"
        assert route["spec"]["parentRefs"] == [{"name": "edge", "sectionName": "host-connect"}]
        assert route["spec"]["rules"] == [
            {"backendRefs": [{"name": "test-volundr-host-connect", "port": 2222}]}
        ]

    def test_network_policy_admits_hosts_to_the_endpoint(self):
        docs = _documents(
            *ON,
            "--set",
            "networkPolicy.enabled=true",
            "--set",
            "hostConnect.networkPolicyFrom[0].ipBlock.cidr=0.0.0.0/0",
        )
        ingress = _find(docs, "NetworkPolicy", "test-volundr")["spec"]["ingress"]
        assert {
            "ports": [{"protocol": "TCP", "port": 2222}],
            "from": [{"ipBlock": {"cidr": "0.0.0.0/0"}}],
        } in ingress


class TestRefusals:
    @pytest.mark.parametrize(
        ("args", "message"),
        [
            (("--set", "replicaCount=2"), "replicaCount: 1"),
            (("--set", "autoscaling.enabled=true"), "autoscaling.enabled: false"),
            (("--set", "strategy.type=RollingUpdate"), "strategy.type: Recreate"),
            (("--set", "hostConnect.hostKeySecret.name="), "hostKeySecret.name"),
            (("--set", "hostConnect.tcpRoute.enabled=true"), "parentRefs"),
            (("--set", "securityContext.runAsUser=null"), "securityContext.runAsUser"),
        ],
    )
    def test_refuses_what_cannot_work(self, args, message):
        assert message in _refusal(*ON, *args)


@pytest.fixture
def short_dir():
    # A Unix socket path is limited to about 100 bytes; pytest's tmp_path can be longer.
    path = Path(tempfile.mkdtemp(prefix="hc", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


async def _run_key_command(script: Path, *args: str) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        "sh", str(script), *args, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    out, err = await process.communicate()
    return process.returncode, out.decode(), err.decode()


class TestKeyCommandAgainstForge:
    """The rendered key command against the real socket server, as sshd runs it."""

    def _script(self, run: Path, tmp_path: Path) -> Path:
        script = tmp_path / "niuu-connect-keys"
        script.write_text(
            _scripts(
                "--set",
                f"hostConnect.runtimeDir={run}",
                "--set",
                f"hostConnect.python={sys.executable}",
            )["niuu-connect-keys"]
        )
        return script

    async def test_a_key_forge_does_not_admit_prints_nothing(self, short_dir, tmp_path):
        async def answer(**_request):
            return None

        server = HostConnectKeyServer(socket_path=str(short_dir / "keys.sock"), answer=answer)
        await server.start()
        try:
            code, out, _ = await _run_key_command(
                self._script(short_dir, tmp_path), "niuu-connect", "ssh-ed25519", "AAAA"
            )
        finally:
            await server.stop()
        assert (code, out) == (0, "")
        assert not (short_dir / "conns").exists()

    async def test_no_answer_from_forge_refuses_the_key(self, short_dir, tmp_path):
        code, out, err = await _run_key_command(
            self._script(short_dir, tmp_path), "niuu-connect", "ssh-ed25519", "AAAA"
        )
        assert code != 0 and out == ""
        assert "keys.sock" in err or "No such file" in err

    @pytest.mark.skipif(not LINUX, reason="the connection record reads Linux /proc")
    async def test_an_admitted_key_prints_its_line_and_records_the_connection(
        self, short_dir, tmp_path
    ):
        host_id = uuid4()
        asked = []

        async def answer(**request):
            asked.append(request)
            return EndpointKey(host_id=host_id, line='restrict,permitlisten="127.0.0.1:20001" k')

        server = HostConnectKeyServer(socket_path=str(short_dir / "keys.sock"), answer=answer)
        await server.start()
        try:
            code, out, err = await _run_key_command(
                self._script(short_dir, tmp_path), "niuu-connect", "ssh-ed25519", "AAAA"
            )
        finally:
            await server.stop()
        assert code == 0, err
        assert out == 'restrict,permitlisten="127.0.0.1:20001" k\n'
        assert asked == [{"user": "niuu-connect", "key_type": "ssh-ed25519", "key": "AAAA"}]
        # The parent of the command (here this test process) is what a revoke ends.
        started = Path(f"/proc/{os.getpid()}/stat").read_text().rsplit(")", 1)[1].split()[19]
        records = [p.name for p in (short_dir / "conns" / str(host_id)).iterdir()]
        assert records == [f"{os.getpid()}-{started}"]


@pytest.mark.skipif(not LINUX, reason="the reaper reads Linux /proc")
def test_the_reaper_ends_a_revoked_hosts_connection_and_nothing_else(tmp_path):
    run = tmp_path / "run"
    (run / "conns").mkdir(parents=True)
    os.mkfifo(run / "revoke")
    reaper = tmp_path / "reaper"
    reaper.write_text(_scripts()["niuu-connect-reaper"])
    connection = subprocess.Popen(["sleep", "60"])
    replaced = subprocess.Popen(["sleep", "60"])
    sshd = subprocess.Popen(["sleep", "60"])
    host = uuid4()
    record = run / "conns" / str(host)
    record.mkdir()

    def started(pid: int) -> str:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]

    (record / f"{connection.pid}-{started(connection.pid)}").touch()
    # Its pid now belongs to another process (another start time): only the record goes.
    (record / f"{replaced.pid}-1").touch()
    process = subprocess.Popen(["sh", str(reaper), str(run), str(sshd.pid)])
    try:
        with open(run / "revoke", "w") as pipe:
            pipe.write("not a host id!\n")
            pipe.write(f"{host}\n")
        deadline = time.monotonic() + 10
        while record.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not record.exists()
        assert connection.wait(timeout=5) == -signal.SIGTERM
        assert replaced.poll() is None
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=5) == 0
        assert sshd.wait(timeout=5) == -signal.SIGTERM
    finally:
        for child in (connection, replaced, sshd, process):
            if child.poll() is None:
                child.kill()
