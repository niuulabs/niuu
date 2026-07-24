"""Tests for the NATS JetStream Environment signal transport.

The adapter is transport only: everything here exercises the durable pull
consumer plumbing and the pass-through handoff to ``GenericSignalAdapter``.
No live NATS — connection, JetStream context, and subscription are faked at
the same seams the real nats-py client exposes.
"""

from __future__ import annotations

import json
from typing import Any

import nats.js.api as js_api
import pytest

import ravn.adapters.environment_signals_nats as signals_nats
from ravn.adapters.environment_signals_nats import (
    NatsJetStreamSignalAdapter,
    _sanitize_durable,
)
from ravn.config import Settings
from ravn.domain.environment import k8s_environment_fixture
from ravn.domain.models import AgentTask
from ravn.environment_signal_runtime import EnvironmentSignalRuntime
from sleipnir.adapters.in_process import InProcessBus


class _FakeMsg:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.acked = False
        self.nacked = False

    async def ack(self) -> None:
        self.acked = True

    async def nak(self) -> None:
        self.nacked = True


class _FakePullSubscription:
    def __init__(self, batches: list[list[_FakeMsg]]) -> None:
        self._batches = list(batches)
        self.fetch_calls: list[tuple[int, float]] = []

    async def fetch(self, batch: int, timeout: float) -> list[_FakeMsg]:
        self.fetch_calls.append((batch, timeout))
        if not self._batches:
            raise TimeoutError("no messages")
        return self._batches.pop(0)


class _FakeJetStream:
    def __init__(self, psub: _FakePullSubscription) -> None:
        self._psub = psub
        self.pull_subscribe_calls: list[dict[str, Any]] = []

    async def pull_subscribe(
        self,
        subject: str,
        *,
        durable: str,
        stream: str,
        config: Any,
    ) -> _FakePullSubscription:
        self.pull_subscribe_calls.append(
            {"subject": subject, "durable": durable, "stream": stream, "config": config}
        )
        return self._psub


class _FakeClient:
    def __init__(self, js: _FakeJetStream) -> None:
        self._js = js
        self.jetstream_calls: list[dict[str, Any]] = []
        self.is_closed = False

    def jetstream(self, **kwargs: Any) -> _FakeJetStream:
        self.jetstream_calls.append(kwargs)
        return self._js

    async def close(self) -> None:
        self.is_closed = True


def _adapter(
    batches: list[list[_FakeMsg]],
    **overrides: Any,
) -> tuple[NatsJetStreamSignalAdapter, _FakeClient, _FakeJetStream, _FakePullSubscription]:
    psub = _FakePullSubscription(batches)
    js = _FakeJetStream(psub)
    client = _FakeClient(js)
    kwargs: dict[str, Any] = {
        "environment": k8s_environment_fixture(),
        "source_id": "workshop-laevateinn",
        "servers": ["tls://nats.test:4222"],
        "stream_name": "workshop-laevateinn-events",
        "subject": "workshop.laevateinn.>",
        "client": client,
    }
    kwargs.update(overrides)
    return NatsJetStreamSignalAdapter(**kwargs), client, js, psub


def _envelope(**extra: Any) -> dict[str, Any]:
    envelope = {
        "type": "status",
        "ts": 1789000000.0,
        "origin": {"agent": "laevateinn-01", "mainboardId": "1aeva7e100000001"},
        "payload": {"CurrentStatus": [1]},
    }
    envelope.update(extra)
    return envelope


@pytest.mark.asyncio
async def test_collect_passes_raw_payload_through_untouched() -> None:
    envelope = _envelope()
    adapter, _, _, _ = _adapter([[_FakeMsg(json.dumps(envelope).encode())]])

    signals = await adapter.collect()

    assert len(signals) == 1
    signal = signals[0]
    assert signal.normalized_payload == envelope
    assert signal.signal_type == "generic"
    assert signal.severity == "info"
    assert signal.source_id == "workshop-laevateinn"
    assert signal.dedupe_key.startswith("workshop-laevateinn:")


