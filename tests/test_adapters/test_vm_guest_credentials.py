"""VM sessions: pod-shaped sidecars, ServiceAccount tokens and the Forge session credential."""

import asyncio
import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from niuu.domain.services.forge_session_token import IssuedForgeSessionToken
from niuu.forge_mcp.models import ForgeMcpGrant
from volundr.adapters.outbound import ssh_vm_runtime as module
from volundr.adapters.outbound.guest_credential_delivery import (
    AGENT_CONFIG_DIRECTORY,
    MetadataCredentialDelivery,
    SshPushCredentialDelivery,
)
from volundr.adapters.outbound.local_process import (
    FORGE_MCP_GRANTS_ENV,
    FORGE_MCP_TOKEN_FILE_ENV,
)
from volundr.adapters.outbound.ssh_vm_runtime import SshContainerVmRuntime
from volundr.compute.config import ComputeConfig
from volundr.compute.main import build_guest_credentials
from volundr.domain.compute import (
    ComputeLease,
    ComputeSlot,
    Machine,
    MachineBootstrap,
    MachineState,
    SlotLease,
)
from volundr.domain.guest_access import GuestCommandResult
from volundr.domain.guest_credentials import (
    METADATA_ADDRESS,
    GuestCredentialPlan,
    GuestIdentity,
    GuestRequest,
)
from volundr.domain.models import GitSource, PodSpecAdditions, Session, SessionSpec
from volundr.domain.service_account_tokens import ServiceAccountToken, ServiceAccountTokenPort
from volundr.domain.vm_runtime import VmRuntimeStageError

TOKEN_VOLUME = {
    "name": "niuu-workload-identity",
    "projected": {
        "sources": [
            {
                "serviceAccountToken": {
                    "path": "token",
                    "audience": "volundr-api",
                    "expirationSeconds": 1200,
                }
            }
        ]
    },
}


class FakeTokens(ServiceAccountTokenPort):
    def __init__(self, lifetime=timedelta(hours=1), **_ports):
        self.calls = []
        self.lifetime = lifetime
        self.fail = 0

    async def issue(self, service_account, *, audience, expiration_seconds):
        self.calls.append((service_account, audience, expiration_seconds))
        if self.fail:
            self.fail -= 1
            raise RuntimeError("issuer down")
        return ServiceAccountToken(
            token=f"jwt-{len(self.calls)}", expires_at=datetime.now(UTC) + self.lifetime
        )


class FakeIdentity(GuestIdentity):
    def __init__(self):
        self.registered = []
        self.unregistered = []

    def agent_auth(self, allocation_id):
        return {"kind": "fake", "allocation": str(allocation_id)}

    def agent_mounts(self):
        return ({"source": "/etc/fake", "target": "/etc/fake", "readonly": True},)

    async def register(self, lease, machine):
        self.registered.append(lease.id)

    async def unregister(self, lease, machine):
        self.unregistered.append(lease.id)

    async def authenticate(self, request: GuestRequest):
        raise AssertionError("not used")


class FakeFiles:
    def __init__(self, present=False):
        self.writes = []
        self.present = present

    async def write_files(self, lease, bootstrap, items):
        self.writes.append(items)

    async def file_exists(self, lease, bootstrap, path):
        return self.present


def _credential():
    return IssuedForgeSessionToken(
        token="forge-launch-token",
        expires_at=0,
        session_id="s",
        launch_id="l",
        scopes=("forge:notify",),
        grants=(ForgeMcpGrant.MESSAGE,),
    )


@pytest.fixture
def runtime(tmp_path):
    return _runtime(tmp_path)


def _runtime(tmp_path, **kwargs):
    key = Ed25519PrivateKey.generate()
    private, public = tmp_path / "key", tmp_path / "key.pub"
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        )
    )
    public.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
        )
    )
    return SshContainerVmRuntime(
        ssh_private_key_file=str(private),
        ssh_public_key_file=str(public),
        data_dir=str(tmp_path / "data"),
        skuld_image="test/skuld:dev",
        default_service_account="skuld-session",
        **kwargs,
    )


