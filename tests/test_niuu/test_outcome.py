"""Tests for outcome block parsing, validation, and instruction generation."""

from __future__ import annotations

import pytest

from niuu.adapters.outcome.block_parser import BlockParserAdapter
from niuu.domain.outcome import (
    OutcomeField,
    OutcomeSchema,
    generate_outcome_instruction,
    parse_outcome_block,
)
from niuu.ports.outcome import OutcomeExtractorPort

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def simple_schema() -> OutcomeSchema:
    return OutcomeSchema(
        fields={
            "verdict": OutcomeField(
                type="enum",
                description="verdict",
                enum_values=["pass", "fail", "needs_changes"],
            ),
            "findings_count": OutcomeField(type="number", description="number of findings"),
            "summary": OutcomeField(type="string", description="one-line summary"),
        }
    )


@pytest.fixture()
def full_agent_output() -> str:
    return """
I reviewed the code and found 3 minor issues. Overall the code is clean.

---outcome---
verdict: pass
findings_count: 3
summary: Clean code with minor style suggestions
---end---
"""


# ---------------------------------------------------------------------------
# parse_outcome_block — basic cases
# ---------------------------------------------------------------------------


def test_parse_returns_none_when_no_block() -> None:
    result = parse_outcome_block("No outcome block here.")
    assert result is None


def test_parse_extracts_simple_block() -> None:
    text = "Some text\n---outcome---\nkey: value\n---end---\n"
    result = parse_outcome_block(text)
    assert result is not None
    assert result.fields == {"key": "value"}
    assert result.valid is True
    assert result.errors == []


def test_parse_block_at_end_of_text(full_agent_output: str) -> None:
    result = parse_outcome_block(full_agent_output)
    assert result is not None
    assert result.fields["verdict"] == "pass"
    assert result.fields["findings_count"] == 3
    assert "style suggestions" in result.fields["summary"]


def test_parse_preserves_source_text(full_agent_output: str) -> None:
    result = parse_outcome_block(full_agent_output)
    assert result is not None
    assert result.source_text == full_agent_output


def test_parse_raw_contains_yaml_content() -> None:
    text = "---outcome---\nfoo: bar\n---end---"
    result = parse_outcome_block(text)
    assert result is not None
    assert "foo" in result.raw


# ---------------------------------------------------------------------------
# parse_outcome_block — edge cases
# ---------------------------------------------------------------------------


def test_parse_case_insensitive_markers() -> None:
    text = "---OUTCOME---\nstatus: ok\n---END---"
    result = parse_outcome_block(text)
    assert result is not None
    assert result.fields == {"status": "ok"}


def test_parse_missing_end_marker_uses_end_of_text() -> None:
    text = "Preamble\n---outcome---\nresult: done"
    result = parse_outcome_block(text)
    assert result is not None
    assert result.fields == {"result": "done"}


def test_parse_multiple_blocks_uses_last() -> None:
    text = (
        "---outcome---\nresult: first\n---end---\nMore text\n---outcome---\nresult: last\n---end---"
    )
    result = parse_outcome_block(text)
    assert result is not None
    assert result.fields["result"] == "last"


def test_parse_block_in_middle_of_response() -> None:
    text = "Before block\n---outcome---\nstatus: done\n---end---\nAfter block"
    result = parse_outcome_block(text)
    assert result is not None
    assert result.fields["status"] == "done"


def test_parse_strips_markdown_code_fence() -> None:
    text = "---outcome---\n```yaml\nresult: clean\n```\n---end---"
    result = parse_outcome_block(text)
    assert result is not None
    assert result.fields == {"result": "clean"}


def test_parse_strips_plain_code_fence() -> None:
    text = "---outcome---\n```\nresult: clean\n```\n---end---"
    result = parse_outcome_block(text)
    assert result is not None
    assert result.fields == {"result": "clean"}


def test_parse_malformed_yaml_returns_invalid() -> None:
    text = "---outcome---\n: invalid: yaml: here\n---end---"
    result = parse_outcome_block(text)
    assert result is not None
    assert result.valid is False
    assert len(result.errors) > 0


def test_parse_non_mapping_yaml_returns_invalid() -> None:
    text = "---outcome---\n- item1\n- item2\n---end---"
    result = parse_outcome_block(text)
    assert result is not None
    assert result.valid is False
    assert any("mapping" in e for e in result.errors)


# ---------------------------------------------------------------------------
# parse_outcome_block — schema validation
# ---------------------------------------------------------------------------


def test_validate_enum_field_pass(simple_schema: OutcomeSchema) -> None:
    text = "---outcome---\nverdict: pass\nfindings_count: 0\nsummary: all good\n---end---"
    result = parse_outcome_block(text, simple_schema)
    assert result is not None
    assert result.valid is True
    assert result.errors == []


def test_validate_enum_field_invalid_value(simple_schema: OutcomeSchema) -> None:
    text = "---outcome---\nverdict: unknown\nfindings_count: 0\nsummary: test\n---end---"
    result = parse_outcome_block(text, simple_schema)
    assert result is not None
    assert result.valid is False
    assert any("verdict" in e for e in result.errors)


def test_validate_required_field_missing(simple_schema: OutcomeSchema) -> None:
    text = "---outcome---\nverdict: pass\nfindings_count: 2\n---end---"
    result = parse_outcome_block(text, simple_schema)
    assert result is not None
    assert result.valid is False
    assert any("summary" in e for e in result.errors)


