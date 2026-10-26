from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ravn.adapters.tools.kubernetes import KubernetesClients, KubernetesInspectTool
from ravn.cap_telemetry import cap_hit_counts
from ravn.cli.commands import _filter_tools, _groups_for_persona
from ravn.config import Settings


class FakeCoreV1:
    def __init__(self) -> None:
        self.event_selector = ""

    async def read_namespaced_pod(self, name: str, namespace: str):
        return SimpleNamespace(
            metadata=SimpleNamespace(
                name=name,
                namespace=namespace,
                uid="pod-uid",
                labels={"app": "checkout"},
                owner_references=[
                    SimpleNamespace(
                        kind="ReplicaSet",
                        name="checkout-abc",
                        uid="rs-uid",
                        controller=True,
                    )
                ],
            ),
            spec=SimpleNamespace(node_name="node-a"),
            status=SimpleNamespace(
                phase="Running",
                container_statuses=[
                    SimpleNamespace(
                        name="api",
                        ready=False,
                        restart_count=3,
                        state=SimpleNamespace(
                            waiting=SimpleNamespace(
                                reason="CrashLoopBackOff",
                                message="back-off restarting failed container",
                            )
                        ),
                        last_state=SimpleNamespace(
                            terminated=SimpleNamespace(reason="Error", exit_code=1)
                        ),
                    )
                ],
                conditions=[
                    SimpleNamespace(
                        type="Ready",
                        status="False",
                        reason="ContainersNotReady",
                        message="container api is not ready",
                    )
                ],
            ),
        )

    async def list_namespaced_event(self, namespace: str, field_selector: str):
        self.event_selector = field_selector
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    metadata=SimpleNamespace(name="checkout-warning", namespace=namespace),
                    type="Warning",
                    reason="BackOff",
                    message="Back-off restarting failed container",
                    count=4,
                    involved_object=SimpleNamespace(
                        kind="Pod",
                        namespace=namespace,
                        name="checkout-abc",
                        uid="pod-uid",
                    ),
                )
            ]
        )

    async def read_namespaced_pod_log(self, name: str, namespace: str, **kwargs):
        return f"{namespace}/{name} tail={kwargs['tail_lines']}"

    async def read_node(self, name: str):
        return SimpleNamespace(
            metadata=SimpleNamespace(name=name, uid="node-uid"),
            status=SimpleNamespace(
                conditions=[SimpleNamespace(type="Ready", status="True", reason="KubeletReady")]
            ),
        )


class FakeAppsV1:
    async def read_namespaced_deployment(self, name: str, namespace: str):
        return SimpleNamespace(
            metadata=SimpleNamespace(name=name, namespace=namespace, uid="deploy-uid"),
            spec=SimpleNamespace(replicas=3),
            status=SimpleNamespace(
                replicas=3,
                ready_replicas=2,
                available_replicas=2,
                unavailable_replicas=1,
                conditions=[
                    SimpleNamespace(
                        type="Available",
                        status="False",
                        reason="MinimumReplicasUnavailable",
                    )
                ],
            ),
        )


class FakeBatchV1:
    pass


@pytest.mark.asyncio
async def test_kubernetes_inspect_pod_returns_read_only_status() -> None:
    tool = KubernetesInspectTool(core_v1=FakeCoreV1(), apps_v1=FakeAppsV1(), batch_v1=FakeBatchV1())

    result = await tool.execute(
        {"action": "inspect_pod", "namespace": "shop", "name": "checkout-abc"}
    )

    assert not result.is_error
    payload = json.loads(result.content)
    assert payload["pod"]["phase"] == "Running"
    assert payload["pod"]["containers"][0]["state"]["kind"] == "waiting"
    assert payload["pod"]["containers"][0]["state"]["reason"] == "CrashLoopBackOff"
    assert payload["pod"]["metadata"]["owner_references"][0]["kind"] == "ReplicaSet"


@pytest.mark.asyncio
async def test_kubernetes_inspect_events_and_logs_are_read_only() -> None:
    core = FakeCoreV1()
    tool = KubernetesInspectTool(
        core_v1=core,
        apps_v1=FakeAppsV1(),
        batch_v1=FakeBatchV1(),
        max_log_lines=50,
    )

    events = await tool.execute(
        {
            "action": "object_events",
            "namespace": "shop",
            "kind": "Pod",
            "name": "checkout-abc",
        }
    )
    logs = await tool.execute(
        {
            "action": "pod_logs",
            "namespace": "shop",
            "name": "checkout-abc",
            "tail_lines": 500,
        }
    )

    assert core.event_selector == "involvedObject.name=checkout-abc,involvedObject.kind=Pod"
    assert json.loads(events.content)["events"][0]["reason"] == "BackOff"
    assert json.loads(logs.content)["tail_lines"] == 50


