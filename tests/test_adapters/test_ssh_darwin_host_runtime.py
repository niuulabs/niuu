"""SshDarwinHostRuntime: sessions directly on a Mac, as a fresh macOS user.

The controller side runs against a recording guest access. The bundle install runs for
real against a fake registry (a ``curl`` on PATH), and the boot script against a fake
Skuld; the programs that need root and macOS (users, launchd) are proven on a Mac.
"""

import ast
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tests.test_adapters.test_ssh_vm_runtime_slots import SlotView, _keys
from volundr.adapters.outbound import darwin_guest_programs as programs
from volundr.adapters.outbound.host_checks import DARWIN_AUDIT_SH
from volundr.adapters.outbound.ssh_darwin_host_runtime import (
    BUNDLE_MEDIA_TYPE,
    DarwinSlotLayout,
    SshDarwinHostRuntime,
    _group_mode,
    translate,
)
from volundr.adapters.outbound.ssh_vm_runtime import SlotNotCleanError
from volundr.domain.compute import Machine, MachineBootstrap, MachineState
from volundr.domain.compute_hosts import HostPlatform
from volundr.domain.guest_access import GuestCommandResult
from volundr.domain.models import GitSource, PodSpecAdditions, Session, SessionSpec
from volundr.domain.session_runtimes import SessionRuntimeKind
from volundr.domain.vm_runtime import SessionImages

DIGEST = "b" * 64
BUNDLE = f"ghcr.io/niuulabs/skuld-darwin@sha256:{DIGEST}"
BAO = "quay.io/openbao/openbao@sha256:" + "c" * 64
ROOT = f"/var/lib/niuu/host-bundles/{DIGEST}"
DESCRIPTION = {"format": 1, "platform": "darwin-arm64"}


def _runtime(tmp_path: Path, **kwargs) -> SshDarwinHostRuntime:
    private, public = _keys(tmp_path)
    options = {
        "ssh_private_key_file": str(private),
        "ssh_public_key_file": str(public),
        "data_dir": str(tmp_path / "data"),
        "skuld_bundle": BUNDLE,
        "native_images": [BAO],
        "command_timeout_seconds": 0.05,
    }
    return SshDarwinHostRuntime(**{**options, **kwargs})


def _session() -> Session:
    return Session(name="test", owner_id="owner", tenant_id="tenant", source=GitSource())


SPEC = SessionSpec(values={"session": {"systemPrompt": "Be brief."}}, pod_spec=PodSpecAdditions())

#: The OpenBao agent as the secret injection describes it for VM sessions.
OPENBAO_POD = PodSpecAdditions(
    volumes=(
        {"name": "openbao-secrets", "emptyDir": {}},
        {"name": "openbao-agent-home", "emptyDir": {}},
    ),
    volume_mounts=({"name": "openbao-secrets", "mountPath": "/run/secrets", "readOnly": True},),
    init_containers=(
        {
            "name": "openbao-agent-init",
            "image": BAO,
            "command": [
                "/bin/sh",
                "-ec",
                'printf "%s" "$CONFIG" > /home/vault/config.hcl && exec bao agent '
                "-config=/home/vault/config.hcl",
            ],
            "env": [
                {
                    "name": "CONFIG",
                    "value": 'template { destination = "/run/secrets/user/claude.json" }\n'
                    'sink { path = "/home/vault/.vault-token" }',
                }
            ],
            "volumeMounts": [
                {"name": "openbao-secrets", "mountPath": "/run/secrets"},
                {"name": "openbao-agent-home", "mountPath": "/home/vault"},
            ],
            "securityContext": {"runAsUser": 100, "runAsGroup": 1000},
        },
    ),
    extra_containers=(
        {
            "name": "openbao-agent",
            "image": BAO,
            "command": ["bao", "agent", "-config=/home/vault/config.hcl"],
            "volumeMounts": [
                {"name": "openbao-secrets", "mountPath": "/run/secrets"},
                {"name": "openbao-agent-home", "mountPath": "/home/vault"},
            ],
        },
    ),
)


def _launch(bootstrap: MachineBootstrap) -> dict:
    return json.loads(
        next(f.content for f in bootstrap.files if f.path == "/etc/niuu/session-launch.json")
    )


def _machine(allocation) -> Machine:
    return Machine(
        allocation_id=allocation,
        resource_id="mac",
        state=MachineState.RUNNING,
        addresses=("192.0.2.38",),
    )


