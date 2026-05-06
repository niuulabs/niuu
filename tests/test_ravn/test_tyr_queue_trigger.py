"""Unit + integration tests for TyrQueueTrigger (NIU-618).

Covers:
1. Trigger polling: mock Tyr API responses, verify ready issues → AgentTasks
2. Dispatcher state respect: running=False/auto_continue=False/max_concurrent → no enqueue
3. Deduplication: same raid returned twice → only one AgentTask enqueued
4. Integration: DriveLoop wiring via InProcessBus pattern
5. Dynamic adapter loading: verifies trigger loads from config YAML pattern
"""

from __future__ import annotations

import asyncio
import importlib
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from ravn.adapters.triggers.tyr_queue import (
    _DISPATCHER_PATH,
    _QUEUE_PATH,
    TyrQueueTrigger,
    _build_initiative_context,
)
from ravn.domain.models import AgentTask, OutputMode

# ---------------------------------------------------------------------------
# Constants / fixtures
# ---------------------------------------------------------------------------

_BASE_URL = "http://tyr:8080"
_TOKEN = "test-pat-token"


def _make_trigger(
    base_url: str = _BASE_URL,
    poll_interval_s: float = 30.0,
    pat_token: str = _TOKEN,
) -> TyrQueueTrigger:
    return TyrQueueTrigger(
        tyr_base_url=base_url,
        poll_interval_s=poll_interval_s,
        pat_token=pat_token,
    )


def _dispatcher_state(
    running: bool = True,
    auto_continue: bool = True,
    max_concurrent_raids: int = 3,
) -> dict:
    return {
        "id": "disp-1",
        "running": running,
        "auto_continue": auto_continue,
        "max_concurrent_raids": max_concurrent_raids,
        "threshold": 0.7,
        "updated_at": "2026-04-16T00:00:00Z",
    }


def _queue_item(
    issue_id: str = "issue-abc-123",
    identifier: str = "NIU-618",
    title: str = "Add TyrQueueTrigger",
    description: str = "Implement poll-based trigger for raid dispatch.",
    saga_id: str = "saga-1",
    saga_name: str = "Vaka",
    repos: list[str] | None = None,
    feature_branch: str = "feat/vaka-ravn-wakefulness",
    phase_name: str = "Phase 1",
) -> dict:
    return {
        "saga_id": saga_id,
        "saga_name": saga_name,
        "saga_slug": saga_name.lower(),
        "repos": repos or ["volundr"],
        "feature_branch": feature_branch,
        "phase_name": phase_name,
        "issue_id": issue_id,
        "identifier": identifier,
        "title": title,
        "description": description,
        "status": "ready",
        "priority": 2,
        "priority_label": "Urgent",
        "estimate": 3.0,
        "url": "https://linear.app/niuu/issue/NIU-618",
    }


async def _collect_enqueued(
    trigger: TyrQueueTrigger,
    items: list[dict] | None = None,
    dispatcher: dict | None = None,
) -> list[AgentTask]:
    """Run _poll_once with mocked Tyr endpoints, collect enqueued tasks."""
    items = items if items is not None else []
    dispatcher = dispatcher if dispatcher is not None else _dispatcher_state()

    enqueued: list[AgentTask] = []

    async def _enqueue(task: AgentTask) -> None:
        enqueued.append(task)

    with respx.mock:
        respx.get(f"{_BASE_URL}{_DISPATCHER_PATH}").mock(
            return_value=httpx.Response(200, json=dispatcher)
        )
        respx.get(f"{_BASE_URL}{_QUEUE_PATH}").mock(return_value=httpx.Response(200, json=items))
        await trigger._poll_once(_enqueue)

    return enqueued


# ---------------------------------------------------------------------------
# 1. Trigger polling — happy path
# ---------------------------------------------------------------------------