@pytest.fixture
def session():
    return Session(name="vm", owner_id="owner", tenant_id="tenant", source=GitSource())


def _lease(session):
    allocation = uuid4()
    machine = ComputeLease(
        id=allocation,
        pool_id="pool",
        profile="small",
        request_fingerprint="test",
        machine=Machine(
            allocation_id=allocation,
            resource_id="vm",
            state=MachineState.RUNNING,
            addresses=("192.0.2.10",),
        ),
    )
    binding = ComputeSlot(
        id=uuid4(),
        allocation_id=allocation,
        slot=0,
        pool_id="pool",
        session_id=session.id,
        owner_id="owner",
        tenant_id="tenant",
    )
    return SlotLease(allocation=machine, binding=binding)


def _launch(bootstrap):
    return json.loads(next(f.content for f in bootstrap.files if f.path == module._LAUNCH))


def _agent_pod():
    return PodSpecAdditions(
        service_account="openbao-session-1",
        volumes=(
            {"name": "openbao-secrets", "emptyDir": {}},
            {
                "name": "openbao-agent-sa-token",
                "projected": {
                    "sources": [{"serviceAccountToken": {"path": "token", "audience": "bao"}}]
                },
            },
        ),
        volume_mounts=({"name": "openbao-secrets", "mountPath": "/run/secrets", "readOnly": True},),
        init_containers=(
            {
                "name": "openbao-agent-init",
                "image": "openbao/openbao:2",
                "command": ["/bin/sh", "-ec", "exec bao agent"],
                "env": [{"name": "BAO_AGENT_CONFIG", "value": "exit_after_auth = true"}],
                "volumeMounts": [
                    {"name": "openbao-secrets", "mountPath": "/run/secrets"},
                    {
                        "name": "openbao-agent-sa-token",
                        "mountPath": "/var/run/secrets/kubernetes.io/serviceaccount",
                        "readOnly": True,
                    },
                ],
                "securityContext": {"runAsUser": 100, "runAsGroup": 1000},
            },
        ),
        extra_containers=(
            {"name": "openbao-agent", "image": "openbao/openbao:2", "args": ["agent"]},
        ),
    )


def test_pod_layout_translates_sidecars_empty_dirs_and_token_volumes(runtime, session):
    runtime.configure_credential_delivery(
        SshPushCredentialDelivery(service_account_tokens=FakeTokens())
    )
    spec = SessionSpec(values={}, pod_spec=_agent_pod())
    launch = _launch(runtime.bootstrap(session, spec, MachineBootstrap()))

    assert launch["service_account"] == "openbao-session-1"
    assert launch["directories"] == [{"path": f"{module._VOLUMES}/openbao-secrets", "mode": "0777"}]
    assert launch["volume_mounts"] == [
        {
            "source": f"{module._VOLUMES}/openbao-secrets",
            "target": "/run/secrets",
            "readonly": True,
        }
    ]
    assert launch["token_volumes"] == [
        {
            "volume": "openbao-agent-sa-token",
            "directory": f"{module._TOKENS}/openbao-agent-sa-token",
            "tokens": [{"path": "token", "audience": "bao", "expiration_seconds": 3600}],
        }
    ]
    [init] = launch["init_containers"]
    assert init["user"] == "100:1000"
    assert init["environment"] == {"BAO_AGENT_CONFIG": "exit_after_auth = true"}
    assert init["mounts"] == [
        {
            "source": f"{module._VOLUMES}/openbao-secrets",
            "target": "/run/secrets",
            "readonly": False,
        },
        {
            "source": f"{module._TOKENS}/openbao-agent-sa-token",
            "target": "/var/run/secrets/kubernetes.io/serviceaccount",
            "readonly": True,
        },
    ]
    assert [c["name"] for c in launch["sidecars"]] == ["openbao-agent"]


