"""The strict reader for a persona's ``produces.schema`` and its validation."""

from __future__ import annotations

import pytest

from niuu.domain.outcome import outcome_errors, outcome_schema_from_mapping


def test_reads_types_enums_and_optional_fields() -> None:
    schema = outcome_schema_from_mapping(
        {
            "verdict": {"type": "enum", "values": ["pass", "fail"], "description": "verdict"},
            "notes": {"type": "string", "required": False},
            "files": {"type": "array"},
        }
    )

    assert schema.fields["verdict"].enum_values == ["pass", "fail"]
    assert schema.fields["notes"].required is False
    assert schema.fields["files"].type == "array"


@pytest.mark.parametrize(
    "raw, message",
    [
        ({}, "non-empty mapping"),
        ([], "non-empty mapping"),
        ({"": {"type": "string"}}, "non-empty strings"),
        ({"x": "string"}, "must be a mapping"),
        ({"x": {"type": "date"}}, "unknown type 'date'"),
        ({"x": {"type": "enum", "values": []}}, "non-empty values list"),
        ({"x": {"type": "string", "required": "yes"}}, "non-boolean required flag"),
    ],
)
def test_refuses_a_malformed_schema(raw, message) -> None:
    with pytest.raises(ValueError, match=message):
        outcome_schema_from_mapping(raw)


def test_errors_name_missing_and_mistyped_fields() -> None:
    schema = outcome_schema_from_mapping(
        {"verdict": {"type": "enum", "values": ["pass"]}, "count": {"type": "number"}}
    )

    assert outcome_errors({"verdict": "pass", "count": 2}, schema) == []
    errors = outcome_errors({"verdict": "nope", "count": True}, schema)
    assert any("not in allowed values" in error for error in errors)
    assert any("expected number, got boolean" in error for error in errors)
    assert outcome_errors({}, schema) == [
        "required field 'verdict' is missing",
        "required field 'count' is missing",
    ]
