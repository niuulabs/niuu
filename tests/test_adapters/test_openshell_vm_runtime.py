"""Managed OpenShell guest bootstrap and cleanup; all guest calls are test doubles."""

import importlib
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from niuu.ports.session_proxy import SessionProxyTarget
from tests.test_adapters.test_openshell_gateway import _import_adapter
from tests.test_adapters.test_ssh_vm_runtime import setup as ssh_setup  # noqa: F401
from volundr.domain.compute import MachineBootstrap

GATEWAY = """[openshell]
version=2
[openshell.gateway]
compute_driver="docker"
bind_address="172.30.0.1:17670"
disable_tls=true
[openshell.gateway.auth]
allow_unauthenticated_users=false
[openshell.gateway.oidc]
issuer="https://identity.test/realm"
[openshell.drivers.docker]
host_gateway_ip="172.30.0.1"
network_name="sessions"
supervisor_image="test/supervisor:version"
enable_bind_mounts=true
"""


@pytest.fixture
def setup(ssh_setup, monkeypatch):  # noqa: F811
    old, session, spec, _, lease = ssh_setup
    _import_adapter(monkeypatch)
    sys.modules.pop("volundr.adapters.outbound.openshell_vm_runtime", None)
    module = importlib.import_module("volundr.adapters.outbound.openshell_vm_runtime")
    runtime = module.OpenShellVmRuntime(
        ssh_private_key_file=old._private_key,
        ssh_public_key_file=old._private_key + ".pub",
        data_dir=str(old._data),
        skuld_image="test/openshell:dev",
        gateway_image="test/gateway:version",
        gateway_config=GATEWAY,
        network_subnet="172.30.0.0/24",
        gateway_kwargs={
            "client_secret": "controller-only-secret",
            "sandbox_policy": {"process": {"run_as_user": "sandbox", "run_as_group": "sandbox"}},
        },
    )
    bootstrap = runtime.bootstrap(session, spec, MachineBootstrap())
    manager = MagicMock()
    manager.start = AsyncMock()
    manager.stop = AsyncMock(return_value=True)
    manager.close = AsyncMock()
    manager._cleanup_resources = AsyncMock()
    manager._service_port = 9200
    manager._client.get_sandbox.return_value = SimpleNamespace(id="sandbox-id", ready=True)
    manager._tcp_proxy_target.return_value = SessionProxyTarget(
        "http://skuld.internal", "127.0.0.1", 1234
    )
    runtime._manager = AsyncMock(return_value=manager)
    runtime._run = AsyncMock(return_value=0)
    runtime._restore_data = AsyncMock()
    runtime._archive_data = AsyncMock()
    runtime._close_tunnel = AsyncMock()
    return module, runtime, manager, session, spec, bootstrap, lease


def test_bootstrap_installs_docker_gateway_without_session_secrets(setup):
    module, runtime, _, _, _, _, _ = setup
    machine = runtime.machine_bootstrap(MachineBootstrap())
    text = machine.model_dump_json()
    assert "controller-only-secret" not in text
    assert module._SPEC not in text
    assert "docker cp" in text and "config preflight" in text
    assert "k3s" not in text and "kube" not in text
    unit = next(f.content for f in machine.files if f.path.endswith(".service"))
    assert "\\n" not in unit and "User=root" in unit
    assert runtime.supports_reuse
    assert runtime._tunnel_options(1234)[-1].startswith("172.30.0.1:")


async def test_start_restores_before_launch_and_sets_persistent_home(setup):
    _, runtime, manager, session, _, bootstrap, lease = setup
    await runtime.start(lease, bootstrap)
    runtime._restore_data.assert_awaited_once_with(lease, bootstrap)
    called_session, spec = manager.start.call_args.args
    assert called_session.id == session.id
    assert spec.values["env"]["CODEX_HOME"] == "/sandbox/workspace/.codex"
    assert spec.values["volundr"]["apiUrl"].startswith("http://172.30.0.1:")
    target = await runtime.target(lease, bootstrap)
    assert target.connect_port == 1234


