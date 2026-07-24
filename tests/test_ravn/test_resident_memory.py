"""Round-trip coverage for the surviving slim resident memory substrate."""

from __future__ import annotations

import stat

import pytest

from ravn.domain.models import TokenUsage
from ravn.domain.resident_continuation import (
    ResidentBudgetLimits,
    ResidentBudgetSnapshot,
    ResidentMemoryEntry,
    ResidentPolicyDecisionRecord,
    ResidentPolicyObservation,
    ResidentTurnRecord,
    ResidentWorkingStateRecord,
)
from ravn.resident_continuation import (
    LocalResidentMemory,
    NullResidentMemory,
    ResidentRunBudget,
)


def _turn(idx: int = 1) -> ResidentTurnRecord:
    return ResidentTurnRecord(
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
