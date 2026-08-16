"""Round-trip coverage for the surviving slim resident memory substrate."""

from __future__ import annotations

import os
import stat
import time
from datetime import UTC, datetime, timedelta

import pytest

from ravn.domain.models import TokenUsage
from ravn.domain.resident_continuation import (
    ResidentBudgetLimits,
    ResidentBudgetSnapshot,
    ResidentMemoryEntry,
    ResidentPolicyDecisionRecord,
    ResidentPolicyObservation,
    ResidentScheduledWakeRecord,
    ResidentTurnRecord,
    ResidentWorkingStateRecord,
)
from ravn.resident_continuation import (
    LocalResidentMemory,
    NullResidentMemory,
    ResidentRunBudget,
)


def _age_case(tmp_path, case_id: str, *, days: float) -> None:
    """Backdate a case directory so age-based retention can see it."""
    target = tmp_path / "resident" / "continuation" / "cases" / case_id
    stamp = time.time() - days * 86400
    os.utime(target, (stamp, stamp))


def _turn(idx: int = 1, *, case_id: str = "") -> ResidentTurnRecord:
    return ResidentTurnRecord(
        case_id=case_id,
        turn_index=idx,
        prompt=f"prompt {idx}",
        response=f"response {idx}",
        outcome_fields={"status": "ok"},
        tool_names=("bash",),
        usage=TokenUsage(input_tokens=10, output_tokens=5),
    )


@pytest.mark.asyncio
async def test_local_memory_turn_round_trip_and_recall(tmp_path) -> None:
    mem = LocalResidentMemory(tmp_path)
    assert await mem.recall("mandate") == []  # empty before any writes

    ref = await mem.write_turn(_turn(1))
    assert ref
    mode = stat.S_IMODE((tmp_path / ref).stat().st_mode)
    assert mode == 0o600
    await mem.write_turn(_turn(2))

    recalled = await mem.recall("mandate", limit=5)
    assert recalled
    assert all(isinstance(e, ResidentMemoryEntry) for e in recalled)
    assert any("response" in e.content for e in recalled)


@pytest.mark.asyncio
async def test_local_memory_working_state_round_trip(tmp_path) -> None:
    mem = LocalResidentMemory(tmp_path)
    record = ResidentWorkingStateRecord(
        resident_id="resident-alpha",
        state={
            "observations": ["source-1 reports an unfamiliar object"],
            "hypotheses": [],
            "unknowns": ["what the object controls"],
            "capability_gaps": ["no source inspection capability"],
            "attempts": [],
        },
        source_turn_ref="resident/continuation/cases/case-1/turns/turn-1.md",
        source_case_id="case-1",
        source_task_id="task-1",
        signal_refs=("source-1",),
    )

    ref = await mem.write_working_state(record)
    loaded = await mem.read_working_state("resident-alpha")

    assert ref == "resident/continuation/working-state/resident-alpha.md"
    assert loaded is not None
    assert '"capability_gaps"' in loaded.content
    assert "source-1" in loaded.content
    assert "resident_id:" not in loaded.content


@pytest.mark.asyncio
async def test_local_memory_policy_observation_round_trip(tmp_path) -> None:
    mem = LocalResidentMemory(tmp_path)
    obs = ResidentPolicyObservation(
        subject="spending",
        observation="operator prefers no paid ads",
        source="operator",
        status="candidate",
    )
    await mem.write_policy_observation(obs)

    loaded = await mem.list_policy_observations()
    assert len(loaded) == 1
    assert loaded[0].subject == "spending"
    assert loaded[0].observation == "operator prefers no paid ads"
    assert loaded[0].source == "operator"


@pytest.mark.asyncio
async def test_local_memory_policy_decision_and_budget(tmp_path) -> None:
    mem = LocalResidentMemory(tmp_path)
    decision = ResidentPolicyDecisionRecord(
        turn_index=1,
        action_title="deploy",
        action="deploy to prod",
        decision_kind="ask",
        allowed=False,
        needs_approval=True,
        reason="production change",
        risk_boundaries=("production_change",),
        question="May I deploy?",
    )
    assert await mem.write_policy_decision(decision)
    assert await mem.write_budget(ResidentBudgetSnapshot(turns_used=2))