def _with_workload_identity(pod: PodSpecAdditions) -> PodSpecAdditions:
    """The OpenBao guest pod plus WorkloadIdentityContributor's token, as on forge-vm."""
    return replace(
        pod,
        volumes=(*pod.volumes, TOKEN_VOLUME),
        volume_mounts=(
            *pod.volume_mounts,
            {
                "name": "niuu-workload-identity",
                "mountPath": "/var/run/secrets/niuu-workload",
                "readOnly": True,
            },
        ),
    )


def test_a_token_inside_the_read_only_secrets_bind_gets_its_mountpoint(runtime, session):
    """In the Skuld image /var/run is /run, so the workload token's target lies inside the
    read-only /run/secrets bind, where runc cannot create a mountpoint (exit 125 on
    forge-vm). The guest creates it in the secrets volume first; both binds stay read-only."""
    runtime.configure_credential_delivery(
        SshPushCredentialDelivery(service_account_tokens=FakeTokens())
    )
    spec = SessionSpec(values={}, pod_spec=_with_workload_identity(_agent_pod()))
    launch = _launch(runtime.bootstrap(session, spec, MachineBootstrap()))

    assert launch["directories"] == [
        {"path": f"{module._VOLUMES}/openbao-secrets", "mode": "0777"},
        {"path": f"{module._VOLUMES}/openbao-secrets/niuu-workload", "mode": "0755"},
    ]
    assert launch["volume_mounts"] == [
        {
            "source": f"{module._VOLUMES}/openbao-secrets",
            "target": "/run/secrets",
            "readonly": True,
        },
        {
            "source": f"{module._TOKENS}/niuu-workload-identity",
            "target": "/var/run/secrets/niuu-workload",
            "readonly": True,
        },
    ]


def test_image_path_links_decide_what_nests(tmp_path, session):
    runtime = _runtime(tmp_path, image_path_links={})
    runtime.configure_credential_delivery(
        SshPushCredentialDelivery(service_account_tokens=FakeTokens())
    )
    spec = SessionSpec(values={}, pod_spec=_with_workload_identity(_agent_pod()))
    launch = _launch(runtime.bootstrap(session, spec, MachineBootstrap()))

    # An image without the /var/run link keeps the token outside /run/secrets.
    assert launch["directories"] == [{"path": f"{module._VOLUMES}/openbao-secrets", "mode": "0777"}]


@pytest.mark.parametrize("links", [{"var/run": "/run"}, {"/var/run": "../run"}])
def test_image_path_links_must_be_absolute(links):
    with pytest.raises(ValueError, match="image_path_links"):
        SshContainerVmRuntime(
            guest_access=MagicMock(), skuld_image="test/skuld:dev", image_path_links=links
        )


def test_a_mount_inside_a_secret_file_is_refused(runtime, session, tmp_path):
    secret = tmp_path / "token"
    secret.write_text("value")
    pod = PodSpecAdditions(
        volumes=(
            {"name": "secret-file", "hostPath": {"path": str(secret), "type": "File"}},
            {"name": "scratch", "emptyDir": {}},
        ),
        volume_mounts=(
            {"name": "secret-file", "mountPath": "/run/secret", "readOnly": True},
            {"name": "scratch", "mountPath": "/var/run/secret/scratch"},
        ),
    )

    with pytest.raises(ValueError, match="inside the secret file mounted at '/run/secret'"):
        runtime.bootstrap(session, SessionSpec(values={}, pod_spec=pod), MachineBootstrap())


