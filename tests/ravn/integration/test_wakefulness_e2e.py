"""Integration tests for the full M2 wakefulness loop (NIU-572).

Exercises the complete Vaka acceptance scenario end-to-end:

  operator conversation
    → silence detected by WakefulnessTrigger
    → LLM reflection creates threads in Mímir (filesystem)
    → ThreadQueueTrigger claims & enqueues the highest-weight thread
    → DriveLoop executes agent task (MockLLM)
    → thread transitions to ``closed`` on success
    → operator returns → RecapTrigger fires with OutputMode.SURFACE
    → budget spend is recorded

Additional scenarios:
  * Budget gate: over-cap tasks are skipped (not dropped — re-tried after reset)
  * Trust gradient: resolve_trust_tools maps forbidden categories to tool blocklists
  * Empty reflection: LLM returns [] → no threads → no tasks → no recap

Uses:
- MarkdownMimirAdapter with pytest ``tmp_path`` (real filesystem)
- MockLLM (scripted, deterministic responses)
- InMemoryChannel (captures agent output for assertion)
- DailyBudgetTracker (real instance)
- Real trigger and DriveLoop instances
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mimir.adapters.markdown import MarkdownMimirAdapter
from niuu.domain.mimir import ThreadState
from ravn.adapters.triggers.recap import RecapTrigger
from ravn.adapters.triggers.thread_queue import ThreadQueueTrigger
from ravn.adapters.triggers.wakefulness import WakefulnessTrigger
from ravn.config import (
    InitiativeConfig,
    RecapConfig,
    Settings,
    ThreadConfig,
    TrustGradientConfig,
    WakefulnessConfig,
    resolve_trust_tools,
)
from ravn.domain.budget import DailyBudgetTracker
from ravn.domain.interaction_tracker import LastInteractionTracker
from ravn.domain.models import AgentTask, LLMResponse, OutputMode, StopReason, TokenUsage
from ravn.drive_loop import DriveLoop

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SILENCE_SECONDS = 100  # short threshold for fast tests
_ABSENCE_SECONDS = 200  # short absence threshold for fast tests
_RETURN_WINDOW = 50  # short return-detection window


def _llm_intents(intents: list[dict]) -> LLMResponse:
    """Build an LLMResponse containing a JSON intent array for WakefulnessTrigger."""
    return LLMResponse(
        content=json.dumps(intents),
        tool_calls=[],
        stop_reason=StopReason.END_TURN,
        usage=TokenUsage(input_tokens=10, output_tokens=20),
    )


def _make_intent(
    title: str = "Follow up on API design",
    next_action_hint: str = "Draft proposal",
) -> dict:
    return {
        "title": title,
        "why": "Unresolved question from conversation",
        "next_action_hint": next_action_hint,
        "budget_hint": "small",
        "surface_when": "on_return",
    }


def _wakefulness_config(
    silence_threshold_seconds: int = _SILENCE_SECONDS,
    initial_thread_weight: float = 5.0,
) -> WakefulnessConfig:
    return WakefulnessConfig(
        enabled=True,
        silence_threshold_seconds=silence_threshold_seconds,
        reflection_cooldown_seconds=9999,
        deep_reflection_threshold_seconds=99999,
        deep_reflection_cooldown_seconds=99999,
        llm_alias="fast",
        max_intents_per_reflection=5,
        initial_thread_weight=initial_thread_weight,
        poll_interval_seconds=60,
    )


def _thread_config() -> ThreadConfig:
    return ThreadConfig(enabled=True)


def _recap_config() -> RecapConfig:
    return RecapConfig(
        enabled=True,
        absence_threshold_seconds=_ABSENCE_SECONDS,
        return_detection_window_seconds=_RETURN_WINDOW,
        scheduled_recap_cron="",
        max_threads_in_recap=10,
        persona="produce-recap",
        poll_interval_seconds=60,
    )


def _make_drive_loop(tmp_path: Path, mimir: MarkdownMimirAdapter) -> tuple[DriveLoop, list[dict]]:
    """Return a DriveLoop wired to a recording agent factory."""
    calls: list[dict] = []

    def factory(ch, task_id, persona, triggered_by):
        calls.append({"task_id": task_id, "persona": persona, "triggered_by": triggered_by})
        agent = AsyncMock()
        agent.run_turn = AsyncMock(
            return_value=MagicMock(usage=TokenUsage(input_tokens=100, output_tokens=50))
        )
        return agent

    cfg = InitiativeConfig(
        enabled=True,
        max_concurrent_tasks=2,
        task_queue_max=20,
        queue_journal_path=str(tmp_path / "queue.json"),
    )
    settings = Settings()
    loop = DriveLoop(
        agent_factory=factory,
        config=cfg,
        settings=settings,
        mimir=mimir,
        budget=DailyBudgetTracker(daily_cap_usd=1.0),
    )
    return loop, calls


# ---------------------------------------------------------------------------
# Full wakefulness loop
# ---------------------------------------------------------------------------


class TestFullWakefulnessLoop:
    """End-to-end: silence → threads → tasks → closed → recap."""

    @pytest.mark.asyncio
    async def test_full_loop(self, tmp_path: Path) -> None:
        """Simulate the complete Vaka overnight scenario."""
        # 1. Set up components.
        mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")
        tracker = LastInteractionTracker()
        reflection_llm = AsyncMock()
        intents = [_make_intent(title="API design follow-up", next_action_hint="Draft note")]
        reflection_llm.generate = AsyncMock(return_value=_llm_intents(intents))

        wakefulness = WakefulnessTrigger(
            tracker=tracker,
            mimir=mimir,
            llm=reflection_llm,
            config=_wakefulness_config(),
            state_dir=tmp_path / "state",
        )

        # 2. Simulate operator conversation then silence.
        now = datetime(2026, 4, 11, 21, 0, 0, tzinfo=UTC)
        with patch("ravn.adapters.triggers.wakefulness.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.fromisoformat = datetime.fromisoformat
            tracker._last_interaction = now - timedelta(seconds=_SILENCE_SECONDS + 10)

            # 3. WakefulnessTrigger polls → reflection → threads created.
            await wakefulness._poll_once()

        reflection_llm.generate.assert_awaited_once()

        # 4. Verify the thread was created in Mímir.
        queue = await mimir.get_thread_queue()
        assert len(queue) == 1
        thread = queue[0]
        assert "api-design" in thread.meta.path or "follow-up" in thread.meta.path

        # 5. ThreadQueueTrigger picks up the thread and enqueues a task.
        enqueued: list[AgentTask] = []
        thread_trigger = ThreadQueueTrigger(mimir=mimir, config=_thread_config())
        await thread_trigger._poll_once(lambda t: enqueued.append(t) or asyncio.sleep(0))
        assert len(enqueued) == 1
        task = enqueued[0]
        assert task.triggered_by.startswith("thread:")
        assert task.output_mode == OutputMode.AMBIENT
        # "draft" keyword in next_action_hint → "draft-a-note" persona
        assert task.persona == "draft-a-note"

        # 6. Thread should now be in "pulling" state.
        thread_path = task.triggered_by.removeprefix("thread:")
        updated = await mimir.get_thread(thread_path)
        assert updated.meta.thread_state == ThreadState.pulling

        # 7. DriveLoop executes the task → thread transitions to closed.
        drive_loop, factory_calls = _make_drive_loop(tmp_path, mimir)
        await drive_loop._run_task(task)

        assert len(factory_calls) == 1
        assert factory_calls[0]["persona"] == "draft-a-note"

        # 8. Verify thread is now closed.
        closed = await mimir.get_thread(thread_path)
        assert closed.meta.thread_state == ThreadState.closed

        # 9. Simulate operator return after absence.
        recap_tasks: list[AgentTask] = []
        recap_trigger = RecapTrigger(
            mimir=mimir,
            config=_recap_config(),
            last_interaction=tracker.last,
            state_dir=tmp_path / "recap_state",
        )
        # Mark as was_away (conservative startup default).
        recap_trigger._was_away = True

        # Return: tracker was last touched > _ABSENCE_SECONDS ago, then returns now.
        morning = now + timedelta(hours=8)
        with patch("ravn.adapters.triggers.recap.datetime") as mock_dt2:
            mock_dt2.now.return_value = morning
            mock_dt2.fromisoformat = datetime.fromisoformat
            # Simulate a recent touch (within return window).
            tracker._last_interaction = morning - timedelta(seconds=10)

            await recap_trigger._poll_once(lambda t: recap_tasks.append(t) or asyncio.sleep(0))

        assert len(recap_tasks) == 1
        recap_task = recap_tasks[0]
        assert recap_task.output_mode == OutputMode.SURFACE
        assert recap_task.priority == 1
        assert recap_task.persona == "produce-recap"
        assert recap_task.triggered_by == "recap:return"

    @pytest.mark.asyncio
    async def test_budget_spend_recorded(self, tmp_path: Path) -> None:
        """DriveLoop records token cost to the budget tracker after a task."""
        mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")
        budget = DailyBudgetTracker(daily_cap_usd=5.0)

        def factory(ch, task_id, persona, triggered_by):
            agent = AsyncMock()
            agent.run_turn = AsyncMock(
                return_value=MagicMock(usage=TokenUsage(input_tokens=1_000_000, output_tokens=0))
            )
            return agent

        cfg = InitiativeConfig(
            enabled=True,
            max_concurrent_tasks=1,
            task_queue_max=10,
            queue_journal_path=str(tmp_path / "queue.json"),
        )
        settings = Settings()
        loop = DriveLoop(
            agent_factory=factory,
            config=cfg,
            settings=settings,
            mimir=mimir,
            budget=budget,
        )

        task = AgentTask(
            task_id="task_test_budget",
            title="budget test",
            initiative_context="test",
            triggered_by="cron:test",
            output_mode=OutputMode.SILENT,
        )
        assert budget.spent_today_usd == 0.0
        await loop._run_task(task)

        # 1M input tokens at $3/M = $3.00
        assert budget.spent_today_usd == pytest.approx(3.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Budget gate: over-cap tasks are skipped
# ---------------------------------------------------------------------------


class TestBudgetGate:
    """Tasks are skipped (not dropped) when the daily cap is reached."""

    @pytest.mark.asyncio
    async def test_over_cap_task_skipped(self, tmp_path: Path) -> None:
        """DriveLoop skips task execution when budget cap is already hit."""
        factory_calls: list[str] = []

        def factory(ch, task_id, persona, triggered_by):
            factory_calls.append(task_id)
            agent = AsyncMock()
            agent.run_turn = AsyncMock(return_value=MagicMock(usage=None))
            return agent

        cfg = InitiativeConfig(
            enabled=True,
            max_concurrent_tasks=1,
            task_queue_max=10,
            queue_journal_path=str(tmp_path / "queue.json"),
        )
        settings = Settings()
        # Budget already exhausted.
        exhausted_budget = DailyBudgetTracker(daily_cap_usd=0.001)
        exhausted_budget.record(0.001)  # push past cap

        loop = DriveLoop(
            agent_factory=factory,
            config=cfg,
            settings=settings,
            budget=exhausted_budget,
        )

        task = AgentTask(
            task_id="task_over_cap",
            title="blocked task",
            initiative_context="should not run",
            triggered_by="cron:test",
            output_mode=OutputMode.SILENT,
        )

        await loop._run_task(task)

        # Agent factory should never have been called.
        assert factory_calls == []
        # Task is not persisted as complete — budget tracker unchanged.
        assert not exhausted_budget.can_spend()

    @pytest.mark.asyncio
    async def test_within_cap_task_runs(self, tmp_path: Path) -> None:
        """DriveLoop executes task when budget has remaining capacity."""
        factory_calls: list[str] = []

        def factory(ch, task_id, persona, triggered_by):
            factory_calls.append(task_id)
            agent = AsyncMock()
            agent.run_turn = AsyncMock(return_value=MagicMock(usage=None))
            return agent

        cfg = InitiativeConfig(
            enabled=True,
            max_concurrent_tasks=1,
            task_queue_max=10,
            queue_journal_path=str(tmp_path / "queue.json"),
        )
        settings = Settings()
        loop = DriveLoop(
            agent_factory=factory,
            config=cfg,
            settings=settings,
            budget=DailyBudgetTracker(daily_cap_usd=1.0),
        )

        task = AgentTask(
            task_id="task_within_cap",
            title="allowed task",
            initiative_context="should run",
            triggered_by="cron:test",
            output_mode=OutputMode.SILENT,
        )

        await loop._run_task(task)

        assert factory_calls == ["task_within_cap"]


# ---------------------------------------------------------------------------
# Trust gradient
# ---------------------------------------------------------------------------


class TestTrustGradient:
    """TrustGradientConfig maps to correct allowed/forbidden tool sets."""

    def test_free_categories_produce_allowed_tools(self) -> None:
        """All-free config → reading tools are in the allowed list."""
        config = TrustGradientConfig(
            reading="free",
            writing_notes="never",
            sandbox_experiments="never",
            consulting_peers="never",
            drafting_tickets="never",
            producing_recaps="never",
            opening_tickets="never",
            closing_tickets="never",
            pushing_branches="never",
            pushing_main="never",
            external_messages="never",
            spending_beyond_cap="never",
        )
        allowed, forbidden = resolve_trust_tools(config)

        # Reading tools should be in allowed list.
        assert any("mimir" in t or "web" in t or "file" in t for t in allowed)
        # Writing tools should be in forbidden list.
        assert any("mimir_write" in t for t in forbidden)

    def test_never_level_tools_are_forbidden(self) -> None:
        """Categories set to 'never' produce forbidden entries."""
        config = TrustGradientConfig(
            reading="free",
            writing_notes="free",
            sandbox_experiments="never",  # bash/volundr/terminal forbidden
            consulting_peers="free",
            drafting_tickets="free",
            producing_recaps="free",
            opening_tickets="free",
            closing_tickets="free",
            pushing_branches="free",
            pushing_main="never",
            external_messages="free",
            spending_beyond_cap="free",
        )
        allowed, forbidden = resolve_trust_tools(config)

        assert "bash" in forbidden or any("bash" in t for t in forbidden)

    def test_approval_level_same_as_never_for_tools(self) -> None:
        """Both 'approval' and 'never' result in tools being in forbidden list."""
        config_never = TrustGradientConfig(opening_tickets="never")
        config_approval = TrustGradientConfig(opening_tickets="approval")

        _, forbidden_never = resolve_trust_tools(config_never)
        _, forbidden_approval = resolve_trust_tools(config_approval)

        # Same tool entries regardless of level (approval vs never).
        assert set(forbidden_never) == set(forbidden_approval)

    def test_default_config_matches_example_yaml(self) -> None:
        """Default TrustGradientConfig matches the values in examples/ravn.yaml."""
        config = TrustGradientConfig()

        assert config.reading == "free"
        assert config.writing_notes == "free"
        assert config.sandbox_experiments == "free"
        assert config.consulting_peers == "free"
        assert config.drafting_tickets == "free"
        assert config.producing_recaps == "free"
        assert config.opening_tickets == "approval"
        assert config.closing_tickets == "approval"
        assert config.pushing_branches == "approval"
        assert config.pushing_main == "never"
        assert config.external_messages == "approval"
        assert config.spending_beyond_cap == "approval"


# ---------------------------------------------------------------------------
# Empty reflection — no threads, no work, no recap
# ---------------------------------------------------------------------------


class TestEmptyReflection:
    """LLM returns [] → no threads created → no tasks enqueued → no recap."""

    @pytest.mark.asyncio
    async def test_empty_reflection_no_threads(self, tmp_path: Path) -> None:
        """WakefulnessTrigger with empty LLM result creates no threads."""
        mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")
        tracker = LastInteractionTracker()
        reflection_llm = AsyncMock()
        reflection_llm.generate = AsyncMock(return_value=_llm_intents([]))

        wakefulness = WakefulnessTrigger(
            tracker=tracker,
            mimir=mimir,
            llm=reflection_llm,
            config=_wakefulness_config(),
            state_dir=tmp_path / "state",
        )

        now = datetime(2026, 4, 11, 21, 0, 0, tzinfo=UTC)
        with patch("ravn.adapters.triggers.wakefulness.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.fromisoformat = datetime.fromisoformat
            tracker._last_interaction = now - timedelta(seconds=_SILENCE_SECONDS + 10)

            await wakefulness._poll_once()

        queue = await mimir.get_thread_queue()
        assert queue == []

    @pytest.mark.asyncio
    async def test_empty_queue_no_tasks(self, tmp_path: Path) -> None:
        """ThreadQueueTrigger with no open threads enqueues nothing."""
        mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")
        enqueued: list[AgentTask] = []

        thread_trigger = ThreadQueueTrigger(mimir=mimir, config=_thread_config())
        await thread_trigger._poll_once(lambda t: enqueued.append(t) or asyncio.sleep(0))

        assert enqueued == []

    @pytest.mark.asyncio
    async def test_no_closed_threads_no_recap(self, tmp_path: Path) -> None:
        """RecapTrigger skips recap when no closed threads exist since last recap."""
        mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")
        tracker = LastInteractionTracker()
        recap_tasks: list[AgentTask] = []

        recap_trigger = RecapTrigger(
            mimir=mimir,
            config=_recap_config(),
            last_interaction=tracker.last,
            state_dir=tmp_path / "recap_state",
        )
        recap_trigger._was_away = True

        now = datetime(2026, 4, 11, 7, 0, 0, tzinfo=UTC)
        with patch("ravn.adapters.triggers.recap.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.fromisoformat = datetime.fromisoformat
            # Simulate recent return.
            tracker._last_interaction = now - timedelta(seconds=10)

            await recap_trigger._poll_once(lambda t: recap_tasks.append(t) or asyncio.sleep(0))

        # No closed threads → no recap enqueued.
        assert recap_tasks == []


# ---------------------------------------------------------------------------
# Thread state transitions via DriveLoop
# ---------------------------------------------------------------------------


class TestThreadStateTransitions:
    """DriveLoop finalises thread state correctly on success and failure."""

    @pytest.mark.asyncio
    async def test_success_closes_thread(self, tmp_path: Path) -> None:
        """Successful task transitions thread from pulling → closed."""
        mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")

        # Create a thread and put it in pulling state.
        page = await mimir.create_thread(
            title="Research caching strategies",
            weight=5.0,
            next_action_hint="read sources",
        )
        path = page.meta.path
        await mimir.update_thread_state(path, ThreadState.pulling)

        def factory(ch, task_id, persona, triggered_by):
            agent = AsyncMock()
            agent.run_turn = AsyncMock(return_value=MagicMock(usage=None))
            return agent

        cfg = InitiativeConfig(
            enabled=True,
            max_concurrent_tasks=1,
            task_queue_max=10,
            queue_journal_path=str(tmp_path / "queue.json"),
        )
        loop = DriveLoop(
            agent_factory=factory,
            config=cfg,
            settings=Settings(),
            mimir=mimir,
        )

        task = AgentTask(
            task_id="task_success",
            title="research task",
            initiative_context="research caching",
            triggered_by=f"thread:{path}",
            output_mode=OutputMode.AMBIENT,
        )
        await loop._run_task(task)

        result = await mimir.get_thread(path)
        assert result.meta.thread_state == ThreadState.closed

    @pytest.mark.asyncio
    async def test_failure_reopens_thread(self, tmp_path: Path) -> None:
        """Failed task transitions thread from pulling → open, clears owner."""
        mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")

        page = await mimir.create_thread(
            title="Failed research task",
            weight=3.0,
            next_action_hint="analyse sources",
        )
        path = page.meta.path
        await mimir.update_thread_state(path, ThreadState.pulling)

        def factory(ch, task_id, persona, triggered_by):
            agent = AsyncMock()
            agent.run_turn = AsyncMock(side_effect=RuntimeError("agent failed"))
            return agent

        cfg = InitiativeConfig(
            enabled=True,
            max_concurrent_tasks=1,
            task_queue_max=10,
            queue_journal_path=str(tmp_path / "queue.json"),
        )
        loop = DriveLoop(
            agent_factory=factory,
            config=cfg,
            settings=Settings(),
            mimir=mimir,
        )

        task = AgentTask(
            task_id="task_failure",
            title="failing research",
            initiative_context="should fail",
            triggered_by=f"thread:{path}",
            output_mode=OutputMode.AMBIENT,
        )
        await loop._run_task(task)

        result = await mimir.get_thread(path)
        assert result.meta.thread_state == ThreadState.open


# ---------------------------------------------------------------------------
# Persona selection in ThreadQueueTrigger
# ---------------------------------------------------------------------------


class TestPersonaSelection:
    """ThreadQueueTrigger selects persona from next_action_hint keywords."""

    @pytest.mark.asyncio
    async def test_draft_keyword_selects_draft_persona(self, tmp_path: Path) -> None:
        """'draft' keyword in hint → draft-a-note persona."""
        mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")
        await mimir.create_thread(
            title="Draft meeting notes",
            weight=5.0,
            next_action_hint="draft a quick note",
        )

        enqueued: list[AgentTask] = []
        trigger = ThreadQueueTrigger(mimir=mimir, config=_thread_config())
        await trigger._poll_once(lambda t: enqueued.append(t) or asyncio.sleep(0))

        assert len(enqueued) == 1
        assert enqueued[0].persona == "draft-a-note"

    @pytest.mark.asyncio
    async def test_research_hint_selects_default_persona(self, tmp_path: Path) -> None:
        """Non-matching hint → research-and-distill persona (default)."""
        mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")
        await mimir.create_thread(
            title="API comparison",
            weight=5.0,
            next_action_hint="research competing solutions",
        )

        enqueued: list[AgentTask] = []
        trigger = ThreadQueueTrigger(mimir=mimir, config=_thread_config())
        await trigger._poll_once(lambda t: enqueued.append(t) or asyncio.sleep(0))

        assert len(enqueued) == 1
        assert enqueued[0].persona == "research-and-distill"
