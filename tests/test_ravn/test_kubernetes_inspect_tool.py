from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ravn.adapters.tools.kubernetes import KubernetesInspectTool
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
