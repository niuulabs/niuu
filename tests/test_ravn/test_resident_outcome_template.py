"""The template shown to the resident must satisfy the validator that judges it.

Ivaldi repaired its outcome block on every turn for nine hours because the
charter-driven wakes were never shown the block at all. Teaching it is only
half a fix: a template that omits a required field, or names a value the
validator rejects, produces the same failure one step later. These tests hold
the prompt and the contract to each other.
"""

from __future__ import annotations

import pytest
import yaml

from ravn.domain.resident_continuation import (
    RESIDENT_WORKING_STATE_FIELDS,
    validate_resident_working_state,
)
from ravn.domain.valkyrie_contracts import (
    _JUDGMENT_REQUIRED_FIELDS,
    RESIDENT_ESCALATION_GUIDANCE,
    VALKYRIE_ASK_OPERATOR_REQUIRES_QUESTION,
    VALKYRIE_ESCALATE_REQUIRES_QUESTION,
    VALKYRIE_JUDGMENT_PROPOSED,
    VALKYRIE_RUNTIME_OWNED_FIELDS,
    resident_outcome_section,
    resident_outcome_template,
    validate_valkyrie_outcome,
)


def _template_fields(**kwargs: object) -> dict:
    body = resident_outcome_template(**kwargs).split("---outcome---")[1]
    body = body.split("---end---")[0]
    parsed = yaml.safe_load(body)
    assert isinstance(parsed, dict)
    return parsed


def test_template_is_parseable_yaml() -> None:
    assert _template_fields()


def test_template_covers_every_model_authored_required_field() -> None:
    fields = _template_fields()
    # The runtime supplies identity and correlation; the model is never asked
    # for them, so their absence from the template is correct.
    expected = set(_JUDGMENT_REQUIRED_FIELDS) - VALKYRIE_RUNTIME_OWNED_FIELDS
    assert expected <= set(fields)


def test_template_working_state_satisfies_the_continuation_validator() -> None:
    fields = _template_fields()
    assert set(RESIDENT_WORKING_STATE_FIELDS) <= set(fields["working_state"])
    assert validate_resident_working_state(fields["working_state"]) == []


def test_working_state_is_shown_as_one_flow_mapping() -> None:
    """Shown as an indented block with a comment inside it, the model
    collapsed the whole thing onto one line and the value arrived as a
    string — every other field was correct and the turn was still rejected.
    A single flow mapping cannot degrade to a scalar."""
    line = next(
        line
        for line in resident_outcome_template().splitlines()
        if line.startswith("working_state:")
    )

    assert line.endswith("}")
    assert "#" not in line
    for field in RESIDENT_WORKING_STATE_FIELDS:
        assert f"{field}: []" in line


def test_template_vocabularies_are_accepted_by_the_validator() -> None:
    """Each enum's first listed option must actually validate."""
    fields = _template_fields()
    filled = dict(fields)
    for key in ("tier", "operational_state", "wakefulness", "action_authority"):
        filled[key] = fields[key].strip("<>").split(" | ")[0]
    filled["confidence"] = 0.5
    for runtime_field in VALKYRIE_RUNTIME_OWNED_FIELDS:
        filled.setdefault(
            runtime_field,
            {} if runtime_field == "correlation_ids" else "runtime-supplied",
        )

    assert validate_valkyrie_outcome(VALKYRIE_JUDGMENT_PROPOSED, filled) == []


def test_signal_refs_and_evidence_are_substituted_when_known() -> None:
    fields = _template_fields(
        signal_refs=["generic://workshop/abc123"],
        evidence_lines=["  - event_id: generic://workshop/abc123"],
    )
    assert fields["signal_refs"] == ["generic://workshop/abc123"]
    assert fields["evidence"] == [{"event_id": "generic://workshop/abc123"}]


def test_section_keeps_the_delimiters_the_parser_looks_for() -> None:
    section = resident_outcome_section()
    assert "---outcome---" in section
    assert "---end---" in section


def _filled_judgment(**overrides: object) -> dict:
    fields = _template_fields()
    filled = dict(fields)
    for key in ("tier", "operational_state", "wakefulness", "action_authority"):
        filled[key] = fields[key].strip("<>").split(" | ")[0]
    filled["confidence"] = 0.5
    filled["correlation_ids"] = {}
    for runtime_field in VALKYRIE_RUNTIME_OWNED_FIELDS - {"correlation_ids"}:
        filled[runtime_field] = "runtime-supplied"
    filled.update(overrides)
    return filled


def test_an_escalation_without_a_question_is_rejected_with_the_remedy() -> None:
    for question in ("", "   ", None):
        errors = validate_valkyrie_outcome(
            VALKYRIE_JUDGMENT_PROPOSED,
            _filled_judgment(decision="escalate", continuation="sleep", question=question),
        )

        assert errors == [VALKYRIE_ESCALATE_REQUIRES_QUESTION]
        assert "requires a non-empty 'question'" in errors[0]
        assert "'ask_operator'" in errors[0]


def test_an_escalation_with_a_question_is_accepted() -> None:
    errors = validate_valkyrie_outcome(
        VALKYRIE_JUDGMENT_PROPOSED,
        _filled_judgment(decision="Escalate", question="May I drain node ymir-3?"),
    )

    assert errors == []


def test_a_decision_that_is_not_an_escalation_needs_no_question() -> None:
    errors = validate_valkyrie_outcome(
        VALKYRIE_JUDGMENT_PROPOSED,
        _filled_judgment(decision="watch", continuation="sleep", question=""),
    )

    assert errors == []


def test_the_prompt_says_escalate_asks_the_operator_and_needs_a_question() -> None:
    section = resident_outcome_section()

    assert RESIDENT_ESCALATION_GUIDANCE in section
    assert "`decision: escalate` asks the operator" in section
    assert "empty `question` is rejected" in section
    assert "court" in section


@pytest.mark.parametrize(
    "overrides",
    [
        {"continuation": "ask_operator"},
        {"continuation": "Ask_Operator", "question": "  "},
        {"verdict": "help_needed", "question": None},
        {"continuation": "ask_operator", "open_questions": ["a fallback must not use this"]},
    ],
)
def test_asking_the_operator_without_a_question_is_rejected_with_the_remedy(
    overrides: dict,
) -> None:
    errors = validate_valkyrie_outcome(
        VALKYRIE_JUDGMENT_PROPOSED, _filled_judgment(decision="watch", **overrides)
    )

    assert errors == [VALKYRIE_ASK_OPERATOR_REQUIRES_QUESTION]
    assert "requires a non-empty 'question'" in errors[0]
    assert "'sleep' or 'stop'" in errors[0]


def test_an_escalation_asking_by_continuation_reports_the_escalation_remedy_once() -> None:
    errors = validate_valkyrie_outcome(
        VALKYRIE_JUDGMENT_PROPOSED,
        _filled_judgment(decision="escalate", continuation="ask_operator", question=""),
    )

    assert errors == [VALKYRIE_ESCALATE_REQUIRES_QUESTION]


def test_asking_the_operator_with_a_question_is_accepted() -> None:
    for overrides in ({"continuation": "ask_operator"}, {"verdict": "help_needed"}):
        errors = validate_valkyrie_outcome(
            VALKYRIE_JUDGMENT_PROPOSED,
            _filled_judgment(decision="watch", question="Which scope is authorized?", **overrides),
        )

        assert errors == []


def test_the_prompt_says_ask_operator_needs_a_question_too() -> None:
    assert "`continuation: ask_operator`" in RESIDENT_ESCALATION_GUIDANCE
    assert "never writes one for you" in resident_outcome_section()
