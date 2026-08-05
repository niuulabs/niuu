"""Tests for persona event contracts — produces, consumes, fan_in, and outcome injection.

Covers NIU-593: PersonaConfig extended with event contract fields.
"""

from __future__ import annotations

from pathlib import Path

from niuu.domain.outcome import OutcomeField
from ravn.adapters.personas.loader import (
    _ARCHIVED_BUILTIN_PERSONAS_DIR,
    _BUILTIN_PERSONAS_DIR,
    FilesystemPersonaAdapter,
    PersonaConfig,
    PersonaConsumes,
    PersonaFanIn,
    PersonaProduces,
    _apply_outcome_instruction,
    _parse_consumes,
    _parse_fan_in,
    _parse_produces,
)
from ravn.domain.valkyrie_contracts import VALKYRIE_CONTINUATION_ORDER
from ravn.drive_loop import _validate_resident_continuation_contract

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
# _parse_produces
# ---------------------------------------------------------------------------


class TestParseProduces:
    def test_none_returns_empty(self) -> None:
        p = _parse_produces(None)
        assert p.event_type == ""
        assert p.schema == {}

    def test_non_dict_returns_empty(self) -> None:
        p = _parse_produces("invalid")
        assert p.event_type == ""
        assert p.schema == {}

    def test_parses_event_type(self) -> None:
        p = _parse_produces({"event_type": "review.completed", "schema": {}})
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
        p = _parse_produces(raw)
        assert "verdict" in p.schema
        f = p.schema["verdict"]
        assert f.type == "enum"
        assert f.enum_values == ["pass", "fail"]

    def test_parses_number_field(self) -> None:
        raw = {"schema": {"count": {"type": "number"}}}
        p = _parse_produces(raw)
        assert p.schema["count"].type == "number"

    def test_parses_string_field(self) -> None:
        raw = {"schema": {"summary": {"type": "string"}}}
        p = _parse_produces(raw)
        assert p.schema["summary"].type == "string"

    def test_field_description_defaults_to_field_name(self) -> None:
        raw = {"schema": {"my_field": {"type": "string"}}}
        p = _parse_produces(raw)
        assert p.schema["my_field"].description == "my_field"

    def test_field_description_from_yaml(self) -> None:
        raw = {"schema": {"summary": {"type": "string", "description": "a summary"}}}
        p = _parse_produces(raw)
        assert p.schema["summary"].description == "a summary"

    def test_field_required_defaults_true(self) -> None:
        raw = {"schema": {"x": {"type": "string"}}}
        p = _parse_produces(raw)
        assert p.schema["x"].required is True

    def test_field_required_false(self) -> None:
        raw = {"schema": {"x": {"type": "string", "required": False}}}
        p = _parse_produces(raw)
        assert p.schema["x"].required is False

    def test_invalid_field_value_skipped(self) -> None:
        raw = {"schema": {"good": {"type": "string"}, "bad": "not-a-dict"}}
        p = _parse_produces(raw)
        assert "good" in p.schema
        assert "bad" not in p.schema

    def test_missing_schema_section(self) -> None:
        p = _parse_produces({"event_type": "x"})
        assert p.schema == {}


# ---------------------------------------------------------------------------
# _parse_consumes
# ---------------------------------------------------------------------------


class TestParseConsumes:
    def test_none_returns_empty(self) -> None:
        c = _parse_consumes(None)
        assert c.event_types == []
        assert c.injects == []

    def test_non_dict_returns_empty(self) -> None:
        c = _parse_consumes("bad")
        assert c.event_types == []

    def test_parses_event_types(self) -> None:
        c = _parse_consumes({"event_types": ["code.changed", "review.requested"]})
        assert c.event_types == ["code.changed", "review.requested"]

    def test_parses_injects(self) -> None:
        c = _parse_consumes({"injects": ["repo", "branch"]})
        assert c.injects == ["repo", "branch"]

    def test_parses_schema(self) -> None:
        c = _parse_consumes({"schema": {"topic": {"type": "string"}}})
        assert "topic" in c.schema
        assert c.schema["topic"].type == "string"

    def test_non_list_event_types_becomes_empty(self) -> None:
        c = _parse_consumes({"event_types": "not-a-list"})
        assert c.event_types == []

    def test_non_list_injects_becomes_empty(self) -> None:
        c = _parse_consumes({"injects": 42})
        assert c.injects == []

    def test_non_dict_schema_becomes_empty(self) -> None:
        c = _parse_consumes({"schema": 42})
        assert c.schema == {}


