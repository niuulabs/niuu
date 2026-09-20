"""Malformed portable definitions fail before entering a model execution."""

import pytest
from google.protobuf.json_format import MessageToDict
from google.protobuf.struct_pb2 import Struct

from niuu.domain.json_schema import (
    PortableSchemaError,
    validate_object_instance,
    validate_object_schema,
)


def _schema(property_schema):
    return {"type": "object", "properties": {"result": property_schema}, "required": ["result"]}


@pytest.mark.parametrize(
    "property_schema",
    [
        {"type": []},
        {"type": "string", "minLength": "x"},
        {"type": "string", "minLength": True},
        {"type": "string", "minLength": -1},
        {"type": "array", "items": {"type": "string"}, "minItems": "x"},
        {"type": "array", "items": {"type": "string"}, "minItems": 1.5},
        {"type": "number", "minimum": "x"},
        {"type": "number", "minimum": True},
        {"type": "number", "minimum": float("nan")},
        {"type": "number", "minimum": float("inf")},
        {"type": "object", "properties": {}, "additionalProperties": "false"},
        {"type": "string", "enum": "one"},
        {"type": "string", "enum": []},
        {"type": "number", "enum": [float("nan")]},
        {"type": "string", "enum": [object()]},
    ],
)
def test_invalid_constraint_values_raise_portable_error(property_schema):
    with pytest.raises(PortableSchemaError):
        validate_object_schema(_schema(property_schema), field="contract")


@pytest.mark.parametrize(
    "keyword,value", [("additionalProperties", {}), ("title", 7), ("$schema", [])]
)
def test_invalid_object_schema_metadata_is_rejected(keyword, value):
    with pytest.raises(PortableSchemaError):
        validate_object_schema({"type": "object", keyword: value}, field="contract")


def test_required_fields_must_be_unique():
    schema = _schema({"type": "string"})
    schema["required"] = ["result", "result"]
    with pytest.raises(PortableSchemaError, match="unique"):
        validate_object_schema(schema, field="contract")


def test_json_enum_does_not_confuse_boolean_and_number():
    with pytest.raises(PortableSchemaError, match="enum"):
        validate_object_instance(
            {"result": 1}, _schema({"type": "integer", "enum": [True]}), field="output"
        )
    validate_object_instance(
        {"result": 1.0}, _schema({"type": "number", "enum": [1]}), field="output"
    )


def test_nested_json_enum_keeps_boolean_and_number_distinct():
    schema = _schema(
        {"type": "array", "items": {"type": "object", "properties": {}}, "enum": [[{"ok": True}]]}
    )
    with pytest.raises(PortableSchemaError, match="enum"):
        validate_object_instance({"result": [{"ok": 1}]}, schema, field="output")
    validate_object_instance({"result": [{"ok": True}]}, schema, field="output")


def test_integer_contract_survives_a2a_protobuf_metadata_round_trip():
    schema = _schema(
        {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"exit_code": {"type": "integer", "minimum": 0}},
                "required": ["exit_code"],
            },
        }
    )
    metadata = Struct()
    metadata.update({"result": [{"exit_code": 0}]})
    received = MessageToDict(metadata)
    assert type(received["result"][0]["exit_code"]) is float
    validate_object_instance(received, schema, field="deliveryResult")


@pytest.mark.parametrize("value", [0, 0.0, -1.0, 42.0])
def test_integer_schema_accepts_integral_json_numbers(value):
    validate_object_instance({"result": value}, _schema({"type": "integer"}), field="output")


@pytest.mark.parametrize("value", [True, False, "0", 0.5, float("nan"), float("inf")])
def test_integer_schema_rejects_non_integral_values(value):
    with pytest.raises(PortableSchemaError, match="must be integer"):
        validate_object_instance({"result": value}, _schema({"type": "integer"}), field="output")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_number_schema_rejects_non_json_numbers(value):
    with pytest.raises(PortableSchemaError, match="must be number"):
        validate_object_instance({"result": value}, _schema({"type": "number"}), field="output")
