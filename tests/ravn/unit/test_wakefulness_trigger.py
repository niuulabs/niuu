"""Unit tests for WakefulnessTrigger (NIU-565).

Covers:
- Disabled trigger exits immediately
- Silence below threshold → no reflection
- Silence above threshold → LLM called, threads created in Mímir
- Cooldown respected → second call within cooldown is no-op
- LLM returns empty array → no threads created
- LLM returns malformed JSON → logged, no crash
- max_intents_per_reflection cap respected
- Deep reflection fires after longer silence
- Deep reflection cooldown respected
- State persisted and restored across restarts
- Intent without title is skipped
- create_thread failure is logged, not raised
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from ravn.adapters.triggers.wakefulness import WakefulnessTrigger
from ravn.config import WakefulnessConfig
from ravn.domain.interaction_tracker import LastInteractionTracker
from ravn.domain.models import LLMResponse, StopReason, TokenUsage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(
    enabled: bool = True,
    silence_threshold_seconds: int = 1800,
    reflection_cooldown_seconds: int = 3600,
    deep_reflection_threshold_seconds: int = 7200,
    deep_reflection_cooldown_seconds: int = 14400,
    max_intents_per_reflection: int = 5,
    initial_thread_weight: float = 5.0,
    poll_interval_seconds: int = 60,
) -> WakefulnessConfig:
    return WakefulnessConfig(
        enabled=enabled,
        silence_threshold_seconds=silence_threshold_seconds,
        reflection_cooldown_seconds=reflection_cooldown_seconds,
        deep_reflection_threshold_seconds=deep_reflection_threshold_seconds,
        deep_reflection_cooldown_seconds=deep_reflection_cooldown_seconds,
        max_intents_per_reflection=max_intents_per_reflection,
        initial_thread_weight=initial_thread_weight,
        poll_interval_seconds=poll_interval_seconds,
    )


def _llm_response(intents: list[dict] | str) -> LLMResponse:
    content = intents if isinstance(intents, str) else json.dumps(intents)
    return LLMResponse(
        content=content,
        tool_calls=[],
        stop_reason=StopReason.END_TURN,
        usage=TokenUsage(input_tokens=10, output_tokens=20),
    )


def _intent(
    title: str = "Follow up on API design",
    why: str = "Unresolved question",
    next_action_hint: str = "Draft proposal",
    budget_hint: str = "small",
    surface_when: str = "on_return",
) -> dict:
    return {
        "title": title,
        "why": why,
        "next_action_hint": next_action_hint,
        "budget_hint": budget_hint,
        "surface_when": surface_when,
    }


def _make_trigger(
    config: WakefulnessConfig | None = None,
    tracker: LastInteractionTracker | None = None,
    mimir: AsyncMock | None = None,
    llm: AsyncMock | None = None,
    state_dir: Path | None = None,
) -> WakefulnessTrigger:
    return WakefulnessTrigger(
        tracker=tracker or LastInteractionTracker(),
        mimir=mimir or AsyncMock(),
        llm=llm or AsyncMock(),
        config=config or _config(),
        state_dir=state_dir,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDisabled:
    """WakefulnessTrigger should exit immediately when disabled."""

    @pytest.mark.asyncio
    async def test_disabled_exits_immediately(self) -> None:
        trigger = _make_trigger(config=_config(enabled=False))
        enqueue = AsyncMock()

        # run() should return without blocking
        await trigger.run(enqueue)
        enqueue.assert_not_awaited()


class TestSilenceDetection:
    """Silence threshold and cooldown logic."""

    @pytest.mark.asyncio
    async def test_no_interaction_no_reflection(self) -> None:
        """No interaction recorded → tracker.last() is None → no reflection."""
        llm = AsyncMock()
        trigger = _make_trigger(llm=llm)

        await trigger._poll_once()

        llm.generate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_silence_below_threshold_no_reflection(self) -> None:
        """Silence shorter than threshold → no reflection."""
        tracker = LastInteractionTracker()
        tracker.touch()  # just now

        llm = AsyncMock()
        trigger = _make_trigger(
            config=_config(silence_threshold_seconds=1800),
            tracker=tracker,
            llm=llm,
        )

        await trigger._poll_once()

        llm.generate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_silence_above_threshold_triggers_reflection(self) -> None:
        """Silence exceeding threshold → LLM called, threads created."""
        tracker = LastInteractionTracker()
        llm = AsyncMock()
        mimir = AsyncMock()

        intents = [_intent()]
        llm.generate = AsyncMock(return_value=_llm_response(intents))
        mimir.create_thread = AsyncMock()

        trigger = _make_trigger(
            config=_config(silence_threshold_seconds=100),
            tracker=tracker,
            llm=llm,
            mimir=mimir,
        )

        # Simulate interaction 200 seconds ago.
        with patch("ravn.adapters.triggers.wakefulness.datetime") as mock_dt:
            now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
            mock_dt.now.return_value = now
            mock_dt.fromisoformat = datetime.fromisoformat
            tracker._last_interaction = now - timedelta(seconds=200)

            await trigger._poll_once()

        llm.generate.assert_awaited_once()
        mimir.create_thread.assert_awaited_once_with(
            title="Follow up on API design",
            weight=5.0,
            context_refs=None,
            next_action_hint="Draft proposal",
        )

    @pytest.mark.asyncio
    async def test_cooldown_prevents_second_reflection(self) -> None:
        """Second poll within cooldown → no LLM call."""
        tracker = LastInteractionTracker()
        llm = AsyncMock()
        mimir = AsyncMock()

        llm.generate = AsyncMock(return_value=_llm_response([]))

        trigger = _make_trigger(
            config=_config(
                silence_threshold_seconds=100,
                reflection_cooldown_seconds=3600,
            ),
            tracker=tracker,
            llm=llm,
            mimir=mimir,
        )

        now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
        with patch("ravn.adapters.triggers.wakefulness.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.fromisoformat = datetime.fromisoformat
            tracker._last_interaction = now - timedelta(seconds=200)

            # First poll → reflection fires.
            await trigger._poll_once()
            assert llm.generate.await_count == 1

            # Second poll at same time → cooldown blocks.
            await trigger._poll_once()
            assert llm.generate.await_count == 1


class TestLLMResponses:
    """LLM response parsing edge cases."""

    @pytest.mark.asyncio
    async def test_empty_array_no_threads(self) -> None:
        """LLM returns [] → no threads created."""
        tracker = LastInteractionTracker()
        llm = AsyncMock()
        mimir = AsyncMock()

        llm.generate = AsyncMock(return_value=_llm_response([]))
        mimir.create_thread = AsyncMock()

        trigger = _make_trigger(
            config=_config(silence_threshold_seconds=100),
            tracker=tracker,
            llm=llm,
            mimir=mimir,
        )

        now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
        with patch("ravn.adapters.triggers.wakefulness.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.fromisoformat = datetime.fromisoformat
            tracker._last_interaction = now - timedelta(seconds=200)

            await trigger._poll_once()

        llm.generate.assert_awaited_once()
        mimir.create_thread.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_malformed_json_no_crash(self) -> None:
        """LLM returns invalid JSON → logged, no crash, no threads."""
        tracker = LastInteractionTracker()
        llm = AsyncMock()
        mimir = AsyncMock()

        llm.generate = AsyncMock(return_value=_llm_response("not valid json {{{"))
        mimir.create_thread = AsyncMock()

        trigger = _make_trigger(
            config=_config(silence_threshold_seconds=100),
            tracker=tracker,
            llm=llm,
            mimir=mimir,
        )

        now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
        with patch("ravn.adapters.triggers.wakefulness.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.fromisoformat = datetime.fromisoformat
            tracker._last_interaction = now - timedelta(seconds=200)

            await trigger._poll_once()  # should not raise

        mimir.create_thread.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_array_json_no_threads(self) -> None:
        """LLM returns a JSON object instead of array → no threads."""
        tracker = LastInteractionTracker()
        llm = AsyncMock()
        mimir = AsyncMock()

        llm.generate = AsyncMock(return_value=_llm_response('{"title": "oops"}'))
        mimir.create_thread = AsyncMock()

        trigger = _make_trigger(
            config=_config(silence_threshold_seconds=100),
            tracker=tracker,
            llm=llm,
            mimir=mimir,
        )

        now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
        with patch("ravn.adapters.triggers.wakefulness.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.fromisoformat = datetime.fromisoformat
            tracker._last_interaction = now - timedelta(seconds=200)

            await trigger._poll_once()

        mimir.create_thread.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_llm_exception_no_crash(self) -> None:
        """LLM call raises → logged, no crash."""
        tracker = LastInteractionTracker()
        llm = AsyncMock()
        mimir = AsyncMock()

        llm.generate = AsyncMock(side_effect=RuntimeError("LLM down"))

        trigger = _make_trigger(
            config=_config(silence_threshold_seconds=100),
            tracker=tracker,
            llm=llm,
            mimir=mimir,
        )

        now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
        with patch("ravn.adapters.triggers.wakefulness.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.fromisoformat = datetime.fromisoformat
            tracker._last_interaction = now - timedelta(seconds=200)

            await trigger._poll_once()  # should not raise

        mimir.create_thread.assert_not_awaited()


class TestMaxIntentsCap:
    """max_intents_per_reflection is respected."""

    @pytest.mark.asyncio
    async def test_cap_limits_threads(self) -> None:
        """LLM returns more intents than cap → only cap threads created."""
        tracker = LastInteractionTracker()
        llm = AsyncMock()
        mimir = AsyncMock()

        intents = [_intent(title=f"Intent {i}") for i in range(10)]
        llm.generate = AsyncMock(return_value=_llm_response(intents))
        mimir.create_thread = AsyncMock()

        trigger = _make_trigger(
            config=_config(
                silence_threshold_seconds=100,
                max_intents_per_reflection=3,
            ),
            tracker=tracker,
            llm=llm,
            mimir=mimir,
        )

        now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
        with patch("ravn.adapters.triggers.wakefulness.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.fromisoformat = datetime.fromisoformat
            tracker._last_interaction = now - timedelta(seconds=200)

            await trigger._poll_once()

        assert mimir.create_thread.await_count == 3


class TestDeepReflection:
    """Deep reflection fires after longer silence."""

    @pytest.mark.asyncio
    async def test_deep_reflection_fires(self) -> None:
        """Silence > deep threshold → deep reflection fires."""
        tracker = LastInteractionTracker()
        llm = AsyncMock()
        mimir = AsyncMock()

        llm.generate = AsyncMock(return_value=_llm_response([_intent()]))
        mimir.create_thread = AsyncMock()

        trigger = _make_trigger(
            config=_config(
                silence_threshold_seconds=100,
                deep_reflection_threshold_seconds=500,
            ),
            tracker=tracker,
            llm=llm,
            mimir=mimir,
        )

        now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
        with patch("ravn.adapters.triggers.wakefulness.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.fromisoformat = datetime.fromisoformat
            tracker._last_interaction = now - timedelta(seconds=600)

            await trigger._poll_once()

        llm.generate.assert_awaited_once()
        # Verify deep prompt was used (contains "deep reflection")
        call_args = llm.generate.call_args
        messages = call_args.kwargs.get("messages") or call_args[0][0]
        prompt = messages[0]["content"]
        assert "deep reflection" in prompt

    @pytest.mark.asyncio
    async def test_deep_cooldown_prevents_second(self) -> None:
        """Second deep reflection within cooldown → falls through to shallow.

        When deep cooldown blocks the deep pass but silence still exceeds the
        shallow threshold, a shallow reflection fires (if its own cooldown
        allows).  After both have fired within their cooldowns, the third poll
        should be a complete no-op.
        """
        tracker = LastInteractionTracker()
        llm = AsyncMock()
        mimir = AsyncMock()

        llm.generate = AsyncMock(return_value=_llm_response([]))

        trigger = _make_trigger(
            config=_config(
                silence_threshold_seconds=100,
                deep_reflection_threshold_seconds=500,
                deep_reflection_cooldown_seconds=10000,
                reflection_cooldown_seconds=10000,
            ),
            tracker=tracker,
            llm=llm,
            mimir=mimir,
        )

        now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
        with patch("ravn.adapters.triggers.wakefulness.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.fromisoformat = datetime.fromisoformat
            tracker._last_interaction = now - timedelta(seconds=600)

            # First poll → deep reflection fires.
            await trigger._poll_once()
            assert llm.generate.await_count == 1

            # Second poll → deep in cooldown, shallow fires.
            await trigger._poll_once()
            assert llm.generate.await_count == 2

            # Third poll → both in cooldown, total no-op.
            await trigger._poll_once()
            assert llm.generate.await_count == 2


class TestIntentEdgeCases:
    """Edge cases in intent processing."""

    @pytest.mark.asyncio
    async def test_intent_without_title_skipped(self) -> None:
        """Intent with empty title → skipped."""
        tracker = LastInteractionTracker()
        llm = AsyncMock()
        mimir = AsyncMock()

        intents = [_intent(title=""), _intent(title="Valid")]
        llm.generate = AsyncMock(return_value=_llm_response(intents))
        mimir.create_thread = AsyncMock()

        trigger = _make_trigger(
            config=_config(silence_threshold_seconds=100),
            tracker=tracker,
            llm=llm,
            mimir=mimir,
        )

        now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
        with patch("ravn.adapters.triggers.wakefulness.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.fromisoformat = datetime.fromisoformat
            tracker._last_interaction = now - timedelta(seconds=200)

            await trigger._poll_once()

        assert mimir.create_thread.await_count == 1
        mimir.create_thread.assert_awaited_with(
            title="Valid",
            weight=5.0,
            context_refs=None,
            next_action_hint="Draft proposal",
        )

    @pytest.mark.asyncio
    async def test_create_thread_failure_logged_not_raised(self) -> None:
        """create_thread failure → logged, other intents still processed."""
        tracker = LastInteractionTracker()
        llm = AsyncMock()
        mimir = AsyncMock()

        intents = [_intent(title="First"), _intent(title="Second")]
        llm.generate = AsyncMock(return_value=_llm_response(intents))
        mimir.create_thread = AsyncMock(side_effect=[RuntimeError("Mímir down"), None])

        trigger = _make_trigger(
            config=_config(silence_threshold_seconds=100),
            tracker=tracker,
            llm=llm,
            mimir=mimir,
        )

        now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
        with patch("ravn.adapters.triggers.wakefulness.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.fromisoformat = datetime.fromisoformat
            tracker._last_interaction = now - timedelta(seconds=200)

            await trigger._poll_once()  # should not raise

        assert mimir.create_thread.await_count == 2


class TestStatePersistence:
    """State persistence and restoration."""

    @pytest.mark.asyncio
    async def test_state_saved(self, tmp_path: Path) -> None:
        """Reflection timestamps are saved to state file."""
        tracker = LastInteractionTracker()
        llm = AsyncMock()
        mimir = AsyncMock()

        llm.generate = AsyncMock(return_value=_llm_response([]))

        trigger = _make_trigger(
            config=_config(silence_threshold_seconds=100),
            tracker=tracker,
            llm=llm,
            mimir=mimir,
            state_dir=tmp_path,
        )

        now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
        with patch("ravn.adapters.triggers.wakefulness.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.fromisoformat = datetime.fromisoformat
            tracker._last_interaction = now - timedelta(seconds=200)

            await trigger._poll_once()

        state_file = tmp_path / "wakefulness_state.json"
        assert state_file.exists()
        state = json.loads(state_file.read_text())
        assert "last_reflection_at" in state

    @pytest.mark.asyncio
    async def test_state_restored(self, tmp_path: Path) -> None:
        """Saved state is loaded on init, preventing immediate re-reflection."""
        # Save a recent reflection timestamp.
        now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
        state_file = tmp_path / "wakefulness_state.json"
        state_file.write_text(json.dumps({"last_reflection_at": now.isoformat()}))

        tracker = LastInteractionTracker()
        llm = AsyncMock()
        mimir = AsyncMock()

        trigger = _make_trigger(
            config=_config(
                silence_threshold_seconds=100,
                reflection_cooldown_seconds=3600,
            ),
            tracker=tracker,
            llm=llm,
            mimir=mimir,
            state_dir=tmp_path,
        )

        # Load state explicitly (normally done in run()).
        trigger._load_state()

        with patch("ravn.adapters.triggers.wakefulness.datetime") as mock_dt:
            # Only 10 seconds after last reflection → still in cooldown.
            mock_dt.now.return_value = now + timedelta(seconds=10)
            mock_dt.fromisoformat = datetime.fromisoformat
            tracker._last_interaction = now - timedelta(seconds=200)

            await trigger._poll_once()

        llm.generate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_corrupt_state_file_handled(self, tmp_path: Path) -> None:
        """Corrupt state file → loaded gracefully, no crash."""
        state_file = tmp_path / "wakefulness_state.json"
        state_file.write_text("not valid json {{{")

        trigger = _make_trigger(state_dir=tmp_path)
        trigger._load_state()  # should not raise

        assert trigger._last_reflection_at is None


class TestRunLoop:
    """Integration tests for the run() loop."""

    @pytest.mark.asyncio
    async def test_run_cancellation(self) -> None:
        """run() re-raises CancelledError."""
        trigger = _make_trigger(config=_config(enabled=True, poll_interval_seconds=1))
        enqueue = AsyncMock()

        # Load state needs a state_dir that doesn't exist — handled gracefully.
        task = asyncio.create_task(trigger.run(enqueue))
        await asyncio.sleep(0.05)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            _ = await task

    @pytest.mark.asyncio
    async def test_name_property(self) -> None:
        trigger = _make_trigger()
        assert trigger.name == "wakefulness"


class TestDeepStatePersistence:
    """Deep reflection timestamp persistence."""

    @pytest.mark.asyncio
    async def test_deep_reflection_state_saved(self, tmp_path: Path) -> None:
        """Deep reflection timestamp is saved."""
        tracker = LastInteractionTracker()
        llm = AsyncMock()
        mimir = AsyncMock()

        llm.generate = AsyncMock(return_value=_llm_response([]))

        trigger = _make_trigger(
            config=_config(
                silence_threshold_seconds=100,
                deep_reflection_threshold_seconds=500,
            ),
            tracker=tracker,
            llm=llm,
            mimir=mimir,
            state_dir=tmp_path,
        )

        now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
        with patch("ravn.adapters.triggers.wakefulness.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.fromisoformat = datetime.fromisoformat
            tracker._last_interaction = now - timedelta(seconds=600)

            await trigger._poll_once()

        state_file = tmp_path / "wakefulness_state.json"
        state = json.loads(state_file.read_text())
        assert "last_deep_reflection_at" in state

    @pytest.mark.asyncio
    async def test_deep_state_restored(self, tmp_path: Path) -> None:
        """Saved deep reflection state blocks re-reflection in cooldown."""
        now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
        state_file = tmp_path / "wakefulness_state.json"
        state_file.write_text(
            json.dumps(
                {
                    "last_reflection_at": now.isoformat(),
                    "last_deep_reflection_at": now.isoformat(),
                }
            )
        )

        tracker = LastInteractionTracker()
        llm = AsyncMock()
        mimir = AsyncMock()

        trigger = _make_trigger(
            config=_config(
                silence_threshold_seconds=100,
                deep_reflection_threshold_seconds=500,
                deep_reflection_cooldown_seconds=10000,
                reflection_cooldown_seconds=10000,
            ),
            tracker=tracker,
            llm=llm,
            mimir=mimir,
            state_dir=tmp_path,
        )

        trigger._load_state()

        with patch("ravn.adapters.triggers.wakefulness.datetime") as mock_dt:
            # 10 seconds later — both cooldowns active.
            mock_dt.now.return_value = now + timedelta(seconds=10)
            mock_dt.fromisoformat = datetime.fromisoformat
            tracker._last_interaction = now - timedelta(seconds=600)

            await trigger._poll_once()

        llm.generate.assert_not_awaited()