class TestTriggerPolling:
    @pytest.mark.asyncio
    async def test_ready_issue_enqueued(self) -> None:
        trigger = _make_trigger()
        items = [_queue_item()]
        enqueued = await _collect_enqueued(trigger, items=items)
        assert len(enqueued) == 1

    @pytest.mark.asyncio
    async def test_task_id_contains_identifier(self) -> None:
        trigger = _make_trigger()
        items = [_queue_item(identifier="NIU-618")]
        enqueued = await _collect_enqueued(trigger, items=items)
        assert "NIU-618" in enqueued[0].task_id

    @pytest.mark.asyncio
    async def test_task_id_prefixed_tyr_raid(self) -> None:
        trigger = _make_trigger()
        items = [_queue_item(identifier="NIU-618")]
        enqueued = await _collect_enqueued(trigger, items=items)
        assert enqueued[0].task_id.startswith("tyr_raid_")

    @pytest.mark.asyncio
    async def test_title_from_queue_item(self) -> None:
        trigger = _make_trigger()
        items = [_queue_item(title="Add TyrQueueTrigger")]
        enqueued = await _collect_enqueued(trigger, items=items)
        assert enqueued[0].title == "Add TyrQueueTrigger"

    @pytest.mark.asyncio
    async def test_output_mode_is_ambient(self) -> None:
        trigger = _make_trigger()
        items = [_queue_item()]
        enqueued = await _collect_enqueued(trigger, items=items)
        assert enqueued[0].output_mode == OutputMode.AMBIENT

    @pytest.mark.asyncio
    async def test_persona_is_raid_executor(self) -> None:
        trigger = _make_trigger()
        items = [_queue_item()]
        enqueued = await _collect_enqueued(trigger, items=items)
        assert enqueued[0].persona == "raid-executor"

    @pytest.mark.asyncio
    async def test_triggered_by_contains_saga_name(self) -> None:
        trigger = _make_trigger()
        items = [_queue_item(saga_name="Vaka")]
        enqueued = await _collect_enqueued(trigger, items=items)
        assert enqueued[0].triggered_by == "tyr_queue:Vaka"

    @pytest.mark.asyncio
    async def test_initiative_context_contains_identifier(self) -> None:
        trigger = _make_trigger()
        items = [_queue_item(identifier="NIU-618")]
        enqueued = await _collect_enqueued(trigger, items=items)
        assert "NIU-618" in enqueued[0].initiative_context

    @pytest.mark.asyncio
    async def test_initiative_context_contains_title(self) -> None:
        trigger = _make_trigger()
        items = [_queue_item(title="Add TyrQueueTrigger")]
        enqueued = await _collect_enqueued(trigger, items=items)
        assert "Add TyrQueueTrigger" in enqueued[0].initiative_context

    @pytest.mark.asyncio
    async def test_initiative_context_contains_description(self) -> None:
        trigger = _make_trigger()
        items = [_queue_item(description="Implement poll-based trigger.")]
        enqueued = await _collect_enqueued(trigger, items=items)
        assert "Implement poll-based trigger." in enqueued[0].initiative_context

    @pytest.mark.asyncio
    async def test_initiative_context_contains_feature_branch(self) -> None:
        trigger = _make_trigger()
        items = [_queue_item(feature_branch="feat/my-branch")]
        enqueued = await _collect_enqueued(trigger, items=items)
        assert "feat/my-branch" in enqueued[0].initiative_context

    @pytest.mark.asyncio
    async def test_empty_queue_no_enqueue(self) -> None:
        trigger = _make_trigger()
        enqueued = await _collect_enqueued(trigger, items=[])
        assert enqueued == []

    @pytest.mark.asyncio
    async def test_multiple_items_all_enqueued_within_capacity(self) -> None:
        trigger = _make_trigger()
        items = [
            _queue_item(issue_id="id-1", identifier="NIU-100"),
            _queue_item(issue_id="id-2", identifier="NIU-101"),
        ]
        dispatcher = _dispatcher_state(max_concurrent_raids=5)
        enqueued = await _collect_enqueued(trigger, items=items, dispatcher=dispatcher)
        assert len(enqueued) == 2

    @pytest.mark.asyncio
    async def test_auth_header_sent(self) -> None:
        trigger = _make_trigger(pat_token="my-secret-token")

        async def _enqueue(task: AgentTask) -> None:
            pass

        with respx.mock as mock:
            dispatcher_route = mock.get(f"{_BASE_URL}{_DISPATCHER_PATH}").mock(
                return_value=httpx.Response(200, json=_dispatcher_state())
            )
            mock.get(f"{_BASE_URL}{_QUEUE_PATH}").mock(return_value=httpx.Response(200, json=[]))
            await trigger._poll_once(_enqueue)

        request = dispatcher_route.calls[0].request
        assert request.headers["Authorization"] == "Bearer my-secret-token"


