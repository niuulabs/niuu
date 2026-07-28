from __future__ import annotations

from dataclasses import replace

import pytest

from ravn.adapters.resident_state.mimir import LocalResidentState
from ravn.domain.models import TokenUsage
from ravn.domain.resident_continuation import ResidentActionCandidate, ResidentTurnRecord
from ravn.resident_timeline import build_resident_timeline


def _turn(index: int, working_state: dict, **overrides) -> ResidentTurnRecord:
    fields = {
        "continuation": "sleep",
        "next_action_timing": "scheduled_time",
        "rationale": f"turn {index} rationale",
        "working_state": working_state,
    }
    fields.update(overrides)
    return ResidentTurnRecord(
        turn_index=index,
        prompt=f"prompt {index}",
        response=f"response {index}",
        outcome_fields=fields,
        tool_names=("web_search", "read_file"),
        usage=TokenUsage(input_tokens=100 * index, output_tokens=10 * index),
        case_id="case-workshop",
    )


def _state(**fields) -> dict:
    base = {
        "observations": [],
        "hypotheses": [],
        "unknowns": [],
        "capability_gaps": [],
        "attempts": [],
    }
    base.update(fields)
    return base


@pytest.mark.asyncio
async def test_timeline_reconstructs_history_from_append_only_turns(tmp_path):
    state = LocalResidentState(tmp_path)
    await state.write_turn(
        _turn(1, _state(hypotheses=["filament stock may be low"], unknowns=["current stock"]))
    )
    await state.write_turn(
        _turn(
            2,
            _state(
                observations=["filament stock is 4 spools (inventory.csv)"],
                unknowns=["reorder lead time"],
            ),
        )
    )

    timeline = await build_resident_timeline(
        state,
        resident_id="ivaldi",
        charter="steward the workshop",
        environment_name="Workshop",
        environment_type="workshop",
    )

    assert timeline.resident_id == "ivaldi"
    assert timeline.charter == "steward the workshop"
    assert len(timeline.turns) == 2

    first, second = timeline.turns
    assert first.working_state["hypotheses"] == ["filament stock may be low"]
    assert first.changes["hypotheses"].added == ("filament stock may be low",)

    # The money shot: a hypothesis was dropped and an observation took its place.
    assert second.changes["hypotheses"].removed == ("filament stock may be low",)
    assert second.changes["observations"].added == ("filament stock is 4 spools (inventory.csv)",)
    assert second.changes["unknowns"].removed == ("current stock",)
    assert second.changes["unknowns"].added == ("reorder lead time",)


@pytest.mark.asyncio
async def test_retained_entries_are_not_reported_as_new(tmp_path):
    state = LocalResidentState(tmp_path)
    await state.write_turn(_turn(1, _state(observations=["printer A is idle"])))
    await state.write_turn(
        _turn(2, _state(observations=["printer A is idle", "printer B is printing"]))
    )

    timeline = await build_resident_timeline(state)

    second = timeline.turns[1]
    assert second.changes["observations"].added == ("printer B is printing",)
    assert second.changes["observations"].retained == ("printer A is idle",)
    assert second.changes["observations"].removed == ()


@pytest.mark.asyncio
async def test_turn_metadata_and_outcome_control_survive_the_round_trip(tmp_path):
    state = LocalResidentState(tmp_path)
    record = _turn(
        1,
        _state(capability_gaps=["cannot read the printer's job queue"]),
        decision="investigate",
        operational_state="nominal",
        correlation_ids={"trace": "trace-123"},
    )
    record = replace(
        record,
        selected_next_action=ResidentActionCandidate(
            title="Queue diagnostics",
            action="build a queue reader",
            reason="The queue is otherwise opaque",
        ),
        root_correlation_id="root-123",
        task_id="task-123",
        triggered_by="nats:printer.signal",
        evidence_refs=("signal:printer-7",),
        inbox_refs=("inbox:event-9",),
    )
    await state.write_turn(record)

    timeline = await build_resident_timeline(state)

    turn = timeline.turns[0]
    assert turn.case_id == "case-workshop"
    assert turn.tools_used == ("web_search", "read_file")
    assert turn.input_tokens == 100
    assert turn.continuation == "sleep"
    assert turn.next_action_timing == "scheduled_time"
    assert turn.selected_next_action == "build a queue reader"
    assert turn.root_correlation_id == "root-123"
    assert turn.task_id == "task-123"
    assert turn.triggered_by == "nats:printer.signal"
    assert turn.judgment["decision"] == "investigate"
    assert turn.judgment["operational_state"] == "nominal"
    assert turn.judgment["correlation_ids"]["trace"] == "trace-123"
    assert turn.evidence_refs == ("signal:printer-7",)
    assert turn.inbox_refs == ("inbox:event-9",)
    assert turn.working_state["capability_gaps"] == ["cannot read the printer's job queue"]
    assert turn.updated_at


@pytest.mark.asyncio
async def test_a_turn_without_a_snapshot_does_not_read_as_wiping_the_model(tmp_path):
    """An invalid outcome must not render as the resident forgetting everything."""
    state = LocalResidentState(tmp_path)
    await state.write_turn(_turn(1, _state(observations=["printer A is idle"])))
    await state.write_turn(_turn(2, {}))
    await state.write_turn(_turn(3, _state(observations=["printer A is idle"])))

    timeline = await build_resident_timeline(state)

    assert timeline.turns[1].working_state["observations"] == ["printer A is idle"]
    assert timeline.turns[1].working_state_authored is False
    assert timeline.turns[1].working_state_turn_index == 1
    assert timeline.turns[1].working_state_updated_at == timeline.turns[0].updated_at
    assert timeline.turns[1].changes["observations"].removed == ()
    # The baseline held, so the unchanged entry is retained rather than "new".
    assert timeline.turns[2].changes["observations"].retained == ("printer A is idle",)
    assert timeline.turns[2].working_state_authored is True
    assert timeline.turns[2].working_state_turn_index == 3


@pytest.mark.asyncio
async def test_dict_entries_render_as_text_without_losing_content(tmp_path):
    state = LocalResidentState(tmp_path)
    await state.write_turn(
        _turn(1, _state(observations=[{"claim": "stock is low", "ref": "inventory.csv"}]))
    )

    timeline = await build_resident_timeline(state)

    rendered = timeline.turns[0].working_state["observations"][0]
    assert "stock is low" in rendered
    assert "inventory.csv" in rendered


@pytest.mark.asyncio
async def test_empty_state_produces_an_empty_timeline(tmp_path):
    timeline = await build_resident_timeline(LocalResidentState(tmp_path))

    assert timeline.turns == ()
    assert timeline.as_dict()["turns"] == []


@pytest.mark.asyncio
async def test_timeline_serializes_to_json_for_the_renderer(tmp_path):
    state = LocalResidentState(tmp_path)
    await state.write_turn(_turn(1, _state(unknowns=["reorder lead time"])))

    payload = await build_resident_timeline(state, charter="steward the workshop")

    text = payload.to_json()
    assert '"charter": "steward the workshop"' in text
    assert "reorder lead time" in text
    assert '"fields"' in text
