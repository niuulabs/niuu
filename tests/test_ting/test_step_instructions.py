"""The instruction text a step-host session receives."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from ting.domain.step_instructions import CLOSE_TOOL_NAME, render_step_instructions
from ting.ports.workflow_steps import DeliveredEvent, StepActivation

from . import workflow_step_contract as contract


def _activation(**overrides) -> StepActivation:
    values = {
        "run_id": UUID(contract.RUN_ID),
        "activation_id": UUID(contract.ACTIVATION_ID),
        "node_id": "implement",
        "node_label": "Implement",
        "member": "coder",
        "attempt": 1,
        "generation": 0,
        "session_id": "ses-1",
        "inputs": (),
        "deadline_at": None,
        "valid_verdicts": ("blocked", "implemented"),
        "verdict_events": {"implemented": "code.ready", "blocked": "work.blocked"},
        "workflow_name": "Build",
    }
    values.update(overrides)
    return StepActivation(**values)


def _event(**overrides) -> DeliveredEvent:
    values = {
        "seq": 2,
        "event_type": "review.failed",
        "edge_id": "e3",
        "node_id": "review",
        "member": "reviewer",
        "payload": {
            "verdict": "fail",
            "note": "the null case is not handled",
            "outcome": {"findings": ["null user crashes"]},
            "evidence": {"items": [{"kind": "test", "ref": "t1", "summary": "fails"}]},
            "attempt": 1,
            "activation_id": "ignored",
        },
    }
    values.update(overrides)
    return DeliveredEvent(**values)


def test_the_text_names_the_workflow_the_step_the_member_and_the_run() -> None:
    text = render_step_instructions(_activation())

    assert 'workflow "Build"' in text
    assert "# Workflow step: Implement" in text
    assert "`coder`" in text and "node `implement`" in text
    assert contract.RUN_ID in text and contract.ACTIVATION_ID in text


def test_a_first_attempt_on_the_first_generation_says_nothing_about_rework() -> None:
    text = render_step_instructions(_activation())

    assert "This is attempt" not in text
    assert "rework" not in text


def test_a_rework_attempt_says_so() -> None:
    text = render_step_instructions(_activation(attempt=2, generation=1))

    assert "This is attempt 2 of this step." in text
    assert "rework generation 1" in text


def test_inputs_carry_each_consumed_outcome_with_its_verdict_note_and_payload() -> None:
    text = render_step_instructions(_activation(inputs=(_event(),)))

    assert "### `review.failed` from review / reviewer" in text
    assert "- verdict: `fail`" in text
    assert "- note: the null case is not handled" in text
    assert '"findings"' in text and "null user crashes" in text
    assert '"kind": "test"' in text
    # Bookkeeping the member has no use for stays out.
    assert "ignored" not in text


def test_a_step_that_consumed_nothing_says_so() -> None:
    assert "consumed no earlier outcome" in render_step_instructions(_activation())


def test_unlisted_payload_fields_are_shown_not_dropped() -> None:
    event = _event(payload={"verdict": "ok", "by": {"kind": "person", "id": "alice"}})

    text = render_step_instructions(_activation(inputs=(event,)))

    assert "other fields" in text and '"alice"' in text


def test_every_valid_ending_and_verdict_is_listed_with_the_event_it_hands_off() -> None:
    text = render_step_instructions(_activation())

    for ending in ("verdict", "waiting", "done", "failed"):
        assert f"- `{ending}`" in text
    assert "`implemented` produces `code.ready`" in text
    assert "`blocked` produces `work.blocked`" in text
    assert "needs `waiting_on`" in text
    assert "needs `failure_reason`" in text


def test_a_persona_without_a_verdict_map_names_the_one_event_everything_hands_off() -> None:
    text = render_step_instructions(
        _activation(valid_verdicts=(), verdict_events={}, default_event="work.finished")
    )

    assert "hands off `work.finished`" in text


def test_only_the_endings_the_step_allows_are_described() -> None:
    text = render_step_instructions(_activation(valid_endings=("verdict",)))

    assert "- `verdict`" in text
    assert "- `waiting`" not in text
    assert "needs `waiting_on`" not in text
    assert "needs `failure_reason`" not in text


def test_the_outcome_schema_is_shown_when_the_persona_declares_one() -> None:
    schema = {"summary": {"type": "string", "description": "what changed"}}

    with_schema = render_step_instructions(_activation(outcome_schema=schema))
    without = render_step_instructions(_activation())

    assert "## Outcome" in with_schema and "what changed" in with_schema
    assert "## Outcome" not in without


def test_the_deadline_is_shown_when_there_is_one() -> None:
    deadline = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)

    with_deadline = render_step_instructions(_activation(deadline_at=deadline))

    assert "2026-11-08T12:00:00+00:00" in with_deadline
    assert "## Deadline" not in render_step_instructions(_activation())


def test_the_closing_section_shows_the_exact_tool_call_for_this_activation() -> None:
    text = render_step_instructions(_activation())

    assert f"call the `{CLOSE_TOOL_NAME}` tool exactly once" in text
    assert f'"activation_id": "{contract.ACTIVATION_ID}"' in text
    assert '"ending": "verdict"' in text
    assert '"verdict": "blocked"' in text
    assert "Ending your turn without closing the step does not close it" in text


def test_the_closing_example_does_not_invent_a_verdict_for_an_open_vocabulary() -> None:
    text = render_step_instructions(_activation(valid_verdicts=(), verdict_events={}))

    assert '"verdict": "<verdict>"' in text


def test_rendering_is_deterministic() -> None:
    activation = _activation(inputs=(_event(),), outcome_schema={"summary": {"type": "string"}})

    assert render_step_instructions(activation) == render_step_instructions(activation)
