"""Pinned SSH and local-data preservation checks; all remote processes are fake."""

import asyncio
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from volundr.adapters.outbound import ssh_vm_runtime as module
from volundr.adapters.outbound.ssh_vm_runtime import SshContainerVmRuntime
from volundr.domain.compute import (
    BootstrapFile,
    ComputeLease,
    Machine,
    MachineBootstrap,
    MachineState,
)
from volundr.domain.models import GitSource, PodSpecAdditions, Session, SessionSpec
from volundr.domain.vm_runtime import VmRuntimeUnavailableError


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
    known = runtime._data / str(session.id) / "known_hosts"
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
    archive.write_bytes(b"explicit-test-archive")
    runtime._run = AsyncMock()
    runtime.target = AsyncMock()
    await runtime.prepare(lease, bootstrap)
    await runtime.start(lease, bootstrap)
    calls = runtime._run.await_args_list
    assert "cloud-init status --wait" in calls[0].args[0][-1]
    assert json.loads(calls[1].kwargs["data"])[0]["path"] == module._LAUNCH
    assert "stdin" in calls[2].kwargs
    assert json.loads(calls[3].kwargs["data"])["allocation_id"] == str(lease.id)
    runtime.target.assert_awaited_once()
    archive.unlink()
    runtime._run.reset_mock()
    await runtime.start(lease, bootstrap)
    assert "stdin" not in runtime._run.await_args_list[1].kwargs


async def test_archive_is_atomic_and_failure_keeps_previous_copy(setup):
    runtime, session, _, bootstrap, lease = setup
    runtime._ssh(lease, bootstrap)
    archive = runtime._data / str(session.id) / "session.tar"
    archive.write_bytes(b"previous")

    async def failing(argv, **kwargs):
        if "stdout" in kwargs:
            kwargs["stdout"].write(b"incomplete")
            raise RuntimeError("connection lost")

    runtime._run = failing
    with pytest.raises(RuntimeError, match="connection lost"):
        await runtime.stop(lease, bootstrap)
    assert archive.read_bytes() == b"previous"
    assert list(archive.parent.glob("session-*.tar")) == []

    async def success(argv, **kwargs):
        if "stdout" in kwargs:
            kwargs["stdout"].write(b"complete")

    runtime._run = success
    await runtime.stop(lease, bootstrap)
    assert archive.read_bytes() == b"complete"


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
    archive.write_bytes(b"previous durable data")
    runtime._run = AsyncMock(return_value=42)
    await runtime.stop(lease, bootstrap)
    assert archive.read_bytes() == b"previous durable data"
    runtime._run.assert_awaited_once()


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
    runtime._run = AsyncMock()
    spare = lease.model_copy(update={"session_id": None})
    await runtime.warm(spare, machine)
    assert runtime._run.await_args.args[0][-1] == "sudo -n docker pull test/skuld:dev"
    with pytest.raises(ValueError, match="unbound"):
        await runtime.start(spare, machine)