@pytest.fixture
def mac(tmp_path):
    runtime = _runtime(tmp_path)
    allocation = uuid4()
    session = _session()
    spec = SessionSpec(values=SPEC.values, pod_spec=OPENBAO_POD)
    bootstrap = runtime.bootstrap(session, spec, MachineBootstrap())
    lease = SlotView(allocation, session.id, 0, _machine(allocation))

    runtime._guest_access.execute = AsyncMock(
        return_value=GuestCommandResult(0, json.dumps(DESCRIPTION).encode())
    )
    runtime._guest_access.open_tunnel = AsyncMock()
    runtime._guest_access.close_tunnel = AsyncMock()
    return runtime, bootstrap, lease


def _calls(runtime):
    return runtime._guest_access.execute.await_args_list


def _program(call) -> str:
    return call.args[2]


# -- configuration -----------------------------------------------------------------------------


def test_a_mac_runtime_runs_sessions_on_darwin_hosts_only(tmp_path):
    runtime = _runtime(tmp_path)

    assert runtime.host_platforms == frozenset({HostPlatform.DARWIN})
    assert runtime.session_runtime is SessionRuntimeKind.HOST
    assert runtime.requires_root_daemon is False
    assert runtime.supports_slots is False
    assert runtime.supports_reuse is True
    limits = runtime.slot_limits
    assert (limits.uid_base, limits.cpu, limits.memory_mib, limits.pids) == (
        61000,
        None,
        None,
        None,
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"slot_isolation": {"cpu": 1, "memory_mib": 1, "pids": 1}}, "remove slot_isolation"),
        ({"skuld_image": "ghcr.io/niuulabs/skuld:dev"}, "set skuld_bundle"),
        ({"skuld_bundle": "ghcr.io/niuulabs/skuld-darwin:dev"}, "digest-pinned"),
        ({"uid_base": 500}, "uid_base"),
        ({"session_start_timeout_seconds": 0}, "session_start_timeout_seconds"),
        ({"native_images": ["quay.io/openbao/openbao:2.5.3"]}, "native_images"),
    ],
)
def test_configuration_a_mac_cannot_honour_is_refused(tmp_path, kwargs, message):
    with pytest.raises(ValueError, match=message):
        _runtime(tmp_path, **kwargs)


def test_the_release_bundle_stands_in_for_skuld_bundle(tmp_path):
    runtime = _runtime(tmp_path, skuld_bundle=None, session_images=SessionImages(darwin=BUNDLE))

    assert runtime._python("print(1)").startswith(f"sudo -n {ROOT}/python/bin/python3 -I -c ")


def test_the_release_bundle_and_a_configured_one_are_not_both_accepted(tmp_path):
    with pytest.raises(ValueError, match="remove skuld_bundle to run the release's"):
        _runtime(tmp_path, session_images=SessionImages(darwin=BUNDLE))


def test_a_mac_runtime_needs_a_bundle_from_one_place_or_the_other(tmp_path):
    for images in (None, SessionImages(linux="ghcr.io/niuulabs/openshell@sha256:" + "a" * 64)):
        with pytest.raises(ValueError, match="compute.session_images.darwin"):
            _runtime(tmp_path, skuld_bundle=None, session_images=images)


def test_a_release_bundle_must_still_be_digest_pinned(tmp_path):
    images = SessionImages(darwin="ghcr.io/niuulabs/skuld-darwin:dev")

    with pytest.raises(ValueError, match="digest-pinned"):
        _runtime(tmp_path, skuld_bundle=None, session_images=images)


async def test_recycling_a_mac_slot_audits_slot_zero_alone(mac):
    runtime, bootstrap, lease = mac
    runtime._guest_access.execute = AsyncMock(return_value=GuestCommandResult(0, b""))

    await runtime.recycle(lease, bootstrap)

    [call] = runtime._guest_access.execute.await_args_list
    assert call.args[2] == "sudo -n sh -s -- 61000 1 '' 0 check 0"
    assert call.kwargs["data"] == DARWIN_AUDIT_SH.encode()


