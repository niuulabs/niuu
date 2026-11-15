"""The instruction text an offered step carries, and the wire shapes around it."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from niuu.domain.workflow_step import (
    WORKFLOW_STEP_CLOSE_TOOL,
    OfferInput,
    PersonaPin,
    StepContract,
    StepOffered,
)
from ting.domain.step_instructions import CLOSE_TOOL_NAME, render_offer_instructions

from . import workflow_step_contract as contract


def _pin(alias: str = "coder") -> PersonaPin:
    return PersonaPin(alias=alias, id=alias, revision="rev", digest="sha256:" + "0" * 64)


def _contract(alias: str = "coder", **overrides: Any) -> StepContract:
    values: dict[str, Any] = {
        "persona": _pin(alias),
        "valid_verdicts": ["blocked", "implemented"],
        "verdict_events": {"implemented": "code.ready", "blocked": "work.blocked"},
    }
    values.update(overrides)
    return StepContract(**values)


def _render(**overrides: Any) -> str:
    values: dict[str, Any] = {
        "workflow_name": "Build",
        "node_id": "implement",
        "node_label": "Implement",
        "run_id": UUID(contract.RUN_ID),
        "attempt": 1,
        "generation": 0,
        "inputs": (),
        "contracts": (_contract(),),
        "deadline_at": None,
    }
    values.update(overrides)
    return render_offer_instructions(**values)


def _input(**overrides: Any) -> OfferInput:
    values: dict[str, Any] = {
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
            "offer_id": "also-ignored",
        },
    }
    values.update(overrides)
    return OfferInput(**values)


def test_the_close_tool_is_the_one_the_members_skuld_serves() -> None:
    assert CLOSE_TOOL_NAME == WORKFLOW_STEP_CLOSE_TOOL == "workflow_step_close"


def test_the_text_names_the_workflow_the_step_and_the_run_but_no_member() -> None:
    text = _render()

    assert 'workflow "Build"' in text
    assert "# Workflow step: Implement" in text
    assert "node `implement`" in text
    assert contract.RUN_ID in text
    # An offer goes to every member that takes the topic: it names no member and no activation.
    assert "`coder`" not in text
    assert contract.ACTIVATION_ID not in text


def test_a_workflow_without_a_name_is_still_described() -> None:
    assert "of the workflow. Run" in _render(workflow_name="")


def test_a_first_attempt_on_the_first_generation_says_nothing_about_rework() -> None:
    text = _render()

    assert "This is attempt" not in text
    assert "rework" not in text


def test_a_rework_attempt_says_so() -> None:
    text = _render(attempt=2, generation=1)

    assert "This is attempt 2 of this step." in text
    assert "rework generation 1" in text


def test_inputs_carry_each_consumed_outcome_with_its_verdict_note_and_payload() -> None:
    text = _render(inputs=(_input(),))

    assert "### `review.failed` from review / reviewer" in text
    assert "- verdict: `fail`" in text
    assert "- note: the null case is not handled" in text
    assert '"findings"' in text and "null user crashes" in text
    assert '"kind": "test"' in text
    # Bookkeeping the member has no use for stays out.
    assert "ignored" not in text


def test_an_input_from_a_node_without_a_member_names_only_the_node() -> None:
    text = _render(inputs=(_input(member=None),))

    assert "### `review.failed` from review\n" in text


def test_inputs_are_listed_in_the_order_given() -> None:
    first = _input(seq=1, event_type="code.ready", node_id="code", member="coder", payload={})
    second = _input(seq=2, event_type="review.failed")

    text = _render(inputs=(first, second))

    assert text.index("`code.ready`") < text.index("`review.failed`")


def test_a_step_that_consumed_nothing_says_so() -> None:
    assert "consumed no earlier outcome" in _render()


def test_unlisted_payload_fields_are_shown_not_dropped() -> None:
    item = _input(payload={"verdict": "ok", "by": {"kind": "person", "id": "alice"}})

    text = _render(inputs=(item,))

    assert "other fields" in text and '"alice"' in text


def test_every_valid_ending_and_verdict_is_listed_with_the_event_it_hands_off() -> None:
    text = _render()

    for ending in ("verdict", "waiting", "done", "failed"):
        assert f"- `{ending}`" in text
    assert "`implemented` produces `code.ready`" in text
    assert "`blocked` produces `work.blocked`" in text
    assert "needs `waiting_on`" in text
    assert "needs `failure_reason`" in text


def test_a_persona_without_a_verdict_map_names_the_one_event_everything_hands_off() -> None:
    text = _render(
        contracts=(_contract(valid_verdicts=[], verdict_events={}, default_event="work.finished"),)
    )

    assert "hands off `work.finished`" in text


def test_a_persona_with_neither_a_map_nor_a_default_accepts_any_verdict_word() -> None:
    text = _render(contracts=(_contract(valid_verdicts=[], verdict_events={}),))

    assert "a short word saying how the step went." in text
    assert "hands off" not in text


def test_only_the_endings_the_step_allows_are_described() -> None:
    text = _render(contracts=(_contract(valid_endings=["verdict"]),))

    assert "- `verdict`" in text
    assert "- `waiting`" not in text
    assert "needs `waiting_on`" not in text
    assert "needs `failure_reason`" not in text


def test_the_outcome_schema_is_shown_when_the_persona_declares_one() -> None:
    schema = {"summary": {"type": "string", "description": "what changed"}}

    with_schema = _render(contracts=(_contract(outcome_schema=schema),))
    without = _render()

    assert "## Outcome" in with_schema and "what changed" in with_schema
    assert "## Outcome" not in without


def test_roles_that_agree_share_one_description() -> None:
    text = _render(contracts=(_contract("r1"), _contract("r2")))

    assert text.count("## How this step may end") == 1
    assert "It depends on your role" not in text


def test_roles_that_differ_are_each_described_under_their_own_alias() -> None:
    text = _render(
        contracts=(
            _contract("r1", valid_endings=["verdict"]),
            _contract(
                "r2",
                valid_verdicts=[],
                verdict_events={},
                default_event="review.done",
                outcome_schema={"summary": {"type": "string"}},
            ),
        )
    )

    assert "It depends on your role" in text
    assert "### As `r1`" in text and "### As `r2`" in text
    assert text.index("### As `r1`") < text.index("### As `r2`")
    # r1 may only give a verdict; r2 may do more, hands off one event and has a schema
    r1_part, r2_part = text.split("### As `r2`")
    assert "- `waiting`" not in r1_part
    assert "- `waiting`" in r2_part
    assert "hands off `review.done`" in r2_part
    assert '"summary"' in r2_part and '"summary"' not in r1_part


def test_the_deadline_is_shown_when_there_is_one() -> None:
    deadline = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)

    with_deadline = _render(deadline_at=deadline)

    assert "2026-11-08T12:00:00+00:00" in with_deadline
    assert "## Deadline" not in _render()


def test_the_text_leaves_the_exact_close_call_to_the_members_skuld() -> None:
    text = _render()

    assert "You hold this step until you close it." in text
    assert '"activation_id"' not in text


def test_rendering_is_deterministic() -> None:
    kwargs: dict[str, Any] = {
        "inputs": (_input(),),
        "contracts": (_contract(outcome_schema={"summary": {"type": "string"}}),),
    }

    assert _render(**kwargs) == _render(**kwargs)


# --- the wire shapes ----------------------------------------------------------------------


def test_the_offer_body_the_root_is_handed_carries_the_offer_and_its_signature() -> None:
    assert contract.OFFER_BODY_KEYS == {"offer", "attestation"}
    assert contract.OFFER_BODY["offer"] is contract.OFFERED_PAYLOAD
    assert contract.OFFER_BODY["attestation"]


def test_the_offered_payload_fixture_is_what_step_offered_writes() -> None:
    offered = StepOffered(**{k: v for k, v in contract.OFFERED_PAYLOAD.items() if k != "kind"})

    assert offered.to_payload() == contract.OFFERED_PAYLOAD
    assert set(contract.OFFERED_PAYLOAD) == contract.OFFERED_KEYS


def test_an_offer_carries_a_contract_for_each_member_that_takes_its_topic() -> None:
    offered = StepOffered(**{k: v for k, v in contract.OFFERED_PAYLOAD.items() if k != "kind"})

    found = offered.contract_for(contract.MEMBER)

    assert found is not None
    assert found.verdict_events["implemented"] == "code.ready"
    assert offered.contract_for("someone-else") is None


@pytest.mark.parametrize("event", sorted(contract.REPORTS))
def test_every_report_fixture_names_its_event_and_the_run(event: str) -> None:
    body = contract.REPORTS[event]["workflow_step"]

    assert body["event"] == event
    assert body["run_id"] == contract.RUN_ID


@pytest.mark.parametrize("event", ["closed", "unclosed"])
def test_a_close_or_unclosed_report_names_the_offer_it_belongs_to(event: str) -> None:
    body = contract.REPORTS[event]["workflow_step"]

    assert body["offer_id"] == contract.OFFER_ID
    assert body["activation_id"] == contract.ACTIVATION_ID
    assert body["attempt"] == 1


def test_the_report_names_are_the_ones_skuld_writes() -> None:
    from niuu.domain.workflow_step import STEP_EVENT_CLOSED, STEP_EVENT_UNCLOSED
    from skuld import workflow_steps as skuld

    assert set(contract.REPORTS) == {
        skuld.REPORT_LISTENING,
        skuld.REPORT_OFFER_PUBLISHED,
        skuld.REPORT_PICKED_UP,
        skuld.REPORT_PASSED,
        skuld.REPORT_OFFER_REFUSED,
        STEP_EVENT_CLOSED,
        STEP_EVENT_UNCLOSED,
    }


def test_each_verdict_says_when_to_report_it_where_the_persona_says() -> None:
    text = _render(
        contracts=(
            _contract(
                verdict_when={"implemented": "The change is pushed and tested."},
            ),
        )
    )

    assert "the one of these that fits; each hands off its event:" in text
    assert (
        "- `implemented` produces `code.ready`. Report it when: The change is pushed and tested."
        in text
    )
    # A verdict the persona gives no rule for is still listed.
    assert "- `blocked` produces `work.blocked`\n" in text + "\n"