@pytest.mark.parametrize(
    "change, message",
    [
        ({"volumes": ({"name": "pvc", "persistentVolumeClaim": {}},)}, "cannot provide"),
        (
            {
                "volumes": (
                    {"name": "cfg", "projected": {"sources": [{"configMap": {"name": "x"}}]}},
                ),
                "volume_mounts": ({"name": "cfg", "mountPath": "/cfg"},),
            },
            "only serviceAccountToken",
        ),
        (
            {
                "volumes": (
                    {
                        "name": "t",
                        "projected": {"sources": [{"serviceAccountToken": {"path": "token"}}]},
                    },
                ),
                "volume_mounts": ({"name": "t", "mountPath": "/t"},),
            },
            "explicit audience",
        ),
        ({"extra_containers": ({"name": "x", "image": "i", "ports": []},)}, "only"),
        (
            {
                "extra_containers": (
                    {"name": "x", "image": "i", "env": [{"name": "A", "valueFrom": {}}]},
                )
            },
            "literal",
        ),
        (
            {
                "extra_containers": (
                    {"name": "x", "image": "i", "securityContext": {"privileged": True}},
                )
            },
            "runAsUser",
        ),
        ({"volumes": ({"name": "unused", "emptyDir": {}},)}, "unmounted"),
        (
            {
                "volumes": ({"name": "scratch", "emptyDir": {}},),
                "volume_mounts": ({"name": "scratch", "mountPath": "/x", "subPath": "y"},),
            },
            "no subPath",
        ),
    ],
)
def test_pod_layout_refuses_what_a_guest_cannot_honour(runtime, session, change, message):
    runtime.configure_credential_delivery(
        SshPushCredentialDelivery(service_account_tokens=FakeTokens())
    )
    spec = SessionSpec(values={}, pod_spec=PodSpecAdditions(**change))
    with pytest.raises(ValueError, match=message):
        runtime.bootstrap(session, spec, MachineBootstrap())


def test_token_volumes_need_a_delivery_and_a_service_account(runtime, session, tmp_path):
    spec = SessionSpec(
        values={},
        pod_spec=PodSpecAdditions(
            volumes=(TOKEN_VOLUME,),
            volume_mounts=(
                {"name": "niuu-workload-identity", "mountPath": "/var/run/secrets/niuu-workload"},
            ),
        ),
    )
    with pytest.raises(ValueError, match="compute.credential_delivery"):
        runtime.bootstrap(session, spec, MachineBootstrap())
    runtime.configure_credential_delivery(
        SshPushCredentialDelivery(service_account_tokens=FakeTokens())
    )
    runtime._default_service_account = ""
    with pytest.raises(ValueError, match="default_service_account"):
        runtime.bootstrap(session, spec, MachineBootstrap())


def test_forge_session_needs_a_delivery(runtime, session):
    spec = SessionSpec(values={}, pod_spec=PodSpecAdditions())
    spec.forge_session = _credential()
    with pytest.raises(ValueError, match="compute.credential_delivery"):
        runtime.bootstrap(session, spec, MachineBootstrap())
    assert not runtime.delivers_forge_session_token


def test_forge_session_token_never_enters_the_stored_bootstrap(runtime, session):
    runtime.configure_credential_delivery(
        SshPushCredentialDelivery(service_account_tokens=FakeTokens())
    )
    spec = SessionSpec(values={}, pod_spec=PodSpecAdditions())
    spec.forge_session = _credential()
    bootstrap = runtime.bootstrap(session, spec, MachineBootstrap())
    assert "forge-launch-token" not in bootstrap.model_dump_json()
    launch = _launch(bootstrap)
    assert launch["environment"][FORGE_MCP_TOKEN_FILE_ENV] == (
        f"{module._FORGE_SESSION_TARGET}/{module._FORGE_SESSION_FILE}"
    )
    assert {
        "source": module._FORGE_SESSION_DIR,
        "target": module._FORGE_SESSION_TARGET,
        "readonly": True,
    } in launch["volume_mounts"]
    assert runtime.delivers_forge_session_token


def test_metadata_delivery_adds_its_agent_first_and_the_metadata_address(runtime, session):
    identity = FakeIdentity()
    runtime.configure_credential_delivery(
        MetadataCredentialDelivery(metadata_url="https://forge.example/", guest_identity=identity)
    )
    spec = SessionSpec(values={}, pod_spec=_agent_pod())
    spec.forge_session = _credential()
    launch = _launch(runtime.bootstrap(session, spec, MachineBootstrap()))

    assert [c["name"] for c in launch["init_containers"]] == [
        "niuu-guest-agent-init",
        "openbao-agent-init",
    ]
    assert [c["name"] for c in launch["sidecars"]] == ["niuu-guest-agent", "openbao-agent"]
    agent = launch["init_containers"][0]
    assert agent["image"] == "test/skuld:dev"
    assert agent["user"] == "0"
    assert agent["command"][-3:] == ["once", "--config", f"{AGENT_CONFIG_DIRECTORY}/config.json"]
    targets = {m["target"]: m["readonly"] for m in agent["mounts"]}
    assert targets[AGENT_CONFIG_DIRECTORY] is True
    assert targets[f"{module._TOKENS}/openbao-agent-sa-token"] is False
    assert targets[module._FORGE_SESSION_DIR] is False
    assert targets["/etc/fake"] is True
    assert launch["metadata_address"] == METADATA_ADDRESS
    assert {"path": module._FORGE_SESSION_DIR, "mode": "0755"} in launch["directories"]


