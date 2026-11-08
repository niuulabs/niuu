"""Tests for persona event contracts — produces, consumes, fan_in, and outcome injection.

Covers NIU-593: PersonaConfig extended with event contract fields.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from niuu.domain.outcome import OutcomeField
from personas.adapters.loader import (
    _BUILTIN_PERSONAS_DIR,
    FilesystemPersonaAdapter,
)
from personas.domain.persona import (
    PersonaConfig,
    PersonaConsumes,
    PersonaFanIn,
    PersonaProduces,
    apply_outcome_instruction,
    parse_consumes,
    parse_fan_in,
    parse_persona,
    parse_produces,
)

# ---------------------------------------------------------------------------
# YAML fixtures — specialist personas
# ---------------------------------------------------------------------------

_REVIEWER_YAML = """\
name: reviewer
system_prompt_template: |
  You are a code reviewer.
allowed_tools: [file, git]
permission_mode: read-only
produces:
  event_type: review.completed
  schema:
    verdict:
      type: enum
      values: [pass, fail, needs_changes]
    findings_count:
      type: number
    critical_count:
      type: number
    summary:
      type: string
consumes:
  event_types: [code.changed, review.requested]
  injects: [repo, branch, diff_url]
  schema:
    topic:
      type: string
    max_sources:
      type: number
fan_in:
  strategy: all_must_pass
  contributes_to: review.verdict
"""

_SECURITY_AUDITOR_YAML = """\
name: security-auditor
system_prompt_template: You are a security auditor.
produces:
  event_type: security.completed
  schema:
    verdict:
      type: enum
      values: [pass, fail, needs_review]
    critical_findings:
      type: number
    summary:
      type: string
consumes:
  event_types: [code.changed]
  injects: [repo, branch]
fan_in:
  strategy: all_must_pass
  contributes_to: review.verdict
"""

_QA_AGENT_YAML = """\
name: qa-agent
system_prompt_template: You are a QA agent.
produces:
  event_type: qa.completed
  schema:
    verdict:
      type: enum
      values: [pass, fail]
    tests_run:
      type: number
    tests_failed:
      type: number
    summary:
      type: string
consumes:
  event_types: [review.completed, test.requested]
  injects: [repo, branch, previous_verdicts]
"""

_SHIP_AGENT_YAML = """\
name: ship-agent
system_prompt_template: You are a ship agent.
produces:
  event_type: ship.completed
  schema:
    verdict:
      type: enum
      values: [shipped, blocked]
    version:
      type: string
    pr_url:
      type: string
    summary:
      type: string
consumes:
  event_types: [qa.completed, ship.requested]
  injects: [repo, branch, previous_verdicts]
"""

_RETRO_ANALYST_YAML = """\
name: retro-analyst
system_prompt_template: You are a retro analyst.
produces:
  event_type: retro.completed
  schema:
    items_shipped:
      type: number
    patterns_found:
      type: number
    summary:
      type: string
consumes:
  event_types: [retro.requested, cron.weekly]