# ---------------------------------------------------------------------------
# _parse_fan_in
# ---------------------------------------------------------------------------


class TestParseFanIn:
    def test_none_returns_defaults(self) -> None:
        f = _parse_fan_in(None)
        assert f.strategy == "merge"
        assert f.contributes_to == ""

    def test_non_dict_returns_defaults(self) -> None:
        f = _parse_fan_in("bad")
        assert f.strategy == "merge"

    def test_parses_strategy(self) -> None:
        f = _parse_fan_in({"strategy": "all_must_pass"})
        assert f.strategy == "all_must_pass"

    def test_parses_contributes_to(self) -> None:
        f = _parse_fan_in({"contributes_to": "review.verdict"})
        assert f.contributes_to == "review.verdict"

    def test_invalid_strategy_defaults_to_merge(self) -> None:
        f = _parse_fan_in({"strategy": "unknown_strategy"})
        assert f.strategy == "merge"

    def test_all_valid_strategies(self) -> None:
        for strategy in ("all_must_pass", "any_pass", "majority", "merge"):
            f = _parse_fan_in({"strategy": strategy})
            assert f.strategy == strategy


# ---------------------------------------------------------------------------
# FilesystemPersonaAdapter.parse — new sections
# ---------------------------------------------------------------------------


class TestFilesystemPersonaAdapterParseContracts:
    def test_reviewer_yaml_parses_produces(self) -> None:
        cfg = FilesystemPersonaAdapter.parse(_REVIEWER_YAML)
        assert cfg is not None
        assert cfg.produces.event_type == "review.completed"
        assert "verdict" in cfg.produces.schema
        assert cfg.produces.schema["verdict"].enum_values == ["pass", "fail", "needs_changes"]
        assert "findings_count" in cfg.produces.schema
        assert "critical_count" in cfg.produces.schema
        assert "summary" in cfg.produces.schema

    def test_reviewer_yaml_parses_consumes(self) -> None:
        cfg = FilesystemPersonaAdapter.parse(_REVIEWER_YAML)
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
        cfg = FilesystemPersonaAdapter.parse(_REVIEWER_YAML)
        assert cfg is not None
        assert cfg.fan_in.strategy == "all_must_pass"
        assert cfg.fan_in.contributes_to == "review.verdict"

    def test_security_auditor_yaml_parses_contract(self) -> None:
        cfg = FilesystemPersonaAdapter.parse(_SECURITY_AUDITOR_YAML)
        assert cfg is not None
        assert cfg.produces.event_type == "security.completed"
        assert "verdict" in cfg.produces.schema
        assert cfg.produces.schema["verdict"].enum_values == ["pass", "fail", "needs_review"]
        assert "code.changed" in cfg.consumes.event_types
        assert cfg.fan_in.strategy == "all_must_pass"

    def test_qa_agent_yaml_parses_contract(self) -> None:
        cfg = FilesystemPersonaAdapter.parse(_QA_AGENT_YAML)
        assert cfg is not None
        assert cfg.produces.event_type == "qa.completed"
        assert cfg.produces.schema["verdict"].enum_values == ["pass", "fail"]
        assert "tests_run" in cfg.produces.schema
        assert "tests_failed" in cfg.produces.schema
        assert "review.completed" in cfg.consumes.event_types
        assert "test.requested" in cfg.consumes.event_types

    def test_ship_agent_yaml_parses_contract(self) -> None:
        cfg = FilesystemPersonaAdapter.parse(_SHIP_AGENT_YAML)
        assert cfg is not None
        assert cfg.produces.event_type == "ship.completed"
        assert cfg.produces.schema["verdict"].enum_values == ["shipped", "blocked"]
        assert "version" in cfg.produces.schema
        assert "pr_url" in cfg.produces.schema
        assert "qa.completed" in cfg.consumes.event_types

    def test_retro_analyst_yaml_parses_contract(self) -> None:
        cfg = FilesystemPersonaAdapter.parse(_RETRO_ANALYST_YAML)
        assert cfg is not None
        assert cfg.produces.event_type == "retro.completed"
        assert "items_shipped" in cfg.produces.schema
        assert "patterns_found" in cfg.produces.schema
        assert "retro.requested" in cfg.consumes.event_types
        assert "cron.weekly" in cfg.consumes.event_types
        # No fan_in section → defaults
        assert cfg.fan_in.strategy == "merge"

    def test_persona_without_contract_keeps_defaults(self) -> None:
        cfg = FilesystemPersonaAdapter.parse(_NO_CONTRACT_YAML)
        assert cfg is not None
        assert cfg.produces.event_type == ""
        assert cfg.produces.schema == {}
        assert cfg.consumes.event_types == []
        assert cfg.fan_in.strategy == "merge"