def test_metadata_delivery_validates_its_configuration():
    with pytest.raises(ValueError, match="https"):
        MetadataCredentialDelivery(metadata_url="http://forge", guest_identity=FakeIdentity())
    with pytest.raises(ValueError, match="guest_identity"):
        MetadataCredentialDelivery(metadata_url="https://forge", guest_identity=None)
    with pytest.raises(ValueError, match="refresh_fraction"):
        MetadataCredentialDelivery(
            metadata_url="https://forge", guest_identity=FakeIdentity(), refresh_fraction=1
        )
    with pytest.raises(ValueError, match="absolute"):
        MetadataCredentialDelivery(
            metadata_url="https://forge", guest_identity=FakeIdentity(), ca_file="ca.pem"
        )
    with pytest.raises(ValueError, match="service_account_tokens"):
        SshPushCredentialDelivery(service_account_tokens=None)


async def test_metadata_delivery_registers_identity_and_writes_agent_config(session):
    identity = FakeIdentity()
    delivery = MetadataCredentialDelivery(
        metadata_url="https://forge.example/", guest_identity=identity
    )
    lease, files, launch = _lease(session), FakeFiles(), {}
    plan = GuestCredentialPlan.from_launch(
        {
            "service_account": "sa",
            "token_volumes": [
                {
                    "volume": "v",
                    "directory": "/run/niuu/session/tokens/v",
                    "tokens": [{"path": "token", "audience": "a", "expiration_seconds": 600}],
                }
            ],
            "forge_session": True,
            "forge_session_directory": "/etc/niuu/forge-session",
        }
    )
    await delivery.start(lease, MachineBootstrap(), plan, launch, files)
    [[item]] = files.writes
    config = json.loads(item["content"])
    assert item["directory"] == AGENT_CONFIG_DIRECTORY
    assert config["metadata_url"] == "https://forge.example"
    assert config["allocation_id"] == str(lease.id)
    assert config["auth"] == {"kind": "fake", "allocation": str(lease.id)}
    assert config["token_volumes"][0]["tokens"] == [{"path": "token"}]
    assert config["forge_session"] == {"directory": "/etc/niuu/forge-session", "file": "token"}
    assert config["listen"] == {"address": METADATA_ADDRESS, "port": 80}
    assert "forge-launch-token" not in item["content"]
    assert launch["relaunch"] is True
    assert identity.registered == [lease.id]
    await delivery.stop(lease, MachineBootstrap())
    assert identity.unregistered == [lease.id]
    assert (
        delivery.session_containers(GuestCredentialPlan(service_account=""), image="i").sidecars
        == ()
    )


def _push_plan(forge=False):
    return GuestCredentialPlan.from_launch(
        {
            "service_account": "skuld-session",
            "token_volumes": [
                {
                    "volume": "wi",
                    "directory": "/run/niuu/session/tokens/wi",
                    "tokens": [
                        {"path": "token", "audience": "volundr-api", "expiration_seconds": 1200}
                    ],
                }
            ],
            "forge_session": forge,
            "forge_session_directory": "/etc/niuu/forge-session",
        }
    )


