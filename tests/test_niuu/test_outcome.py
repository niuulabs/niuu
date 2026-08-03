"""Tests for outcome block parsing, validation, and instruction generation."""

from __future__ import annotations

import pytest

from niuu.adapters.outcome.block_parser import BlockParserAdapter
from niuu.domain.outcome import (
    OutcomeField,
    OutcomeSchema,
    _coerce_simple_scalar,
    _join_soft_wrapped_parts,
    _merge_soft_wrapped_key_line,
    _parse_soft_wrapped_mapping,
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


def test_parse_soft_wrapped_markers_with_spaced_letters() -> None:
    text = "--- o u t c o m e ---\nstatus: ok\n--- e n d ---"
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


def test_validate_array_and_object_fields_pass() -> None:
    schema = OutcomeSchema(
        fields={
            "signal_refs": OutcomeField(type="array", description="signal refs"),
            "correlation_ids": OutcomeField(type="object", description="correlations"),
        }
    )
    text = """\
---outcome---
signal_refs:
  - evt-1
  - evt-2
correlation_ids:
  root: corr-1
  task: task-1
---end---
"""
    result = parse_outcome_block(text, schema)
    assert result is not None
    assert result.valid is True
    assert result.fields["signal_refs"] == ["evt-1", "evt-2"]
    assert result.fields["correlation_ids"] == {"root": "corr-1", "task": "task-1"}


def test_validate_wrong_type_for_array_and_object_fields() -> None:
    schema = OutcomeSchema(
        fields={
            "signal_refs": OutcomeField(type="array", description="signal refs"),
            "correlation_ids": OutcomeField(type="object", description="correlations"),
        }
    )
    text = """\
---outcome---
signal_refs: evt-1
correlation_ids:
  - corr-1
---end---
"""
    result = parse_outcome_block(text, schema)
    assert result is not None
    assert result.valid is False
    assert any("signal_refs" in e and "array" in e for e in result.errors)
    assert any("correlation_ids" in e and "object" in e for e in result.errors)


def test_soft_wrapped_scalar_before_next_key_uses_schema_to_recover_verdict() -> None:
    schema = OutcomeSchema(
        fields={
            "verdict": OutcomeField(
                type="enum",
                description="workflow verdict",
                enum_values=["opinion_submitted", "review_submitted", "blocked"],
            ),
            "summary": OutcomeField(type="string", description="one-line summary"),
            "page_path": OutcomeField(type="string", description="written page path"),
        }
    )
    text = """\
---outcome---
ver
dict: opinion_sub
mitted
summary:
 Recommended lightweight human approval by
 default for final council publication
, with autonomous scratch
 work and risk-based graduation
 conditions.
page_path: council
/niu-906
-human-approval-gate
/opinions/opinion
-b.md
---
end---
"""
    result = parse_outcome_block(text, schema)
    assert result is not None
    assert result.valid is True
    assert result.fields["verdict"] == "opinion_submitted"
    assert result.fields["summary"].startswith("Recommended lightweight human approval")
    assert result.fields["page_path"] == "council/niu-906-human-approval-gate/opinions/opinion-b.md"


def test_soft_wrapped_manifest_path_is_compacted() -> None:
    schema = OutcomeSchema(
        fields={
            "verdict": OutcomeField(
                type="enum",
                description="publish verdict",
                enum_values=["published", "blocked"],
            ),
            "summary": OutcomeField(type="string", description="summary"),
            "manifest_path": OutcomeField(type="string", description="manifest path"),
        }
    )
    text = """\
---outcome---
verdict: published
summary: Delivery record is complete.
manifest_path: deliveries
/niu
-991
-validation
-force
-review
-loop
-add
-tr
oubles
hooting
-formatter
-helper
/40
-man
ifest
.md
---end---
"""
    result = parse_outcome_block(text, schema)
    assert result is not None
    assert result.valid is True
    assert (
        result.fields["manifest_path"]
        == "deliveries/niu-991-validation-force-review-loop-add-troubleshooting-formatter-helper/"
        "40-manifest.md"
    )


def test_schema_recovery_prefers_salvaged_wrapped_codex_outcome() -> None:
    schema = OutcomeSchema(
        fields={
            "verdict": OutcomeField(
                type="enum",
                description="workflow verdict",
                enum_values=["opinion_submitted", "review_submitted", "blocked"],
            ),
            "summary": OutcomeField(type="string", description="one-line summary"),
            "page_path": OutcomeField(type="string", description="written page path"),
        }
    )
    text = """\
---outcome---
ver
dict: opinion
_submitted
summary
: Wrote
 Opinion B recommending Q
wen3.
6-35B
-A3B as
 the Spark-first
 default, with Deep
Seek-V4
-Flash as the
 long-context research
 tier and larger models
 rejected for operational
 fit.
page_path
: council/
niu-929-local
-model-eval
/opinions/op
inion-b.md

---end---
"""
    result = parse_outcome_block(text, schema)
    assert result is not None
    assert result.valid is True
    assert result.fields["verdict"] == "opinion_submitted"
    assert result.fields["summary"].startswith("Wrote Opinion B recommending Qwen3. 6-35B-A3B")
    assert result.fields["page_path"] == "council/niu-929-local-model-eval/opinions/opinion-b.md"


def test_schema_recovery_does_not_replace_structured_yaml_on_equal_error_count() -> None:
    schema = OutcomeSchema(
        fields={
            "runtime_identity": OutcomeField(
                type="string",
                description="field supplied after model parsing",
            ),
            "question": OutcomeField(type="string", description="operator question"),
            "working_state": OutcomeField(type="object", description="resident state"),
            "optional_note": OutcomeField(
                type="string",
                description="optional note",
                required=False,
            ),
        }
    )
    text = """\
---outcome---
question:
working_state: {observations: [idle], hypotheses: [], unknowns: []}
---end---
"""

    result = parse_outcome_block(text, schema)

    assert result is not None
    assert result.valid is False
    assert result.fields["working_state"] == {
        "observations": ["idle"],
        "hypotheses": [],
        "unknowns": [],
    }
    assert result.errors == [
        "required field 'runtime_identity' is missing",
        "field 'question': expected string, got NoneType",
    ]


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


def test_join_soft_wrapped_parts_handles_punctuation_and_compact_keys() -> None:
    assert (
        _join_soft_wrapped_parts(["hello", ",", "world", "!", "(test", ")"])
        == "hello,world! (test)"
    )
    assert _join_soft_wrapped_parts(["opinion_", "submitted"], compact=True) == "opinion_submitted"


@pytest.mark.parametrize(
    ("key", "parts", "expected"),
    [
        ("flag", ["true"], True),
        ("flag", ["false"], False),
        ("count", ["-12"], -12),
        ("ratio", ["3.5"], 3.5),
        ("summary", ["hello", "world"], "hello world"),
        ("page_path", ["council", "/example", "/page", ".md"], "council/example/page.md"),
    ],
)
def test_coerce_simple_scalar_recovers_common_scalar_shapes(
    key: str,
    parts: list[str],
    expected: object,
) -> None:
    assert _coerce_simple_scalar(key, parts) == expected


def test_merge_soft_wrapped_key_line_handles_inline_colon_and_fragments() -> None:
    assert _merge_soft_wrapped_key_line(["summary: hello"], 0) == ("summary: hello", 1)
    assert _merge_soft_wrapped_key_line(["sum", "mary: hello"], 0) == ("summary: hello", 2)
    assert _merge_soft_wrapped_key_line(["verdict", ": pass"], 0) == ("verdict: pass", 2)
    assert _merge_soft_wrapped_key_line(["not a key", "ignored"], 0) == ("not a key", 1)


def test_parse_soft_wrapped_mapping_supports_lists_and_schema_guardrails() -> None:
    parsed = _parse_soft_wrapped_mapping(
        "\n".join(
            [
                "ver",
                "dict: needs_",
                "changes",
                "attempted:",
                "- checked the happy path",
                "continued evidence",
                "- inspected edge cases",
                "summary",
                ": still needs fixes",
            ]
        ),
        expected_keys={"verdict", "attempted", "summary"},
    )
    assert parsed == {
        "verdict": "needs_changes",
        "attempted": ["checked the happy path continued evidence", "inspected edge cases"],
        "summary": "still needs fixes",
    }


def test_parse_soft_wrapped_mapping_keeps_fragment_as_scalar_when_next_key_is_expected() -> None:
    parsed = _parse_soft_wrapped_mapping(
        "\n".join(
            [
                "summary: keep the overview",
                "page",
                "verdict: pass",
            ]
        ),
        expected_keys={"summary", "verdict"},
    )
    assert parsed == {
        "summary": "keep the overview page",
        "verdict": "pass",
    }


def test_parse_outcome_block_salvages_yaml_colons_in_scalar_values() -> None:
    schema = OutcomeSchema(
        fields={
            "environment_id": OutcomeField(type="string", description="environment id"),
            "valkyrie_id": OutcomeField(type="string", description="valkyrie id"),
            "signal_refs": OutcomeField(type="array", description="signal refs"),
            "tier": OutcomeField(
                type="enum",
                description="attention tier",
                enum_values=["silent", "ambient", "present", "urgent"],
            ),
            "confidence": OutcomeField(type="number", description="confidence"),
            "operational_state": OutcomeField(type="string", description="state"),
            "rationale": OutcomeField(type="string", description="rationale"),
            "evidence": OutcomeField(type="array", description="evidence"),
            "recommended_action": OutcomeField(type="string", description="action"),
            "action_authority": OutcomeField(
                type="enum",
                description="authority",
                enum_values=["autonomous", "yolo_allowed", "court_required"],
            ),
            "target_surfaces": OutcomeField(type="array", description="surfaces"),
            "expires_at": OutcomeField(type="string", description="expiry"),
            "dissent_refs": OutcomeField(type="array", description="dissent"),
            "correlation_ids": OutcomeField(type="object", description="correlation"),
        }
    )
    text = """\
---outcome---
environment_id: valhalla
valkyrie_id: k8s-valkyrie
signal_refs:
  - evt1
tier: urgent
confidence: 0.8
operational_state: degraded
rationale: Pod is in CrashLoopBackOff. Root cause: container fails on startup
evidence:
  - event_id: evt1
recommended_action: Inspect pod logs. Root cause: check container config
action_authority: autonomous
target_surfaces:
  - surface:ops
expires_at:
dissent_refs: []
correlation_ids:
  root: corr1
---end---
"""

    result = parse_outcome_block(text, schema)

    assert result is not None
    assert result.fields["environment_id"] == "valhalla"
    assert result.fields["rationale"].endswith("container fails on startup")
    assert result.fields["recommended_action"].endswith("check container config")
    assert result.fields["signal_refs"] == ["evt1"]


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


def test_generate_instruction_array_and_object_hints() -> None:
    schema = OutcomeSchema(
        fields={
            "items": OutcomeField(type="array", description="items"),
            "metadata": OutcomeField(type="object", description="metadata"),
        }
    )
    instruction = generate_outcome_instruction(schema)
    assert "items: [<item>, ...]" in instruction
    assert "metadata: {key: value}" in instruction


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


# ---------------------------------------------------------------------------
# Unquoted ``key: value`` inside a scalar (observed on Ivaldi, 2026-08-03)
# ---------------------------------------------------------------------------


def test_unquoted_colon_in_a_value_keeps_the_rest_of_the_structure() -> None:
    """One sloppy line must not discard an otherwise well-formed judgment.

    The model wrote ``result: completed: reviewed signals`` inside a nested
    list. That makes the whole document invalid YAML, and the flat fallback
    cannot represent nesting — so ``working_state`` used to arrive as ``''``
    and the resident was told its working_state was the wrong type, pointing
    the repair prompt at entirely the wrong line.
    """
    text = (
        "---outcome---\n"
        "decision: watch\n"
        "target_surfaces: []\n"
        "working_state:\n"
        "  objectives:\n"
        "    - description: Continue monitoring\n"
        "      reference: resident/continuation/cases/abc/latest.md\n"
        "  attempts:\n"
        "    - action: received operator input\n"
        "      result: completed: reviewed signals, all informational\n"
        "---end---"
    )

    result = parse_outcome_block(text)

    assert result is not None
    assert isinstance(result.fields["working_state"], dict)
    assert result.fields["working_state"]["objectives"][0]["description"] == "Continue monitoring"
    # The ambiguous value is preserved verbatim, not truncated at the colon.
    attempt = result.fields["working_state"]["attempts"][0]
    assert attempt["result"] == "completed: reviewed signals, all informational"
    # A real empty list, not the string "[]".
    assert result.fields["target_surfaces"] == []


def test_repair_leaves_urls_timestamps_and_quoted_values_alone() -> None:
    text = (
        "---outcome---\n"
        "evidence: generic://workshop-laevateinn/a8311607cd0b3950\n"
        "expires_at: 2026-08-03T10:30:00Z\n"
        "state_summary: 'watching: operator confirmed'\n"
        "note: plain value with a colon: needs quoting\n"
        "---end---"
    )

    result = parse_outcome_block(text)

    assert result is not None
    assert result.fields["evidence"] == "generic://workshop-laevateinn/a8311607cd0b3950"
    # YAML resolves this to a datetime on its own; the point is that the repair
    # did not quote it into a string first.
    assert not isinstance(result.fields["expires_at"], str)
    assert result.fields["state_summary"] == "watching: operator confirmed"
    assert result.fields["note"] == "plain value with a colon: needs quoting"


def test_a_block_that_is_genuinely_broken_still_reports_a_parse_error() -> None:
    """The repair must not launder unparseable text into a fake success."""
    text = "---outcome---\n\t[unclosed\n  - stray\n---end---"

    result = parse_outcome_block(text)

    assert result is not None
    assert result.valid is False
    assert any("YAML parse error" in error for error in result.errors)


def test_a_stray_trailing_start_marker_does_not_discard_the_block() -> None:
    """Observed on Ivaldi: the model finished its block, then emitted a bare
    ``---outcome---`` as its last line with nothing after it.

    The marker is not YAML, so the whole document failed to parse and a complete
    judgment was thrown away — the second distinct cause of the reject loop.
    """
    text = (
        "---outcome---\n"
        "decision: investigate\n"
        "working_state:\n"
        "  objectives:\n"
        "    - Determine printer status\n"
        "---outcome---\n"
    )

    result = parse_outcome_block(text)

    assert result is not None
    assert result.fields["decision"] == "investigate"
    assert isinstance(result.fields["working_state"], dict)
    assert result.fields["working_state"]["objectives"] == ["Determine printer status"]


def test_a_restarted_block_prefers_the_later_attempt() -> None:
    text = (
        "---outcome---\n"
        "decision: watch\n"
        "working_state:\n"
        "  objectives:\n"
        "    - first attempt, abandoned\n"
        "---outcome---\n"
        "decision: investigate\n"
        "working_state:\n"
        "  objectives:\n"
        "    - second attempt, complete\n"
        "---end---"
    )

    result = parse_outcome_block(text)

    assert result is not None
    assert result.fields["decision"] == "investigate"
    assert result.fields["working_state"]["objectives"] == ["second attempt, complete"]