# ---------------------------------------------------------------------------
# _apply_outcome_instruction
# ---------------------------------------------------------------------------


class TestApplyOutcomeInstruction:
    def test_no_schema_returns_persona_unchanged(self) -> None:
        persona = PersonaConfig(name="x", system_prompt_template="hello")
        result = _apply_outcome_instruction(persona)
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
        result = _apply_outcome_instruction(persona)
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
        result = _apply_outcome_instruction(persona)
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
        result = _apply_outcome_instruction(persona)
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
        result = _apply_outcome_instruction(persona)
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

    def test_coding_agent_has_outcome_instruction(self) -> None:
        """coding-agent now produces code.changed events with outcome schema."""
        loader = FilesystemPersonaAdapter()
        persona = loader.load("coding-agent")
        assert persona is not None
        assert "---outcome---" in persona.system_prompt_template
        assert "files_changed" in persona.system_prompt_template
        assert "summary" in persona.system_prompt_template

    def test_security_auditor_has_outcome_instruction(self) -> None:
        loader = FilesystemPersonaAdapter()
        persona = loader.load("security-auditor")
        assert persona is not None
        assert "---outcome---" in persona.system_prompt_template
        assert "pass | fail | needs_review" in persona.system_prompt_template

    def test_qa_agent_has_outcome_instruction(self) -> None:
        loader = FilesystemPersonaAdapter()
        persona = loader.load("qa-agent")
        assert persona is not None
        assert "---outcome---" in persona.system_prompt_template
        assert "tests_run" in persona.system_prompt_template

    def test_ship_agent_has_outcome_instruction(self) -> None:
        loader = FilesystemPersonaAdapter()
        persona = loader.load("ship-agent")
        assert persona is not None
        assert "---outcome---" in persona.system_prompt_template
        assert "shipped | blocked" in persona.system_prompt_template

    def test_retro_analyst_has_outcome_instruction(self) -> None:
        loader = FilesystemPersonaAdapter()
        persona = loader.load("retro-analyst")
        assert persona is not None
        assert "---outcome---" in persona.system_prompt_template
        assert "items_shipped" in persona.system_prompt_template

    def test_mimir_curator_has_outcome_instruction(self) -> None:
        loader = FilesystemPersonaAdapter()
        persona = loader.load("mimir-curator")
        assert persona is not None
        assert "---outcome---" in persona.system_prompt_template

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
    def test_persona_outcome_instruction_injected(self) -> None:
        """Loading reviewer persona → effective system prompt ends with outcome block."""
        loader = FilesystemPersonaAdapter()
        persona = loader.load("reviewer")
        assert persona is not None
        assert "---outcome---" in persona.system_prompt_template
        assert "verdict:" in persona.system_prompt_template
        assert "pass | fail | needs_changes" in persona.system_prompt_template

    def test_persona_without_contract_unchanged(self) -> None:
        """Loading research-agent (no produces) → no outcome block in system prompt."""
        loader = FilesystemPersonaAdapter()
        persona = loader.load("research-agent")
        assert persona is not None
        assert "---outcome---" not in persona.system_prompt_template


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

    def test_find_producers_mimir_curated_returns_memory_curator(self) -> None:
        loader = FilesystemPersonaAdapter()
        producers = loader.find_producers("mimir.curated")
        names = [p.name for p in producers]
        assert names == ["mimir-memory-curator"]

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

    def test_find_producers_dream_completed_returns_empty_for_archived_curator(self) -> None:
        loader = FilesystemPersonaAdapter()
        producers = loader.find_producers("dream.completed")
        assert producers == []