# ---------------------------------------------------------------------------
# 2. Dispatcher state respect
# ---------------------------------------------------------------------------


class TestDispatcherStateRespect:
    @pytest.mark.asyncio
    async def test_running_false_no_enqueue(self) -> None:
        trigger = _make_trigger()
        items = [_queue_item()]
        dispatcher = _dispatcher_state(running=False)
        enqueued = await _collect_enqueued(trigger, items=items, dispatcher=dispatcher)
        assert enqueued == []

    @pytest.mark.asyncio
    async def test_running_false_queue_not_fetched(self) -> None:
        trigger = _make_trigger()

        async def _enqueue(task: AgentTask) -> None:
            pass

        with respx.mock as mock:
            mock.get(f"{_BASE_URL}{_DISPATCHER_PATH}").mock(
                return_value=httpx.Response(200, json=_dispatcher_state(running=False))
            )
            queue_route = mock.get(f"{_BASE_URL}{_QUEUE_PATH}").mock(
                return_value=httpx.Response(200, json=[_queue_item()])
            )
            await trigger._poll_once(_enqueue)

        assert len(queue_route.calls) == 0

    @pytest.mark.asyncio
    async def test_auto_continue_false_no_enqueue(self) -> None:
        trigger = _make_trigger()
        items = [_queue_item()]
        dispatcher = _dispatcher_state(auto_continue=False)
        enqueued = await _collect_enqueued(trigger, items=items, dispatcher=dispatcher)
        assert enqueued == []

    @pytest.mark.asyncio
    async def test_auto_continue_false_queue_not_fetched(self) -> None:
        trigger = _make_trigger()

        async def _enqueue(task: AgentTask) -> None:
            pass

        with respx.mock as mock:
            mock.get(f"{_BASE_URL}{_DISPATCHER_PATH}").mock(
                return_value=httpx.Response(200, json=_dispatcher_state(auto_continue=False))
            )
            queue_route = mock.get(f"{_BASE_URL}{_QUEUE_PATH}").mock(
                return_value=httpx.Response(200, json=[_queue_item()])
            )
            await trigger._poll_once(_enqueue)

        assert len(queue_route.calls) == 0

    @pytest.mark.asyncio
    async def test_max_concurrent_reached_no_enqueue(self) -> None:
        """When in-flight raids still appear in queue, no new raids are enqueued."""
        trigger = _make_trigger()
        # Pre-fill enqueued_ids with IDs that still appear in the queue (in-flight)
        trigger._enqueued_ids = {"id-a", "id-b", "id-c"}
        items = [
            _queue_item(issue_id="id-a"),
            _queue_item(issue_id="id-b"),
            _queue_item(issue_id="id-c"),
            _queue_item(issue_id="id-new", identifier="NIU-999"),
        ]
        dispatcher = _dispatcher_state(max_concurrent_raids=3)
        enqueued = await _collect_enqueued(trigger, items=items, dispatcher=dispatcher)
        assert enqueued == []

    @pytest.mark.asyncio
    async def test_one_below_max_enqueues_one(self) -> None:
        """With one slot free, exactly one new raid is enqueued."""
        trigger = _make_trigger()
        trigger._enqueued_ids = {"id-a", "id-b"}  # 2 of 3 still in queue
        items = [
            _queue_item(issue_id="id-a"),
            _queue_item(issue_id="id-b"),
            _queue_item(issue_id="id-new", identifier="NIU-999"),
        ]
        dispatcher = _dispatcher_state(max_concurrent_raids=3)
        enqueued = await _collect_enqueued(trigger, items=items, dispatcher=dispatcher)
        assert len(enqueued) == 1

    @pytest.mark.asyncio
    async def test_dispatcher_http_error_no_enqueue(self) -> None:
        trigger = _make_trigger()

        async def _enqueue(task: AgentTask) -> None:
            pass

        with respx.mock:
            respx.get(f"{_BASE_URL}{_DISPATCHER_PATH}").mock(return_value=httpx.Response(503))
            respx.get(f"{_BASE_URL}{_QUEUE_PATH}").mock(
                return_value=httpx.Response(200, json=[_queue_item()])
            )
            await trigger._poll_once(_enqueue)  # must not raise

    @pytest.mark.asyncio
    async def test_queue_http_error_no_enqueue(self) -> None:
        trigger = _make_trigger()
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        with respx.mock:
            respx.get(f"{_BASE_URL}{_DISPATCHER_PATH}").mock(
                return_value=httpx.Response(200, json=_dispatcher_state())
            )
            respx.get(f"{_BASE_URL}{_QUEUE_PATH}").mock(return_value=httpx.Response(500))
            await trigger._poll_once(_enqueue)

        assert enqueued == []


