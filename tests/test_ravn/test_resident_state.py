from __future__ import annotations

import pytest

from ravn.adapters.resident_state.mimir import LocalResidentState, MimirResidentState
from ravn.domain.models import TokenUsage
from ravn.domain.resident_continuation import (
    ResidentTurnRecord,
    ResidentWorkingStateRecord,
)


@pytest.mark.asyncio
async def test_local_resident_state_is_single_memory_boundary(tmp_path):
    state = LocalResidentState(tmp_path)

    turn_ref = await state.write_turn(
        ResidentTurnRecord(
            turn_index=1,
            prompt="inspect resident state",
            response="resident state recorded",
            outcome_fields={},
            tool_names=(),
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )
    )

    assert turn_ref.startswith("resident/continuation/turns/")
    assert (tmp_path / turn_ref).exists()
    assert turn_ref in await state.list_refs()


@pytest.mark.asyncio
async def test_local_resident_state_rejects_refs_outside_root(tmp_path):
    root = tmp_path / "resident-state"
    state = LocalResidentState(root)
    await state.write_turn(
        ResidentTurnRecord(
            turn_index=1,
            prompt="inspect resident state",
            response="resident state recorded",
            outcome_fields={},
            tool_names=(),
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.md").write_text("not resident state", encoding="utf-8")

    assert await state.list_refs("../outside") == []
    assert await state.list_refs(str(outside)) == []
    assert len(await state.list_refs()) == 1
    assert len(await state.list_refs("resident/continuation/turns")) == 1
    assert await state.list_refs("resident/continuation/policy") == []


@pytest.mark.asyncio
async def test_mimir_resident_state_is_single_memory_boundary(tmp_path):
    from mimir.adapters.markdown import MarkdownMimirAdapter

    mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")
    state = MimirResidentState(mimir)

    turn_ref = await state.write_turn(
        ResidentTurnRecord(
            turn_index=1,
            prompt="inspect resident state",
            response="resident state recorded",
            outcome_fields={},
            tool_names=(),
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )
    )

    assert turn_ref.startswith("resident/continuation/turns/")
    assert "resident state recorded" in await mimir.read_page(turn_ref)
    assert turn_ref in await state.list_refs()

    working_ref = await state.write_working_state(
        ResidentWorkingStateRecord(
            resident_id="resident-alpha",
            state={"unknowns": ["whether the source is reachable"]},
            source_turn_ref=turn_ref,
            source_case_id="case-1",
            source_task_id="task-1",
        )
    )
    working = await state.read_working_state("resident-alpha")
    assert working_ref == "resident/continuation/working-state/resident-alpha.md"
    assert working is not None
    assert "whether the source is reachable" in working.content


# ---------------------------------------------------------------------------
# Mimir-backed mutations
#
# Run against a real MarkdownMimirAdapter, not a stub: these delete pages, and
# a stub would happily agree with a delete that never reached the corpus.
# ---------------------------------------------------------------------------


def _mimir_state(tmp_path):
    from mimir.adapters.markdown import MarkdownMimirAdapter

    return MimirResidentState(MarkdownMimirAdapter(root=tmp_path / "mimir"))


async def _mimir_turn(state, case_id: str) -> str:
    return await state.write_turn(
        ResidentTurnRecord(
            turn_index=1,
            prompt="p",
            response="r",
            outcome_fields={},
            tool_names=(),
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            case_id=case_id,
        )
    )


@pytest.mark.asyncio
async def test_mimir_delete_case_removes_every_page_under_it(tmp_path):
    state = _mimir_state(tmp_path)
    await _mimir_turn(state, "phantom")
    keeper = await _mimir_turn(state, "keeper")

    removed = await state.delete_case("phantom")

    assert removed >= 1
    refs = await state.list_refs()
    assert keeper in refs
    assert not any("cases/phantom/" in ref for ref in refs)


@pytest.mark.asyncio
async def test_mimir_delete_case_does_not_match_a_prefix_neighbour(tmp_path):
    """cases/watch must not take cases/watch-2 with it."""
    state = _mimir_state(tmp_path)
    await _mimir_turn(state, "watch")
    neighbour = await _mimir_turn(state, "watch-2")

    await state.delete_case("watch")

    assert neighbour in await state.list_refs()


@pytest.mark.asyncio
async def test_mimir_delete_case_ignores_an_unknown_case(tmp_path):
    state = _mimir_state(tmp_path)
    await _mimir_turn(state, "keeper")

    assert await state.delete_case("no-such-case") == 0
    assert await state.delete_case("") == 0


@pytest.mark.asyncio
async def test_mimir_clear_decision_streak(tmp_path):
    from ravn.domain.resident_continuation import ResidentDecisionStreakRecord

    state = _mimir_state(tmp_path)
    await state.write_decision_streak(
        ResidentDecisionStreakRecord(
            resident_id="regin", fingerprint="watch:x", count=7, decision="watch"
        )
    )

    assert await state.clear_decision_streak("regin") is True
    assert await state.read_decision_streak("regin") is None
    assert await state.clear_decision_streak("regin") is False


@pytest.mark.asyncio
async def test_mimir_prune_spares_what_can_still_resume(tmp_path):
    from datetime import UTC, datetime, timedelta

    from ravn.domain.resident_continuation import ResidentScheduledWakeRecord

    state = _mimir_state(tmp_path)
    await _mimir_turn(state, "sleeping")
    await state.write_scheduled_wake(
        ResidentScheduledWakeRecord(
            case_id="sleeping",
            root_correlation_id="c",
            wake_at=datetime.now(UTC) + timedelta(hours=1),
            reason="waiting",
        )
    )
    await _mimir_turn(state, "waiting")
    await state.write_operator_needed(
        question="may I?",
        reason="because",
        turn=ResidentTurnRecord(
            turn_index=1,
            prompt="p",
            response="r",
            outcome_fields={},
            tool_names=(),
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            case_id="waiting",
        ),
        case_id="waiting",
    )
    await _mimir_turn(state, "dead")

    removed = await state.prune_cases()

    refs = await state.list_refs()
    assert removed == 1
    assert any("cases/sleeping/" in ref for ref in refs)
    assert any("cases/waiting/" in ref for ref in refs)
    assert not any("cases/dead/" in ref for ref in refs)


@pytest.mark.asyncio
async def test_mimir_prune_spares_an_unconsumed_answer(tmp_path):
    """Same rule as the local store: answering must not make a case prunable."""
    state = _mimir_state(tmp_path)
    turn = ResidentTurnRecord(
        turn_index=1,
        prompt="p",
        response="r",
        outcome_fields={},
        tool_names=(),
        usage=TokenUsage(input_tokens=1, output_tokens=1),
        case_id="answered",
    )
    await _mimir_turn(state, "answered")
    await state.write_operator_needed(
        question="may I?", reason="because", turn=turn, case_id="answered"
    )
    await state.write_operator_answer("yes", case_id="answered")

    removed = await state.prune_cases()

    assert removed == 0
    assert any("cases/answered/" in ref for ref in await state.list_refs())
