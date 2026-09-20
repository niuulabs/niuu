"""Pinned SSH and local-data preservation checks; all remote processes are fake."""

import asyncio
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from volundr.adapters.outbound import ssh_vm_runtime as module
from volundr.adapters.outbound.ssh_host_preparation import SshHostPreparation
from volundr.adapters.outbound.ssh_vm_runtime import SshContainerVmRuntime
from volundr.domain.compute import (
    BootstrapFile,
    ComputeLease,
    Machine,
    MachineBootstrap,
    MachineState,
)
from volundr.domain.guest_access import GuestCommandResult
from volundr.domain.models import GitSource, PodSpecAdditions, Session, SessionSpec
from volundr.domain.vm_runtime import VmRuntimeStageError, VmRuntimeUnavailableError


def _archive_bytes(*members: tarfile.TarInfo) -> bytes:
    destination = io.BytesIO()
    with tarfile.open(fileobj=destination, mode="w") as archive:
        for member in members:
            content = b"persistent" if member.isreg() else None
            archive.addfile(member, io.BytesIO(content) if content is not None else None)
    return destination.getvalue()


def test_restore_skips_codex_tmp_links_but_keeps_data_filter_for_other_links(tmp_path: Path):
    allocation = str(uuid4())
    root = tmp_path / "restored"
    script = module._PREPARE.replace(
        "pathlib.Path('/var/lib/niuu/session')", f"pathlib.Path({str(root)!r})"
    )
    ephemeral = tarfile.TarInfo("./home/.codex/tmp/arg0/codex-arg0/codex-execve-wrapper")
    ephemeral.type = tarfile.SYMTYPE
    ephemeral.linkname = "/usr/lib/node_modules/@openai/codex/bin/codex"
    persistent = tarfile.TarInfo("./home/.codex/sessions/thread.jsonl")
    persistent.size = len(b"persistent")

    restored = subprocess.run(
        [sys.executable, "-c", script, allocation, "restore", '["home/.codex/tmp"]'],
        input=_archive_bytes(ephemeral, persistent),
        capture_output=True,
        check=False,
    )

    assert restored.returncode == 0, restored.stderr
    assert (root / "home/.codex/sessions/thread.jsonl").read_bytes() == b"persistent"
    assert not (root / "home/.codex/tmp").exists()

    unsafe_root = tmp_path / "unsafe"
    unsafe_script = module._PREPARE.replace(
        "pathlib.Path('/var/lib/niuu/session')", f"pathlib.Path({str(unsafe_root)!r})"
    )
    unsafe = tarfile.TarInfo("./home/persistent-link")
    unsafe.type = tarfile.SYMTYPE
    unsafe.linkname = "/etc/passwd"

    rejected = subprocess.run(
        [sys.executable, "-c", unsafe_script, allocation, "restore", '["home/.codex/tmp"]'],
        input=_archive_bytes(unsafe),
        capture_output=True,
        check=False,
    )

    assert rejected.returncode != 0
    assert not (unsafe_root / ".allocation").exists()


@pytest.fixture
def setup(tmp_path):
    key = Ed25519PrivateKey.generate()
    private = tmp_path / "key"
    public = tmp_path / "key.pub"
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
    runtime = SshContainerVmRuntime(
        ssh_private_key_file=str(private),
        ssh_public_key_file=str(public),
        data_dir=str(tmp_path / "data"),
        skuld_image="test/skuld:dev",
        command_timeout_seconds=0.05,
    )
    session = Session(name="test", owner_id="owner", tenant_id="tenant", source=GitSource())
    spec = SessionSpec(
        values={"session": {"systemPrompt": "first\nsecond"}}, pod_spec=PodSpecAdditions()
    )
    bootstrap = runtime.bootstrap(session, spec, MachineBootstrap())
    allocation = uuid4()
    lease = ComputeLease(
        id=allocation,
        session_id=session.id,
        pool_id="test",
        profile="small",
        owner_id="owner",
        tenant_id="tenant",
        request_fingerprint="test",
        machine=Machine(
            allocation_id=allocation,
            resource_id="test-vm",
            state=MachineState.RUNNING,
            addresses=("192.0.2.10",),
        ),
    )
    return runtime, session, spec, bootstrap, lease


