"""Unit tests for TaskDispatchChannel (NIU-505)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ravn.adapters.channels.event import (
    ROUTING_ACCEPTED,
    ROUTING_COMPLETED,
    ROUTING_FAILED,
    ROUTING_PROGRESS,
    ROUTING_REJECTED,
    TaskDispatchChannel,
    _build_initiative_context,
    _parse_deadline,
)
from ravn.adapters.personas.loader import FilesystemPersonaAdapter, PersonaConfig
from ravn.config import SleipnirConfig
from ravn.domain.models import AgentTask, OutputMode

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(**kwargs) -> SleipnirConfig:
    defaults = dict(
        enabled=True,
        amqp_url_env="SLEIPNIR_AMQP_URL",
        exchange="ravn.events",
        agent_id="test-agent",
        reconnect_delay_s=0.0,
        publish_timeout_s=2.0,
    )
    defaults.update(kwargs)
    return SleipnirConfig(**defaults)


def _dispatch_payload(**kwargs) -> bytes:
    """Build a valid ravn.task.dispatch message body."""
    base = {
        "type": "ravn.task.dispatch",
        "task_id": "task-abc-123",
        "persona": "autonomous-agent",
        "task": "Review all open PRs and summarise status",
        "context": {"org": "niuulabs"},
        "deadline": None,
        "dispatched_by": "tyr",
    }
    base.update(kwargs)
    return json.dumps(base).encode("utf-8")


def _fake_persona_loader(names: list[str]) -> FilesystemPersonaAdapter:
    """Return a FilesystemPersonaAdapter that only knows the listed persona names."""
    loader = MagicMock(spec=FilesystemPersonaAdapter)

    def _load(name: str) -> PersonaConfig | None:
        if name in names:
            return PersonaConfig(name=name)
        return None

    loader.load.side_effect = _load
    return loader


class FakeMessage:
    """Minimal aio_pika message stub for unit tests."""

    def __init__(self, body: bytes, routing_key: str = "ravn.task.dispatch") -> None:
        self.body = body
        self.routing_key = routing_key


# ---------------------------------------------------------------------------
# Utility function tests
# ---------------------------------------------------------------------------


class TestParseDeadline:
    def test_none_returns_none(self) -> None:
        assert _parse_deadline(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert _parse_deadline("") is None

    def test_valid_iso_string(self) -> None:
        result = _parse_deadline("2026-04-05T00:00:00Z")
        assert result is not None
        assert result.year == 2026

    def test_invalid_string_returns_none(self) -> None:
        assert _parse_deadline("not-a-date") is None


class TestBuildInitiativeContext:
    def test_no_context(self) -> None:
        result = _build_initiative_context("do the thing", {})
        assert result == "do the thing"

    def test_with_context(self) -> None:
        result = _build_initiative_context("do the thing", {"key": "val"})
        assert "do the thing" in result
        assert "key" in result
        assert "val" in result

    def test_empty_task(self) -> None:
        result = _build_initiative_context("", {"a": 1})
        assert "a" in result


# ---------------------------------------------------------------------------
# Routing key constants
# ---------------------------------------------------------------------------


class TestRoutingKeyConstants:
    def test_accepted_key(self) -> None:
        assert ROUTING_ACCEPTED == "ravn.task.accepted"

    def test_rejected_key(self) -> None:
        assert ROUTING_REJECTED == "ravn.task.rejected"

    def test_progress_key(self) -> None:
        assert ROUTING_PROGRESS == "ravn.task.progress"

    def test_completed_key(self) -> None:
        assert ROUTING_COMPLETED == "ravn.task.completed"

    def test_failed_key(self) -> None:
        assert ROUTING_FAILED == "ravn.task.failed"


# ---------------------------------------------------------------------------
# TaskDispatchChannel — construction
# ---------------------------------------------------------------------------


class TestTaskDispatchChannelConstruction:
    def test_name_includes_agent_id(self) -> None:
        ch = TaskDispatchChannel(_config(agent_id="my-bot"))
        assert "my-bot" in ch.name

    def test_name_format(self) -> None:
        ch = TaskDispatchChannel(_config(agent_id="x"))
        assert ch.name == "task_dispatch:x"

    def test_default_persona_loader(self) -> None:
        ch = TaskDispatchChannel(_config())
        assert isinstance(ch._persona_loader, FilesystemPersonaAdapter)

    def test_custom_persona_loader(self) -> None:
        loader = MagicMock(spec=FilesystemPersonaAdapter)
        ch = TaskDispatchChannel(_config(), persona_loader=loader)
        assert ch._persona_loader is loader


# ---------------------------------------------------------------------------
# TaskDispatchChannel — handle_message (persona validation)
# ---------------------------------------------------------------------------


class TestHandleMessage:
    @pytest.mark.asyncio
    async def test_unknown_persona_is_rejected(self) -> None:
        """Unknown persona → publish ravn.task.rejected, do not enqueue."""
        loader = _fake_persona_loader(["coding-agent"])  # not autonomous-agent
        ch = TaskDispatchChannel(_config(), persona_loader=loader)

        enqueued: list[AgentTask] = []
        published: list[tuple[str, dict]] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        async def _fake_publish(routing_key: str, payload: dict) -> None:
            published.append((routing_key, payload))

        ch._publish_response = _fake_publish  # type: ignore[method-assign]

        msg = FakeMessage(_dispatch_payload(persona="autonomous-agent"))
        await ch._handle_message(msg, _enqueue)

        assert len(enqueued) == 0
        assert len(published) == 1
        rk, payload = published[0]
        assert rk == ROUTING_REJECTED
        assert "autonomous-agent" in payload["reason"]

    @pytest.mark.asyncio
    async def test_known_persona_is_accepted_and_enqueued(self) -> None:
        """Known persona → publish ravn.task.accepted and enqueue the task."""
        loader = _fake_persona_loader(["autonomous-agent"])
        ch = TaskDispatchChannel(_config(), persona_loader=loader)

        enqueued: list[AgentTask] = []
        published: list[tuple[str, dict]] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        async def _fake_publish(routing_key: str, payload: dict) -> None:
            published.append((routing_key, payload))

        ch._publish_response = _fake_publish  # type: ignore[method-assign]

        msg = FakeMessage(_dispatch_payload())
        await ch._handle_message(msg, _enqueue)

        assert len(enqueued) == 1
        assert enqueued[0].task_id == "task-abc-123"
        assert enqueued[0].persona == "autonomous-agent"
        assert enqueued[0].output_mode == OutputMode.AMBIENT

        assert len(published) == 1
        rk, _ = published[0]
        assert rk == ROUTING_ACCEPTED

    @pytest.mark.asyncio
    async def test_task_id_from_payload(self) -> None:
        loader = _fake_persona_loader(["autonomous-agent"])
        ch = TaskDispatchChannel(_config(), persona_loader=loader)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        ch._publish_response = AsyncMock()  # type: ignore[method-assign]

        msg = FakeMessage(_dispatch_payload(task_id="custom-id-42"))
        await ch._handle_message(msg, _enqueue)

        assert enqueued[0].task_id == "custom-id-42"

    @pytest.mark.asyncio
    async def test_deadline_parsed(self) -> None:
        loader = _fake_persona_loader(["autonomous-agent"])
        ch = TaskDispatchChannel(_config(), persona_loader=loader)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        ch._publish_response = AsyncMock()  # type: ignore[method-assign]

        future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        msg = FakeMessage(_dispatch_payload(deadline=future))
        await ch._handle_message(msg, _enqueue)

        assert enqueued[0].deadline is not None

    @pytest.mark.asyncio
    async def test_invalid_deadline_ignored(self) -> None:
        loader = _fake_persona_loader(["autonomous-agent"])
        ch = TaskDispatchChannel(_config(), persona_loader=loader)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        ch._publish_response = AsyncMock()  # type: ignore[method-assign]

        msg = FakeMessage(_dispatch_payload(deadline="not-a-date"))
        await ch._handle_message(msg, _enqueue)

        assert enqueued[0].deadline is None

    @pytest.mark.asyncio
    async def test_malformed_json_publishes_rejected(self) -> None:
        """Non-JSON body → publish ravn.task.rejected, do not enqueue."""
        loader = _fake_persona_loader(["autonomous-agent"])
        ch = TaskDispatchChannel(_config(), persona_loader=loader)

        enqueued: list[AgentTask] = []
        published: list[tuple[str, dict]] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        async def _fake_publish(routing_key: str, payload: dict) -> None:
            published.append((routing_key, payload))

        ch._publish_response = _fake_publish  # type: ignore[method-assign]

        msg = FakeMessage(b"this is not json")
        await ch._handle_message(msg, _enqueue)

        assert len(enqueued) == 0
        assert len(published) == 1
        rk, payload = published[0]
        assert rk == ROUTING_REJECTED
        assert "malformed" in payload["reason"]

    @pytest.mark.asyncio
    async def test_empty_task_field_is_rejected(self) -> None:
        """Empty/missing task field → publish ravn.task.rejected, do not enqueue."""
        loader = _fake_persona_loader(["autonomous-agent"])
        ch = TaskDispatchChannel(_config(), persona_loader=loader)

        enqueued: list[AgentTask] = []
        published: list[tuple[str, dict]] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        async def _fake_publish(routing_key: str, payload: dict) -> None:
            published.append((routing_key, payload))

        ch._publish_response = _fake_publish  # type: ignore[method-assign]

        msg = FakeMessage(_dispatch_payload(task=""))
        await ch._handle_message(msg, _enqueue)

        assert len(enqueued) == 0
        assert len(published) == 1
        rk, payload = published[0]
        assert rk == ROUTING_REJECTED
        assert "task" in payload["reason"]

    @pytest.mark.asyncio
    async def test_context_included_in_initiative_context(self) -> None:
        loader = _fake_persona_loader(["autonomous-agent"])
        ch = TaskDispatchChannel(_config(), persona_loader=loader)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        ch._publish_response = AsyncMock()  # type: ignore[method-assign]

        msg = FakeMessage(
            _dispatch_payload(
                task="do something",
                context={"repo": "niuulabs/volundr"},
            )
        )
        await ch._handle_message(msg, _enqueue)

        assert "niuulabs/volundr" in enqueued[0].initiative_context

    @pytest.mark.asyncio
    async def test_accepted_payload_contains_task_id_and_agent(self) -> None:
        loader = _fake_persona_loader(["autonomous-agent"])
        ch = TaskDispatchChannel(_config(agent_id="my-agent"), persona_loader=loader)

        published: list[tuple[str, dict]] = []

        async def _fake_publish(routing_key: str, payload: dict) -> None:
            published.append((routing_key, payload))

        ch._publish_response = _fake_publish  # type: ignore[method-assign]

        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        msg = FakeMessage(_dispatch_payload(task_id="t-99"))
        await ch._handle_message(msg, _enqueue)

        _, payload = published[0]
        assert payload["task_id"] == "t-99"
        assert payload["agent_id"] == "my-agent"

    @pytest.mark.asyncio
    async def test_rejected_payload_contains_reason(self) -> None:
        loader = _fake_persona_loader([])  # no known personas
        ch = TaskDispatchChannel(_config(), persona_loader=loader)

        published: list[tuple[str, dict]] = []

        async def _fake_publish(routing_key: str, payload: dict) -> None:
            published.append((routing_key, payload))

        ch._publish_response = _fake_publish  # type: ignore[method-assign]

        msg = FakeMessage(_dispatch_payload(persona="hacker-persona"))
        await ch._handle_message(msg, AsyncMock())

        _, payload = published[0]
        assert "reason" in payload
        assert "hacker-persona" in payload["reason"]


# ---------------------------------------------------------------------------
# TaskDispatchChannel — outbound publish methods
# ---------------------------------------------------------------------------


class TestOutboundPublishMethods:
    @pytest.mark.asyncio
    async def test_publish_progress(self) -> None:
        ch = TaskDispatchChannel(_config())
        ch._publish_response = AsyncMock()  # type: ignore[method-assign]

        await ch.publish_progress("task-1", iteration=3, message="still running")

        ch._publish_response.assert_awaited_once()
        rk = ch._publish_response.call_args[0][0]
        payload = ch._publish_response.call_args[0][1]
        assert rk == ROUTING_PROGRESS
        assert payload["task_id"] == "task-1"
        assert payload["iteration"] == 3

    @pytest.mark.asyncio
    async def test_publish_completed(self) -> None:
        ch = TaskDispatchChannel(_config())
        ch._publish_response = AsyncMock()  # type: ignore[method-assign]

        await ch.publish_completed("task-2", outcome="success", summary="done")

        ch._publish_response.assert_awaited_once()
        rk = ch._publish_response.call_args[0][0]
        payload = ch._publish_response.call_args[0][1]
        assert rk == ROUTING_COMPLETED
        assert payload["task_id"] == "task-2"
        assert payload["outcome"] == "success"

    @pytest.mark.asyncio
    async def test_publish_failed(self) -> None:
        ch = TaskDispatchChannel(_config())
        ch._publish_response = AsyncMock()  # type: ignore[method-assign]

        await ch.publish_failed("task-3", error="agent exploded")

        ch._publish_response.assert_awaited_once()
        rk = ch._publish_response.call_args[0][0]
        payload = ch._publish_response.call_args[0][1]
        assert rk == ROUTING_FAILED
        assert payload["task_id"] == "task-3"
        assert payload["error"] == "agent exploded"


# ---------------------------------------------------------------------------
# TaskDispatchChannel — publish_response (outbound connection)
# ---------------------------------------------------------------------------


class TestPublishResponse:
    @pytest.mark.asyncio
    async def test_no_amqp_url_drops_silently(self) -> None:
        """Missing AMQP URL → _publish_response drops event without raising."""
        ch = TaskDispatchChannel(_config(amqp_url_env="NONEXISTENT_VAR"))

        import os

        os.environ.pop("NONEXISTENT_VAR", None)
        # Must not raise
        await ch._publish_response(ROUTING_ACCEPTED, {"task_id": "t1"})

    @pytest.mark.asyncio
    async def test_no_aio_pika_drops_silently(self) -> None:
        """aio_pika not installed → _publish_response drops silently."""
        import ravn.adapters.channels._rabbitmq_base as base_mod

        ch = TaskDispatchChannel(_config())
        ch._exchange = MagicMock()

        with patch.object(base_mod, "aio_pika", None):
            await ch._publish_response(ROUTING_ACCEPTED, {"task_id": "t1"})
        # No exception

    @pytest.mark.asyncio
    async def test_publish_uses_correct_routing_key(self) -> None:
        """_publish_response publishes to the exact routing_key passed in."""
        import ravn.adapters.channels._rabbitmq_base as base_mod

        ch = TaskDispatchChannel(_config(reconnect_delay_s=0.0))

        published_keys: list[str] = []

        class FakeMsgCls:
            def __init__(self, body, **kwargs):
                self.body = body

        mock_exchange = AsyncMock()

        async def fake_publish(message, *, routing_key):
            published_keys.append(routing_key)

        mock_exchange.publish.side_effect = fake_publish
        ch._exchange = mock_exchange
        ch._connection = AsyncMock()
        ch._channel = AsyncMock()

        fake_pika = MagicMock()
        fake_pika.Message = FakeMsgCls

        with patch.object(base_mod, "aio_pika", fake_pika):
            await ch._publish_response(ROUTING_ACCEPTED, {"task_id": "t1"})

        assert published_keys == [ROUTING_ACCEPTED]

    @pytest.mark.asyncio
    async def test_publish_failure_invalidates_connection(self) -> None:
        """Exchange is cleared after publish failure to force reconnect."""
        import ravn.adapters.channels._rabbitmq_base as base_mod

        ch = TaskDispatchChannel(_config(reconnect_delay_s=0.0))

        mock_exchange = AsyncMock()
        mock_exchange.publish.side_effect = RuntimeError("conn reset")
        ch._exchange = mock_exchange
        ch._connection = AsyncMock()
        ch._channel = AsyncMock()

        fake_pika = MagicMock()
        fake_pika.Message = MagicMock(return_value=MagicMock())

        with patch.object(base_mod, "aio_pika", fake_pika):
            await ch._publish_response(ROUTING_ACCEPTED, {"task_id": "t1"})

        assert ch._exchange is None

    @pytest.mark.asyncio
    async def test_reconnect_delay_respected(self) -> None:
        """Reconnect is not attempted within reconnect_delay_s."""
        ch = TaskDispatchChannel(_config(reconnect_delay_s=999.0))
        ch._last_connect_attempt = asyncio.get_event_loop().time()

        result = await ch._ensure_exchange()
        assert result is None


# ---------------------------------------------------------------------------
# TaskDispatchChannel — run() entry point
# ---------------------------------------------------------------------------


class TestRunMethod:
    @pytest.mark.asyncio
    async def test_run_exits_on_cancellation(self) -> None:
        """run() returns cleanly when the task is cancelled."""
        import ravn.adapters.channels.event as event_mod

        ch = TaskDispatchChannel(_config())

        async def fake_connect_and_consume(*_args, **_kwargs):
            await asyncio.sleep(999)

        with patch.object(ch, "_connect_and_consume", fake_connect_and_consume):
            with patch.object(event_mod, "aio_pika", MagicMock()):
                task = asyncio.create_task(ch.run(AsyncMock()))
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=2.0)

    @pytest.mark.asyncio
    async def test_run_disabled_when_aio_pika_missing(self) -> None:
        """run() exits immediately when aio_pika is not installed."""
        import ravn.adapters.channels.event as event_mod

        ch = TaskDispatchChannel(_config())
        with patch.object(event_mod, "aio_pika", None):
            # Should return without raising
            await ch.run(AsyncMock())

    @pytest.mark.asyncio
    async def test_run_retries_on_connection_error(self) -> None:
        """run() retries after a connection error."""
        import ravn.adapters.channels.event as event_mod

        ch = TaskDispatchChannel(_config(reconnect_delay_s=0.0))
        call_count = 0

        async def failing_consume(*_args, **_kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("refused")
            # Second call succeeds but suspends — let the test cancel us
            await asyncio.sleep(999)

        with patch.object(ch, "_connect_and_consume", failing_consume):
            with patch.object(event_mod, "aio_pika", MagicMock()):
                task = asyncio.create_task(ch.run(AsyncMock()))
                # Wait until second attempt
                for _ in range(50):
                    await asyncio.sleep(0)
                    if call_count >= 2:
                        break
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        assert call_count >= 2


# ---------------------------------------------------------------------------
# TaskDispatchChannel — TriggerPort protocol compliance
# ---------------------------------------------------------------------------


class TestTriggerPortCompliance:
    def test_satisfies_trigger_port(self) -> None:
        from ravn.ports.trigger import TriggerPort

        ch = TaskDispatchChannel(_config())
        assert isinstance(ch, TriggerPort)


# ---------------------------------------------------------------------------
# TaskDispatchChannel — _connect / _ensure_exchange / _invalidate paths
# ---------------------------------------------------------------------------


class TestConnectPub:
    @pytest.mark.asyncio
    async def test_connect_success_stores_exchange(self) -> None:
        """_connect sets _exchange when connection succeeds."""
        import os

        import ravn.adapters.channels._rabbitmq_base as base_mod

        ch = TaskDispatchChannel(_config(reconnect_delay_s=0.0))

        mock_exchange = MagicMock()
        mock_channel = AsyncMock()
        mock_channel.declare_exchange = AsyncMock(return_value=mock_exchange)
        mock_connection = AsyncMock()
        mock_connection.channel = AsyncMock(return_value=mock_channel)

        fake_pika = MagicMock()
        fake_pika.connect_robust = AsyncMock(return_value=mock_connection)
        fake_pika.ExchangeType.TOPIC = "topic"

        with patch.dict(os.environ, {"SLEIPNIR_AMQP_URL": "amqp://localhost"}):
            with patch.object(base_mod, "aio_pika", fake_pika):
                result = await ch._connect()

        assert result is mock_exchange
        assert ch._exchange is mock_exchange

    @pytest.mark.asyncio
    async def test_connect_failure_returns_none(self) -> None:
        """_connect returns None when connection raises."""
        import os

        import ravn.adapters.channels._rabbitmq_base as base_mod

        ch = TaskDispatchChannel(_config(reconnect_delay_s=0.0))

        fake_pika = MagicMock()
        fake_pika.connect_robust = AsyncMock(side_effect=ConnectionError("refused"))

        with patch.dict(os.environ, {"SLEIPNIR_AMQP_URL": "amqp://localhost"}):
            with patch.object(base_mod, "aio_pika", fake_pika):
                result = await ch._connect()

        assert result is None

    @pytest.mark.asyncio
    async def test_connect_no_aio_pika_returns_none(self) -> None:
        import ravn.adapters.channels._rabbitmq_base as base_mod

        ch = TaskDispatchChannel(_config(reconnect_delay_s=0.0))
        with patch.object(base_mod, "aio_pika", None):
            result = await ch._connect()
        assert result is None

    @pytest.mark.asyncio
    async def test_ensure_exchange_double_check_locking(self) -> None:
        """Second waiter in lock sees exchange already set."""
        ch = TaskDispatchChannel(_config(reconnect_delay_s=0.0))

        sentinel = MagicMock()

        # Pre-seed: exchange is None but gets set during the lock wait
        async def _acquire_then_check():
            async with ch._connect_lock:
                # Another coroutine already set it inside the lock
                ch._exchange = sentinel
            return await ch._ensure_exchange()

        result = await _acquire_then_check()
        assert result is sentinel

    @pytest.mark.asyncio
    async def test_invalidate_closes_connection(self) -> None:
        """_invalidate closes existing connection and clears all fields."""
        ch = TaskDispatchChannel(_config())

        mock_conn = AsyncMock()
        ch._connection = mock_conn
        ch._exchange = MagicMock()
        ch._channel = MagicMock()

        await ch._invalidate()

        assert ch._exchange is None
        assert ch._channel is None
        assert ch._connection is None
        mock_conn.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invalidate_swallows_close_error(self) -> None:
        """_invalidate does not raise if connection.close() fails."""
        ch = TaskDispatchChannel(_config())

        mock_conn = AsyncMock()
        mock_conn.close.side_effect = RuntimeError("already closed")
        ch._connection = mock_conn
        ch._exchange = MagicMock()

        # Must not raise
        await ch._invalidate()
        assert ch._connection is None


# ---------------------------------------------------------------------------
# TaskDispatchChannel — _connect_and_consume path
# ---------------------------------------------------------------------------


class TestConnectAndConsume:
    @pytest.mark.asyncio
    async def test_no_amqp_url_sleeps_and_returns(self) -> None:
        """_connect_and_consume returns early when AMQP URL is not set."""
        import os

        import ravn.adapters.channels.event as event_mod

        ch = TaskDispatchChannel(_config())
        enqueued: list = []

        async def _enqueue(task):
            enqueued.append(task)

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SLEIPNIR_AMQP_URL", None)
            with patch.object(event_mod, "aio_pika", MagicMock()):
                await ch._connect_and_consume(_enqueue)

        assert len(enqueued) == 0

    @pytest.mark.asyncio
    async def test_messages_are_consumed(self) -> None:
        """_connect_and_consume delivers messages via _handle_message."""
        import os

        import ravn.adapters.channels.event as event_mod

        loader = _fake_persona_loader(["autonomous-agent"])
        ch = TaskDispatchChannel(_config(reconnect_delay_s=0.0), persona_loader=loader)
        enqueued: list = []

        async def _enqueue(task):
            enqueued.append(task)

        # Build fake aio_pika infrastructure
        fake_body = _dispatch_payload()

        class FakeContextManager:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class FakeMessage:
            body = fake_body
            routing_key = "ravn.task.dispatch"

            def process(self):
                return FakeContextManager()

        class FakeQueueIter:
            def __init__(self):
                self._msgs = [FakeMessage()]
                self._idx = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._idx >= len(self._msgs):
                    raise StopAsyncIteration
                msg = self._msgs[self._idx]
                self._idx += 1
                return msg

        mock_queue = AsyncMock()
        mock_queue.iterator = MagicMock(return_value=FakeQueueIter())
        mock_queue.bind = AsyncMock()

        mock_channel = AsyncMock()
        mock_channel.declare_exchange = AsyncMock(return_value=MagicMock())
        mock_channel.declare_queue = AsyncMock(return_value=mock_queue)

        class FakeConnection:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def channel(self):
                return mock_channel

        fake_pika = MagicMock()
        fake_pika.connect_robust = AsyncMock(return_value=FakeConnection())
        fake_pika.ExchangeType.TOPIC = "topic"

        ch._publish_response = AsyncMock()  # type: ignore[method-assign]

        with patch.dict(os.environ, {"SLEIPNIR_AMQP_URL": "amqp://localhost"}):
            with patch.object(event_mod, "aio_pika", fake_pika):
                await ch._connect_and_consume(_enqueue)

        assert len(enqueued) == 1
        assert enqueued[0].task_id == "task-abc-123"
