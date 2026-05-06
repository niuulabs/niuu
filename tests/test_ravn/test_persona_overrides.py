"""Tests for apply_config_overrides (NIU-638).

Covers the full matrix of persona_overrides application:
- system_prompt_extra concatenation (order asserted)
- iteration_budget override
- security key dropping with WARN
- merge precedence validation
- startup log assertions
"""

import logging

import pytest

from ravn.adapters.personas.loader import PersonaConfig, PersonaLLMConfig
from ravn.adapters.personas.overrides import apply_config_overrides

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def base_persona() -> PersonaConfig:
    """A minimal persona with a known system prompt."""
    return PersonaConfig(
        name="reviewer",
        system_prompt_template="You are a code reviewer.",
        llm=PersonaLLMConfig(primary_alias="balanced"),
        iteration_budget=20,
    )


@pytest.fixture
def persona_no_prompt() -> PersonaConfig:
    """A persona with an empty system_prompt_template."""
    return PersonaConfig(
        name="coordinator",
        system_prompt_template="",
    )


# ---------------------------------------------------------------------------
# Empty / no-op cases
# ---------------------------------------------------------------------------


class TestNoOverrides:
    def test_empty_overrides_returns_same_persona(self, base_persona):
        result = apply_config_overrides(base_persona, {})
        assert result is base_persona

    def test_none_equivalent_keys_treated_as_empty(self, base_persona):
        """system_prompt_extra='' and iteration_budget=0 are both no-ops."""
        result = apply_config_overrides(
            base_persona,
            {"system_prompt_extra": "", "iteration_budget": 0},
        )
        assert result.system_prompt_template == base_persona.system_prompt_template
        assert result.iteration_budget == base_persona.iteration_budget

    def test_whitespace_only_prompt_extra_is_no_op(self, base_persona):
        result = apply_config_overrides(base_persona, {"system_prompt_extra": "   \n  "})
        assert result.system_prompt_template == base_persona.system_prompt_template


# ---------------------------------------------------------------------------
# system_prompt_extra concatenation
# ---------------------------------------------------------------------------


class TestSystemPromptExtra:
    def test_extra_appended_to_base_prompt(self, base_persona):
        extra = "Pay special attention to security vulnerabilities."
        result = apply_config_overrides(base_persona, {"system_prompt_extra": extra})

        assert result.system_prompt_template.startswith("You are a code reviewer.")
        assert extra in result.system_prompt_template

    def test_concatenation_order_base_then_extra(self, base_persona):
        """Concatenation order: base template first, then extra."""
        extra = "EXTRA_CONTENT"
        result = apply_config_overrides(base_persona, {"system_prompt_extra": extra})

        prompt = result.system_prompt_template
        base_pos = prompt.index("You are a code reviewer.")
        extra_pos = prompt.index("EXTRA_CONTENT")
        assert base_pos < extra_pos, "Base template must precede system_prompt_extra"

    def test_separator_between_base_and_extra(self, base_persona):
        """The two parts are joined with double-newline."""
        extra = "Extra instructions here."
        result = apply_config_overrides(base_persona, {"system_prompt_extra": extra})
        assert "\n\n" in result.system_prompt_template

    def test_extra_appended_to_empty_base_prompt(self, persona_no_prompt):
        extra = "Be concise."
        result = apply_config_overrides(persona_no_prompt, {"system_prompt_extra": extra})
        assert result.system_prompt_template == extra

    def test_original_persona_not_mutated(self, base_persona):
        """PersonaConfig is frozen — original must not change."""
        extra = "New instructions."
        apply_config_overrides(base_persona, {"system_prompt_extra": extra})
        assert base_persona.system_prompt_template == "You are a code reviewer."

    def test_extra_stripped_before_join(self, base_persona):
        """Leading/trailing whitespace in extra is stripped before joining."""
        extra = "  Focus on type safety.  "
        result = apply_config_overrides(base_persona, {"system_prompt_extra": extra})
        assert "Focus on type safety." in result.system_prompt_template
        # No leading space after the separator
        assert "\n\nFocus on type safety." in result.system_prompt_template


# ---------------------------------------------------------------------------
# iteration_budget override
# ---------------------------------------------------------------------------