async def test_ssh_push_writes_tokens_and_staged_forge_credential(session):
    tokens = FakeTokens()
    delivery = SshPushCredentialDelivery(service_account_tokens=tokens)
    lease, files, launch = _lease(session), FakeFiles(), {}
    delivery.stage_forge_session(session.id, _credential())
    await delivery.start(lease, MachineBootstrap(), _push_plan(forge=True), launch, files)
    try:
        assert tokens.calls == [("skuld-session", "volundr-api", 1200)]
        assert files.writes[0] == [
            {"directory": "/run/niuu/session/tokens/wi", "name": "token", "content": "jwt-1"}
        ]
        assert files.writes[1][0]["content"] == "forge-launch-token"
        assert launch == {"forge_session_grants": ["message"], "relaunch": True}
    finally:
        await delivery.stop(lease, MachineBootstrap())


async def test_ssh_push_refuses_a_replayed_start_without_its_credential(session):
    delivery = SshPushCredentialDelivery(service_account_tokens=FakeTokens())
    lease = _lease(session)
    with pytest.raises(VmRuntimeStageError, match="restart the session"):
        await delivery.start(
            lease, MachineBootstrap(), _push_plan(forge=True), {}, FakeFiles(present=False)
        )
    await delivery.stop(lease, MachineBootstrap())
    launch = {}
    await delivery.start(lease, MachineBootstrap(), _push_plan(forge=True), launch, FakeFiles(True))
    assert "relaunch" not in launch
    await delivery.close()


async def test_ssh_push_refresh_retries_loudly_until_it_succeeds(session, monkeypatch, caplog):
    counted = MagicMock()
    monkeypatch.setattr(
        "volundr.adapters.outbound.guest_credential_delivery.get_observability",
        lambda: counted,
    )
    tokens = FakeTokens(lifetime=timedelta(milliseconds=50))
    delivery = SshPushCredentialDelivery(
        service_account_tokens=tokens, refresh_fraction=0.5, retry_seconds=0.01
    )
    lease, files = _lease(session), FakeFiles()
    await delivery.start(lease, MachineBootstrap(), _push_plan(), {}, files)
    tokens.fail = 2
    for _ in range(200):
        if len(files.writes) >= 2:
            break
        await asyncio.sleep(0.01)
    await delivery.stop(lease, MachineBootstrap())
    assert len(files.writes) >= 2
    assert counted.count.call_count == 2
    assert "retrying" in caplog.text
    assert "jwt-" not in caplog.text


async def test_runtime_start_runs_delivery_before_the_guest_program(runtime, session):
    delivery = SshPushCredentialDelivery(service_account_tokens=FakeTokens())
    runtime.configure_credential_delivery(delivery)
    spec = SessionSpec(values={}, pod_spec=PodSpecAdditions())
    spec.forge_session = _credential()
    bootstrap = runtime.bootstrap(session, spec, MachineBootstrap())
    lease = _lease(session)
    runtime.stage_forge_session(session.id, _credential())
    runtime._guest_access.execute = AsyncMock(return_value=GuestCommandResult(0))
    runtime._guest_access.open_tunnel = AsyncMock()
    await runtime.start(lease, bootstrap)
    payloads = [
        json.loads(call.kwargs["data"])
        for call in runtime._guest_access.execute.await_args_list
        if call.kwargs.get("data")
    ]
    written = payloads[-2]
    assert written[0]["content"] == "forge-launch-token"
    start = payloads[-1]
    assert start["relaunch"] is True
    assert start["environment"][FORGE_MCP_GRANTS_ENV] == "message"
    assert "forge_session_grants" not in start
    runtime.discard_forge_session(session.id)
    await runtime.close()


def test_guest_file_programs_write_atomically_and_report_presence(tmp_path):
    directory = tmp_path / "tokens" / "wi"
    items = [{"directory": str(directory), "name": "token", "content": "jwt"}]
    subprocess.run(
        [sys.executable, "-c", module._REFRESHED_FILES],
        input=json.dumps(items).encode(),
        check=True,
    )
    assert (directory / "token").read_text() == "jwt"
    assert oct(os.stat(directory / "token").st_mode & 0o777) == "0o644"
    assert oct(os.stat(directory).st_mode & 0o777) == "0o755"
    present = subprocess.run(
        [sys.executable, "-c", module._REQUIRE_FILE, str(directory / "token")], check=False
    )
    missing = subprocess.run(
        [sys.executable, "-c", module._REQUIRE_FILE, str(directory / "absent")], check=False
    )
    assert (present.returncode, missing.returncode) == (0, 43)