async def test_a_mac_slot_with_anything_left_is_not_recycled(mac):
    runtime, bootstrap, lease = mac
    runtime._guest_access.execute = AsyncMock(
        return_value=GuestCommandResult(0, b"finding user niuu-slot-0 still exists\n")
    )

    with pytest.raises(SlotNotCleanError, match="user niuu-slot-0 still exists"):
        await runtime.recycle(lease, bootstrap)


def test_guest_programs_run_under_the_bundles_isolated_python_as_root(tmp_path):
    command = _runtime(tmp_path)._python("print(1)", "arg")

    assert command.startswith(f"sudo -n {ROOT}/python/bin/python3 -I -c ")
    assert command.endswith(" arg")


def test_every_guest_program_parses_and_install_runs_on_apples_python():
    for name in ("INSTALL", "PRUNE", "SLOT", "START", "INIT", "STOP"):
        ast.parse(getattr(programs, name))
    # INSTALL runs before the bundle exists, under the Command Line Tools' Python 3.9.
    ast.parse(programs.INSTALL, feature_version=(3, 9))


def test_a_mac_has_only_slot_zero(tmp_path):
    with pytest.raises(ValueError, match="one session at a time"):
        DarwinSlotLayout(allocation_id=uuid4(), slot=1, uid_base=61000)


def test_the_slot_layout_is_where_the_darwin_audit_looks():
    layout = DarwinSlotLayout(allocation_id=uuid4(), slot=0, uid_base=61000)

    assert (layout.name, layout.uid, layout.label) == ("niuu-slot-0", 61000, "world.niuu.slot.0")
    assert layout.key == str(layout.allocation_id)
    assert layout.data_root == "/var/lib/niuu/slots/0"
    assert layout.run_root == "/var/run/niuu/slots/0"
    assert (
        layout.relocate("/etc/niuu/session-secrets/0") == "/var/run/niuu/slots/0/session-secrets/0"
    )
    assert layout.relocate("/etc/niuu/guest-agent") == "/var/run/niuu/host-sessions/0/guest-agent"
    assert layout.relocate("/run/niuu/session/volumes/x") == "/var/run/niuu/slots/0/run/volumes/x"
    assert layout.relocate("/etc/ssh/ssh_host_ed25519_key") == "/etc/ssh/ssh_host_ed25519_key"
    spec = layout.spec(stop_timeout=30, poll=2)
    assert spec["audit"] == DARWIN_AUDIT_SH
    assert spec["labels"] == ["world.niuu.slot.0"]
    assert layout.launch({"a": 1}, platform_port=1) == {"a": 1}


# -- translation -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/workspace", "/mac/ws"),
        ("/workspace/repo", "/mac/ws/repo"),
        ("cd /workspace && ls", "cd /mac/ws && ls"),
        ('path = "/run/secrets/user/x"', 'path = "/mac/secrets/user/x"'),
        ("-config=/home/vault/config.hcl", "-config=/mac/vault/config.hcl"),
        ("/a:/workspace/bin:/b", "/a:/mac/ws/bin:/b"),
        ("https://x/workspace/y", "https://x/workspace/y"),
        ("/workspaces", "/workspaces"),
        ("/workspace.bak", "/workspace.bak"),
        ("/var/run/secrets", "/var/run/secrets"),
        ("/run/secrets-old", "/run/secrets-old"),
    ],
)
def test_translation_moves_whole_paths_only(text, expected):
    places = {"/workspace": "/mac/ws", "/run/secrets": "/mac/secrets", "/home/vault": "/mac/vault"}

    assert translate(text, places) == expected


def test_translation_is_one_pass_longest_first():
    places = {"/a": "/a/b", "/a/b": "/z"}

    assert translate("/a/b/c /a/x", places) == "/z/c /a/b/x"
    assert translate("/a", {}) == "/a"


@pytest.mark.parametrize(
    ("mode", "expected"), [("0777", "0770"), ("0755", "0750"), ("0700", "0700")]
)
def test_world_access_becomes_the_slot_groups(mode, expected):
    assert _group_mode(mode) == expected


# -- the session's bootstrap -------------------------------------------------------------------


