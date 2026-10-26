"""Every kubernetes_asyncio ApiClient a resident opens must be closed.

``ApiClient()`` builds an ``aiohttp.ClientSession`` in its constructor. One that
is dropped without ``await api_client.close()`` makes the garbage collector log
``asyncio ERROR Unclosed client session`` long after the work that opened it.

The fake ``kubernetes_asyncio`` below mirrors the one behaviour that matters:
an API group built without an explicit ``ApiClient`` creates its own, and every
ApiClient created must be closed exactly once.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from ravn.adapters.deployment import KubernetesApplyWardenDeploymentAdapter
from ravn.adapters.environment_signals import KubernetesSignalAdapter
from ravn.adapters.tools.kubernetes import KubernetesClients, KubernetesInspectTool
from ravn.domain.environment import k8s_environment_fixture
from ravn.environment_signal_runtime import EnvironmentSignalRuntime
from ravn.ports.signal_adapter import NormalizedSignal, SignalAdapter
from ravn.valkyrie_evolution.k8s_tool_runner import KubernetesJobExecutor
from ravn.valkyrie_evolution.learned_tools import LearnedToolError
from ravn.warden import WardenSpec


class _FakeApiClient:
    """Stands in for ``kubernetes_asyncio.client.ApiClient`` and its aiohttp session."""

    registry: list[_FakeApiClient] = []

    def __init__(self) -> None:
        self.close_calls = 0
        _FakeApiClient.registry.append(self)

    async def close(self) -> None:
        self.close_calls += 1


class _FakeApi:
    """Any ``*V1Api``: owns a private ApiClient when none is passed, like the real one."""

    def __init__(self, api_client: _FakeApiClient | None = None) -> None:
        self.api_client = api_client or _FakeApiClient()

    async def list_event_for_all_namespaces(self, **_: Any) -> Any:
        return SimpleNamespace(items=[])

    async def read_namespaced_pod(self, name: str, namespace: str) -> Any:
        return SimpleNamespace(
            metadata=SimpleNamespace(name=name, namespace=namespace, uid="u", labels={}),
            spec=SimpleNamespace(node_name="n"),
            status=SimpleNamespace(phase="Running", container_statuses=[], conditions=[]),
        )

    async def read_namespaced_deployment(self, name: str, namespace: str) -> Any:
        return SimpleNamespace(
            spec=SimpleNamespace(replicas=1),
            status=SimpleNamespace(ready_replicas=1, available_replicas=1),
        )

    async def read_namespaced_config_map(self, name: str, namespace: str) -> Any:
        return SimpleNamespace(data={})

    async def read_namespaced_network_policy(self, name: str, namespace: str) -> Any:
        raise RuntimeError("network policy API unreachable")


@pytest.fixture
def fake_kubernetes(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[_FakeApiClient]]:
    _FakeApiClient.registry = []

    async def new_client_from_config(**_: Any) -> _FakeApiClient:
        return _FakeApiClient()

    async def load_kube_config(**_: Any) -> None:
        return None

    module = ModuleType("kubernetes_asyncio")
    module.client = SimpleNamespace(  # type: ignore[attr-defined]
        ApiClient=_FakeApiClient,
        CoreV1Api=_FakeApi,
        AppsV1Api=_FakeApi,
        BatchV1Api=_FakeApi,
        NetworkingV1Api=_FakeApi,
    )
    module.config = SimpleNamespace(  # type: ignore[attr-defined]
        load_incluster_config=lambda: None,
        load_kube_config=load_kube_config,
        new_client_from_config=new_client_from_config,
        ConfigException=RuntimeError,
    )
    monkeypatch.setitem(sys.modules, "kubernetes_asyncio", module)
    yield _FakeApiClient.registry


def _assert_every_client_closed_once(registry: list[_FakeApiClient], expected: int) -> None:
    assert len(registry) == expected
    assert [api.close_calls for api in registry] == [1] * expected


# --- kubernetes_inspect (in-cluster) -----------------------------------------


@pytest.mark.asyncio
async def test_in_cluster_inspect_closes_its_api_client_after_every_call(
    fake_kubernetes: list[_FakeApiClient],
) -> None:
    """The tool is rebuilt per task, so each call must clean up after itself."""
    tool = KubernetesInspectTool(in_cluster=True)
    request = {"action": "inspect_pod", "namespace": "shop", "name": "api"}

    first = await tool.execute(request)
    second = await tool.execute(request)

    assert not first.is_error and not second.is_error
    # One shared ApiClient per call (not one per API group), each closed once.
    _assert_every_client_closed_once(fake_kubernetes, expected=2)


@pytest.mark.asyncio
async def test_in_cluster_inspect_closes_its_api_client_when_the_call_fails(
    fake_kubernetes: list[_FakeApiClient],
) -> None:
    tool = KubernetesInspectTool(in_cluster=True)

    result = await tool.execute({"action": "inspect_pod", "namespace": "shop"})

    assert result.is_error and "name is required" in result.content
    _assert_every_client_closed_once(fake_kubernetes, expected=1)


@pytest.mark.asyncio
async def test_inspect_does_not_open_a_client_for_an_unsupported_action(
    fake_kubernetes: list[_FakeApiClient],
) -> None:
    tool = KubernetesInspectTool(in_cluster=True)

    result = await tool.execute({"action": "delete_everything"})

    assert result.is_error and "unsupported" in result.content
    assert fake_kubernetes == []


# --- kubernetes_inspect (kubeconfig / Teleport) ------------------------------

_KUBECONFIG = """
contexts:
- name: teleport.niuu.world-ymir
"""


@pytest.mark.asyncio
async def test_kubeconfig_inspect_closes_its_api_client_after_every_call(
    fake_kubernetes: list[_FakeApiClient], tmp_path
) -> None:
    kubeconfig = tmp_path / "kubeconfig.yaml"
    kubeconfig.write_text(_KUBECONFIG, encoding="utf-8")
    tool = KubernetesInspectTool(in_cluster=False, kubeconfig_path=str(kubeconfig))
    request = {"action": "inspect_pod", "namespace": "shop", "name": "api", "cluster": "ymir"}

    first = await tool.execute(request)
    second = await tool.execute(request)

    assert not first.is_error and not second.is_error
    _assert_every_client_closed_once(fake_kubernetes, expected=2)


@pytest.mark.asyncio
async def test_factory_clients_are_closed_by_the_tool_after_each_call(tmp_path) -> None:
    kubeconfig = tmp_path / "kubeconfig.yaml"
    kubeconfig.write_text(_KUBECONFIG, encoding="utf-8")
    closed: list[str] = []

    async def factory(path: str, context: str) -> KubernetesClients:
        async def aclose() -> None:
            closed.append(context)

        return KubernetesClients(
            core_v1=_FakeApi(), apps_v1=_FakeApi(), batch_v1=None, aclose=aclose
        )

    tool = KubernetesInspectTool(
        in_cluster=False, kubeconfig_path=str(kubeconfig), client_factory=factory
    )
    request = {"action": "inspect_pod", "namespace": "shop", "name": "api", "cluster": "ymir"}

    await tool.execute(request)
    await tool.execute(request)

    assert closed == ["teleport.niuu.world-ymir"] * 2


@pytest.mark.asyncio
async def test_a_failed_close_is_reported_not_swallowed(tmp_path) -> None:
    kubeconfig = tmp_path / "kubeconfig.yaml"
    kubeconfig.write_text(_KUBECONFIG, encoding="utf-8")

    async def factory(path: str, context: str) -> KubernetesClients:
        async def aclose() -> None:
            raise OSError("session already torn down")

        return KubernetesClients(
            core_v1=_FakeApi(), apps_v1=_FakeApi(), batch_v1=None, aclose=aclose
        )

    tool = KubernetesInspectTool(
        in_cluster=False, kubeconfig_path=str(kubeconfig), client_factory=factory
    )

    result = await tool.execute(
        {"action": "inspect_pod", "namespace": "shop", "name": "api", "cluster": "ymir"}
    )

    assert result.is_error
    assert "session already torn down" in result.content


@pytest.mark.asyncio
async def test_caller_owned_clients_are_never_closed_by_the_tool() -> None:
    api = _FakeApi()
    tool = KubernetesInspectTool(core_v1=api, apps_v1=_FakeApi(), batch_v1=_FakeApi())

    result = await tool.execute({"action": "inspect_pod", "namespace": "shop", "name": "api"})

    assert not result.is_error
    assert json.loads(result.content)["name"] == "api"
    assert api.api_client.close_calls == 0


# --- kubernetes-events signal source -----------------------------------------


@pytest.mark.asyncio
async def test_kubernetes_signal_adapter_polls_on_one_client_and_closes_it(
    fake_kubernetes: list[_FakeApiClient],
) -> None:
    adapter = KubernetesSignalAdapter(environment=k8s_environment_fixture(), in_cluster=True)

    for _ in range(3):
        await adapter.collect()
    assert len(fake_kubernetes) == 1
    assert fake_kubernetes[0].close_calls == 0

    await adapter.close()

    _assert_every_client_closed_once(fake_kubernetes, expected=1)


@pytest.mark.asyncio
async def test_kubernetes_signal_adapter_close_leaves_an_injected_client_alone() -> None:
    api = _FakeApi()
    adapter = KubernetesSignalAdapter(environment=k8s_environment_fixture(), core_v1=api)

    await adapter.collect()
    await adapter.close()

    assert api.api_client.close_calls == 0


class _ClosableAdapter(SignalAdapter):
    environment = k8s_environment_fixture()
    closed = 0

    @property
    def source_id(self) -> str:
        return "closable"

    @property
    def signal_type(self) -> str:  # type: ignore[override]
        return "generic"

    async def collect(self) -> list[NormalizedSignal]:
        return []

    async def close(self) -> None:
        type(self).closed += 1


@pytest.mark.asyncio
async def test_signal_runtime_stop_closes_every_adapter() -> None:
    runtime = object.__new__(EnvironmentSignalRuntime)
    runtime._tasks = []
    runtime._adapters = [_ClosableAdapter(), _ClosableAdapter()]
    runtime._owns_publisher = False
    _ClosableAdapter.closed = 0

    await runtime.stop()

    assert _ClosableAdapter.closed == 2


# --- k8s_job learned-tool executor --------------------------------------------


def _executor() -> KubernetesJobExecutor:
    return KubernetesJobExecutor(
        namespace="ravn",
        deny_policy_name="tool-deny",
        allow_policy_name="tool-allow",
        in_cluster=True,
    )


_PINNED_IMAGE = "registry.example/tool@sha256:" + "0" * 64


@pytest.mark.asyncio
async def test_job_executor_closes_its_api_client_after_execute(
    fake_kubernetes: list[_FakeApiClient],
) -> None:
    """The executor is rebuilt for every learned-tool load; it cannot cache a client."""
    executor = _executor()

    with pytest.raises(RuntimeError, match="network policy API unreachable"):
        await executor.execute(
            run_name="r",
            image=_PINNED_IMAGE,
            code="",
            payload={},
            entry_point="run",
            requirements=[],
            timeout_seconds=1.0,
            network_allowed=False,
        )

    _assert_every_client_closed_once(fake_kubernetes, expected=1)


@pytest.mark.asyncio
async def test_job_executor_closes_its_api_client_after_verification(
    fake_kubernetes: list[_FakeApiClient],
) -> None:
    executor = _executor()

    with pytest.raises(RuntimeError, match="network policy API unreachable"):
        await executor.execute_verification(
            run_name="r",
            image=_PINNED_IMAGE,
            tool_name="t",
            tool_code="",
            test_code="",
            requirements=[],
            timeout_seconds=1.0,
        )

    _assert_every_client_closed_once(fake_kubernetes, expected=1)


@pytest.mark.asyncio
async def test_job_executor_refuses_an_unpinned_image_before_opening_a_client(
    fake_kubernetes: list[_FakeApiClient],
) -> None:
    with pytest.raises(LearnedToolError, match="pinned by sha256"):
        await _executor().execute(
            run_name="r",
            image="unpinned:latest",
            code="",
            payload={},
            entry_point="run",
            requirements=[],
            timeout_seconds=1.0,
            network_allowed=False,
        )

    assert fake_kubernetes == []


# --- warden k8s-apply deployment ----------------------------------------------


@pytest.mark.asyncio
async def test_warden_apply_adapter_closes_its_api_client_after_each_operation(
    fake_kubernetes: list[_FakeApiClient],
) -> None:
    """Each operation runs on its own asyncio.run loop; a cached client outlives it."""
    adapter = KubernetesApplyWardenDeploymentAdapter(namespace="ravn-test")
    spec = WardenSpec(id="research-warden", name="Research Warden")

    first = await adapter._observe_install(spec)
    second = await adapter._observe_install(spec)

    assert first["deployment_present"] and second["deployment_present"]
    _assert_every_client_closed_once(fake_kubernetes, expected=2)
