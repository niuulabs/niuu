"""Portable JSON-schema subset shared by workflow authors and runtimes."""

from __future__ import annotations

import json
import math
from typing import Any


class PortableSchemaError(ValueError):
    """A schema or value does not satisfy the supported portable subset."""


def validate_object_schema(schema: dict[str, Any], *, field: str) -> None:
    """Validate an object schema using the workflow-portable keyword subset."""
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise PortableSchemaError(f"{field} must be an object JSON schema")
    supported = {"type", "properties", "required", "additionalProperties", "$schema", "title"}
    unknown = set(schema) - supported
    if unknown:
        raise PortableSchemaError(
            f"{field} uses unsupported JSON schema keywords: {', '.join(sorted(unknown))}"
        )
    _validate_keyword_values(schema, field=field)
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    if not isinstance(properties, dict) or not isinstance(required, list):
        raise PortableSchemaError(f"{field} properties must be a mapping and required a list")
    if any(not isinstance(item, str) or item not in properties for item in required):
        raise PortableSchemaError(f"{field} required fields must name declared properties")
    if len(required) != len(set(required)):
        raise PortableSchemaError(f"{field} required fields must be unique")
    for name, subschema in properties.items():
        if not isinstance(name, str) or not isinstance(subschema, dict):
            raise PortableSchemaError(f"{field} properties must contain schema mappings")
        _validate_property_schema(subschema, field=f"{field}.properties.{name}")


def validate_object_instance(value: object, schema: dict[str, Any], *, field: str) -> None:
    """Validate an object value using the workflow-portable keyword subset."""
    validate_object_schema(schema, field=f"{field} schema")
    if not isinstance(value, dict):
        raise PortableSchemaError(f"{field} must be an object")
    properties = schema.get("properties", {})
    missing = set(schema.get("required", [])) - set(value)
    if missing:
        raise PortableSchemaError(f"{field} is missing: {', '.join(sorted(missing))}")
    if schema.get("additionalProperties") is False:
        unknown = set(value) - set(properties)
        if unknown:
            raise PortableSchemaError(f"{field} has unknown fields: {', '.join(sorted(unknown))}")
    for name, item in value.items():
        subschema = properties.get(name)
        if isinstance(subschema, dict):
            _validate_property(item, subschema, field=f"{field}.{name}")


def _validate_property_schema(schema: dict[str, Any], *, field: str) -> None:
    supported = {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "minLength",
        "minimum",
        "minItems",
    }
    unknown = set(schema) - supported
    if unknown:
        raise PortableSchemaError(
            f"{field} uses unsupported JSON schema keywords: {', '.join(sorted(unknown))}"
        )
    schema_type = schema.get("type")
    if not isinstance(schema_type, str) or schema_type not in {
        "string",
        "integer",
        "number",
        "boolean",
        "object",
        "array",
        "null",
    }:
        raise PortableSchemaError(f"{field} has unsupported type {schema_type!r}")
    _validate_keyword_values(schema, field=field)
    if schema_type == "object":
        validate_object_schema(schema, field=field)
    if schema_type == "array":
        items = schema.get("items")
        if not isinstance(items, dict):
            raise PortableSchemaError(f"{field}.items must be a schema")
        _validate_property_schema(items, field=f"{field}.items")


def _validate_keyword_values(schema: dict[str, Any], *, field: str) -> None:
    if "additionalProperties" in schema and type(schema["additionalProperties"]) is not bool:
        raise PortableSchemaError(f"{field}.additionalProperties must be a boolean")
    for keyword in ("minLength", "minItems"):
        if keyword not in schema:
            continue
        bound = schema[keyword]
        if type(bound) is not int or bound < 0:
            raise PortableSchemaError(f"{field}.{keyword} must be a nonnegative integer")
    if "minimum" in schema:
        bound = schema["minimum"]
        if type(bound) not in {int, float} or (type(bound) is float and not math.isfinite(bound)):
            raise PortableSchemaError(f"{field}.minimum must be a finite number")
    for keyword in ("title", "$schema"):
        if keyword in schema and not isinstance(schema[keyword], str):
            raise PortableSchemaError(f"{field}.{keyword} must be a string")
    if "enum" not in schema:
        return
    values = schema["enum"]
    if not isinstance(values, list) or not values:
        raise PortableSchemaError(f"{field}.enum must be a nonempty array")
    try:
        json.dumps(values, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise PortableSchemaError(f"{field}.enum must contain JSON values") from exc


def _json_equal(left: object, right: object) -> bool:
    """JSON booleans and numbers differ even though Python True equals 1."""
    if type(left) in {int, float} and type(right) in {int, float}:
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _json_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _json_equal(value, right[key]) for key, value in left.items()
        )
    return left == right


def _validate_property(value: object, schema: dict[str, Any], *, field: str) -> None:
    schema_type = schema["type"]
    type_matches = {
        "string": isinstance(value, str),
        # JSON Schema defines integers by value. Protobuf Struct, used by A2A,
        # represents every JSON number as a double, including integer receipts.
        "integer": type(value) is int
        or (type(value) is float and math.isfinite(value) and value.is_integer()),
        "number": type(value) is int or (type(value) is float and math.isfinite(value)),
        "boolean": type(value) is bool,
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "null": value is None,
    }
    if not type_matches[schema_type]:
        raise PortableSchemaError(f"{field} must be {schema_type}")
    if "enum" in schema and not any(_json_equal(value, item) for item in schema["enum"]):
        raise PortableSchemaError(f"{field} must be one of the declared enum values")
    if isinstance(value, str) and len(value) < int(schema.get("minLength", 0)):
        raise PortableSchemaError(f"{field} is shorter than minLength")
    if type(value) in {int, float} and value < schema.get("minimum", value):
        raise PortableSchemaError(f"{field} is below minimum")
    if isinstance(value, list):
        if len(value) < int(schema.get("minItems", 0)):
            raise PortableSchemaError(f"{field} has fewer than minItems")
        for index, item in enumerate(value):
            _validate_property(item, schema["items"], field=f"{field}[{index}]")
    if isinstance(value, dict):
        validate_object_instance(value, schema, field=field)