@pytest.mark.asyncio
async def test_declared_severity_is_honored_without_heuristics() -> None:
    envelope = _envelope(severity="critical")
    adapter, _, _, _ = _adapter([[_FakeMsg(json.dumps(envelope).encode())]])

    signals = await adapter.collect()

    assert signals[0].severity == "critical"
    assert signals[0].normalized_payload["severity"] == "critical"


@pytest.mark.asyncio
async def test_non_json_payload_is_delivered_as_raw_text() -> None:
    adapter, _, _, _ = _adapter([[_FakeMsg(b"not json at all")]])

    signals = await adapter.collect()

    assert signals[0].normalized_payload == {"value": "not json at all"}


@pytest.mark.asyncio
async def test_scalar_json_payload_is_wrapped() -> None:
    adapter, _, _, _ = _adapter([[_FakeMsg(b"42")]])

    signals = await adapter.collect()

    assert signals[0].normalized_payload == {"value": 42}


@pytest.mark.asyncio
async def test_idle_stream_yields_empty_batch() -> None:
    adapter, _, _, psub = _adapter([])

    assert await adapter.collect() == []
    assert psub.fetch_calls == [
        (signals_nats.DEFAULT_BATCH_SIZE, signals_nats.DEFAULT_FETCH_TIMEOUT_S)
    ]


@pytest.mark.asyncio
async def test_messages_are_acked_only_after_commit() -> None:
    msgs = [_FakeMsg(json.dumps(_envelope()).encode()), _FakeMsg(b"plain")]
    adapter, _, _, _ = _adapter([msgs])

    await adapter.collect()

    assert not any(msg.acked for msg in msgs)
    await adapter.commit()

    assert all(msg.acked for msg in msgs)


@pytest.mark.asyncio
async def test_rollback_releases_uncommitted_messages_for_redelivery() -> None:
    msgs = [_FakeMsg(json.dumps(_envelope()).encode())]
    adapter, _, _, _ = _adapter([msgs])

    await adapter.collect()
    await adapter.rollback()

    assert msgs[0].nacked is True
    assert msgs[0].acked is False


@pytest.mark.asyncio
async def test_runtime_commits_only_after_durable_window_is_enqueued() -> None:
    message = _FakeMsg(json.dumps(_envelope()).encode())
    adapter, _, _, _ = _adapter([[message]])
    enqueued: list[AgentTask] = []

    async def enqueue(task: AgentTask) -> None:
        assert message.acked is False
        enqueued.append(task)

    runtime = EnvironmentSignalRuntime(
        settings=Settings(),
        publisher=InProcessBus(),
        enqueue=enqueue,
    )
    runtime._adapters = [adapter]

    assert await runtime.collect_once() == 1
    assert message.acked is True
    assert enqueued[0].triggered_by == "signal:durable_window"
    assert '"CurrentStatus": [1]' in enqueued[0].initiative_context


@pytest.mark.asyncio
async def test_durable_transport_persists_to_inbox_without_queueing_each_poll() -> None:
    message = _FakeMsg(json.dumps(_envelope()).encode())
    adapter, _, _, _ = _adapter([[message]])
    enqueued: list[AgentTask] = []
    processed: list[Any] = []

    async def process(event: Any) -> dict[str, str]:
        processed.append(event)
        return {
            "residentAutonomySignalPersisted": True,
            "residentAutonomySignalRef": "resident/inbox/signals/event.md",
        }

    async def enqueue(task: AgentTask) -> None:
        enqueued.append(task)

    runtime = EnvironmentSignalRuntime(
        settings=Settings(),
        publisher=InProcessBus(),
        enqueue=enqueue,
        resident_signal_processor=process,
        durable_home_enabled=True,
    )
    runtime._adapters = [adapter]

    assert await runtime.collect_once() == 1
    assert message.acked is True
    assert len(processed) == 1
    assert enqueued == []


@pytest.mark.asyncio
async def test_durable_transport_rolls_back_when_inbox_persistence_fails() -> None:
    message = _FakeMsg(json.dumps(_envelope()).encode())
    adapter, _, _, _ = _adapter([[message]])

    async def fail(_event: Any) -> dict[str, str]:
        raise RuntimeError("inbox unavailable")

    runtime = EnvironmentSignalRuntime(
        settings=Settings(),
        publisher=InProcessBus(),
        resident_signal_processor=fail,
        durable_home_enabled=True,
    )
    runtime._adapters = [adapter]

    with pytest.raises(RuntimeError, match="inbox unavailable"):
        await runtime.collect_once()

    assert message.acked is False
    assert message.nacked is True