# ---------------------------------------------------------------------------
# Built-in specialist personas — contract fields
# ---------------------------------------------------------------------------


class TestBuiltinSpecialistContracts:
    _loader = FilesystemPersonaAdapter()

    def _load(self, name: str) -> PersonaConfig:
        path = _BUILTIN_PERSONAS_DIR / f"{name}.yaml"
        if not path.is_file():
            path = _ARCHIVED_BUILTIN_PERSONAS_DIR / f"{name}.yaml"
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

    def test_qa_agent_builtin_produces_qa_completed(self) -> None:
        cfg = self._load("qa-agent")
        assert cfg.produces.event_type == "qa.completed"
        assert "tests_run" in cfg.produces.schema
        assert "tests_failed" in cfg.produces.schema

    def test_ship_agent_builtin_produces_ship_completed(self) -> None:
        cfg = self._load("ship-agent")
        assert cfg.produces.event_type == "ship.completed"
        assert cfg.produces.schema["verdict"].enum_values == ["shipped", "blocked"]

    def test_retro_analyst_builtin_produces_retro_completed(self) -> None:
        cfg = self._load("retro-analyst")
        assert cfg.produces.event_type == "retro.completed"
        assert "items_shipped" in cfg.produces.schema
        assert "patterns_found" in cfg.produces.schema

    def test_mimir_curator_builtin_produces_dream_completed(self) -> None:
        cfg = self._load("mimir-curator")
        assert cfg.produces.event_type == "dream.completed"
        assert "pages_updated" in cfg.produces.schema

    def test_mimir_warden_curates_raw_sources(self) -> None:
        cfg = self._load("mimir-warden")
        prompt = cfg.system_prompt_template
        assert "Never complete a raw-source task with prose alone" in prompt
        assert "mimir_write" in cfg.allowed_tools
        assert "<!-- sources: <source_id> -->" in prompt
        assert "pages_written" in cfg.produces.schema

    def test_all_specialist_personas_have_system_prompts(self) -> None:
        specialist_names = [
            "reviewer",
            "security-auditor",
            "qa-agent",
            "ship-agent",
            "retro-analyst",
        ]
        for name in specialist_names:
            cfg = self._load(name)
            assert cfg.system_prompt_template, f"{name} missing system_prompt_template"

    def test_all_specialist_personas_have_positive_budgets(self) -> None:
        specialist_names = [
            "reviewer",
            "security-auditor",
            "qa-agent",
            "ship-agent",
            "retro-analyst",
        ]
        for name in specialist_names:
            cfg = self._load(name)
            assert cfg.iteration_budget > 0, f"{name} has non-positive iteration_budget"


# ---------------------------------------------------------------------------
# FilesystemPersonaAdapter.merge — new fields preserved
# ---------------------------------------------------------------------------