def test_bootstrap_unique_host_identity_and_no_host_environment(setup, monkeypatch):
    runtime, session, spec, bootstrap, lease = setup
    monkeypatch.setenv("PROVIDER_ADMIN_TOKEN", "never-copy")
    another = runtime.bootstrap(session, spec, MachineBootstrap())
    assert bootstrap != another
    files = {f.path: f.content for f in bootstrap.files}
    assert files["/etc/ssh/sshd_config.d/10-niuu-host-key.conf"] == f"HostKey {module._HOST_KEY}\n"
    assert not module._HOST_KEY.startswith("/etc/ssh/ssh_host_")
    payload = json.loads(files[module._LAUNCH])
    assert payload["environment"]["SKULD__SESSION__SYSTEM_PROMPT"] == "first\nsecond"
    assert payload["environment"]["SKULD__HOST"] == "127.0.0.1"
    assert payload["environment"]["SESSION_ID"] == str(session.id)
    assert payload["environment"]["WORKSPACE_DIR"] == "/workspace"
    assert "PROVIDER_ADMIN_TOKEN" not in payload["environment"]
    argv = runtime._ssh(lease, bootstrap)
    assert "StrictHostKeyChecking=yes" in argv
    assert "IdentitiesOnly=yes" in argv
    known = runtime._data / str(lease.id) / "known_hosts"
    assert files[module._HOST_KEY + ".pub"] in known.read_text()
    assert "niuu-" + str(lease.id) in known.read_text()
    assert files[module._HOST_KEY] not in known.read_text()


@pytest.mark.parametrize("change", ["volume", "env-reference", "storage", "nul", "file-conflict"])
def test_unsupported_contract_rejected_before_vm_allocation(setup, change):
    runtime, session, spec, bootstrap, lease = setup
    defaults = MachineBootstrap()
    if change == "volume":
        spec.pod_spec = PodSpecAdditions(volumes=({"name": "pvc"},))
    if change == "env-reference":
        spec.pod_spec = PodSpecAdditions(env=({"name": "SECRET", "valueFrom": {}},))
    if change == "storage":
        spec.values["persistence"] = {"existingClaim": "data"}
    if change == "nul":
        spec.values["env"] = {"BAD": "\x00"}
    if change == "file-conflict":
        defaults = MachineBootstrap(
            files=(BootstrapFile(path=module._HOST_KEY, content="collision"),)
        )
    with pytest.raises(ValueError):
        runtime.bootstrap(session, spec, defaults)


async def test_start_restores_archive_then_uses_persisted_launch_payload(setup):
    runtime, session, spec, bootstrap, lease = setup
    runtime._ssh(lease, bootstrap)
    archive = runtime._data / str(session.id) / "session.tar"
    archive.parent.mkdir(mode=0o700, parents=True)
    archive.write_bytes(b"explicit-test-archive")
    runtime._guest_access.ensure = AsyncMock()
    runtime._guest_access.execute = AsyncMock(return_value=GuestCommandResult(0))
    runtime.target = AsyncMock()
    await runtime.prepare(lease, bootstrap)
    await runtime.start(lease, bootstrap)
    calls = runtime._guest_access.execute.await_args_list
    assert json.loads(calls[0].kwargs["data"])[0]["path"] == module._LAUNCH
    assert "stdin" in calls[1].kwargs
    assert json.loads(calls[2].kwargs["data"])["allocation_id"] == str(lease.id)
    runtime.target.assert_awaited_once()
    archive.unlink()
    runtime._guest_access.execute.reset_mock()
    await runtime.start(lease, bootstrap)
    assert "stdin" not in runtime._guest_access.execute.await_args_list[1].kwargs