class TestIterationBudget:
    def test_budget_overrides_persona_default(self, base_persona):
        result = apply_config_overrides(base_persona, {"iteration_budget": 40})
        assert result.iteration_budget == 40

    def test_zero_budget_is_no_op(self, base_persona):
        result = apply_config_overrides(base_persona, {"iteration_budget": 0})
        assert result.iteration_budget == base_persona.iteration_budget

    def test_string_budget_coerced_to_int(self, base_persona):
        result = apply_config_overrides(base_persona, {"iteration_budget": 35})
        assert result.iteration_budget == 35
        assert isinstance(result.iteration_budget, int)

    def test_budget_does_not_affect_prompt(self, base_persona):
        result = apply_config_overrides(base_persona, {"iteration_budget": 50})
        assert result.system_prompt_template == base_persona.system_prompt_template

    def test_original_budget_unchanged_when_zero_override(self, base_persona):
        apply_config_overrides(base_persona, {"iteration_budget": 0})
        assert base_persona.iteration_budget == 20


# ---------------------------------------------------------------------------
# Security key dropping
# ---------------------------------------------------------------------------


class TestSecurityKeyDropping:
    def test_allowed_tools_dropped_with_warn(self, base_persona, caplog):
        with caplog.at_level(logging.WARNING):
            result = apply_config_overrides(base_persona, {"allowed_tools": ["bash", "read"]})
        assert "allowed_tools" in caplog.text
        # Persona's own allowed_tools list is unchanged
        assert result.allowed_tools == base_persona.allowed_tools

    def test_forbidden_tools_dropped_with_warn(self, base_persona, caplog):
        with caplog.at_level(logging.WARNING):
            result = apply_config_overrides(base_persona, {"forbidden_tools": ["rm"]})
        assert "forbidden_tools" in caplog.text
        assert result.forbidden_tools == base_persona.forbidden_tools

    def test_security_keys_dropped_other_keys_applied(self, base_persona, caplog):
        """Security keys are dropped but other overrides in the same dict still apply."""
        with caplog.at_level(logging.WARNING):
            result = apply_config_overrides(
                base_persona,
                {
                    "allowed_tools": ["bash"],
                    "system_prompt_extra": "Extra instructions.",
                    "iteration_budget": 30,
                },
            )
        assert "allowed_tools" in caplog.text
        assert "Extra instructions." in result.system_prompt_template
        assert result.iteration_budget == 30
        assert result.allowed_tools == base_persona.allowed_tools


# ---------------------------------------------------------------------------
# Combined / full matrix
# ---------------------------------------------------------------------------


class TestCombinedOverrides:
    def test_all_overrides_applied_together(self, base_persona):
        result = apply_config_overrides(
            base_persona,
            {
                "system_prompt_extra": "Be thorough.",
                "iteration_budget": 60,
            },
        )
        assert "You are a code reviewer." in result.system_prompt_template
        assert "Be thorough." in result.system_prompt_template
        assert result.iteration_budget == 60

    def test_name_preserved(self, base_persona):
        result = apply_config_overrides(base_persona, {"iteration_budget": 10})
        assert result.name == "reviewer"

    def test_llm_config_preserved(self, base_persona):
        """apply_config_overrides does not touch persona.llm (that's merge_llm's job)."""
        result = apply_config_overrides(base_persona, {"system_prompt_extra": "Extra."})
        assert result.llm.primary_alias == "balanced"


# ---------------------------------------------------------------------------
# Startup log — integration asserts (NIU-638 AC)
# ---------------------------------------------------------------------------


class TestStartupLog:
    def test_log_emitted_when_system_prompt_extra_applied(self, base_persona, caplog):
        """Ravn startup log reflects merged effective config when overrides applied."""
        with caplog.at_level(logging.INFO, logger="ravn.adapters.personas.overrides"):
            apply_config_overrides(
                base_persona, {"system_prompt_extra": "Security focus required."}
            )
        assert any("system_prompt_extra" in r.message for r in caplog.records)

    def test_log_emitted_when_iteration_budget_applied(self, base_persona, caplog):
        """Ravn startup log reflects iteration_budget override."""
        with caplog.at_level(logging.INFO, logger="ravn.adapters.personas.overrides"):
            apply_config_overrides(base_persona, {"iteration_budget": 42})
        assert any("iteration_budget" in r.message for r in caplog.records)

    def test_no_log_when_no_effective_overrides(self, base_persona, caplog):
        """No INFO log emitted when overrides are all empty/default."""
        with caplog.at_level(logging.INFO, logger="ravn.adapters.personas.overrides"):
            apply_config_overrides(base_persona, {"system_prompt_extra": "", "iteration_budget": 0})
        info_records = [r for r in caplog.records if r.levelno == logging.INFO]
        assert len(info_records) == 0