def test_the_session_runs_the_bundles_agents_first_then_nix_then_the_mac(tmp_path):
    runtime = _runtime(tmp_path)

    launch = _launch(runtime.bootstrap(_session(), SPEC, MachineBootstrap()))

    environment = launch["environment"]
    assert environment["PATH"].split(":") == [
        f"{ROOT}/bin",
        "/home/skuld/.nix-profile/bin",
        "/nix/var/nix/profiles/default/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
        f"{ROOT}/fallback",
    ]
    assert environment["NIUU_SKULD"] == f"{ROOT}/libexec/skuld"
    assert environment["NIUU_PYTHON"] == f"{ROOT}/python/bin/python3"
    assert environment["NIX_REMOTE"] == "daemon"
    prompt = environment["SKULD__SESSION__SYSTEM_PROMPT"]
    assert prompt.startswith("Be brief.\n\n")
    assert "directly on a Mac (macOS) as the unprivileged user {slot_user}" in prompt
    assert "nix profile install" in prompt
    assert environment["SKULD__PREVIEW__ENABLED"] == "false"


def test_without_nix_the_agent_is_only_told_where_it_runs(tmp_path):
    runtime = _runtime(tmp_path, nix=False)
    spec = SessionSpec(values={"session": {}}, pod_spec=PodSpecAdditions())

    environment = _launch(runtime.bootstrap(_session(), spec, MachineBootstrap()))["environment"]

    assert "nix" not in environment["PATH"]
    assert "NIX_REMOTE" not in environment
    assert environment["SKULD__SESSION__SYSTEM_PROMPT"].startswith("You are running directly")


def test_live_preview_finds_the_sessions_listeners_with_lsof(tmp_path):
    runtime = _runtime(
        tmp_path, preview_router_auth={"adapter": "skuld.preview_adapters.X", "kwargs": {}}
    )
    session = _session()

    environment = _launch(runtime.bootstrap(session, SPEC, MachineBootstrap()))["environment"]

    scanner = json.loads(environment["SKULD__PREVIEW__PORT_SCANNER"])
    assert scanner["adapter"] == "skuld.preview_adapters.LsofSessionPortScanner"
    assert scanner["kwargs"]["session_id"] == str(session.id)


def test_a_container_the_mac_cannot_run_is_refused_at_create(tmp_path):
    runtime = _runtime(tmp_path, native_images=[])
    spec = SessionSpec(values=SPEC.values, pod_spec=OPENBAO_POD)

    with pytest.raises(ValueError, match="native_images"):
        runtime.bootstrap(_session(), spec, MachineBootstrap())


# -- start and stop ----------------------------------------------------------------------------