async def test_explicit_ssh_bootstrap_uses_tofu_once_then_strict_pin(setup, tmp_path):
    runtime, session, spec, provider_bootstrap, lease = setup
    post_connect = SshContainerVmRuntime(
        ssh_private_key_file=runtime._private_key,
        ssh_public_key_file=runtime._private_key + ".pub",
        data_dir=str(tmp_path / "post-connect"),
        skuld_image="test/skuld:dev",
        bootstrap_delivery="ssh",
    )
    launch_sentinel = "session-launch-must-be-strict"
    credential_sentinel = "credential-must-be-strict"
    spec.values["session"]["systemPrompt"] = launch_sentinel
    credential_file = tmp_path / "credential"
    credential_file.write_text(credential_sentinel)
    spec.pod_spec = PodSpecAdditions(
        volumes=(
            {
                "name": "credential",
                "hostPath": {"path": str(credential_file), "type": "File"},
            },
        ),
        volume_mounts=(
            {
                "name": "credential",
                "mountPath": "/run/secrets/credential",
                "readOnly": True,
            },
        ),
    )
    bootstrap = post_connect.bootstrap(session, spec, MachineBootstrap())
    provider_private = next(
        item.content for item in provider_bootstrap.files if item.path == module._HOST_KEY
    )
    assert all(
        item.path not in {module._HOST_KEY, module._HOST_KEY + ".pub"} for item in bootstrap.files
    )
    assert post_connect._public_key in bootstrap.ssh_authorized_keys
    calls = []

    async def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if "StrictHostKeyChecking=accept-new" in argv:
            known_option = next(arg for arg in argv if arg.startswith("UserKnownHostsFile="))
            known_path = module.Path(known_option.split("=", 1)[1])
            alias_option = next(arg for arg in argv if arg.startswith("HostKeyAlias="))
            known_path.write_text(f"{alias_option.split('=', 1)[1]} {runtime._public_key}\n")
        return GuestCommandResult(0)

    post_connect._guest_access.run_argv = run
    await post_connect.prepare(lease, bootstrap)

    assert "StrictHostKeyChecking=accept-new" in calls[0][0]
    assert f"HostKeyAlias=niuu-{lease.id}" in calls[0][0]
    assert "StrictHostKeyChecking=yes" in calls[1][0]
    assert calls[1][0][-1] == "true"
    payload = json.loads(calls[0][1]["data"])
    assert payload["ssh_user"] == "ubuntu"
    assert payload["digest"]
    tofu_data = calls[0][1]["data"].decode()
    assert module._LAUNCH not in tofu_data
    assert "/etc/niuu/session-secrets/" not in tofu_data
    assert launch_sentinel not in tofu_data
    assert credential_sentinel not in tofu_data
    assert provider_private not in tofu_data
    assert post_connect._bootstrap_verified_marker(lease).read_text() == payload["digest"]

    post_connect.target = AsyncMock()
    await post_connect.start(lease, bootstrap)

    session_files_call = calls[2]
    assert "StrictHostKeyChecking=yes" in session_files_call[0]
    assert "StrictHostKeyChecking=accept-new" not in session_files_call[0]
    session_data = session_files_call[1]["data"].decode()
    assert module._LAUNCH in session_data
    assert launch_sentinel in session_data
    assert credential_sentinel in session_data


async def test_verified_ssh_bootstrap_never_falls_back_to_tofu(setup, tmp_path):
    runtime, session, spec, _, lease = setup
    post_connect = SshContainerVmRuntime(
        ssh_private_key_file=runtime._private_key,
        ssh_public_key_file=runtime._private_key + ".pub",
        data_dir=str(tmp_path / "post-connect"),
        skuld_image="test/skuld:dev",
        bootstrap_delivery="ssh",
    )
    bootstrap = post_connect.bootstrap(session, spec, MachineBootstrap())
    marker = post_connect._bootstrap_verified_marker(lease)
    marker.write_text("previously-verified")
    known = marker.parent / "known_hosts"
    known.write_text(f"niuu-{lease.id} {runtime._public_key}\n")
    post_connect._guest_access.run_argv = AsyncMock(
        side_effect=VmRuntimeUnavailableError("strict host key no longer matches")
    )

    with pytest.raises(VmRuntimeUnavailableError, match="strict host key"):
        await post_connect.prepare(lease, bootstrap)

    post_connect._guest_access.run_argv.assert_awaited_once()
    assert "StrictHostKeyChecking=yes" in post_connect._guest_access.run_argv.await_args.args[0]
    assert (
        "StrictHostKeyChecking=accept-new"
        not in post_connect._guest_access.run_argv.await_args.args[0]
    )