def test_validate_wrong_type_for_number(simple_schema: OutcomeSchema) -> None:
    text = "---outcome---\nverdict: pass\nfindings_count: not_a_number\nsummary: ok\n---end---"
    result = parse_outcome_block(text, simple_schema)
    assert result is not None
    assert result.valid is False
    assert any("findings_count" in e for e in result.errors)


def test_validate_boolean_rejected_for_number_field(simple_schema: OutcomeSchema) -> None:
    text = "---outcome---\nverdict: pass\nfindings_count: true\nsummary: ok\n---end---"
    result = parse_outcome_block(text, simple_schema)
    assert result is not None
    assert result.valid is False
    assert any("findings_count" in e for e in result.errors)


def test_validate_boolean_field() -> None:
    schema = OutcomeSchema(
        fields={
            "passed": OutcomeField(type="boolean", description="did it pass"),
        }
    )
    text = "---outcome---\npassed: true\n---end---"
    result = parse_outcome_block(text, schema)
    assert result is not None
    assert result.valid is True
    assert result.fields["passed"] is True


def test_validate_boolean_field_wrong_type() -> None:
    schema = OutcomeSchema(
        fields={
            "passed": OutcomeField(type="boolean", description="did it pass"),
        }
    )
    text = "---outcome---\npassed: not_a_bool\n---end---"
    result = parse_outcome_block(text, schema)
    assert result is not None
    assert result.valid is False
    assert any("passed" in e for e in result.errors)


def test_optional_field_can_be_missing() -> None:
    schema = OutcomeSchema(
        fields={
            "required_field": OutcomeField(type="string", description="required"),
            "optional_field": OutcomeField(type="string", description="optional", required=False),
        }
    )
    text = "---outcome---\nrequired_field: present\n---end---"
    result = parse_outcome_block(text, schema)
    assert result is not None
    assert result.valid is True


def test_no_schema_always_valid_if_yaml_parses() -> None:
    text = "---outcome---\nanything: goes\n---end---"
    result = parse_outcome_block(text, schema=None)
    assert result is not None
    assert result.valid is True


# ---------------------------------------------------------------------------
# generate_outcome_instruction
# ---------------------------------------------------------------------------


def test_generate_instruction_contains_markers(simple_schema: OutcomeSchema) -> None:
    instruction = generate_outcome_instruction(simple_schema)
    assert "---outcome---" in instruction
    assert "---end---" in instruction


def test_generate_instruction_contains_field_names(simple_schema: OutcomeSchema) -> None:
    instruction = generate_outcome_instruction(simple_schema)
    assert "verdict" in instruction
    assert "findings_count" in instruction
    assert "summary" in instruction


def test_generate_instruction_enum_shows_values(simple_schema: OutcomeSchema) -> None:
    instruction = generate_outcome_instruction(simple_schema)
    assert "pass | fail | needs_changes" in instruction


def test_generate_instruction_number_hint(simple_schema: OutcomeSchema) -> None:
    instruction = generate_outcome_instruction(simple_schema)
    assert "<number>" in instruction


def test_generate_instruction_boolean_hint() -> None:
    schema = OutcomeSchema(fields={"ok": OutcomeField(type="boolean", description="ok flag")})
    instruction = generate_outcome_instruction(schema)
    assert "true | false" in instruction


def test_generate_instruction_string_uses_description() -> None:
    schema = OutcomeSchema(
        fields={"summary": OutcomeField(type="string", description="one-line summary")}
    )
    instruction = generate_outcome_instruction(schema)
    assert "<one-line summary>" in instruction


# ---------------------------------------------------------------------------
# Round-trip test
# ---------------------------------------------------------------------------


def test_outcome_round_trip(simple_schema: OutcomeSchema) -> None:
    instruction = generate_outcome_instruction(simple_schema)
    assert "---outcome---" in instruction

    agent_output = """
    I reviewed the code and found 3 minor issues. Overall the code is clean.

    ---outcome---
    verdict: pass
    findings_count: 3
    summary: Clean code with minor style suggestions
    ---end---
    """

    result = parse_outcome_block(agent_output, simple_schema)
    assert result is not None
    assert result.valid is True
    assert result.fields["verdict"] == "pass"
    assert result.fields["findings_count"] == 3
    assert isinstance(result.fields["summary"], str)


# ---------------------------------------------------------------------------
# BlockParserAdapter (hexagonal pattern)
# ---------------------------------------------------------------------------


def test_block_parser_adapter_implements_port() -> None:
    adapter = BlockParserAdapter()
    assert isinstance(adapter, OutcomeExtractorPort)


def test_block_parser_adapter_extract_returns_none_when_no_block() -> None:
    adapter = BlockParserAdapter()
    result = adapter.extract("No block here.")
    assert result is None


def test_block_parser_adapter_extract_with_schema(simple_schema: OutcomeSchema) -> None:
    adapter = BlockParserAdapter()
    text = "---outcome---\nverdict: fail\nfindings_count: 5\nsummary: Issues found\n---end---"
    result = adapter.extract(text, simple_schema)
    assert result is not None
    assert result.valid is True
    assert result.fields["verdict"] == "fail"


def test_block_parser_adapter_extract_without_schema() -> None:
    adapter = BlockParserAdapter()
    text = "---outcome---\nkey: value\n---end---"
    result = adapter.extract(text)
    assert result is not None
    assert result.fields == {"key": "value"}