# ---------------------------------------------------------------------------
# 3. Deduplication
# ---------------------------------------------------------------------------


class TestDeduplication:
    @pytest.mark.asyncio
    async def test_same_raid_enqueued_once_across_polls(self) -> None:
        trigger = _make_trigger()
        item = _queue_item(issue_id="id-1", identifier="NIU-618")
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        # First poll: item appears, should be enqueued
        with respx.mock:
            respx.get(f"{_BASE_URL}{_DISPATCHER_PATH}").mock(
                return_value=httpx.Response(200, json=_dispatcher_state())
            )
            respx.get(f"{_BASE_URL}{_QUEUE_PATH}").mock(
                return_value=httpx.Response(200, json=[item])
            )
            await trigger._poll_once(_enqueue)

        assert len(enqueued) == 1

        # Second poll: same item still in queue → NOT re-enqueued
        with respx.mock:
            respx.get(f"{_BASE_URL}{_DISPATCHER_PATH}").mock(
                return_value=httpx.Response(200, json=_dispatcher_state())
            )
            respx.get(f"{_BASE_URL}{_QUEUE_PATH}").mock(
                return_value=httpx.Response(200, json=[item])
            )
            await trigger._poll_once(_enqueue)

        assert len(enqueued) == 1  # still 1 — not re-enqueued

    @pytest.mark.asyncio
    async def test_completed_raid_cleared_from_tracking(self) -> None:
        """When a raid leaves the queue (dispatched), its ID is removed from tracking."""
        trigger = _make_trigger()
        item = _queue_item(issue_id="id-completed")
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        # First poll: item enqueued
        with respx.mock:
            respx.get(f"{_BASE_URL}{_DISPATCHER_PATH}").mock(
                return_value=httpx.Response(200, json=_dispatcher_state())
            )
            respx.get(f"{_BASE_URL}{_QUEUE_PATH}").mock(
                return_value=httpx.Response(200, json=[item])
            )
            await trigger._poll_once(_enqueue)

        assert "id-completed" in trigger._enqueued_ids

        # Second poll: item gone from queue (completed) → cleared from tracking
        with respx.mock:
            respx.get(f"{_BASE_URL}{_DISPATCHER_PATH}").mock(
                return_value=httpx.Response(200, json=_dispatcher_state())
            )
            respx.get(f"{_BASE_URL}{_QUEUE_PATH}").mock(
                return_value=httpx.Response(200, json=[])  # empty queue
            )
            await trigger._poll_once(_enqueue)

        assert "id-completed" not in trigger._enqueued_ids

    @pytest.mark.asyncio
    async def test_new_raid_enqueued_after_previous_completes(self) -> None:
        """After first raid leaves the queue a new raid can be enqueued."""
        trigger = _make_trigger()
        item_first = _queue_item(issue_id="id-first", identifier="NIU-100")
        item_second = _queue_item(issue_id="id-second", identifier="NIU-200")
        dispatcher = _dispatcher_state(max_concurrent_raids=1)
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        # First poll: first item enqueued, max_concurrent=1 reached
        with respx.mock:
            respx.get(f"{_BASE_URL}{_DISPATCHER_PATH}").mock(
                return_value=httpx.Response(200, json=dispatcher)
            )
            respx.get(f"{_BASE_URL}{_QUEUE_PATH}").mock(
                return_value=httpx.Response(200, json=[item_first])
            )
            await trigger._poll_once(_enqueue)

        assert len(enqueued) == 1

        # Second poll: first item gone, second item in queue
        with respx.mock:
            respx.get(f"{_BASE_URL}{_DISPATCHER_PATH}").mock(
                return_value=httpx.Response(200, json=dispatcher)
            )
            respx.get(f"{_BASE_URL}{_QUEUE_PATH}").mock(
                return_value=httpx.Response(200, json=[item_second])
            )
            await trigger._poll_once(_enqueue)

        assert len(enqueued) == 2
        assert enqueued[1].task_id.startswith("tyr_raid_NIU-200")

    @pytest.mark.asyncio
    async def test_capacity_limits_enqueue_count(self) -> None:
        """Only up to max_concurrent_raids items enqueued per poll."""
        trigger = _make_trigger()
        items = [_queue_item(issue_id=f"id-{i}", identifier=f"NIU-{i}") for i in range(5)]
        dispatcher = _dispatcher_state(max_concurrent_raids=2)
        enqueued = await _collect_enqueued(trigger, items=items, dispatcher=dispatcher)
        assert len(enqueued) == 2