def test_warm_spares_use_allocation_scoped_ssh_bootstrap_paths(setup, tmp_path):
    runtime, _, _, _, lease = setup
    post_connect = SshContainerVmRuntime(
        ssh_private_key_file=runtime._private_key,
        ssh_public_key_file=runtime._private_key + ".pub",
        data_dir=str(tmp_path / "post-connect"),
        skuld_image="test/skuld:dev",
        bootstrap_delivery="ssh",
    )
    first = lease.model_copy(update={"session_id": None})
    second_id = uuid4()
    second = lease.model_copy(
        update={
            "id": second_id,
            "session_id": None,
            "machine": lease.machine.model_copy(update={"allocation_id": second_id}),
        }
    )

    first_ssh = post_connect._bootstrap_ssh(first)
    second_ssh = post_connect._bootstrap_ssh(second)
    first_known = module.Path(
        next(arg for arg in first_ssh if arg.startswith("UserKnownHostsFile=")).split("=", 1)[1]
    )
    second_known = module.Path(
        next(arg for arg in second_ssh if arg.startswith("UserKnownHostsFile=")).split("=", 1)[1]
    )
    first_marker = post_connect._bootstrap_verified_marker(first)
    second_marker = post_connect._bootstrap_verified_marker(second)

    assert first_known != second_known
    assert first_marker != second_marker
    assert first_known.parent.name == str(first.id)
    assert second_known.parent.name == str(second.id)
    assert "None" not in str(first_known)
    assert "None" not in str(second_known)


def test_rejects_unknown_bootstrap_delivery(setup, tmp_path):
    runtime, *_ = setup
    with pytest.raises(ValueError, match="bootstrap_delivery"):
        SshContainerVmRuntime(
            ssh_private_key_file=runtime._private_key,
            ssh_public_key_file=runtime._private_key + ".pub",
            data_dir=str(tmp_path / "invalid"),
            skuld_image="test/skuld:dev",
            bootstrap_delivery="automatic",
        )


def test_access_trust_contract_fails_for_mismatch_or_unimplemented_ca(setup):
    runtime, *_ = setup
    access = runtime._guest_access

    access.configure_trust(principal="ubuntu", mode="provider_identity", kwargs={})
    with pytest.raises(ValueError, match="does not match"):
        access.configure_trust(principal="another", mode="provider_identity", kwargs={})
    with pytest.raises(ValueError, match="not implemented"):
        access.configure_trust(principal="ubuntu", mode="ssh_ca", kwargs={})
    with pytest.raises(ValueError, match="conflicts"):
        access.configure_trust(principal="ubuntu", mode="tofu", kwargs={})


def test_file_backed_preparation_rejects_legacy_machine_commands(setup):
    runtime, *_ = setup
    runtime._guest_access._machine_commands = (("legacy-installer",),)

    with pytest.raises(ValueError, match="legacy machine_commands"):
        SshHostPreparation(guest_access=runtime._guest_access)