@pytest.mark.asyncio
async def test_runtime_rolls_back_durable_delivery_without_resident_queue() -> None:
    message = _FakeMsg(json.dumps(_envelope()).encode())
    adapter, _, _, _ = _adapter([[message]])
    runtime = EnvironmentSignalRuntime(
        settings=Settings(),
        publisher=InProcessBus(),
    )
    runtime._adapters = [adapter]

    with pytest.raises(RuntimeError, match="requires a resident task queue"):
        await runtime.collect_once()

    assert message.acked is False
    assert message.nacked is True


@pytest.mark.asyncio
async def test_runtime_rolls_back_when_resident_queue_rejects_window() -> None:
    message = _FakeMsg(json.dumps(_envelope()).encode())
    adapter, _, _, _ = _adapter([[message]])

    async def reject(_task: AgentTask) -> bool:
        return False

    runtime = EnvironmentSignalRuntime(
        settings=Settings(),
        publisher=InProcessBus(),
        enqueue=reject,
    )
    runtime._adapters = [adapter]

    with pytest.raises(RuntimeError, match="rejected durable signal window"):
        await runtime.collect_once()

    assert message.acked is False
    assert message.nacked is True


@pytest.mark.asyncio
async def test_runtime_rolls_back_when_publish_fails() -> None:
    class FailingPublisher:
        async def publish_batch(self, _events: list[Any]) -> None:
            raise RuntimeError("publisher unavailable")

    message = _FakeMsg(json.dumps(_envelope()).encode())
    adapter, _, _, _ = _adapter([[message]])
    runtime = EnvironmentSignalRuntime(
        settings=Settings(),
        publisher=FailingPublisher(),  # type: ignore[arg-type]
    )
    runtime._adapters = [adapter]

    with pytest.raises(RuntimeError, match="publisher unavailable"):
        await runtime.collect_once()

    assert message.acked is False
    assert message.nacked is True
    assert runtime._seen == {}


@pytest.mark.asyncio
async def test_redelivery_reuses_event_identity_after_enqueue_failure() -> None:
    class RecordingPublisher:
        def __init__(self) -> None:
            self.event_ids: list[str] = []

        async def publish_batch(self, events: list[Any]) -> None:
            self.event_ids.extend(event.event_id for event in events)

    envelope = json.dumps(_envelope()).encode()
    first = _FakeMsg(envelope)
    redelivery = _FakeMsg(envelope)
    adapter, _, _, _ = _adapter([[first], [redelivery]])
    publisher = RecordingPublisher()
    attempts = 0

    async def enqueue(_task: AgentTask) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("queue unavailable")

    runtime = EnvironmentSignalRuntime(
        settings=Settings(),
        publisher=publisher,  # type: ignore[arg-type]
        enqueue=enqueue,
    )
    runtime._adapters = [adapter]

    with pytest.raises(RuntimeError, match="queue unavailable"):
        await runtime.collect_once()
    assert await runtime.collect_once() == 1

    assert first.nacked is True
    assert redelivery.acked is True
    assert publisher.event_ids[0] == publisher.event_ids[1]


@pytest.mark.asyncio
async def test_durable_consumer_is_bound_once_with_configured_names() -> None:
    adapter, client, js, _ = _adapter(
        [[_FakeMsg(b"{}")], [_FakeMsg(b"{}")]],
        durable_name="ivaldi-workshop",
        deliver_policy="all",
        jetstream_domain="eitri",
    )

    await adapter.collect()
    await adapter.commit()
    await adapter.collect()

    assert client.jetstream_calls == [{"domain": "eitri"}]
    assert len(js.pull_subscribe_calls) == 1
    call = js.pull_subscribe_calls[0]
    assert call["subject"] == "workshop.laevateinn.>"
    assert call["durable"] == "ivaldi-workshop"
    assert call["stream"] == "workshop-laevateinn-events"
    assert call["config"].deliver_policy == js_api.DeliverPolicy.ALL
    assert call["config"].ack_policy == js_api.AckPolicy.EXPLICIT


