"""Tests for PersonaConfig.to_dict() and FilesystemPersonaAdapter.to_yaml() serialization.

Round-trip invariant: parse(to_yaml(config)) == config for every built-in persona.
"""

from __future__ import annotations

import pytest

from niuu.domain.outcome import OutcomeField
from ravn.adapters.personas.loader import (
    _BUILTIN_PERSONAS_DIR,
    FilesystemPersonaAdapter,
    PersonaConfig,
    PersonaConsumes,
    PersonaExecutorConfig,
    PersonaFanIn,
    PersonaLLMConfig,
    PersonaProduces,
)

# ---------------------------------------------------------------------------
# Round-trip: all built-in personas
# ---------------------------------------------------------------------------

_loader = FilesystemPersonaAdapter()
_builtin_personas = {
    p.stem: _loader.load_from_file(p) for p in sorted(_BUILTIN_PERSONAS_DIR.glob("*.yaml"))
}


@pytest.mark.parametrize("name,persona", list(_builtin_personas.items()))
def test_round_trip_builtin_persona(name: str, persona: PersonaConfig) -> None:
    """parse(to_yaml(config)) == config for every built-in persona."""
    yaml_text = FilesystemPersonaAdapter.to_yaml(persona)
    restored = FilesystemPersonaAdapter.parse(yaml_text)
    assert restored is not None, f"parse() returned None for persona '{name}'"
    assert restored == persona, f"Round-trip failed for persona '{name}'"


# ---------------------------------------------------------------------------
# to_dict: zero-value omission
# ---------------------------------------------------------------------------


def test_to_dict_omits_empty_system_prompt() -> None:
    p = PersonaConfig(name="minimal")
    d = p.to_dict()
    assert "system_prompt_template" not in d


def test_to_dict_omits_empty_allowed_tools() -> None:
    p = PersonaConfig(name="minimal")
    d = p.to_dict()
    assert "allowed_tools" not in d


def test_to_dict_omits_empty_forbidden_tools() -> None:
    p = PersonaConfig(name="minimal")
    d = p.to_dict()
    assert "forbidden_tools" not in d


def test_to_dict_omits_empty_permission_mode() -> None:
    p = PersonaConfig(name="minimal")
    d = p.to_dict()
    assert "permission_mode" not in d


def test_to_dict_omits_zero_iteration_budget() -> None:
    p = PersonaConfig(name="minimal")
    d = p.to_dict()
    assert "iteration_budget" not in d


def test_to_dict_omits_default_llm() -> None:
    p = PersonaConfig(name="minimal")
    d = p.to_dict()
    assert "llm" not in d


def test_to_dict_omits_false_thinking_enabled() -> None:
    p = PersonaConfig(
        name="test",
        llm=PersonaLLMConfig(primary_alias="balanced", thinking_enabled=False),
    )
    d = p.to_dict()
    assert "thinking_enabled" not in d["llm"]


def test_to_dict_omits_zero_max_tokens() -> None:
    p = PersonaConfig(
        name="test",
        llm=PersonaLLMConfig(primary_alias="balanced", max_tokens=0),
    )
    d = p.to_dict()
    assert "max_tokens" not in d["llm"]


def test_to_dict_omits_default_produces() -> None:
    p = PersonaConfig(name="minimal")
    d = p.to_dict()
    assert "produces" not in d


def test_to_dict_omits_default_consumes() -> None:
    p = PersonaConfig(name="minimal")
    d = p.to_dict()
    assert "consumes" not in d


def test_to_dict_omits_default_fan_in() -> None:
    """fan_in with strategy='merge' and no contributes_to is omitted."""
    p = PersonaConfig(name="minimal")
    d = p.to_dict()
    assert "fan_in" not in d


def test_to_dict_omits_default_executor() -> None:
    p = PersonaConfig(name="minimal")
    d = p.to_dict()
    assert "executor" not in d


def test_executor_round_trip() -> None:
    p = PersonaConfig(
        name="executor-test",
        executor=PersonaExecutorConfig(
            adapter="ravn.adapters.executors.agent.AgentExecutor",
            kwargs={"mode": "default"},
        ),
    )
    yaml_text = FilesystemPersonaAdapter.to_yaml(p)
    restored = FilesystemPersonaAdapter.parse(yaml_text)
    assert restored == p


def test_to_dict_only_name_for_minimal() -> None:
    p = PersonaConfig(name="minimal")
    d = p.to_dict()
    assert d == {"name": "minimal"}


# ---------------------------------------------------------------------------
# to_dict: nested structures
# ---------------------------------------------------------------------------


def test_to_dict_produces_schema_with_enum_field() -> None:
    p = PersonaConfig(
        name="test",
        produces=PersonaProduces(
            event_type="review.completed",
            schema={
                "verdict": OutcomeField(
                    type="enum",
                    description="review verdict",
                    enum_values=["pass", "fail"],
                )
            },
        ),
    )
    d = p.to_dict()
    assert d["produces"]["event_type"] == "review.completed"
    verdict = d["produces"]["schema"]["verdict"]
    assert verdict["type"] == "enum"
    assert verdict["description"] == "review verdict"
    assert verdict["values"] == ["pass", "fail"]
    assert "required" not in verdict  # True is default, omitted