def test_prepared_runtime_requires_digest_pinned_image(setup, tmp_path):
    runtime, *_ = setup

    with pytest.raises(ValueError, match="digest-pinned"):
        SshContainerVmRuntime(
            data_dir=str(tmp_path / "runtime"),
            skuld_image="test/skuld:latest",
            guest_access=runtime._guest_access,
            host_prepared=True,
        )


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"skuld_image": ""}, "Skuld image"),
        ({"ssh_port": 0}, "ports"),
        ({"connect_timeout_seconds": 0}, "timeouts"),
        ({"guest_platform_bind_host": "::"}, "bind_host"),
        ({"machine_commands": [[]]}, "machine commands"),
    ],
)
def test_runtime_rejects_invalid_control_configuration(setup, tmp_path, override, message):
    runtime, *_ = setup
    kwargs = {
        "data_dir": str(tmp_path / "invalid-runtime"),
        "skuld_image": "test/skuld:dev",
        "guest_access": runtime._guest_access,
        **override,
    }

    with pytest.raises(ValueError, match=message):
        SshContainerVmRuntime(**kwargs)


def test_injected_access_rejects_runtime_owned_machine_commands(setup, tmp_path):
    runtime, *_ = setup

    with pytest.raises(ValueError, match="injected guest access"):
        SshContainerVmRuntime(
            data_dir=str(tmp_path / "invalid-runtime"),
            skuld_image="test/skuld:dev",
            guest_access=runtime._guest_access,
            machine_commands=[["true"]],
        )


def test_numeric_container_uid_gets_nss_identity(setup, tmp_path, monkeypatch):
    runtime, *_ = setup
    library = tmp_path / "usr" / "lib" / "test-linux-gnu" / "libnss_wrapper.so"
    library.parent.mkdir(parents=True)
    library.touch()
    monkeypatch.setattr(module.pwd, "getpwuid", lambda _uid: (_ for _ in ()).throw(KeyError()))
    original_glob = module.Path.glob

    def glob(path, pattern):
        if str(path) == "/usr/lib" and pattern == "*/libnss_wrapper.so":
            return iter([library])
        return original_glob(path, pattern)

    monkeypatch.setattr(module.Path, "glob", glob)
    wrapped = SshContainerVmRuntime(
        ssh_private_key_file=runtime._private_key,
        ssh_public_key_file=runtime._private_key + ".pub",
        data_dir=str(tmp_path / "numeric-user"),
        skuld_image="test/skuld:dev",
    )

    assert wrapped._subprocess_env is not None
    assert wrapped._subprocess_env["LD_PRELOAD"] == str(library)
    passwd = module.Path(wrapped._subprocess_env["NSS_WRAPPER_PASSWD"])
    group = module.Path(wrapped._subprocess_env["NSS_WRAPPER_GROUP"])
    assert f":{module.os.getuid()}:{module.os.getgid()}:" in passwd.read_text()
    assert f":{module.os.getgid()}:" in group.read_text()
    assert passwd.stat().st_mode & 0o777 == 0o600
    assert group.stat().st_mode & 0o777 == 0o600


async def test_archive_is_atomic_and_failure_keeps_previous_copy(setup):
    runtime, session, _, bootstrap, lease = setup
    runtime._ssh(lease, bootstrap)
    archive = runtime._data / str(session.id) / "session.tar"
    archive.parent.mkdir(mode=0o700, parents=True)
    archive.write_bytes(b"previous")

    async def failing(lease, supplied_bootstrap, command, **kwargs):
        if "stdout" in kwargs:
            kwargs["stdout"].write(b"incomplete")
            raise RuntimeError("connection lost")
        return GuestCommandResult(0)

    runtime._guest_access.execute = failing
    with pytest.raises(RuntimeError, match="connection lost"):
        await runtime.stop(lease, bootstrap)
    assert archive.read_bytes() == b"previous"
    assert list(archive.parent.glob("session-*.tar")) == []

    commands = []

    async def success(lease, supplied_bootstrap, command, **kwargs):
        commands.append(command)
        if "stdout" in kwargs:
            kwargs["stdout"].write(b"complete")
        return GuestCommandResult(0)

    runtime._guest_access.execute = success
    await runtime.stop(lease, bootstrap)
    assert archive.read_bytes() == b"complete"
    assert "--exclude=./home/.codex/tmp" in commands[-1]


