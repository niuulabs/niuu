"""Tests for the portable workflow JSON-schema subset."""

import pytest

from niuu.domain.json_schema import (
    PortableSchemaError,
    validate_object_instance,
    validate_object_schema,
)


def _schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "evidence": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {"requirement_id": {"type": "string", "minLength": 1}},
                    "required": ["requirement_id"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["evidence"],
        "additionalProperties": False,
    }


def test_portable_schema_accepts_nested_contract_value() -> None:
    validate_object_schema(_schema(), field="resultSchema")
    validate_object_instance(
        {"evidence": [{"requirement_id": "REQ-one"}]},
        _schema(),
        field="result",
    )


def test_portable_schema_reports_nested_type_path() -> None:
    with pytest.raises(PortableSchemaError, match=r"result\.evidence must be array"):
        validate_object_instance(
            {"evidence": {"REQ-one": {"requirement_id": "REQ-one"}}},
            _schema(),
            field="result",
        )


def test_portable_schema_rejects_keywords_outside_supported_subset() -> None:
    schema = _schema()
    schema["properties"]["evidence"]["maxItems"] = 3

    with pytest.raises(PortableSchemaError, match="unsupported JSON schema keywords: maxItems"):
        validate_object_schema(schema, field="resultSchema")