class TestFilesystemPersonaAdapterMergeWithContracts:
    def _make_project(self):  # type: ignore[return]
        from ravn.config import ProjectConfig

        return ProjectConfig(
            project_name="proj",
            persona="",
            allowed_tools=[],
            forbidden_tools=[],
            permission_mode="",
            iteration_budget=0,
            notes="",
        )

    def test_merge_preserves_produces(self) -> None:
        produces = PersonaProduces(
            event_type="x.done",
            schema={"v": OutcomeField(type="string", description="v")},
        )
        persona = PersonaConfig(name="x", produces=produces)
        merged = FilesystemPersonaAdapter.merge(persona, self._make_project())
        assert merged.produces.event_type == "x.done"
        assert "v" in merged.produces.schema

    def test_merge_preserves_consumes(self) -> None:
        consumes = PersonaConsumes(event_types=["a.b"], injects=["x"])
        persona = PersonaConfig(name="x", consumes=consumes)
        merged = FilesystemPersonaAdapter.merge(persona, self._make_project())
        assert merged.consumes.event_types == ["a.b"]
        assert merged.consumes.injects == ["x"]

    def test_merge_preserves_fan_in(self) -> None:
        fan_in = PersonaFanIn(strategy="all_must_pass", contributes_to="verdict")
        persona = PersonaConfig(name="x", fan_in=fan_in)
        merged = FilesystemPersonaAdapter.merge(persona, self._make_project())
        assert merged.fan_in.strategy == "all_must_pass"
        assert merged.fan_in.contributes_to == "verdict"


# ---------------------------------------------------------------------------
# Continuation vocabulary — persona schema must match what the runtime accepts
# ---------------------------------------------------------------------------


class TestContinuationVocabularyMatchesContract:
    """A persona may not advertise a continuation value the runtime rejects.

    ``k8s-valkyrie`` offered ``continue`` alongside the canonical three. The
    drive loop rejects it outright — a resident case has to name a real wake
    source — so a resident that reached for it mid-investigation had its whole
    outcome thrown away, and the repair turn made it worse. ``ivaldi``, which
    works, has only ever offered the canonical three. The persona is the
    contract the model reads; it has to be the contract the runtime enforces.
    """

    _loader = FilesystemPersonaAdapter()

    def test_every_bundled_persona_offers_only_canonical_continuations(self) -> None:
        offenders: dict[str, list[str]] = {}
        for path in sorted(_BUILTIN_PERSONAS_DIR.glob("*.yaml")):
            persona = self._loader.load_from_file(path)
            if persona is None:
                continue
            field = persona.produces.schema.get("continuation")
            if field is None:
                continue
            values = list(field.enum_values or [])
            if tuple(values) != VALKYRIE_CONTINUATION_ORDER:
                offenders[path.name] = values
        assert not offenders, (
            "personas advertise continuation values the drive loop rejects: "
            f"{offenders}; canonical is {list(VALKYRIE_CONTINUATION_ORDER)}"
        )

    def test_continue_is_not_a_canonical_continuation(self) -> None:
        # Guards the constant: if 'continue' ever becomes canonical, the drive
        # loop has to stop rejecting it in the same change.
        assert "continue" not in VALKYRIE_CONTINUATION_ORDER

    def test_drive_loop_rejects_non_canonical_continuations(self) -> None:
        for value in ("continue", "retry", "loop"):
            errors = _validate_resident_continuation_contract(
                {"continuation": value, "selected_next_action": "look again"}
            )
            assert errors, f"continuation {value!r} was accepted but is not canonical"

    def test_drive_loop_accepts_each_canonical_continuation(self) -> None:
        timings = {
            "ask_operator": "operator_input",
            "sleep": "scheduled_time",
            "stop": "none",
        }
        for value in VALKYRIE_CONTINUATION_ORDER:
            errors = _validate_resident_continuation_contract(
                {
                    "continuation": value,
                    "next_action_timing": timings[value],
                    "selected_next_action": "look again",
                }
            )
            assert not errors, f"canonical continuation {value!r} was rejected: {errors}"