async def test_command_failure_is_sanitized_and_timeout_kills_process(setup, monkeypatch):
    runtime, *_ = setup
    process = AsyncMock()
    process.returncode = 255
    process.communicate.return_value = (None, b"private secret")
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(VmRuntimeUnavailableError):
        await runtime._run(["ssh", "test"])
    process.returncode = 1
    with pytest.raises(RuntimeError, match="exit code 1") as exc:
        await runtime._run(["ssh", "test"])
    assert "private secret" not in str(exc.value)
    process.communicate.return_value = (
        None,
        b"private secret\nNIIU_OPEN_SHELL_STAGE:sandbox-create\n"
        b"NIIU_OPEN_SHELL_DETAIL:UG9kbWFuIG1vdW50IHdhcyByZWplY3RlZA==\n",
    )
    with pytest.raises(VmRuntimeStageError, match="Podman mount was rejected") as exc:
        await runtime._run(
            ["ssh", "test"],
            safe_error_prefix="NIIU_OPEN_SHELL_STAGE:",
            safe_detail_prefix="NIIU_OPEN_SHELL_DETAIL:",
        )
    assert "private secret" not in str(exc.value)
    process.returncode = None
    from unittest.mock import Mock

    process.kill = Mock()

    async def hang(*args):
        await asyncio.sleep(1)

    process.communicate.side_effect = hang
    with pytest.raises(TimeoutError):
        await runtime._run(["ssh", "test"])
    process.kill.assert_called_once()
    process.wait.assert_awaited()


async def test_tunnel_is_loopback_only_reused_and_closed(setup, monkeypatch):
    runtime, _, _, bootstrap, lease = setup
    from unittest.mock import Mock

    process = AsyncMock()
    process.returncode = None
    process.terminate = Mock()
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
    first = await runtime.target(lease, bootstrap)
    assert first.connect_host == "127.0.0.1"
    assert await runtime.target(lease, bootstrap) == first
    args = spawn.call_args.args
    assert f"127.0.0.1:{first.connect_port}:127.0.0.1:8081" in args
    assert "127.0.0.1:18080:127.0.0.1:8080" in args
    await runtime.close()
    process.terminate.assert_called_once()
    process.wait.assert_awaited_once()


async def test_tunnel_can_bind_guest_callback_for_rootless_container(setup, monkeypatch):
    runtime, _, _, bootstrap, lease = setup
    runtime._guest_platform_bind_host = "0.0.0.0"
    process = AsyncMock()
    process.returncode = None
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)

    await runtime.target(lease, bootstrap)

    assert "0.0.0.0:18080:127.0.0.1:8080" in spawn.call_args.args


async def test_health_reports_connection_failure_without_claiming_ready(setup, monkeypatch):
    runtime, _, _, bootstrap, lease = setup
    from niuu.ports.session_proxy import SessionProxyTarget

    runtime.target = AsyncMock(
        return_value=SessionProxyTarget("http://127.0.0.1:9100", "127.0.0.1", 9100)
    )
    original = httpx.AsyncClient

    def respond(request):
        return httpx.Response(200)

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(respond)),
    )
    assert await runtime.ready(lease, bootstrap)

    def fail(request):
        raise httpx.ConnectError("not ready")

    monkeypatch.setattr(
        module.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(fail))
    )
    assert not await runtime.ready(lease, bootstrap)


async def test_cancel_before_restore_preserves_previous_local_archive(setup):
    runtime, session, _, bootstrap, lease = setup
    runtime._ssh(lease, bootstrap)
    archive = runtime._data / str(session.id) / "session.tar"
    archive.parent.mkdir(mode=0o700, parents=True)
    archive.write_bytes(b"previous durable data")
    runtime._guest_access.execute = AsyncMock(return_value=GuestCommandResult(42))
    await runtime.stop(lease, bootstrap)
    assert archive.read_bytes() == b"previous durable data"
    runtime._guest_access.execute.assert_awaited_once()