"""

_NO_CONTRACT_YAML = """\
name: simple-agent
system_prompt_template: You are a simple agent.
allowed_tools: [file]
permission_mode: read-only
"""


# ---------------------------------------------------------------------------
# PersonaProduces / PersonaConsumes / PersonaFanIn dataclass defaults
# ---------------------------------------------------------------------------


class TestPersonaContractDefaults:
    def test_produces_defaults(self) -> None:
        p = PersonaProduces()
        assert p.event_type == ""
        assert p.schema == {}

    def test_consumes_defaults(self) -> None:
        c = PersonaConsumes()
        assert c.event_types == []
        assert c.injects == []
        assert c.schema == {}

    def test_fan_in_defaults(self) -> None:
        f = PersonaFanIn()
        assert f.strategy == "merge"
        assert f.contributes_to == ""

    def test_persona_config_contract_defaults(self) -> None:
        cfg = PersonaConfig(name="x")
        assert cfg.produces.event_type == ""
        assert cfg.produces.schema == {}
        assert cfg.consumes.event_types == []
        assert cfg.fan_in.strategy == "merge"


# ---------------------------------------------------------------------------
# parse_produces
# ---------------------------------------------------------------------------


class TestParseProduces:
    def test_none_returns_empty(self) -> None:
        p = parse_produces(None)
        assert p.event_type == ""
        assert p.schema == {}

    def test_non_dict_returns_empty(self) -> None:
        p = parse_produces("invalid")
        assert p.event_type == ""
        assert p.schema == {}

    def test_parses_event_type(self) -> None:
        p = parse_produces({"event_type": "review.completed", "schema": {}})
        assert p.event_type == "review.completed"

    def test_parses_enum_field(self) -> None:
        raw = {
            "event_type": "x",
            "schema": {
                "verdict": {
                    "type": "enum",
                    "values": ["pass", "fail"],
                }
            },
        }
        p = parse_produces(raw)
        assert "verdict" in p.schema
        f = p.schema["verdict"]
        assert f.type == "enum"
        assert f.enum_values == ["pass", "fail"]

    def test_parses_number_field(self) -> None:
        raw = {"schema": {"count": {"type": "number"}}}
        p = parse_produces(raw)
        assert p.schema["count"].type == "number"

    def test_parses_string_field(self) -> None:
        raw = {"schema": {"summary": {"type": "string"}}}
        p = parse_produces(raw)
        assert p.schema["summary"].type == "string"

    def test_field_description_defaults_to_field_name(self) -> None:
        raw = {"schema": {"my_field": {"type": "string"}}}
        p = parse_produces(raw)
        assert p.schema["my_field"].description == "my_field"

    def test_field_description_from_yaml(self) -> None:
        raw = {"schema": {"summary": {"type": "string", "description": "a summary"}}}
        p = parse_produces(raw)
        assert p.schema["summary"].description == "a summary"

    def test_field_required_defaults_true(self) -> None:
        raw = {"schema": {"x": {"type": "string"}}}
        p = parse_produces(raw)
        assert p.schema["x"].required is True

    def test_field_required_false(self) -> None:
        raw = {"schema": {"x": {"type": "string", "required": False}}}
        p = parse_produces(raw)
        assert p.schema["x"].required is False

    def test_invalid_field_value_skipped(self) -> None:
        raw = {"schema": {"good": {"type": "string"}, "bad": "not-a-dict"}}
        p = parse_produces(raw)
        assert "good" in p.schema
        assert "bad" not in p.schema

    def test_missing_schema_section(self) -> None:
        p = parse_produces({"event_type": "x"})
        assert p.schema == {}


# ---------------------------------------------------------------------------
# parse_consumes
# ---------------------------------------------------------------------------


class TestParseConsumes:
    def test_none_returns_empty(self) -> None:
        c = parse_consumes(None)
        assert c.event_types == []
        assert c.injects == []

    def test_non_dict_returns_empty(self) -> None:
        c = parse_consumes("bad")
        assert c.event_types == []

    def test_parses_event_types(self) -> None:
        c = parse_consumes({"event_types": ["code.changed", "review.requested"]})
        assert c.event_types == ["code.changed", "review.requested"]

    def test_parses_injects(self) -> None:
        c = parse_consumes({"injects": ["repo", "branch"]})
        assert c.injects == ["repo", "branch"]

    def test_parses_schema(self) -> None:
        c = parse_consumes({"schema": {"topic": {"type": "string"}}})
        assert "topic" in c.schema
        assert c.schema["topic"].type == "string"

    def test_non_list_event_types_becomes_empty(self) -> None:
        c = parse_consumes({"event_types": "not-a-list"})
        assert c.event_types == []

    def test_non_list_injects_becomes_empty(self) -> None:
        c = parse_consumes({"injects": 42})
        assert c.injects == []

    def test_non_dict_schema_becomes_empty(self) -> None:
        c = parse_consumes({"schema": 42})
        assert c.schema == {}


# ---------------------------------------------------------------------------
# parse_fan_in
# ---------------------------------------------------------------------------


class TestParseFanIn:
    def test_none_returns_defaults(self) -> None:
        f = parse_fan_in(None)
        assert f.strategy == "merge"
        assert f.contributes_to == ""

    def test_non_mapping_is_refused(self) -> None:
        with pytest.raises(ValueError, match="fan_in must be a mapping"):
            parse_fan_in("bad")

    def test_parses_strategy(self) -> None:
        f = parse_fan_in({"strategy": "all_must_pass"})
        assert f.strategy == "all_must_pass"

    def test_parses_contributes_to(self) -> None:
        f = parse_fan_in({"contributes_to": "review.verdict"})
        assert f.contributes_to == "review.verdict"

    def test_invalid_strategy_is_refused_with_the_valid_ones(self) -> None:
        with pytest.raises(ValueError, match="'unknown_strategy'") as excinfo:
            parse_fan_in({"strategy": "unknown_strategy"})
        for strategy in ("all_must_pass", "any_pass", "majority", "merge"):
            assert strategy in str(excinfo.value)

    def test_all_valid_strategies(self) -> None:
        for strategy in ("all_must_pass", "any_pass", "majority", "merge"):
            f = parse_fan_in({"strategy": strategy})
            assert f.strategy == strategy


# ---------------------------------------------------------------------------
# parse_persona — new sections
# ---------------------------------------------------------------------------


class TestFilesystemPersonaAdapterParseContracts:
    def test_reviewer_yaml_parses_produces(self) -> None:
        cfg = parse_persona(_REVIEWER_YAML)
        assert cfg is not None
        assert cfg.produces.event_type == "review.completed"
        assert "verdict" in cfg.produces.schema
        assert cfg.produces.schema["verdict"].enum_values == ["pass", "fail", "needs_changes"]
        assert "findings_count" in cfg.produces.schema
        assert "critical_count" in cfg.produces.schema
        assert "summary" in cfg.produces.schema

    def test_reviewer_yaml_parses_consumes(self) -> None:
        cfg = parse_persona(_REVIEWER_YAML)
        assert cfg is not None
        assert "code.changed" in cfg.consumes.event_types
        assert "review.requested" in cfg.consumes.event_types
        assert "repo" in cfg.consumes.injects
        assert "branch" in cfg.consumes.injects
        assert "diff_url" in cfg.consumes.injects
        assert "topic" in cfg.consumes.schema
        assert cfg.consumes.schema["topic"].type == "string"
        assert "max_sources" in cfg.consumes.schema
        assert cfg.consumes.schema["max_sources"].type == "number"

    def test_reviewer_yaml_parses_fan_in(self) -> None:
        cfg = parse_persona(_REVIEWER_YAML)
        assert cfg is not None
        assert cfg.fan_in.strategy == "all_must_pass"
        assert cfg.fan_in.contributes_to == "review.verdict"

    def test_security_auditor_yaml_parses_contract(self) -> None:
        cfg = parse_persona(_SECURITY_AUDITOR_YAML)
        assert cfg is not None
        assert cfg.produces.event_type == "security.completed"
        assert "verdict" in cfg.produces.schema
        assert cfg.produces.schema["verdict"].enum_values == ["pass", "fail", "needs_review"]
        assert "code.changed" in cfg.consumes.event_types
        assert cfg.fan_in.strategy == "all_must_pass"

    def test_persona_without_contract_keeps_defaults(self) -> None:
        cfg = parse_persona(_NO_CONTRACT_YAML)
        assert cfg is not None
        assert cfg.produces.event_type == ""
        assert cfg.produces.schema == {}
        assert cfg.consumes.event_types == []
        assert cfg.fan_in.strategy == "merge"


# ---------------------------------------------------------------------------
# apply_outcome_instruction
# ---------------------------------------------------------------------------


class TestApplyOutcomeInstruction:
    def test_no_schema_returns_persona_unchanged(self) -> None:
        persona = PersonaConfig(name="x", system_prompt_template="hello")
        result = apply_outcome_instruction(persona)
        assert result is persona  # same object, not a copy

    def test_with_schema_appends_instruction(self) -> None:
        persona = PersonaConfig(
            name="x",
            system_prompt_template="hello",
            produces=PersonaProduces(
                event_type="x.done",
                schema={"summary": OutcomeField(type="string", description="summary")},
            ),
        )
        result = apply_outcome_instruction(persona)
        assert result is not persona
        assert "---outcome---" in result.system_prompt_template
        assert "---end---" in result.system_prompt_template

    def test_with_enum_schema_includes_values_in_instruction(self) -> None:
        persona = PersonaConfig(
            name="x",
            system_prompt_template="base",
            produces=PersonaProduces(
                event_type="x.done",
                schema={
                    "verdict": OutcomeField(
                        type="enum",
                        description="verdict",
                        enum_values=["pass", "fail"],
                    )
                },
            ),
        )
        result = apply_outcome_instruction(persona)
        assert "pass | fail" in result.system_prompt_template

    def test_original_prompt_preserved_before_instruction(self) -> None:
        persona = PersonaConfig(
            name="x",
            system_prompt_template="original prompt",
            produces=PersonaProduces(
                event_type="x.done",
                schema={"v": OutcomeField(type="string", description="v")},
            ),
        )
        result = apply_outcome_instruction(persona)
        assert result.system_prompt_template.startswith("original prompt")

    def test_other_fields_unchanged(self) -> None:
        persona = PersonaConfig(
            name="x",
            allowed_tools=["file"],
            permission_mode="read-only",
            produces=PersonaProduces(
                event_type="x.done",
                schema={"v": OutcomeField(type="string", description="v")},
            ),
        )
        result = apply_outcome_instruction(persona)
        assert result.allowed_tools == ["file"]
        assert result.permission_mode == "read-only"


# ---------------------------------------------------------------------------
# FilesystemPersonaAdapter.load — outcome injection
# ---------------------------------------------------------------------------


class TestFilesystemPersonaAdapterLoadInjection:
    def test_reviewer_builtin_has_outcome_instruction(self) -> None:
        loader = FilesystemPersonaAdapter()
        persona = loader.load("reviewer")
        assert persona is not None
        assert "---outcome---" in persona.system_prompt_template
        assert "---end---" in persona.system_prompt_template

    def test_reviewer_outcome_includes_verdict_field(self) -> None:
        loader = FilesystemPersonaAdapter()
        persona = loader.load("reviewer")
        assert persona is not None
        assert "verdict:" in persona.system_prompt_template
        assert "pass | fail | needs_changes" in persona.system_prompt_template

    def test_coder_has_outcome_instruction(self) -> None:
        loader = FilesystemPersonaAdapter()
        persona = loader.load("coder")
        assert persona is not None
        assert "---outcome---" in persona.system_prompt_template
        assert "pass | blocked | help_needed | fail" in persona.system_prompt_template
        assert "files_changed" in persona.system_prompt_template

    def test_security_auditor_has_outcome_instruction(self) -> None:
        loader = FilesystemPersonaAdapter()
        persona = loader.load("security-auditor")
        assert persona is not None
        assert "---outcome---" in persona.system_prompt_template
        assert "pass | fail | needs_review" in persona.system_prompt_template

    def test_file_persona_with_contract_gets_injection(self, tmp_path: Path) -> None:
        p = tmp_path / "my-persona.yaml"
        p.write_text(_REVIEWER_YAML, encoding="utf-8")
        loader = FilesystemPersonaAdapter([str(tmp_path)])
        persona = loader.load("my-persona")
        assert persona is not None
        assert "---outcome---" in persona.system_prompt_template

    def test_file_persona_without_contract_no_injection(self, tmp_path: Path) -> None:
        p = tmp_path / "simple.yaml"
        p.write_text(_NO_CONTRACT_YAML, encoding="utf-8")
        loader = FilesystemPersonaAdapter([str(tmp_path)])
        persona = loader.load("simple")
        assert persona is not None
        assert "---outcome---" not in persona.system_prompt_template


# ---------------------------------------------------------------------------
# FilesystemPersonaAdapter E2E — matches delivery criteria
# ---------------------------------------------------------------------------


class TestPersonaContractE2E:
    def test_persona_without_contract_unchanged(self) -> None:
        """Loading ticket-coordinator (no produces) → no outcome block in system prompt."""
        loader = FilesystemPersonaAdapter()
        persona = loader.load("ticket-coordinator")
        assert persona is not None
        assert "---outcome---" not in persona.system_prompt_template

    def test_persona_outcome_instruction_injected(self) -> None:
        """Loading reviewer persona → effective system prompt ends with outcome block."""
        loader = FilesystemPersonaAdapter()
        persona = loader.load("reviewer")
        assert persona is not None
        assert "---outcome---" in persona.system_prompt_template
        assert "verdict:" in persona.system_prompt_template
        assert "pass | fail | needs_changes" in persona.system_prompt_template


# ---------------------------------------------------------------------------
# FilesystemPersonaAdapter.find_consumers / find_producers
# ---------------------------------------------------------------------------


class TestFindConsumersProducers:
    def test_find_consumers_code_changed_returns_reviewer_and_security_auditor(self) -> None:
        loader = FilesystemPersonaAdapter()
        consumers = loader.find_consumers("code.changed")
        names = [p.name for p in consumers]
        assert "reviewer" in names
        assert "security-auditor" in names

    def test_find_consumers_review_completed_returns_postmortem_analyst(self) -> None:
        loader = FilesystemPersonaAdapter()
        consumers = loader.find_consumers("review.completed")
        names = [p.name for p in consumers]
        assert names == ["postmortem-analyst"]

    def test_find_consumers_unknown_event_returns_empty(self) -> None:
        loader = FilesystemPersonaAdapter()
        consumers = loader.find_consumers("does.not.exist")
        assert consumers == []

    def test_find_producers_review_completed_returns_reviewer(self) -> None:
        loader = FilesystemPersonaAdapter()
        producers = loader.find_producers("review.completed")
        names = [p.name for p in producers]
        assert "reviewer" in names

    def test_find_producers_unknown_event_returns_empty(self) -> None:
        loader = FilesystemPersonaAdapter()
        producers = loader.find_producers("no.such.event")
        assert producers == []

    def test_find_consumers_returns_personas_with_injected_instructions(self) -> None:
        loader = FilesystemPersonaAdapter()
        consumers = loader.find_consumers("code.changed")
        for persona in consumers:
            if persona.produces.schema:
                assert "---outcome---" in persona.system_prompt_template

    def test_find_consumers_with_custom_dir_includes_file_personas(self, tmp_path: Path) -> None:
        p = tmp_path / "custom-reviewer.yaml"
        p.write_text(_REVIEWER_YAML.replace("name: reviewer", "name: custom-reviewer"), "utf-8")
        loader = FilesystemPersonaAdapter([str(tmp_path)])
        consumers = loader.find_consumers("code.changed")
        names = [p.name for p in consumers]
        # Built-in reviewer still present, plus our custom one from file
        assert "reviewer" in names
        assert "custom-reviewer" in names


# ---------------------------------------------------------------------------
# Built-in specialist personas — contract fields
# ---------------------------------------------------------------------------


class TestBuiltinSpecialistContracts:
    _loader = FilesystemPersonaAdapter()

    def _load(self, name: str) -> PersonaConfig:
        path = _BUILTIN_PERSONAS_DIR / f"{name}.yaml"
        cfg = self._loader.load_from_file(path)
        assert cfg is not None, f"Failed to load built-in persona {name!r}"
        return cfg

    def test_reviewer_builtin_produces_review_completed(self) -> None:
        cfg = self._load("reviewer")
        assert cfg.produces.event_type == "review.completed"
        assert "verdict" in cfg.produces.schema
        assert "findings_count" in cfg.produces.schema
        assert "critical_count" in cfg.produces.schema

    def test_reviewer_builtin_consumes_code_changed(self) -> None:
        cfg = self._load("reviewer")
        assert "code.changed" in cfg.consumes.event_types
        assert "review.requested" in cfg.consumes.event_types

    def test_reviewer_fan_in_all_must_pass(self) -> None:
        cfg = self._load("reviewer")
        assert cfg.fan_in.strategy == "all_must_pass"
        assert cfg.fan_in.contributes_to == "review.verdict"

    def test_security_auditor_builtin_produces_security_completed(self) -> None:
        cfg = self._load("security-auditor")
        assert cfg.produces.event_type == "security.completed"
        assert "verdict" in cfg.produces.schema
        assert "critical_findings" in cfg.produces.schema

    def test_security_auditor_fan_in_all_must_pass(self) -> None:
        cfg = self._load("security-auditor")
        assert cfg.fan_in.strategy == "all_must_pass"

    def test_capability_publisher_uses_json_artifact_publisher(self) -> None:
        cfg = self._load("capability-publisher")

        assert "mimir_publish_artifact" in cfg.allowed_tools
        assert "mimir_write" not in cfg.allowed_tools
        assert "mimir_publish_artifact capabilities/{slug}/learned_tool.json" in (
            cfg.system_prompt_template
        )

    def test_all_specialist_personas_have_system_prompts(self) -> None:
        specialist_names = ["reviewer", "security-auditor"]
        for name in specialist_names:
            cfg = self._load(name)
            assert cfg.system_prompt_template, f"{name} missing system_prompt_template"

    def test_all_specialist_personas_have_positive_budgets(self) -> None:
        specialist_names = ["reviewer", "security-auditor"]
        for name in specialist_names:
            cfg = self._load(name)
            assert cfg.iteration_budget > 0, f"{name} has non-positive iteration_budget"