async def test_start_fetches_the_bundle_makes_the_slot_then_starts_the_session(mac):
    runtime, bootstrap, lease = mac
    steps = []
    runtime.report_startup_to(lambda sid, key, label: steps.append(key))

    await runtime.start(lease, bootstrap)

    calls = _calls(runtime)
    commands = [_program(call) for call in calls]
    assert commands[0].startswith("sudo -n python3 -c ")
    assert commands[0].endswith(f"{BUNDLE} /var/lib/niuu/host-bundles {BUNDLE_MEDIA_TYPE}")
    assert commands[1] == runtime._python(
        programs.PRUNE, "/var/lib/niuu/host-bundles", DIGEST, "world.niuu.slot.0"
    )
    assert commands[2] == runtime._python(programs.SLOT, "up")
    slot = json.loads(calls[2].kwargs["data"])
    assert (slot["name"], slot["uid"]) == ("niuu-slot-0", 61000)
    assert slot["labels"] == ["world.niuu.slot.0", "world.niuu.slot.0.openbao-agent"]
    assert calls[2].kwargs["safe_error_prefix"] == "NIUU_SLOT_STAGE:"
    assert commands[-1] == runtime._python(programs.START)
    placed = json.loads(calls[-1].kwargs["data"])
    environment = placed["environment"]
    assert environment["HOME"] == "/var/lib/niuu/slots/0/session/home"
    assert environment["WORKSPACE_DIR"] == "/var/lib/niuu/slots/0/session/workspace"
    assert environment["TMPDIR"] == "/var/lib/niuu/slots/0/tmp"
    assert (
        environment["PATH"].split(":")[1] == "/var/lib/niuu/slots/0/session/home/.nix-profile/bin"
    )
    assert environment["NIUU_SECRETS_DIR"] == "/var/run/niuu/slots/0/run/volumes/openbao-secrets"
    assert json.loads(environment["NIUU_SESSION_PLACES"]) == {
        "/home/skuld": "/var/lib/niuu/slots/0/session/home",
        "/workspace": "/var/lib/niuu/slots/0/session/workspace",
    }
    assert json.loads(environment["NIUU_SESSION_LINKS"]) == []
    assert "unprivileged user niuu-slot-0" in environment["SKULD__SESSION__SYSTEM_PROMPT"]
    assert environment["SKULD__HOST"] == "127.0.0.1"
    init = placed["init_containers"][0]
    secrets = "/var/run/niuu/slots/0/run/volumes/openbao-secrets"
    vault = "/var/run/niuu/slots/0/run/volumes/openbao-agent-home"
    assert init["command"][2] == (
        f'printf "%s" "$CONFIG" > {vault}/config.hcl && exec bao agent -config={vault}/config.hcl'
    )
    assert init["environment"]["CONFIG"] == (
        f'template {{ destination = "{secrets}/user/claude.json" }}\n'
        f'sink {{ path = "{vault}/.vault-token" }}'
    )
    assert placed["sidecars"][0]["command"] == ["bao", "agent", f"-config={vault}/config.hcl"]
    modes = {item["path"]: item["mode"] for item in placed["directories"]}
    # The session reads its secrets through its group; the agent's home is root's alone.
    assert modes[secrets] == "0770"
    assert modes[vault] == "0700"
    darwin = placed["darwin"]
    assert darwin["label"] == "world.niuu.slot.0"
    assert darwin["python"] == f"{ROOT}/python/bin/python3"
    assert darwin["native_path"].startswith(f"{ROOT}/libexec:{ROOT}/bin:")
    assert darwin["init"] == programs.INIT and darwin["boot"] == programs.BOOT
    assert darwin["links"] == []
    assert placed["slot"] == {
        "index": 0,
        "uid": 61000,
        "data_root": "/var/lib/niuu/slots/0/session",
        "marker": str(lease.id),
    }
    runtime._guest_access.open_tunnel.assert_awaited_with(
        lease,
        bootstrap,
        remote_port=8081,
        reverse_bind_host="127.0.0.1",
        reverse_port=18080,
        controller_host="127.0.0.1",
        controller_port=8080,
        key=str(lease.id),
    )
    assert steps == ["guest_bundle", "guest_slot", "guest_files", "guest_tunnel", "guest_host"]


async def test_mounts_at_fixed_paths_become_links_of_the_session_or_of_root(tmp_path):
    runtime = _runtime(tmp_path)
    layout = DarwinSlotLayout(allocation_id=uuid4(), slot=0, uid_base=61000)
    payload = {
        "environment": {"TOKEN": "/var/run/secrets/niuu-workload/token"},
        "secret_mounts": [
            {"source": "/etc/niuu/session-secrets/0", "target": "/home/skuld/.config/gh/hosts.yml"}
        ],
        "volume_mounts": [
            {
                "source": "/run/niuu/session/tokens/workload",
                "target": "/var/run/secrets/niuu-workload",
                "readonly": True,
            }
        ],
        "directories": [{"path": "/run/niuu/session/tokens/workload", "mode": "0755"}],
    }

    placed = runtime._place(layout, payload)

    tokens = "/var/run/niuu/slots/0/run/tokens/workload"
    assert placed["environment"]["TOKEN"] == f"{tokens}/token"
    assert json.loads(placed["environment"]["NIUU_SESSION_LINKS"]) == [
        {
            "target": "/var/lib/niuu/slots/0/session/home/.config/gh/hosts.yml",
            "source": "/var/run/niuu/slots/0/session-secrets/0",
        }
    ]
    assert placed["darwin"]["links"] == [
        {"target": "/var/run/secrets/niuu-workload", "source": tokens}
    ]
    assert placed["directories"] == [{"path": tokens, "mode": "0750"}]
    assert "NIUU_SECRETS_DIR" not in placed["environment"]
    spec = runtime._slot_spec(layout, payload)
    assert spec["links"] == ["/var/run/secrets/niuu-workload"]