@pytest.mark.asyncio
async def test_local_memory_operator_marker_lifecycle(tmp_path) -> None:
    mem = LocalResidentMemory(tmp_path)

    await mem.write_operator_needed(
        question="Approve external send?",
        reason="external_side_effect",
        turn=_turn(1),
    )
    pending = await mem.read_operator_needed()
    assert pending is not None
    assert "Approve external send?" in pending.content

    await mem.write_operator_answer("yes, go ahead")
    # answering clears the pending marker...
    assert await mem.read_operator_needed() is None
    # ...and the answer is readable until consumed
    answer = await mem.read_operator_answer()
    assert answer is not None

    await mem.consume_operator_answer(answer)
    assert await mem.read_operator_answer() is None


@pytest.mark.asyncio
async def test_null_memory_is_noop(tmp_path) -> None:
    mem = NullResidentMemory()
    assert await mem.recall("m") == []
    assert await mem.write_turn(_turn()) == ""
    assert (
        await mem.write_policy_observation(
            ResidentPolicyObservation(subject="s", observation="o", source="x")
        )
        == ""
    )
    assert await mem.list_policy_observations() == []
    assert await mem.read_operator_needed() is None
    assert await mem.read_operator_answer() is None


def test_run_budget_snapshot_and_limits() -> None:
    budget = ResidentRunBudget(ResidentBudgetLimits(max_turns=2, max_tokens=100))
    assert budget.can_continue().allowed is True

    budget.record_usage(TokenUsage(input_tokens=80, output_tokens=40))  # 120 > 100
    snap = budget.snapshot()
    assert snap.total_tokens == 120
    assert budget.can_continue().allowed is False


# ---------------------------------------------------------------------------
# Case retention
#
# Residents accumulated cases without bound: 841 on one, 937 on another, of
# which 754 and 923 could no longer be resumed by anything. recall() rglobs the
# whole tree and reads every file, so that cost 3.5s of every turn spent
# reading 56 MB of dead cases.
# ---------------------------------------------------------------------------


def _memory(tmp_path, **kwargs):
    """The state adapter residents actually run, so wake markers are writable."""
    from ravn.adapters.resident_state.mimir import LocalResidentState

    defaults = {"retention_max_cases": 0, "retention_max_age_days": 0.0}
    return LocalResidentState(tmp_path, **{**defaults, **kwargs})


async def _dead_case(mem, case_id: str) -> None:
    """A case with turns but nothing that can resume it."""
    await mem.write_turn(_turn(1, case_id=case_id))


async def _sleeping_case(mem, case_id: str) -> None:
    """A case with a pending scheduled wake — the runtime will come back to it."""
    await mem.write_turn(_turn(1, case_id=case_id))
    await mem.write_scheduled_wake(
        ResidentScheduledWakeRecord(
            case_id=case_id,
            root_correlation_id=case_id,
            wake_at=datetime.now(UTC) + timedelta(hours=1),
            reason="waiting on the next measurement",
        )
    )


async def _waiting_case(mem, case_id: str) -> None:
    """A case blocked on an unanswered operator question."""
    await mem.write_turn(_turn(1, case_id=case_id))
    await mem.write_operator_needed(
        question="May I restart the node?",
        reason="production change",
        turn=_turn(1, case_id=case_id),
        case_id=case_id,
    )


def _case_ids(tmp_path) -> set[str]:
    base = tmp_path / "resident" / "continuation" / "cases"
    return {p.name for p in base.iterdir()} if base.is_dir() else set()


