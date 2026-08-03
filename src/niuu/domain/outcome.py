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
_KEY_VALUE_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$")
_WRAPPED_OUTCOME_START = re.compile(
    r"---\s*o\s*u\s*t\s*c\s*o\s*m\s*e\s*---",
    re.IGNORECASE,
)
_WRAPPED_OUTCOME_END = re.compile(
    r"---\s*e\s*n\s*d\s*---",
    re.IGNORECASE,
)
_SOFT_KEY_FRAGMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")

_TYPE_VALIDATORS: dict[str, type] = {
    "string": str,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


@dataclass
class OutcomeField:
    """Declares a single field in an outcome schema."""

    type: Literal["string", "number", "boolean", "enum", "array", "object"]
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
        elif f.type == "array":
            hint = "[<item>, ...]"
        elif f.type == "object":
            hint = "{key: value}"
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
    normalized_text = _WRAPPED_OUTCOME_START.sub("---outcome---", text)
    normalized_text = _WRAPPED_OUTCOME_END.sub("---end---", normalized_text)
    blocks: list[str] = []
    pos = 0
    while True:
        start_m = _OUTCOME_START.search(normalized_text, pos)
        if start_m is None:
            break
        content_start = start_m.end()
        end_m = _OUTCOME_END.search(normalized_text, content_start)
        if end_m is None:
            # Missing ---end--- — use end of text
            raw = textwrap.dedent(normalized_text[content_start:]).strip()
        else:
            raw = textwrap.dedent(normalized_text[content_start : end_m.start()]).strip()
        blocks.append(raw)
        pos = end_m.end() if end_m else len(normalized_text)
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


def _join_soft_wrapped_parts(parts: list[str], *, compact: bool = False) -> str:
    cleaned = [part.strip() for part in parts if part.strip()]
    if compact:
        return "".join(cleaned)

    result = ""
    previous = ""
    for part in cleaned:
        if not result:
            result = part
        elif part.startswith((".", ",", ";", ":", "!", "?", ")", "]", "}", "/", "_", "-")):
            result += part
        elif result.endswith(("(", "[", "{", "/", "_", "-")):
            result += part
        elif re.search(r"\s[A-Za-z]$", previous) and part[:1].isalnum():
            result += part
        elif len(previous) == 1 and part[:1].islower():
            result += part
        else:
            result += f" {part}"
        previous = part
    return result


def _coerce_simple_scalar(key: str, parts: list[str]) -> Any:
    compact = key == "verdict" or key.endswith("_path") or key.endswith("_id")
    text = _join_soft_wrapped_parts(parts, compact=compact).strip()
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if re.fullmatch(r"-?\d+", text):
        try:
            return int(text)
        except ValueError:
            return text
    if re.fullmatch(r"-?\d+\.\d+", text):
        try:
            return float(text)
        except ValueError:
            return text
    return text


def _merge_soft_wrapped_key_line(lines: list[str], start_index: int) -> tuple[str, int]:
    stripped = lines[start_index].strip()
    inline_match = _KEY_VALUE_LINE.match(stripped)
    if inline_match:
        return stripped, start_index + 1

    if not _SOFT_KEY_FRAGMENT.match(stripped):
        return stripped, start_index + 1

    fragments = [stripped]
    index = start_index + 1
    while index < len(lines):
        next_stripped = lines[index].strip()
        if not next_stripped:
            break

        inline_match = _KEY_VALUE_LINE.match(next_stripped)
        if inline_match:
            merged_key = "".join(fragments) + inline_match.group(1)
            merged_value = inline_match.group(2).strip()
            if merged_value:
                return f"{merged_key}: {merged_value}", index + 1
            return f"{merged_key}:", index + 1

        if next_stripped.startswith(":"):
            return f"{''.join(fragments)}{next_stripped}", index + 1

        if not _SOFT_KEY_FRAGMENT.match(next_stripped):
            break

        fragments.append(next_stripped)
        index += 1

    return stripped, start_index + 1


def _parse_soft_wrapped_mapping(
    text: str,
    *,
    expected_keys: set[str] | None = None,
) -> dict[str, Any] | None:
    """Recover simple key/value outcome blocks that were soft-wrapped by a model."""
    lines = text.splitlines()
    parsed: dict[str, Any] = {}
    current_key: str | None = None
    scalar_parts: list[str] = []
    list_parts: list[str] = []
    current_mode: Literal["scalar", "list"] = "scalar"

    def flush() -> None:
        nonlocal current_key, scalar_parts, list_parts, current_mode
        if current_key is None:
            return
        if current_mode == "list":
            parsed[current_key] = list_parts[:]
        else:
            parsed[current_key] = _coerce_simple_scalar(current_key, scalar_parts)
        current_key = None
        scalar_parts = []
        list_parts = []
        current_mode = "scalar"

    index = 0
    while index < len(lines):
        current_raw = lines[index].strip()
        if (
            current_key is not None
            and current_mode == "scalar"
            and current_raw
            and (
                current_raw[:1] in {"_", "/", "."}
                or (current_raw.startswith("-") and not current_raw.startswith("- "))
            )
        ):
            scalar_parts.append(current_raw)
            index += 1
            continue
        if (
            current_key is not None
            and current_mode == "scalar"
            and current_raw
            and _KEY_VALUE_LINE.match(current_raw) is None
            and _SOFT_KEY_FRAGMENT.match(current_raw)
            and expected_keys
        ):
            merged_line, _ = _merge_soft_wrapped_key_line(lines, index)
            merged_match = _KEY_VALUE_LINE.match(merged_line)
            next_match = None
            if index + 1 < len(lines):
                next_match = _KEY_VALUE_LINE.match(lines[index + 1].strip())
            if (
                merged_match is not None
                and merged_match.group(1) not in expected_keys
                and next_match is not None
                and next_match.group(1) in expected_keys
            ):
                scalar_parts.append(current_raw)
                index += 1
                continue

        stripped, index = _merge_soft_wrapped_key_line(lines, index)

        if not stripped:
            continue

        match = _KEY_VALUE_LINE.match(stripped)
        if match:
            flush()
            current_key = match.group(1)
            value = match.group(2).strip()
            if value:
                scalar_parts.append(value)
            continue

        if current_key is None:
            continue

        if stripped.startswith("- "):
            if current_mode != "list":
                current_mode = "list"
            list_parts.append(stripped[2:].strip())
            continue

        if current_mode == "list":
            if list_parts:
                list_parts[-1] = f"{list_parts[-1]} {stripped}".strip()
            else:
                scalar_parts.append(stripped)
            continue

        scalar_parts.append(stripped)

    flush()
    return parsed or None


#: A plain scalar whose value itself contains ``": "``. YAML reads the second
#: colon as a nested mapping and rejects the document; quoting the value is a
#: faithful reading of what was written, not a guess at what was meant.
_AMBIGUOUS_SCALAR_LINE = re.compile(
    r"""^(?P<prefix>\s*(?:-\s+)?[A-Za-z_][\w .\-]*:[ \t]+)
         (?P<value>(?!["'\[{&*!|>#])\S.*:[ \t].*?)[ \t]*$""",
    re.VERBOSE,
)


def _quote_ambiguous_scalars(text: str) -> str | None:
    """Quote scalar values containing ``": "``; return None when none were found.

    Deliberately conservative: it never touches a value that is already quoted,
    a flow collection, an anchor/alias, or a block scalar, and it requires a
    colon *followed by whitespace* so URLs (``https://…``) and timestamps
    (``10:30:00``) are left alone.
    """
    changed = False
    lines: list[str] = []
    for line in text.splitlines():
        match = _AMBIGUOUS_SCALAR_LINE.match(line)
        if match is None:
            lines.append(line)
            continue
        value = match.group("value").replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{match.group("prefix")}"{value}"')
        changed = True
    if not changed:
        return None
    return "\n".join(lines)


def _validate_against_schema(
    parsed_fields: dict[str, Any],
    schema: OutcomeSchema | None,
) -> list[str]:
    errors: list[str] = []
    if schema is None:
        return errors

    for name, field_def in schema.fields.items():
        if name not in parsed_fields:
            if field_def.required:
                errors.append(f"required field '{name}' is missing")
        else:
            _validate_field(name, parsed_fields[name], field_def, errors)
    return errors


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

    parse_errors: list[str] = []
    parsed_fields: dict[str, Any] = {}

    try:
        loaded = yaml.safe_load(clean)
        if isinstance(loaded, dict):
            parsed_fields = loaded
        else:
            got = type(loaded).__name__
            parse_errors.append(f"outcome block did not parse as a YAML mapping; got {got}")
    except yaml.YAMLError as exc:
        # One unquoted ``key: value`` inside a plain scalar invalidates the whole
        # document, and the flat fallback below cannot represent nesting — so a
        # single sloppy line would otherwise discard an entire well-formed
        # judgment. Quote the ambiguous scalars and try once more before giving
        # up on structure.
        repaired = _quote_ambiguous_scalars(clean)
        loaded = None
        if repaired is not None:
            try:
                loaded = yaml.safe_load(repaired)
            except yaml.YAMLError:
                loaded = None
        if isinstance(loaded, dict):
            parsed_fields = loaded
        else:
            parse_errors.append(f"YAML parse error: {exc}")

    validation_errors = _validate_against_schema(parsed_fields, schema) if not parse_errors else []
    errors = list(parse_errors or validation_errors)

    expected_keys = set(schema.fields) if schema is not None else None
    required_keys = (
        {name for name, field in schema.fields.items() if field.required}
        if schema is not None
        else None
    )
    salvaged = _parse_soft_wrapped_mapping(clean, expected_keys=expected_keys)
    if salvaged is not None:
        salvage_errors = _validate_against_schema(salvaged, schema)
        parsed_missing_required_keys = (
            required_keys.difference(parsed_fields) if required_keys is not None else set()
        )
        salvaged_missing_required_keys = (
            required_keys.difference(salvaged) if required_keys is not None else set()
        )
        salvage_recovers_required_keys = len(salvaged_missing_required_keys) < len(
            parsed_missing_required_keys
        )
        if (
            parse_errors
            or validation_errors
            or parsed_missing_required_keys
            or len(salvage_errors) < len(validation_errors)
        ) and (
            parse_errors
            or len(salvage_errors) < len(errors)
            or (salvage_recovers_required_keys and len(salvage_errors) <= len(errors))
        ):
            parsed_fields = salvaged
            errors = salvage_errors

    return ParsedOutcome(
        raw=raw,
        fields=parsed_fields,
        valid=len(errors) == 0,
        errors=errors,
        source_text=text,
    )