async def test_the_guest_agents_configuration_names_the_macs_places(mac):
    runtime, bootstrap, lease = mac
    config = {"token_volumes": [{"directory": "/run/niuu/session/tokens/sa"}]}

    await runtime.write_files(
        lease,
        bootstrap,
        [
            {
                "directory": "/etc/niuu/guest-agent",
                "name": "config.json",
                "content": json.dumps(config),
            },
            {"directory": "/run/niuu/session/tokens/sa", "name": "token", "content": "/run/niuu/x"},
        ],
    )

    items = json.loads(_calls(runtime)[-1].kwargs["data"])
    assert items[0]["directory"] == "/var/run/niuu/host-sessions/0/guest-agent"
    assert json.loads(items[0]["content"]) == {
        "token_volumes": [{"directory": "/var/run/niuu/slots/0/run/tokens/sa"}]
    }
    # A credential's own content is never rewritten.
    assert items[1] == {
        "directory": "/var/run/niuu/slots/0/run/tokens/sa",
        "name": "token",
        "content": "/run/niuu/x",
    }


async def test_warm_reaches_the_mac_and_fetches_the_bundle(mac, tmp_path):
    runtime, bootstrap, lease = mac
    runtime.prepare = AsyncMock()

    await runtime.warm(lease, bootstrap)

    runtime.prepare.assert_awaited_once()
    assert BUNDLE_MEDIA_TYPE in _program(_calls(runtime)[0])


async def test_stop_ends_the_jobs_archives_then_removes_the_slot_verified(mac):
    runtime, bootstrap, lease = mac

    await runtime.stop(lease, bootstrap)

    calls = _calls(runtime)
    commands = [_program(call) for call in calls]
    assert commands[0] == runtime._python(
        programs.STOP,
        json.dumps(
            {
                "labels": ["world.niuu.slot.0", "world.niuu.slot.0.openbao-agent"],
                "state_root": "/var/run/niuu/host-sessions/0",
                "data_root": "/var/lib/niuu/slots/0/session",
                "marker": str(lease.id),
                "stop_timeout": 30,
                "poll": 2,
            }
        ),
    )
    assert calls[0].kwargs["expected_exit_codes"] == (0, 42)
    assert "-C /var/lib/niuu/slots/0/session" in commands[1]
    assert commands[2] == runtime._python(programs.SLOT, "down")
    runtime._guest_access.close_tunnel.assert_awaited_once_with(str(lease.id))


async def test_a_stop_before_storage_was_prepared_keeps_the_previous_archive(mac):
    runtime, bootstrap, lease = mac
    runtime._guest_access.execute = AsyncMock(return_value=GuestCommandResult(42, b""))
    delivery = AsyncMock()
    runtime._credential_delivery = delivery

    await runtime.stop(lease, bootstrap)

    commands = [_program(call) for call in _calls(runtime)]
    assert not any(" tar " in command for command in commands)
    assert commands[-1] == runtime._python(programs.SLOT, "down")
    delivery.stop.assert_awaited_once_with(lease, bootstrap)


# -- the install program, against a fake registry ----------------------------------------------


def _bundle_archive(description: dict) -> bytes:
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        content = json.dumps(description).encode()
        info = tarfile.TarInfo("./bundle.json")
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    return data.getvalue()


FAKE_CURL = r"""#!PYTHON
import json, os, sys
args = sys.argv[1:]
def value(flag):
    return args[args.index(flag) + 1]
url, output, headers = args[-1], value('--output'), value('--dump-header')
auth = [a for a in args if a.startswith('Authorization: ')]
served = json.load(open(os.environ['FAKE_REGISTRY']))
open(os.environ['FAKE_LOG'], 'a').write(url + ' ' + ('auth' if auth else 'anon') + '\n')
if url.startswith('https://ghcr.io/token'):
    open(output, 'w').write(json.dumps({'token': 'anonymous'}))
    open(headers, 'w').write('')
    print('200', end='')
    sys.exit(0)
if not auth:
    open(output, 'w').write('')
    open(headers, 'w').write('HTTP/2 401\r\nwww-authenticate: Bearer realm="https://ghcr.io/token",'
        'service="ghcr.io",scope="repository:niuulabs/skuld-darwin:pull"\r\n')
    print('401', end='')
    sys.exit(0)
path = served.get(url.split('/v2/niuulabs/skuld-darwin/', 1)[1])
if path is None:
    print('404', end='')
    sys.exit(0)
open(output, 'wb').write(open(path, 'rb').read())
open(headers, 'w').write('')
print('200', end='')
"""