_TELEPORT_KUBECONFIG = """
apiVersion: v1
kind: Config
current-context: teleport.niuu.world-valhalla
contexts:
  - name: teleport.niuu.world-valhalla
    context: {cluster: teleport.niuu.world, user: bot}
  - name: teleport.niuu.world-ymir
    context: {cluster: teleport.niuu.world, user: bot}
clusters:
  - name: teleport.niuu.world
    cluster: {server: https://teleport.niuu.world:443}
users:
  - name: bot
    user: {token: unused}
"""


class _RolledOutApps:
    def __init__(self, cluster: str) -> None:
        self.cluster = cluster

    async def read_namespaced_deployment(self, name: str, namespace: str):
        return SimpleNamespace(
            metadata=SimpleNamespace(name=name, namespace=namespace, uid="d", generation=7),
            spec=SimpleNamespace(
                replicas=1,
                template=SimpleNamespace(
                    spec=SimpleNamespace(
                        containers=[SimpleNamespace(name="ravn", image=f"niuu:dev-{self.cluster}")]
                    )
                ),
            ),
            status=SimpleNamespace(
                replicas=1, ready_replicas=1, updated_replicas=1, observed_generation=7
            ),
        )


class _ConfigCore:
    async def read_namespaced_config_map(self, name: str, namespace: str):
        return SimpleNamespace(
            metadata=SimpleNamespace(name=name, namespace=namespace, uid="cm"),
            data={"config.yaml": "x" * 50, "small": "ok"},
        )


def _multi_cluster_tool(tmp_path, built: list[str]) -> KubernetesInspectTool:
    kubeconfig = tmp_path / "kubeconfig.yaml"
    kubeconfig.write_text(_TELEPORT_KUBECONFIG, encoding="utf-8")

    async def factory(path: str, context: str) -> KubernetesClients:
        assert path == str(kubeconfig)
        built.append(context)
        cluster = context.rsplit("-", 1)[-1]
        return KubernetesClients(
            core_v1=_ConfigCore(), apps_v1=_RolledOutApps(cluster), batch_v1=FakeBatchV1()
        )

    return KubernetesInspectTool(
        in_cluster=False,
        kubeconfig_path=str(kubeconfig),
        max_configmap_value_chars=10,
        client_factory=factory,
    )


@pytest.mark.asyncio
async def test_clusters_lists_every_context_in_the_kubeconfig(tmp_path) -> None:
    tool = _multi_cluster_tool(tmp_path, [])

    result = await tool.execute({"action": "clusters"})

    assert json.loads(result.content)["clusters"] == [
        "teleport.niuu.world-valhalla",
        "teleport.niuu.world-ymir",
    ]


@pytest.mark.asyncio
async def test_a_named_cluster_reads_that_cluster_with_a_client_per_call(tmp_path) -> None:
    built: list[str] = []
    tool = _multi_cluster_tool(tmp_path, built)
    request = {"action": "workload", "namespace": "nats", "name": "regin", "cluster": "ymir"}

    first = json.loads((await tool.execute(request)).content)
    await tool.execute(request)

    workload = first["workload"]
    assert workload["images"] == {"ravn": "niuu:dev-ymir"}
    assert workload["generation"] == workload["observed_generation"] == 7
    assert workload["updated_replicas"] == 1
    # A client is opened and closed per call: the tool is rebuilt per task and
    # has no shutdown hook, so a cached client would never be closed.
    assert built == ["teleport.niuu.world-ymir"] * 2


@pytest.mark.asyncio
async def test_a_multi_cluster_kubeconfig_never_guesses_the_cluster(tmp_path) -> None:
    tool = _multi_cluster_tool(tmp_path, [])

    missing = await tool.execute({"action": "workload", "namespace": "nats", "name": "regin"})
    unknown = await tool.execute(
        {"action": "workload", "namespace": "nats", "name": "regin", "cluster": "asgard"}
    )

    assert missing.is_error and "cluster is required" in missing.content
    assert unknown.is_error and "unknown cluster 'asgard'" in unknown.content
    assert "teleport.niuu.world-valhalla" in unknown.content


@pytest.mark.asyncio
async def test_configmap_values_are_capped(tmp_path) -> None:
    tool = _multi_cluster_tool(tmp_path, [])

    result = await tool.execute(
        {"action": "configmap", "namespace": "nats", "name": "regin-config", "cluster": "valhalla"}
    )

    payload = json.loads(result.content)
    assert payload["data"] == {"config.yaml": "x" * 10, "small": "ok"}
    assert payload["truncated_keys"] == ["config.yaml"]
    assert cap_hit_counts() == {"tools.kubernetes.max_configmap_value_chars": 1}


@pytest.mark.asyncio
async def test_an_in_cluster_resident_cannot_name_another_cluster() -> None:
    tool = KubernetesInspectTool(core_v1=FakeCoreV1(), apps_v1=FakeAppsV1(), batch_v1=FakeBatchV1())

    result = await tool.execute(
        {"action": "workload", "namespace": "shop", "name": "checkout", "cluster": "ymir"}
    )

    assert result.is_error and "only its own cluster" in result.content


