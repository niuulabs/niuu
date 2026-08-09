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