# ---------------------------------------------------------------------------
# 4. Integration: DriveLoop wiring
# ---------------------------------------------------------------------------


class TestDriveLoopIntegration:
    @pytest.mark.asyncio
    async def test_tasks_appear_in_queue_when_tyr_returns_ready_items(self) -> None:
        """Trigger delivers tasks to the enqueue callback (simulating DriveLoop)."""
        trigger = _make_trigger()
        items = [
            _queue_item(issue_id="id-1", identifier="NIU-618"),
        ]
        collected: list[AgentTask] = []

        async def mock_enqueue(task: AgentTask) -> None:
            collected.append(task)

        with respx.mock:
            respx.get(f"{_BASE_URL}{_DISPATCHER_PATH}").mock(
                return_value=httpx.Response(200, json=_dispatcher_state())
            )
            respx.get(f"{_BASE_URL}{_QUEUE_PATH}").mock(
                return_value=httpx.Response(200, json=items)
            )
            await trigger._poll_once(mock_enqueue)

        assert len(collected) == 1
        assert collected[0].persona == "raid-executor"
        assert collected[0].output_mode == OutputMode.AMBIENT

    @pytest.mark.asyncio
    async def test_run_polls_on_interval_then_cancels(self) -> None:
        """run() sleeps then calls _poll_once; cancellation is clean."""
        trigger = _make_trigger(poll_interval_s=0.01)

        calls: list[int] = []

        async def fake_poll_once(enqueue):
            calls.append(1)

        enqueue_mock = AsyncMock()

        with patch.object(trigger, "_poll_once", side_effect=fake_poll_once):
            task = asyncio.create_task(trigger.run(enqueue_mock))
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert len(calls) >= 1

    @pytest.mark.asyncio
    async def test_run_survives_poll_exception(self) -> None:
        """run() continues despite errors in _poll_once."""
        trigger = _make_trigger(poll_interval_s=0.01)
        call_count = 0

        async def failing_poll(enqueue):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("transient error")

        enqueue_mock = AsyncMock()

        with patch.object(trigger, "_poll_once", side_effect=failing_poll):
            task = asyncio.create_task(trigger.run(enqueue_mock))
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert call_count >= 3


# ---------------------------------------------------------------------------
# 5. Dynamic adapter loading
# ---------------------------------------------------------------------------