def test_guest_credentials_compose_from_dynamic_config():
    config = ComputeConfig.model_validate(
        {
            "pool_id": "pool",
            "max_machines": 1,
            "provider": {"adapter": "x.Provider"},
            "service_account_tokens": {
                "adapter": f"{__name__}.FakeTokens",
            },
            "credential_delivery": {
                "adapter": (
                    "volundr.adapters.outbound.guest_credential_delivery.SshPushCredentialDelivery"
                ),
                "kwargs": {"retry_seconds": 3},
            },
        }
    )
    built = build_guest_credentials(config, {"unused_port": object()})
    assert isinstance(built.service_account_tokens, FakeTokens)
    assert isinstance(built.delivery, SshPushCredentialDelivery)
    assert built.delivery._tokens is built.service_account_tokens
    assert built.guest_identity is None


def _stub_kubernetes(monkeypatch, *, response=None, error=None):
    import types

    created = []

    class ApiException(Exception):  # noqa: N818 - mirrors kubernetes_asyncio
        def __init__(self, status):
            self.status = status

    class CoreV1Api:
        def __init__(self, api_client):
            pass

        async def create_namespaced_service_account_token(self, name, namespace, body):
            created.append((name, namespace, body))
            if error is not None:
                raise ApiException(error)
            return response

    class ApiClient:
        async def close(self):
            created.append("closed")

    class ConfigException(Exception):  # noqa: N818 - mirrors kubernetes_asyncio
        pass

    def load_incluster_config():
        raise ConfigException()

    client = types.SimpleNamespace(
        ApiClient=ApiClient,
        CoreV1Api=CoreV1Api,
        AuthenticationV1TokenRequest=lambda spec: {"spec": spec},
        V1TokenRequestSpec=lambda **kwargs: kwargs,
    )
    config = types.SimpleNamespace(
        ConfigException=ConfigException,
        load_incluster_config=load_incluster_config,
        load_kube_config=AsyncMock(),
    )
    package = types.ModuleType("kubernetes_asyncio")
    package.client, package.config = client, config
    exceptions = types.ModuleType("kubernetes_asyncio.client.exceptions")
    exceptions.ApiException = ApiException
    monkeypatch.setitem(sys.modules, "kubernetes_asyncio", package)
    monkeypatch.setitem(sys.modules, "kubernetes_asyncio.client", client)
    monkeypatch.setitem(sys.modules, "kubernetes_asyncio.client.exceptions", exceptions)
    return created


async def test_kubernetes_token_issuer_requests_an_audience_bound_token(monkeypatch):
    import types

    from volundr.adapters.outbound.k8s_service_account_tokens import (
        KubernetesServiceAccountTokenIssuer,
    )

    expires = datetime.now(UTC) + timedelta(minutes=20)
    created = _stub_kubernetes(
        monkeypatch,
        response=types.SimpleNamespace(
            status=types.SimpleNamespace(token="jwt", expiration_timestamp=expires)
        ),
    )
    issuer = KubernetesServiceAccountTokenIssuer(namespace="skuld")
    issued = await issuer.issue("sa", audience="volundr-api", expiration_seconds=1200)
    assert (issued.token, issued.expires_at) == ("jwt", expires)
    assert "jwt" not in repr(issued)
    name, namespace, body = created[0]
    assert (name, namespace) == ("sa", "skuld")
    assert body["spec"] == {"audiences": ["volundr-api"], "expiration_seconds": 1200}
    assert created[-1] == "closed"