class _PodsCore:
    def __init__(self) -> None:
        self.selectors: list[str] = []

    async def list_namespaced_pod(self, namespace: str, label_selector: str = ""):
        self.selectors.append(label_selector)

        def pod(name: str, ready: bool) -> SimpleNamespace:
            return SimpleNamespace(
                metadata=SimpleNamespace(
                    name=name,
                    owner_references=[SimpleNamespace(kind="ReplicaSet", name=f"{name[:-6]}")],
                ),
                spec=SimpleNamespace(node_name="node-a"),
                status=SimpleNamespace(
                    phase="Running",
                    start_time=None,
                    container_statuses=[
                        SimpleNamespace(name="ravn", ready=ready, restart_count=1),
                        SimpleNamespace(name="tbot", ready=True, restart_count=0),
                    ],
                ),
            )

        return SimpleNamespace(
            items=[
                pod("valkyrie-ymir-k8s-7b864d-abcde", True),
                pod("valkyrie-ymir-k8s-7b864d-fghij", False),
                pod("nats-0", True),
            ]
        )


@pytest.mark.asyncio
async def test_pods_lists_a_namespace_so_a_changing_pod_name_can_be_found(tmp_path) -> None:
    built: list[str] = []
    core = _PodsCore()
    kubeconfig = tmp_path / "kubeconfig.yaml"
    kubeconfig.write_text(_TELEPORT_KUBECONFIG, encoding="utf-8")

    async def factory(path: str, context: str) -> KubernetesClients:
        built.append(context)
        return KubernetesClients(core_v1=core, apps_v1=None, batch_v1=None)

    tool = KubernetesInspectTool(
        in_cluster=False, kubeconfig_path=str(kubeconfig), max_list_items=1, client_factory=factory
    )

    result = await tool.execute(
        {
            "action": "pods",
            "cluster": "ymir",
            "namespace": "nats",
            "label_selector": "app.kubernetes.io/name=valkyrie",
            "name_prefix": "valkyrie-ymir-k8s",
        }
    )

    payload = json.loads(result.content)
    assert core.selectors == ["app.kubernetes.io/name=valkyrie"]
    assert payload["total"] == 2
    assert payload["truncated"] is True
    assert cap_hit_counts() == {"tools.kubernetes.max_list_items": 1}
    assert payload["pods"] == [
        {
            "name": "valkyrie-ymir-k8s-7b864d-abcde",
            "phase": "Running",
            "ready": "2/2",
            "restarts": 1,
            "node_name": "node-a",
            "started_at": "",
            "owner": "ReplicaSet/valkyrie-ymir-k8s-7b864d",
        }
    ]


@pytest.mark.asyncio
async def test_pods_needs_a_namespace() -> None:
    tool = KubernetesInspectTool(core_v1=_PodsCore(), apps_v1=FakeAppsV1(), batch_v1=FakeBatchV1())

    result = await tool.execute({"action": "pods"})

    assert result.is_error and "namespace is required" in result.content


def test_k8s_persona_can_request_kubernetes_inspect_tool() -> None:
    persona = SimpleNamespace(
        allowed_tools=["ravn", "kubernetes_inspect"],
        forbidden_tools=[],
    )

    assert "kubernetes" in _groups_for_persona(persona)
    settings = Settings(environment={"id": "cluster-a", "type": "k8s"})
    filtered = _filter_tools(
        [KubernetesInspectTool(core_v1=FakeCoreV1())],
        settings,
        persona,
    )

    assert [tool.name for tool in filtered] == ["kubernetes_inspect"]


class _NamedTool:
    """Minimal stand-in so _filter_tools can be exercised by tool name."""

    def __init__(self, name: str) -> None:
        self.name = name


def test_k8s_valkyrie_persona_has_no_shell() -> None:
    """The resident may inspect and build, but never shell out.

    An ad-hoc script is neither reusable nor auditable and cannot be taught to
    a peer — but the sharper reason is incentive. build_tool costs ~30 minutes
    of A2A polling; `bash` costs seconds. With both available the resident
    always took the shortcut, and the only builds it ever commissioned came
    while kubernetes_inspect was missing and shelling out to the API failed.
    """
    from ravn.adapters.personas.loader import (  # noqa: PLC0415
        _BUILTIN_PERSONAS_DIR,
        FilesystemPersonaAdapter,
    )

    persona = FilesystemPersonaAdapter().load_from_file(_BUILTIN_PERSONAS_DIR / "k8s-valkyrie.yaml")
    assert persona is not None

    settings = Settings(environment={"id": "cluster-a", "type": "k8s"})
    offered = ["bash", "terminal", "kubernetes_inspect", "build_tool", "read_file"]
    surviving = {
        tool.name for tool in _filter_tools([_NamedTool(n) for n in offered], settings, persona)
    }

    assert "bash" not in surviving
    assert "terminal" not in surviving
    # The replacements have to still be there, or this just removes capability.
    assert {"kubernetes_inspect", "build_tool", "read_file"} <= surviving