@pytest.fixture
def registry(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(FAKE_CURL.replace("PYTHON", sys.executable))
    curl.chmod(0o755)
    for tool in ("chown", "chmod"):
        # Root's ownership change is not this test's to make.
        (bin_dir / tool).write_text("#!/bin/sh\nexit 0\n")
        (bin_dir / tool).chmod(0o755)
    served: dict[str, str] = {}
    (tmp_path / "served.json").write_text("{}")

    def serve(archive: bytes, *, media_type=BUNDLE_MEDIA_TYPE, layers=1):
        blob = tmp_path / "blob"
        blob.write_bytes(archive)
        layer = hashlib.sha256(archive).hexdigest()
        manifest = json.dumps(
            {"layers": [{"mediaType": media_type, "digest": f"sha256:{layer}"}] * layers}
        ).encode()
        digest = hashlib.sha256(manifest).hexdigest()
        (tmp_path / "manifest").write_bytes(manifest)
        served.update(
            {
                f"manifests/sha256:{digest}": str(tmp_path / "manifest"),
                f"blobs/sha256:{layer}": str(blob),
            }
        )
        (tmp_path / "served.json").write_text(json.dumps(served))
        return digest

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_REGISTRY": str(tmp_path / "served.json"),
        "FAKE_LOG": str(tmp_path / "log"),
    }
    return serve, env, tmp_path


def _install(env, digest, cache, tag=""):
    return subprocess.run(
        [
            sys.executable,
            "-c",
            programs.INSTALL,
            f"ghcr.io/niuulabs/skuld-darwin{tag}@sha256:{digest}",
            str(cache),
            BUNDLE_MEDIA_TYPE,
        ],
        env=env,
        capture_output=True,
        text=True,
    )


def test_install_fetches_the_bundle_anonymously_verifies_and_unpacks_it(registry):
    serve, env, tmp_path = registry
    description = {**DESCRIPTION, "platform": f"darwin-{os.uname().machine}"}
    digest = serve(_bundle_archive(description))
    cache = tmp_path / "cache"

    result = _install(env, digest, cache)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == description
    assert json.loads((cache / digest / "bundle.json").read_text()) == description
    assert [path.name for path in cache.iterdir()] == [digest]
    log = (tmp_path / "log").read_text().split("\n")
    assert log[0].endswith(f"manifests/sha256:{digest} anon")
    assert log[1].startswith("https://ghcr.io/token?service=ghcr.io&scope=repository:niuulabs")
    assert log[2].endswith(f"manifests/sha256:{digest} auth")
    # A second install reads the unpacked bundle and fetches nothing.
    before = (tmp_path / "log").read_text()
    assert _install(env, digest, cache).returncode == 0
    assert (tmp_path / "log").read_text() == before


def test_install_fetches_a_pin_that_names_its_tag_by_digest(registry):
    serve, env, tmp_path = registry
    description = {**DESCRIPTION, "platform": f"darwin-{os.uname().machine}"}
    digest = serve(_bundle_archive(description))

    result = _install(env, digest, tmp_path / "cache", tag=":dev-" + "e" * 40)

    assert result.returncode == 0, result.stderr
    assert f"/v2/niuulabs/skuld-darwin/manifests/sha256:{digest}" in (tmp_path / "log").read_text()


def test_install_refuses_a_bundle_whose_digest_the_registry_does_not_match(registry):
    serve, env, tmp_path = registry
    serve(_bundle_archive(DESCRIPTION))

    result = _install(env, "d" * 64, tmp_path / "cache")

    assert result.returncode != 0
    assert "answered HTTP 404" in result.stderr


def test_install_refuses_a_bundle_for_another_platform(registry):
    serve, env, tmp_path = registry
    digest = serve(_bundle_archive({"format": 1, "platform": "darwin-x86_128"}))

    result = _install(env, digest, tmp_path / "cache")

    assert result.returncode != 0
    assert "is a bundle for darwin-x86_128" in result.stderr
    assert not (tmp_path / "cache" / digest).exists()


def test_install_refuses_an_artifact_without_exactly_one_bundle_layer(registry):
    serve, env, tmp_path = registry
    digest = serve(_bundle_archive(DESCRIPTION), layers=2)

    result = _install(env, digest, tmp_path / "cache")

    assert result.returncode != 0
    assert "holds 2 layers" in result.stderr