@pytest.mark.asyncio
async def test_retention_prunes_only_unresumable_cases(tmp_path) -> None:
    mem = _memory(tmp_path, retention_max_cases=2)
    await _sleeping_case(mem, "sleeping")
    await _waiting_case(mem, "waiting")
    for idx in range(5):
        await _dead_case(mem, f"dead-{idx}")

    removed = await mem.prune_cases()

    remaining = _case_ids(tmp_path)
    assert "sleeping" in remaining, "a pending scheduled wake must never be pruned"
    assert "waiting" in remaining, "an unanswered operator question must never be pruned"
    assert removed == 5


@pytest.mark.asyncio
async def test_retention_keeps_the_most_recent_dead_cases(tmp_path) -> None:
    mem = _memory(tmp_path, retention_max_cases=3)
    for idx in range(6):
        await _dead_case(mem, f"dead-{idx}")
        _age_case(tmp_path, f"dead-{idx}", days=6 - idx)

    await mem.prune_cases()

    assert _case_ids(tmp_path) == {"dead-3", "dead-4", "dead-5"}


@pytest.mark.asyncio
async def test_retention_prunes_by_age(tmp_path) -> None:
    mem = _memory(tmp_path, retention_max_age_days=7.0)
    await _dead_case(mem, "recent")
    await _dead_case(mem, "ancient")
    _age_case(tmp_path, "ancient", days=30)

    removed = await mem.prune_cases()

    assert removed == 1
    assert _case_ids(tmp_path) == {"recent"}


@pytest.mark.asyncio
async def test_age_rule_still_spares_a_live_case(tmp_path) -> None:
    """An old case the resident is still sleeping on is live, not stale."""
    mem = _memory(tmp_path, retention_max_age_days=7.0)
    await _sleeping_case(mem, "long-sleeper")
    _age_case(tmp_path, "long-sleeper", days=30)

    assert await mem.prune_cases() == 0
    assert _case_ids(tmp_path) == {"long-sleeper"}


@pytest.mark.asyncio
async def test_retention_disabled_by_default(tmp_path) -> None:
    mem = _memory(tmp_path)
    for idx in range(20):
        await _dead_case(mem, f"dead-{idx}")

    assert await mem.prune_cases() == 0
    assert len(_case_ids(tmp_path)) == 20


@pytest.mark.asyncio
async def test_sweep_is_throttled_between_turns(tmp_path) -> None:
    """The sweep rides the turn write path, so it must not run on every turn."""
    mem = _memory(tmp_path, retention_max_cases=1, retention_sweep_interval_seconds=3600)

    sweeps = 0
    original = mem.prune_cases

    async def _counting_prune() -> int:
        nonlocal sweeps
        sweeps += 1
        return await original()

    mem.prune_cases = _counting_prune  # type: ignore[method-assign]
    await mem.write_turn(_turn(2, case_id="one"))
    await mem.write_turn(_turn(3, case_id="two"))
    await mem.write_turn(_turn(4, case_id="three"))

    assert sweeps == 1, "the interval throttle should collapse three turns into one sweep"


@pytest.mark.asyncio
async def test_pruning_shrinks_what_recall_has_to_read(tmp_path) -> None:
    """The point of the whole exercise: recall stops reading dead cases."""
    mem = _memory(tmp_path, retention_max_cases=3)
    for idx in range(25):
        await _dead_case(mem, f"dead-{idx}")

    before = len(list((tmp_path / "resident" / "continuation").rglob("*.md")))
    await mem.prune_cases()
    after = len(list((tmp_path / "resident" / "continuation").rglob("*.md")))

    assert after < before
    assert len(_case_ids(tmp_path)) == 3


@pytest.mark.asyncio
async def test_count_cases_reports_live_and_total(tmp_path) -> None:
    """The scorecard's case numbers come straight from resumability."""
    mem = _memory(tmp_path)
    await _sleeping_case(mem, "sleeping")
    await _waiting_case(mem, "waiting")
    for idx in range(3):
        await _dead_case(mem, f"dead-{idx}")

    assert await mem.count_cases() == (2, 5)


@pytest.mark.asyncio
async def test_count_cases_on_an_empty_store(tmp_path) -> None:
    assert await _memory(tmp_path).count_cases() == (0, 0)