async def test_kubernetes_token_issuer_failures_name_the_remedy(monkeypatch):
    import types

    from volundr.adapters.outbound.k8s_service_account_tokens import (
        KubernetesServiceAccountTokenIssuer,
    )
    from volundr.domain.service_account_tokens import ServiceAccountTokenError

    with pytest.raises(ValueError, match="namespace"):
        KubernetesServiceAccountTokenIssuer(namespace="")
    _stub_kubernetes(monkeypatch, error=403)
    issuer = KubernetesServiceAccountTokenIssuer(namespace="skuld")
    with pytest.raises(ServiceAccountTokenError, match="serviceaccounts/token"):
        await issuer.issue("sa", audience="a", expiration_seconds=60)
    _stub_kubernetes(
        monkeypatch,
        response=types.SimpleNamespace(
            status=types.SimpleNamespace(token="", expiration_timestamp=None)
        ),
    )
    with pytest.raises(ServiceAccountTokenError, match="no token"):
        await issuer.issue("sa", audience="a", expiration_seconds=60)
    with pytest.raises(ValueError):
        await issuer.issue("", audience="a", expiration_seconds=60)


def test_vm_pod_manager_claims_forge_session_delivery_only_with_a_delivering_runtime(
    runtime, session
):
    from volundr.adapters.outbound.profiled_vm_runtime import ProfiledVmRuntime
    from volundr.adapters.outbound.vm_pod_manager import VmPodManager

    manager = VmPodManager(profile="small", pool_id="pool", max_machines=1)
    assert not manager.delivers_forge_session_token
    profiled = ProfiledVmRuntime(default=runtime)
    manager.configure_compute(
        MagicMock(), MagicMock(), profiled, MachineBootstrap(), pool_id="pool", max_machines=1
    )
    assert not manager.delivers_forge_session_token
    delivery = SshPushCredentialDelivery(service_account_tokens=FakeTokens())
    profiled.configure_credential_delivery(delivery)
    assert manager.delivers_forge_session_token
    profiled.stage_forge_session(session.id, _credential())
    assert delivery._forge_sessions[session.id].token == "forge-launch-token"
    profiled.discard_forge_session(session.id)
    assert session.id not in delivery._forge_sessions


async def test_guest_identity_composes_with_only_its_declared_ports():
    config = ComputeConfig.model_validate(
        {
            "pool_id": "pool",
            "max_machines": 1,
            "provider": {"adapter": "x.Provider"},
            "guest_identity": {
                "adapter": (
                    "volundr.adapters.outbound.guest_identity_host_key.HostKeyGuestIdentity"
                ),
                "kwargs": {"clock_skew_seconds": 10},
            },
            "credential_delivery": {
                "adapter": (
                    "volundr.adapters.outbound.guest_credential_delivery.MetadataCredentialDelivery"
                ),
                "kwargs": {"metadata_url": "https://forge.example"},
            },
        }
    )
    watermarks = MagicMock()
    built = build_guest_credentials(
        config,
        {"pool_id": "pool", "machine_bootstrap_for": AsyncMock(), "watermarks": watermarks},
    )
    assert built.delivery.identity is built.guest_identity
    config.guest_identity.kwargs["clock_skew"] = 10
    with pytest.raises(TypeError):
        build_guest_credentials(
            config,
            {"pool_id": "pool", "machine_bootstrap_for": AsyncMock(), "watermarks": watermarks},
        )


async def test_provider_attested_identity_receives_the_machine_provider():
    from volundr.adapters.outbound.guest_identity_provider import ProviderAttestedGuestIdentity

    class AttestingProvider:
        attests_guests = True
        guest_token_path = "/etc/niuu/guest-identity/token"

        async def attested_allocation(self, token):
            return uuid4()

    config = ComputeConfig.model_validate(
        {
            "pool_id": "pool",
            "max_machines": 1,
            "provider": {"adapter": "x.Provider"},
            "guest_identity": {
                "adapter": (
                    "volundr.adapters.outbound.guest_identity_provider."
                    "ProviderAttestedGuestIdentity"
                ),
            },
        }
    )
    provider = AttestingProvider()

    built = build_guest_credentials(
        config,
        {"pool_id": "pool", "watermarks": MagicMock(), "machine_provider": provider},
    )

    assert isinstance(built.guest_identity, ProviderAttestedGuestIdentity)
    assert built.guest_identity._provider is provider