@pytest.mark.asyncio
async def test_default_durable_name_is_sanitized_from_source_id() -> None:
    adapter, _, js, _ = _adapter([[]], source_id="workshop.laevateinn/events")

    await adapter.collect()

    assert js.pull_subscribe_calls[0]["durable"] == "signal-workshop-laevateinn-events"


@pytest.mark.asyncio
async def test_connects_via_shared_transport_helper_when_no_client_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    psub = _FakePullSubscription([[]])
    js = _FakeJetStream(psub)
    client = _FakeClient(js)
    connect_calls: list[dict[str, Any]] = []

    async def _fake_connect(**kwargs: Any) -> _FakeClient:
        connect_calls.append(kwargs)
        return client

    monkeypatch.setattr(signals_nats, "connect_nats", _fake_connect)
    adapter = NatsJetStreamSignalAdapter(
        environment=k8s_environment_fixture(),
        source_id="workshop-laevateinn",
        servers="tls://nats.test:4222, tls://nats-2.test:4222",
        stream_name="workshop-laevateinn-events",
        subject="workshop.laevateinn.>",
        user="ivaldi",
        password="secret",
    )

    await adapter.collect()

    assert connect_calls[0]["servers"] == [
        "tls://nats.test:4222",
        "tls://nats-2.test:4222",
    ]
    assert connect_calls[0]["options"] == {"user": "ivaldi", "password": "secret"}
    assert js.pull_subscribe_calls[0]["stream"] == "workshop-laevateinn-events"


@pytest.mark.asyncio
async def test_fetch_failure_reconnects_and_rebinds_on_next_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingSubscription(_FakePullSubscription):
        async def fetch(self, batch: int, timeout: float) -> list[_FakeMsg]:
            self.fetch_calls.append((batch, timeout))
            raise ConnectionError("NATS connection closed")

    failed_subscription = FailingSubscription([])
    first_client = _FakeClient(_FakeJetStream(failed_subscription))
    recovered_message = _FakeMsg(json.dumps(_envelope()).encode())
    recovered_subscription = _FakePullSubscription([[recovered_message]])
    second_client = _FakeClient(_FakeJetStream(recovered_subscription))
    clients = [first_client, second_client]

    async def _fake_connect(**_kwargs: Any) -> _FakeClient:
        return clients.pop(0)

    monkeypatch.setattr(signals_nats, "connect_nats", _fake_connect)
    adapter = NatsJetStreamSignalAdapter(
        environment=k8s_environment_fixture(),
        source_id="workshop-laevateinn",
        servers=["tls://nats.test:4222"],
        stream_name="workshop-laevateinn-events",
        subject="workshop.laevateinn.>",
    )

    with pytest.raises(ConnectionError, match="connection closed"):
        await adapter.collect()
    signals = await adapter.collect()

    assert first_client.is_closed is True
    assert len(signals) == 1
    assert signals[0].normalized_payload == _envelope()


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("stream_name", "  ", "stream_name"),
        ("subject", "", "subject"),
        ("deliver_policy", "sideways", "deliver_policy"),
        ("batch_size", 0, "batch_size"),
        ("servers", [], "server"),
    ],
)
def test_invalid_configuration_fails_loudly(field: str, value: Any, match: str) -> None:
    kwargs: dict[str, Any] = {
        "environment": k8s_environment_fixture(),
        "source_id": "workshop-laevateinn",
        "servers": ["tls://nats.test:4222"],
        "stream_name": "workshop-laevateinn-events",
        "subject": "workshop.laevateinn.>",
        "client": object(),
    }
    kwargs[field] = value
    with pytest.raises(ValueError, match=match):
        NatsJetStreamSignalAdapter(**kwargs)


def test_sanitize_durable_strips_wildcards_and_dots() -> None:
    assert _sanitize_durable("workshop.laevateinn.>") == "workshop-laevateinn"
    assert _sanitize_durable("...") == "signals"