async def test_stop_archives_before_recycle_and_cleanup_is_idempotent(setup):
    _, runtime, manager, _, _, bootstrap, lease = setup
    events = []
    manager.stop.side_effect = lambda *a: events.append("stop")
    runtime._archive_data.side_effect = lambda *a: events.append("archive")
    manager._cleanup_resources.side_effect = lambda *a: events.append("delete")
    await runtime.stop(lease, bootstrap)
    assert events == ["stop", "archive"]
    await runtime.recycle(lease, bootstrap)
    assert events == ["stop", "archive", "delete"]
    await runtime.recycle(lease, bootstrap)
    assert manager._cleanup_resources.await_count == 2


async def test_archive_failure_does_not_delete_sandbox(setup):
    _, runtime, manager, _, _, bootstrap, lease = setup
    runtime._archive_data.side_effect = OSError("disk full")
    with pytest.raises(OSError, match="disk full"):
        await runtime.stop(lease, bootstrap)
    manager._cleanup_resources.assert_not_called()


async def test_partial_start_without_storage_keeps_previous_archive(setup):
    _, runtime, manager, _, _, bootstrap, lease = setup
    runtime._run.return_value = 42
    await runtime.stop(lease, bootstrap)
    manager.stop.assert_not_called()
    runtime._archive_data.assert_not_called()


def test_session_payload_retains_identity_but_machine_has_no_session(setup):
    module, runtime, _, session, _, bootstrap, _ = setup
    payload = json.loads(next(f.content for f in bootstrap.files if f.path == module._SPEC))
    assert payload["session"]["id"] == str(session.id)
    restored, spec = runtime._session(bootstrap)
    assert restored.id == session.id
    assert spec.values["session"]["systemPrompt"] == "first\nsecond"


@pytest.mark.parametrize(
    "change,match",
    [
        ("driver", "requires Docker"),
        ("auth", "require authentication"),
        ("oidc", "OIDC"),
        ("tls", "plaintext"),
        ("address", "bridge address"),
        ("image", "matching"),
        ("owned", "owns gateway"),
    ],
)
def test_invalid_gateway_contract_is_rejected_before_provisioning(setup, change, match):
    module, _, _, _, _, _, _ = setup
    config = GATEWAY
    options = dict(gateway_image="test/gateway", gateway_kwargs={}, network_subnet="172.30.0.0/24")
    if change == "driver":
        config = config.replace('compute_driver="docker"', 'compute_driver="kubernetes"')
    if change == "auth":
        config = config.replace(
            "allow_unauthenticated_users=false", "allow_unauthenticated_users=true"
        )
    if change == "oidc":
        config = config.replace("[openshell.gateway.oidc]", "[openshell.gateway.other]")
    if change == "tls":
        config = config.replace("disable_tls=true", "disable_tls=false")
    if change == "address":
        config = config.replace('bind_address="172.30.0.1:17670"', 'bind_address="127.0.0.1:17670"')
    if change == "image":
        options["gateway_image"] = ""
    if change == "owned":
        options["gateway_kwargs"] = {"gateway_endpoint": "unmanaged.test:1"}
    with pytest.raises(ValueError, match=match):
        module.OpenShellVmRuntime(gateway_config=config, **options)