def test_install_needs_a_digest_pinned_reference(registry):
    _, env, tmp_path = registry

    result = subprocess.run(
        [sys.executable, "-c", programs.INSTALL, "ghcr.io/x/y:dev", str(tmp_path), "t"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert "must be pinned by digest" in result.stderr


# -- the boot script, against a fake Skuld -----------------------------------------------------


def test_boot_turns_secrets_into_environment_and_links_then_runs_skuld(tmp_path):
    secrets = tmp_path / "secrets"
    (secrets / "user").mkdir(parents=True)
    (secrets / "user" / "claude.json").write_text(json.dumps({"token": "sk-oauth"}))
    (secrets / "user" / "gh.yml").write_text("hosts")
    (secrets / "manifest.json").write_text(
        json.dumps(
            {
                "env": {"CLAUDE_CODE_OAUTH_TOKEN": {"file": "claude.json", "key": "token"}},
                "files": {"/home/skuld/.config/gh/hosts.yml": {"file": "gh.yml"}},
            }
        )
    )
    (secrets / "env.sh").write_text("export FROM_ENV_SH=yes\n")
    home, workspace = tmp_path / "home", tmp_path / "workspace"
    home.mkdir()
    skuld = tmp_path / "skuld"
    skuld.write_text('#!/bin/sh\nenv > "$HOME/env.txt"\n')
    skuld.chmod(0o755)
    source = tmp_path / "token"
    source.write_text("t")
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "WORKSPACE_DIR": str(workspace),
        "TMPDIR": str(tmp_path / "tmp"),
        "NIUU_PYTHON": sys.executable,
        "NIUU_SKULD": str(skuld),
        "NIUU_SECRETS_DIR": str(secrets),
        "NIUU_SESSION_PLACES": json.dumps({"/home/skuld": str(home), "/workspace": str(workspace)}),
        "NIUU_SESSION_LINKS": json.dumps(
            [{"target": str(home / ".kube" / "token"), "source": str(source)}]
        ),
    }

    result = subprocess.run(
        ["/bin/sh", "-c", programs.BOOT], env=env, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    seen = dict(
        line.split("=", 1) for line in (home / "env.txt").read_text().splitlines() if "=" in line
    )
    assert seen["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-oauth"
    assert seen["FROM_ENV_SH"] == "yes"
    assert seen["CLAUDE_CONFIG_DIR"] == f"{home}/.claude"
    assert not {"NIUU_SESSION_LINKS", "NIUU_SECRETS_DIR", "NIUU_SKULD"} & set(seen)
    assert os.readlink(home / ".config" / "gh" / "hosts.yml") == str(secrets / "user" / "gh.yml")
    assert os.readlink(home / ".kube" / "token") == str(source)
    assert workspace.is_dir()


def test_boot_refuses_a_secret_file_outside_the_sessions_storage(tmp_path):
    secrets = tmp_path / "secrets"
    (secrets / "user").mkdir(parents=True)
    (secrets / "user" / "x").write_text("x")
    (secrets / "manifest.json").write_text(json.dumps({"files": {"/etc/x": {"file": "x"}}}))
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "WORKSPACE_DIR": str(tmp_path / "w"),
        "TMPDIR": str(tmp_path / "t"),
        "NIUU_PYTHON": sys.executable,
        "NIUU_SKULD": "/usr/bin/true",
        "NIUU_SECRETS_DIR": str(secrets),
        "NIUU_SESSION_PLACES": json.dumps({"/home/skuld": str(tmp_path)}),
        "NIUU_SESSION_LINKS": "[]",
    }

    result = subprocess.run(
        ["/bin/sh", "-c", programs.BOOT], env=env, capture_output=True, text=True
    )

    assert result.returncode != 0
    assert "lies outside the session home and workspace" in result.stderr


def test_boot_refuses_a_file_in_the_way_of_a_mount(tmp_path):
    (tmp_path / "taken").write_text("mine")
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "WORKSPACE_DIR": str(tmp_path / "w"),
        "TMPDIR": str(tmp_path / "t"),
        "NIUU_PYTHON": sys.executable,
        "NIUU_SKULD": "/usr/bin/true",
        "NIUU_SESSION_PLACES": "{}",
        "NIUU_SESSION_LINKS": json.dumps([{"target": str(tmp_path / "taken"), "source": "/x"}]),
    }

    result = subprocess.run(
        ["/bin/sh", "-c", programs.BOOT], env=env, capture_output=True, text=True
    )

    assert result.returncode != 0
    assert "is in the way" in result.stderr
