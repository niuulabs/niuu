"""Outcome block parsing and validation for persona output."""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from typing import Any, Literal

import yaml

_OUTCOME_START = re.compile(r"---outcome---", re.IGNORECASE)
# Accept ---end--- or just --- on its own line as end marker
_OUTCOME_END = re.compile(r"---end---|(?:^|\n)---(?:\s*$|\n)", re.IGNORECASE)
_CODE_FENCE = re.compile(r"^```[a-z]*\s*\n?(.*?)```\s*$", re.DOTALL)

_TYPE_VALIDATORS: dict[str, type] = {
    "string": str,
    "number": (int, float),
    "boolean": bool,
}


@dataclass
class OutcomeField:
    """Declares a single field in an outcome schema."""

    type: Literal["string", "number", "boolean", "enum"]
    description: str
    enum_values: list[str] | None = None
    required: bool = True


@dataclass
class OutcomeSchema:
    """Declares the fields a persona produces in its ---outcome--- block."""

    fields: dict[str, OutcomeField]


@dataclass
class ParsedOutcome:
    """Result of parsing an ---outcome--- block from agent output."""

    raw: str
    fields: dict[str, Any]
    valid: bool
    errors: list[str]
    source_text: str


def generate_outcome_instruction(schema: OutcomeSchema) -> str:
    """Generate the system prompt appendix that tells the persona to produce an outcome block.

    When your work is complete, output the outcome block and STOP:

        ---outcome---
        verdict: pass | fail | needs_changes
        findings_count: <number>
        summary: <one-line summary>
        ---end---
    """
    lines = [
        "IMPORTANT: When your work is complete, output this EXACT outcome block format and STOP.",
        "The outcome block MUST be valid YAML with key: value pairs. Do NOT write prose or lists.",
        "Do not call any more tools after producing the outcome block.",
        "",
        "Required format (copy this structure exactly):",
        "---outcome---",
    ]
    for name, f in schema.fields.items():
        if f.type == "enum" and f.enum_values:
            hint = " | ".join(f.enum_values)
        elif f.type == "number":
            hint = "<number>"
        elif f.type == "boolean":
            hint = "true | false"
        else:
            hint = f"<{f.description}>"
        lines.append(f"{name}: {hint}")
    lines.append("---end---")
    return "\n".join(lines)


def _strip_code_fence(text: str) -> str:
    m = _CODE_FENCE.match(text.strip())
    if m:
        return m.group(1)
    return text


def _find_outcome_blocks(text: str) -> list[str]:
    """Return list of raw content strings between ---outcome--- and ---end--- markers."""
    blocks: list[str] = []
    pos = 0
    while True:
        start_m = _OUTCOME_START.search(text, pos)
        if start_m is None:
            break
        content_start = start_m.end()
        end_m = _OUTCOME_END.search(text, content_start)
        if end_m is None:
            # Missing ---end--- — use end of text
            raw = textwrap.dedent(text[content_start:]).strip()
        else:
            raw = textwrap.dedent(text[content_start : end_m.start()]).strip()
        blocks.append(raw)
        pos = end_m.end() if end_m else len(text)
    return blocks


def _validate_field(
    name: str,
    value: Any,
    field_def: OutcomeField,
    errors: list[str],
) -> None:
    if field_def.type == "enum":
        allowed = field_def.enum_values or []
        if str(value) not in allowed:
            errors.append(f"field '{name}': value {value!r} not in allowed values {allowed}")
        return

    if field_def.type == "number" and isinstance(value, bool):
        errors.append(f"field '{name}': expected number, got boolean")
        return

    expected = _TYPE_VALIDATORS.get(field_def.type)
    if expected is None:
        return

    if not isinstance(value, expected):
        errors.append(f"field '{name}': expected {field_def.type}, got {type(value).__name__}")


def parse_outcome_block(text: str, schema: OutcomeSchema | None = None) -> ParsedOutcome | None:
    """Extract and parse the ---outcome--- block from agent/session output.

    1. Find ---outcome--- marker (case-insensitive, tolerant of whitespace)
    2. Find ---end--- marker
    3. Parse content between markers as YAML
    4. If schema provided, validate types and required fields
    5. Return ParsedOutcome or None if no block found

    When multiple blocks are present, the last one is used.
    """
    blocks = _find_outcome_blocks(text)
    if not blocks:
        return None

    raw = blocks[-1]
    clean = _strip_code_fence(raw)

    errors: list[str] = []
    parsed_fields: dict[str, Any] = {}

    try:
        loaded = yaml.safe_load(clean)
        if isinstance(loaded, dict):
            parsed_fields = loaded
        else:
            got = type(loaded).__name__
            errors.append(f"outcome block did not parse as a YAML mapping; got {got}")
    except yaml.YAMLError as exc:
        errors.append(f"YAML parse error: {exc}")

    if schema is not None and not errors:
        for name, field_def in schema.fields.items():
            if name not in parsed_fields:
                if field_def.required:
                    errors.append(f"required field '{name}' is missing")
            else:
                _validate_field(name, parsed_fields[name], field_def, errors)

    return ParsedOutcome(
        raw=raw,
        fields=parsed_fields,
        valid=len(errors) == 0,
        errors=errors,
        source_text=text,
    )
