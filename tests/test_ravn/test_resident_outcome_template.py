"""The template shown to the resident must satisfy the validator that judges it.

Ivaldi repaired its outcome block on every turn for nine hours because the
charter-driven wakes were never shown the block at all. Teaching it is only
half a fix: a template that omits a required field, or names a value the
validator rejects, produces the same failure one step later. These tests hold
the prompt and the contract to each other.
"""

from __future__ import annotations

import yaml

from ravn.domain.resident_continuation import (
    RESIDENT_WORKING_STATE_FIELDS,
    validate_resident_working_state,
)
from ravn.domain.valkyrie_contracts import (
    _JUDGMENT_REQUIRED_FIELDS,
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