class TestDynamicAdapterLoading:
    def test_trigger_importable_via_dotted_path(self) -> None:
        """TyrQueueTrigger can be imported from the config-specified dotted path."""
        module = importlib.import_module("ravn.adapters.triggers.tyr_queue")
        cls = getattr(module, "TyrQueueTrigger")
        assert cls is TyrQueueTrigger

    def test_trigger_instantiable_from_kwargs(self) -> None:
        """TyrQueueTrigger can be instantiated from plain kwargs (as config wiring does)."""
        kwargs = {
            "tyr_base_url": "http://tyr:8080",
            "poll_interval_s": 30.0,
            "pat_token": "secret",
        }
        trigger = TyrQueueTrigger(**kwargs)
        assert trigger.name == "tyr_queue"
        assert trigger._tyr_base_url == "http://tyr:8080"
        assert trigger._poll_interval_s == 30.0

    def test_trailing_slash_stripped_from_base_url(self) -> None:
        trigger = TyrQueueTrigger(
            tyr_base_url="http://tyr:8080/",
            poll_interval_s=30.0,
            pat_token="token",
        )
        assert not trigger._tyr_base_url.endswith("/")

    def test_implements_trigger_port(self) -> None:
        from ravn.ports.trigger import TriggerPort

        trigger = _make_trigger()
        assert isinstance(trigger, TriggerPort)

    def test_name_property(self) -> None:
        trigger = _make_trigger()
        assert trigger.name == "tyr_queue"


# ---------------------------------------------------------------------------
# _build_initiative_context
# ---------------------------------------------------------------------------


class TestNetworkErrors:
    @pytest.mark.asyncio
    async def test_dispatcher_network_error_no_enqueue(self) -> None:
        """Network-level errors on dispatcher fetch are handled gracefully."""
        trigger = _make_trigger()
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        with respx.mock:
            respx.get(f"{_BASE_URL}{_DISPATCHER_PATH}").mock(
                side_effect=httpx.ConnectError("connection refused")
            )
            respx.get(f"{_BASE_URL}{_QUEUE_PATH}").mock(
                return_value=httpx.Response(200, json=[_queue_item()])
            )
            await trigger._poll_once(_enqueue)  # must not raise

        assert enqueued == []

    @pytest.mark.asyncio
    async def test_queue_network_error_no_enqueue(self) -> None:
        """Network-level errors on queue fetch are handled gracefully."""
        trigger = _make_trigger()
        enqueued: list[AgentTask] = []

        async def _enqueue(task: AgentTask) -> None:
            enqueued.append(task)

        with respx.mock:
            respx.get(f"{_BASE_URL}{_DISPATCHER_PATH}").mock(
                return_value=httpx.Response(200, json=_dispatcher_state())
            )
            respx.get(f"{_BASE_URL}{_QUEUE_PATH}").mock(
                side_effect=httpx.ConnectError("connection refused")
            )
            await trigger._poll_once(_enqueue)  # must not raise

        assert enqueued == []


class TestBuildInitiativeContext:
    def test_contains_identifier_and_title(self) -> None:
        item = _queue_item(identifier="NIU-618", title="My Title")
        ctx = _build_initiative_context(item)
        assert "NIU-618" in ctx
        assert "My Title" in ctx

    def test_contains_description(self) -> None:
        item = _queue_item(description="Do the thing.")
        ctx = _build_initiative_context(item)
        assert "Do the thing." in ctx

    def test_contains_saga_name(self) -> None:
        item = _queue_item(saga_name="Vaka")
        ctx = _build_initiative_context(item)
        assert "Vaka" in ctx

    def test_contains_feature_branch(self) -> None:
        item = _queue_item(feature_branch="feat/my-branch")
        ctx = _build_initiative_context(item)
        assert "feat/my-branch" in ctx

    def test_contains_repos(self) -> None:
        item = _queue_item(repos=["volundr", "tyr"])
        ctx = _build_initiative_context(item)
        assert "volundr" in ctx
        assert "tyr" in ctx

    def test_empty_description_omitted(self) -> None:
        item = _queue_item(description="")
        ctx = _build_initiative_context(item)
        assert "## Description" not in ctx

    def test_instructions_always_present(self) -> None:
        item = _queue_item()
        ctx = _build_initiative_context(item)
        assert "## Instructions" in ctx