def test_vm_uses_existing_codex_broker_and_preserves_selected_connection(setup):
    runtime, session, spec, bootstrap, lease = setup
    payload = json.loads(next(f.content for f in bootstrap.files if f.path == module._LAUNCH))
    assert payload["environment"]["SKULD__CODEX_AUTH__ADAPTER"] == (
        "skuld.codex_auth.VolundrCodexAuthProvider"
    )
    spec.values["broker"] = {"codexAuth": {"kwargs": {"credential_name": "selected-codex"}}}
    supplied = runtime.bootstrap(session, spec, MachineBootstrap())
    payload = json.loads(next(f.content for f in supplied.files if f.path == module._LAUNCH))
    assert json.loads(payload["environment"]["SKULD__CODEX_AUTH__KWARGS"]) == {
        "credential_name": "selected-codex"
    }


async def test_existing_session_injector_files_reach_vm_without_entering_workspace(setup, tmp_path):
    from volundr.adapters.outbound.session_file_secret_injection import (
        SessionFileSecretInjectionAdapter,
    )
    from volundr.domain.models import CredentialMapping

    runtime, session, spec, _, _ = setup
    source = tmp_path / "credentials" / "user" / session.owner_id
    source.mkdir(parents=True)
    (source / "credentials.json").write_text(
        json.dumps({"values": {"claude-connection": {"token": "explicit-test-token"}}})
    )
    injection = SessionFileSecretInjectionAdapter(base_dir=str(tmp_path / "credentials"))
    await injection.ensure_secret_provider_class(
        session.owner_id,
        [
            CredentialMapping(
                credential_name="claude-connection",
                env_mappings={"CLAUDE_CODE_OAUTH_TOKEN": "token"},
            )
        ],
        session_id=str(session.id),
    )
    spec.pod_spec = await injection.pod_spec_additions(session.owner_id, str(session.id))
    bootstrap = runtime.bootstrap(session, spec, MachineBootstrap())
    payload = json.loads(next(f.content for f in bootstrap.files if f.path == module._LAUNCH))
    assert "explicit-test-token" not in json.dumps(payload)
    mount = payload["secret_mounts"][0]
    assert mount["target"] == "/run/secrets/env.sh"
    assert not mount["source"].startswith(module._REMOTE_DATA)
    assert "explicit-test-token" in next(
        f.content for f in bootstrap.files if f.path == mount["source"]
    )
    spec.pod_spec = PodSpecAdditions(
        volumes=spec.pod_spec.volumes,
        volume_mounts=(
            {"name": "secret-env", "mountPath": "/run/secrets/env.sh", "readOnly": False},
        ),
    )
    with pytest.raises(ValueError, match="read-only"):
        runtime.bootstrap(session, spec, MachineBootstrap())


async def test_warm_bootstrap_is_unbound_and_binding_keeps_machine_identity(setup):
    runtime, session, spec, _, lease = setup
    machine = runtime.machine_bootstrap(MachineBootstrap())
    assert all(f.path != module._LAUNCH for f in machine.files)
    bound = runtime.session_bootstrap(session, spec, machine)
    assert all(f in bound.files for f in machine.files)
    assert json.loads(next(f.content for f in bound.files if f.path == module._LAUNCH))[
        "environment"
    ]["SESSION_ID"] == str(session.id)
    runtime._guest_access.ensure = AsyncMock()
    runtime._guest_access.execute = AsyncMock(return_value=GuestCommandResult(0))
    spare = lease.model_copy(update={"session_id": None})
    await runtime.warm(spare, machine)
    assert runtime._guest_access.execute.await_args.args[2] == (
        "sudo -n docker pull test/skuld:dev"
    )
    with pytest.raises(ValueError, match="unbound"):
        await runtime.start(spare, machine)