async def test_manager_cache_tracks_endpoint_and_session_binding(setup, monkeypatch):
    module, runtime, manager, _, _, bootstrap, lease = setup
    monkeypatch.setattr(
        module.SshContainerVmRuntime,
        "target",
        AsyncMock(return_value=SessionProxyTarget("http://local", "127.0.0.1", 1234)),
    )
    factory = MagicMock(return_value=manager)
    monkeypatch.setattr(module, "OpenShellGatewayPodManager", factory)
    runtime._manager = module.OpenShellVmRuntime._manager.__get__(runtime)
    assert await runtime._manager(lease, bootstrap) is manager
    assert await runtime._manager(lease, bootstrap) is manager
    assert factory.call_count == 1
    assert (
        factory.call_args.kwargs["driver_config"]["mounts"][0]["source"]
        == "/var/lib/niuu/session/workspace"
    )
    assert factory.call_args.kwargs["codex_auth_adapter"] == runtime._codex_auth_adapter
    await runtime._manager(lease.model_copy(update={"session_id": None}), bootstrap)
    assert factory.call_count == 2
    manager.close.assert_awaited_once()
    await runtime.close()
    assert manager.close.await_count == 2


async def test_warm_authenticates_and_only_retries_gateway_unavailability(setup, monkeypatch):
    module, runtime, manager, _, _, bootstrap, lease = setup
    monkeypatch.setattr(module.SshContainerVmRuntime, "warm", AsyncMock())
    runtime._managers[str(lease.id)] = ("probe", manager)
    await runtime.warm(lease, bootstrap)
    manager.close.assert_awaited_once()
    assert str(lease.id) not in runtime._managers
    runtime._close_tunnel.assert_awaited_with(str(lease.id))
    manager._client.get_sandbox.assert_called_once_with("niuu-readiness-check")
    module.grpc.StatusCode.UNAVAILABLE = "unavailable"

    class Unavailable(module.grpc.RpcError):
        def code(self):
            return "unavailable"

    manager._client.get_sandbox.side_effect = Unavailable()
    with pytest.raises(module.VmRuntimeUnavailableError):
        await runtime.warm(lease, bootstrap)
    manager._client.get_sandbox.side_effect = module.grpc.RpcError()
    with pytest.raises(module.grpc.RpcError):
        await runtime.warm(lease, bootstrap)


async def test_readiness_checks_skuld_and_unavailable_sandbox(setup, respx_mock):
    module, runtime, manager, _, _, bootstrap, lease = setup
    import httpx

    route = respx_mock.get("http://127.0.0.1:1234/health").respond(200)
    assert await runtime.ready(lease, bootstrap)
    route.mock(side_effect=httpx.ConnectError("not listening"))
    assert not await runtime.ready(lease, bootstrap)
    manager._client.get_sandbox.return_value = None
    assert not await runtime.ready(lease, bootstrap)
    with pytest.raises(ValueError, match="unbound"):
        await runtime.start(lease.model_copy(update={"session_id": None}), bootstrap)


def test_bootstrap_conflicts_and_unsupported_identity_contract_fail_explicitly(setup):
    module, runtime, _, session, spec, _, _ = setup
    from volundr.domain.compute import BootstrapFile

    with pytest.raises(ValueError, match="conflicts"):
        runtime.machine_bootstrap(
            MachineBootstrap(files=(BootstrapFile(path=module._CONFIG, content="conflict"),))
        )
    spec.values["credentialMappings"] = [{"name": "requires-spiffe"}]
    with pytest.raises(ValueError, match="SPIFFE"):
        runtime.session_bootstrap(session, spec, MachineBootstrap())


async def test_gateway_channel_must_be_ready_before_cleanup(setup):
    module, runtime, manager, _, _, _, _ = setup
    module.grpc.FutureTimeoutError = TimeoutError
    manager._client.wait_for_ready.side_effect = TimeoutError()
    with pytest.raises(module.VmRuntimeUnavailableError, match="tunnel"):
        await runtime._gateway_ready(manager)
    manager._cleanup_resources.assert_not_called()


def test_restart_uses_session_sandbox_name_not_allocation_uuid(setup):
    _, runtime, _, session, spec, _, lease = setup
    session.pod_name = str(lease.id)
    bootstrap = runtime.bootstrap(session, spec, MachineBootstrap())
    restored, _ = runtime._session(bootstrap)
    assert restored.pod_name == "forge-" + session.id.hex[:13]