def test_to_dict_produces_schema_required_false_included() -> None:
    p = PersonaConfig(
        name="test",
        produces=PersonaProduces(
            event_type="thing.done",
            schema={
                "note": OutcomeField(type="string", description="optional note", required=False)
            },
        ),
    )
    d = p.to_dict()
    note = d["produces"]["schema"]["note"]
    assert note["required"] is False


def test_to_dict_produces_schema_no_values_for_non_enum() -> None:
    p = PersonaConfig(
        name="test",
        produces=PersonaProduces(
            event_type="thing.done",
            schema={"count": OutcomeField(type="number", description="count")},
        ),
    )
    d = p.to_dict()
    count = d["produces"]["schema"]["count"]
    assert "values" not in count


def test_to_dict_consumes_injects() -> None:
    p = PersonaConfig(
        name="test",
        consumes=PersonaConsumes(
            event_types=["code.changed"],
            injects=["repo", "branch"],
        ),
    )
    d = p.to_dict()
    assert d["consumes"]["event_types"] == ["code.changed"]
    assert d["consumes"]["injects"] == ["repo", "branch"]


def test_to_dict_consumes_omits_empty_injects() -> None:
    p = PersonaConfig(
        name="test",
        consumes=PersonaConsumes(event_types=["code.changed"]),
    )
    d = p.to_dict()
    assert "injects" not in d["consumes"]


def test_to_dict_fan_in_non_default_strategy() -> None:
    p = PersonaConfig(
        name="test",
        fan_in=PersonaFanIn(strategy="all_must_pass", contributes_to="review.verdict"),
    )
    d = p.to_dict()
    assert d["fan_in"]["strategy"] == "all_must_pass"
    assert d["fan_in"]["contributes_to"] == "review.verdict"


def test_to_dict_fan_in_merge_strategy_omitted() -> None:
    p = PersonaConfig(name="test", fan_in=PersonaFanIn(strategy="merge"))
    d = p.to_dict()
    assert "fan_in" not in d


def test_to_dict_fan_in_with_contributes_to_only() -> None:
    """contributes_to alone (without changing strategy) triggers fan_in emission."""
    p = PersonaConfig(
        name="test",
        fan_in=PersonaFanIn(strategy="merge", contributes_to="some.event"),
    )
    d = p.to_dict()
    assert "fan_in" in d
    assert d["fan_in"]["contributes_to"] == "some.event"


# ---------------------------------------------------------------------------
# to_yaml: multiline system_prompt_template
# ---------------------------------------------------------------------------


def test_to_yaml_uses_block_scalar_for_multiline_prompt() -> None:
    p = PersonaConfig(
        name="test",
        system_prompt_template="Line one.\nLine two.\nLine three.",
    )
    yaml_text = FilesystemPersonaAdapter.to_yaml(p)
    # Block scalar indicator must be present
    assert "|-" in yaml_text or "|\n" in yaml_text or "|+" in yaml_text


def test_to_yaml_multiline_prompt_round_trips() -> None:
    original = "Line one.\nLine two.\nLine three."
    p = PersonaConfig(name="test", system_prompt_template=original)
    yaml_text = FilesystemPersonaAdapter.to_yaml(p)
    restored = FilesystemPersonaAdapter.parse(yaml_text)
    assert restored is not None
    assert restored.system_prompt_template == original


def test_to_yaml_single_line_prompt_round_trips() -> None:
    original = "You are a simple agent."
    p = PersonaConfig(name="test", system_prompt_template=original)
    yaml_text = FilesystemPersonaAdapter.to_yaml(p)
    restored = FilesystemPersonaAdapter.parse(yaml_text)
    assert restored is not None
    assert restored.system_prompt_template == original


# ---------------------------------------------------------------------------
# to_yaml: parseable output
# ---------------------------------------------------------------------------


def test_to_yaml_output_is_valid_yaml() -> None:
    import yaml

    p = _builtin_personas["reviewer"]
    yaml_text = FilesystemPersonaAdapter.to_yaml(p)
    parsed = yaml.safe_load(yaml_text)
    assert isinstance(parsed, dict)
    assert parsed["name"] == "reviewer"


def test_to_yaml_includes_all_non_zero_fields() -> None:
    p = PersonaConfig(
        name="full",
        system_prompt_template="Do something.",
        allowed_tools=["file"],
        forbidden_tools=["terminal"],
        permission_mode="read-only",
        llm=PersonaLLMConfig(primary_alias="balanced", thinking_enabled=True, max_tokens=1000),
        iteration_budget=25,
    )
    d = p.to_dict()
    assert d["allowed_tools"] == ["file"]
    assert d["forbidden_tools"] == ["terminal"]
    assert d["permission_mode"] == "read-only"
    assert d["llm"]["primary_alias"] == "balanced"
    assert d["llm"]["thinking_enabled"] is True
    assert d["llm"]["max_tokens"] == 1000
    assert d["iteration_budget"] == 25
