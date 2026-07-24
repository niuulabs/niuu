"""Tests for Environment signal adapters."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from ravn.adapters.environment_signals import (
    KubernetesSignalAdapter,
    demo_signal_adapters,
    signal_sources_observatory_fragment,
)
from ravn.domain.environment import (
    k8s_environment_fixture,
)
from ravn.ports.signal_adapter import NormalizedSignal
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain.catalog import valkyrie_judgment_recorded
from sleipnir.domain.events import SleipnirEvent


@dataclass
class _FakeK8sList:
    items: list[dict]


class _FakeCoreV1:
    def __init__(self, items: list[dict]) -> None:
        self.items = items

    def list_event_for_all_namespaces(self) -> _FakeK8sList:
        return _FakeK8sList(self.items)


def _k8s_warning_event() -> dict:
    return {
        "metadata": {"name": "api-oom", "uid": "ev-k8s-oom", "namespace": "shop"},
        "involvedObject": {
            "apiVersion": "v1",
            "kind": "Pod",
            "namespace": "shop",
            "name": "api-7d9",
            "uid": "pod-api-7d9",
        },
        "type": "Warning",
        "reason": "OOMKilled",
        "message": "Container was terminated by OOM killer",
        "eventTime": "2026-06-03T12:05:00Z",
        "count": 2,
    }


@pytest.mark.asyncio
async def test_kubernetes_adapter_normalizes_client_events() -> None:
    environment = k8s_environment_fixture()
    adapter = KubernetesSignalAdapter.from_kubernetes_client(
        environment=environment,
        core_v1=_FakeCoreV1([_k8s_warning_event()]),
        critical_reasons=["oomkilled"],
    )

    signals = await adapter.collect()

    assert signals == [
        NormalizedSignal(
            source_id="kubernetes-events",
            environment_id="cluster-prod-a",
            environment_type="k8s",
            signal_type="kubernetes",
            severity="critical",
            timestamp=signals[0].timestamp,
            raw_payload_ref="k8s://shop/events/api-oom#ev-k8s-oom",
            normalized_payload={
                "namespace": "shop",
                "kind": "Pod",
                "name": "api-7d9",
                "reason": "OOMKilled",
                "message": "Container was terminated by OOM killer",
                "count": 2,
                "type": "Warning",
            },
            dedupe_key="k8s:shop:Pod:api-7d9:OOMKilled",
            correlation_id="cluster-prod-a:k8s:shop:Pod:api-7d9:OOMKilled",
            provider="kubernetes",
            provider_event_id="ev-k8s-oom",
            object_ref={
                "api_version": "v1",
                "kind": "Pod",
                "namespace": "shop",
                "name": "api-7d9",
                "uid": "pod-api-7d9",
            },
            provenance={"adapter": "kubernetes.events", "source_id": "kubernetes-events"},
        )
    ]


@pytest.mark.asyncio
async def test_signal_adapter_publishes_standard_sleipnir_events() -> None:
    environment = k8s_environment_fixture()
    bus = InProcessBus()
    received: list[SleipnirEvent] = []
    await bus.subscribe(["signal.*"], lambda event: _record(received, event))
    adapter = KubernetesSignalAdapter(
        environment=environment,
        source_id="kubernetes-events",
        raw_items=[_k8s_warning_event()],
    )

    events = await adapter.publish(bus)
    await bus.flush()

    assert [event.event_type for event in events] == ["signal.kubernetes.event"]
    assert received == events
    assert received[0].payload["nats_subject"] == "ravn.environment.signal.kubernetes.event"
    assert received[0].payload["data"]["dedupe_key"] == "k8s:shop:Pod:api-7d9:OOMKilled"
    assert received[0].tenant_id == "niuu"

    replay = KubernetesSignalAdapter(
        environment=environment,
        source_id="kubernetes-events",
        raw_items=[_k8s_warning_event()],
    )
    replay_events = await replay.publish(InProcessBus())
    assert replay_events[0].event_id == events[0].event_id


@pytest.mark.asyncio
async def test_signal_to_valkyrie_judgment_path_over_in_process_sleipnir() -> None:
    environment = k8s_environment_fixture()
    bus = InProcessBus()
    judgments: list[SleipnirEvent] = []

    async def judge(event: SleipnirEvent) -> None:
        judgment = valkyrie_judgment_recorded(
            environment_id=event.payload["environment_id"],
            valkyrie_id="valkyrie:k8s-prod-a",
            attention_tier="watch",
            recommended_action="k8s.inspect_pod",
            authority_boundary="autonomous",
            confidence=0.87,
            evidence=[
                {
                    "event_id": event.event_id,
                    "dedupe_key": event.payload["data"]["dedupe_key"],
                }
            ],
            source="valkyrie:k8s-prod-a",
            correlation_id=event.correlation_id,
            causation_id=event.event_id,
            tenant_id=event.tenant_id,
        )
        await bus.publish(judgment)

    await bus.subscribe(["signal.kubernetes.*"], judge)
    await bus.subscribe(["valkyrie.judgment.*"], lambda event: _record(judgments, event))

    adapter = KubernetesSignalAdapter(
        environment=environment,
        source_id="kubernetes-events",
        raw_items=[_k8s_warning_event()],
    )
    [signal_event] = await adapter.publish(bus)
    await bus.flush()

    assert len(judgments) == 1
    assert judgments[0].causation_id == signal_event.event_id
    assert judgments[0].payload["environment_id"] == "cluster-prod-a"
    assert judgments[0].payload["recommended_action"] == "k8s.inspect_pod"


def test_signal_sources_project_into_existing_observatory_surface() -> None:
    environment = k8s_environment_fixture()

    fragment = signal_sources_observatory_fragment(environment)

    assert [node["typeId"] for node in fragment["nodes"]] == ["service", "service"]
    assert [node["sourceKind"] for node in fragment["nodes"]] == [
        "signal_source",
        "signal_source",
    ]
    assert fragment["nodes"][0]["parentId"] == "cluster:prod-a"
    assert fragment["edges"][0]["kind"] == "dashed-anim"
    assert fragment["meta"]["sourceKind"] == "environment_signal_sources"


@pytest.mark.asyncio
async def test_kubernetes_adapter_accepts_runtime_client_kwargs() -> None:
    environment = k8s_environment_fixture()
    adapter = KubernetesSignalAdapter(
        environment=environment,
        source_id="kubernetes-events",
        core_v1=_FakeCoreV1([_k8s_warning_event()]),
        in_cluster=True,
        namespaces=[],
        exclude_reasons=["Pulled"],
    )

    signals = await adapter.collect()

    assert len(signals) == 1
    assert signals[0].provider_event_id == "ev-k8s-oom"


@pytest.mark.asyncio
async def test_kubernetes_adapter_can_ignore_historical_events() -> None:
    environment = k8s_environment_fixture()
    recent_event = {
        **_k8s_warning_event(),
        "metadata": {"name": "api-recent", "uid": "ev-k8s-recent", "namespace": "shop"},
        "eventTime": None,
    }
    adapter = KubernetesSignalAdapter(
        environment=environment,
        source_id="kubernetes-events",
        raw_items=[_k8s_warning_event(), recent_event],
        max_event_age_seconds=60,
    )

    signals = await adapter.collect()

    assert [signal.provider_event_id for signal in signals] == ["ev-k8s-recent"]


@pytest.mark.asyncio
async def test_demo_signal_adapters_cover_mvp_environment_fixtures() -> None:
    adapters = demo_signal_adapters()
    events_by_environment: dict[str, list[SleipnirEvent]] = {}

    for adapter in adapters:
        for signal in await adapter.collect():
            event = signal.to_event(
                source=f"adapter:{adapter.source_id}",
                environment=adapter.environment,
            )
            events_by_environment.setdefault(event.payload["environment_id"], []).append(event)

    assert set(events_by_environment) == {
        "cluster-prod-a",
    }
    assert [event.event_type for event in events_by_environment["cluster-prod-a"]] == [
        "signal.kubernetes.event",
        "signal.kubernetes.event",
    ]


async def _record(events: list[SleipnirEvent], event: SleipnirEvent) -> None:
    events.append(event)


async def test_kubernetes_adapter_reads_raw_items_from_file(tmp_path) -> None:
    import json as _json

    from ravn.adapters.environment_signals import KubernetesSignalAdapter
    from ravn.domain.environment import k8s_environment_fixture

    signal_file = tmp_path / "signals.json"
    adapter = KubernetesSignalAdapter(
        environment=k8s_environment_fixture(),
        source_id="file-events",
        raw_items_file=str(signal_file),
        critical_reasons=["oomkilled"],
    )

    assert await adapter.collect() == []

    signal_file.write_text(
        _json.dumps(
            [
                {
                    "metadata": {"name": "payments-oom.1", "uid": "uid-1"},
                    "involvedObject": {"kind": "Pod", "name": "payments-1", "namespace": "pay"},
                    "reason": "OOMKilled",
                    "message": "Container killed",
                    "type": "Warning",
                }
            ]
        ),
        encoding="utf-8",
    )
    signals = await adapter.collect()
    assert len(signals) == 1
    assert signals[0].normalized_payload["reason"] == "OOMKilled"
    assert signals[0].severity == "critical"


async def test_raw_items_file_rejects_non_array_content(tmp_path) -> None:
    import pytest as _pytest

    from ravn.adapters.environment_signals import KubernetesSignalAdapter
    from ravn.domain.environment import k8s_environment_fixture

    signal_file = tmp_path / "signals.json"
    signal_file.write_text('{"not": "a list"}', encoding="utf-8")
    adapter = KubernetesSignalAdapter(
        environment=k8s_environment_fixture(),
        source_id="file-events",
        raw_items_file=str(signal_file),
        critical_reasons=["oomkilled"],
    )
    with _pytest.raises(ValueError, match="JSON array"):
        await adapter.collect()