def test_restore_excludes_runtime_cache_but_rejects_other_escaping_links(tmp_path):
    import io
    import subprocess
    import sys
    import tarfile

    root = tmp_path / "session"
    program = module._PREPARE.replace("/var/lib/niuu/session", str(root))

    def archive(link_name):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            marker = tarfile.TarInfo(".allocation")
            marker.size = len(b"old-allocation")
            tar.addfile(marker, io.BytesIO(b"old-allocation"))
            link = tarfile.TarInfo(link_name)
            link.type = tarfile.SYMTYPE
            link.linkname = "/outside/runtime-binary"
            tar.addfile(link)
            data = tarfile.TarInfo("workspace/marker.txt")
            data.size = 9
            tar.addfile(data, io.BytesIO(b"preserved"))
        return buffer.getvalue()

    args = [sys.executable, "-c", program, "new-allocation", "restore", '["home/.codex/tmp"]']
    rejected = subprocess.run(args, input=archive("workspace/escape"), capture_output=True)
    assert rejected.returncode != 0
    assert b"AbsoluteLinkError" in rejected.stderr
    assert not (root / ".allocation").exists()
    restored = subprocess.run(
        args, input=archive("home/.codex/tmp/arg0/apply_patch"), capture_output=True
    )
    assert restored.returncode == 0, restored.stderr
    assert (root / ".allocation").read_text() == "new-allocation"
    assert (root / "workspace/marker.txt").read_text() == "preserved"
    assert not (root / "home/.codex/tmp").exists()


@pytest.mark.parametrize("excluded", ["/home", "../home", ".", "home/*"])
def test_archive_exclusions_require_explicit_relative_paths(setup, tmp_path, excluded):
    with pytest.raises(ValueError, match="explicit relative paths"):
        SshContainerVmRuntime(
            ssh_private_key_file=str(tmp_path / "key"),
            ssh_public_key_file=str(tmp_path / "key.pub"),
            data_dir=str(tmp_path / "excluded-data"),
            skuld_image="test-image",
            archive_excludes=[excluded],
        )


def test_forge_controls_reach_vm_guest(setup):
    runtime, session, spec, _, _ = setup
    spec.values["session"]["reasoningEffort"] = "high"
    spec.values["broker"] = {
        "historyHydrationEnabled": False,
        "codexReceiveMaxBytes": 123456,
        "pi": {"binary": "/opt/pi"},
    }
    bootstrap = runtime.bootstrap(session, spec, MachineBootstrap())
    payload = json.loads(next(f.content for f in bootstrap.files if f.path == module._LAUNCH))
    env = payload["environment"]
    assert env["SKULD__SESSION__REASONING_EFFORT"] == "high"
    assert env["SKULD__HISTORY_HYDRATION_ENABLED"] == "false"
    assert env["SKULD__CODEX_RECEIVE_MAX_BYTES"] == "123456"
    assert json.loads(env["SKULD__PI"])["binary"] == "/opt/pi"


async def test_prepared_runtime_only_proves_strict_access_with_session_bootstrap(setup, tmp_path):
    legacy, session, spec, _, lease = setup
    access = legacy._guest_access
    access._bootstrap_delivery = "ssh"
    runtime = SshContainerVmRuntime(
        data_dir=str(tmp_path / "prepared-runtime"),
        skuld_image="test/skuld@sha256:" + "a" * 64,
        guest_access=access,
        host_prepared=True,
    )
    machine = access.machine_bootstrap(MachineBootstrap())
    bootstrap = runtime.session_bootstrap(session, spec, machine)
    access.ensure = AsyncMock()
    access.execute = AsyncMock(return_value=GuestCommandResult(0))

    await runtime.prepare(lease, bootstrap)

    access.ensure.assert_not_awaited()
    access.execute.assert_awaited_once_with(lease, bootstrap, "true")


async def test_injected_access_is_closed_only_by_composition_owner(setup, tmp_path):
    legacy, *_ = setup
    access = legacy._guest_access
    access.close = AsyncMock()
    runtime = SshContainerVmRuntime(
        data_dir=str(tmp_path / "prepared-runtime"),
        skuld_image="test/skuld@sha256:" + "a" * 64,
        guest_access=access,
        host_prepared=True,
    )

    await runtime.close()

    access.close.assert_not_awaited()
