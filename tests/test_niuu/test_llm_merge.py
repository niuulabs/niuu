"""Tests for niuu.domain.llm_merge — exhaustive merge matrix."""

import logging

from niuu.domain.llm_merge import concat_prompt_extras, merge_llm

# ---------------------------------------------------------------------------
# merge_llm — basic layering
# ---------------------------------------------------------------------------


class TestMergeLlmBasicLayering:
    def test_defaults_only(self):
        result = merge_llm(defaults={"model": "gpt-4", "max_tokens": 4096})
        assert result == {"model": "gpt-4", "max_tokens": 4096}

    def test_empty_defaults(self):
        result = merge_llm(defaults={})
        assert result == {}

    def test_none_defaults(self):
        result = merge_llm(defaults=None)
        assert result == {}

    def test_all_none(self):
        result = merge_llm()
        assert result == {}

    def test_global_override_replaces_default(self):
        result = merge_llm(
            defaults={"model": "gpt-4", "max_tokens": 4096},
            global_override={"model": "claude-sonnet"},
        )
        assert result["model"] == "claude-sonnet"
        assert result["max_tokens"] == 4096

    def test_persona_override_replaces_global(self):
        result = merge_llm(
            defaults={"model": "gpt-4"},
            global_override={"model": "claude-sonnet"},
            persona_override={"model": "claude-opus"},
        )
        assert result["model"] == "claude-opus"

    def test_persona_override_replaces_default_without_global(self):
        result = merge_llm(
            defaults={"model": "gpt-4", "max_tokens": 4096},
            persona_override={"model": "claude-opus"},
        )
        assert result["model"] == "claude-opus"
        assert result["max_tokens"] == 4096

    def test_global_adds_new_key(self):
        result = merge_llm(
            defaults={"model": "gpt-4"},
            global_override={"thinking_enabled": True},
        )
        assert result == {"model": "gpt-4", "thinking_enabled": True}

    def test_persona_adds_new_key(self):
        result = merge_llm(
            defaults={"model": "gpt-4"},
            persona_override={"max_tokens": 16000},
        )
        assert result == {"model": "gpt-4", "max_tokens": 16000}


# ---------------------------------------------------------------------------
# merge_llm — empty/None/zero inheritance
# ---------------------------------------------------------------------------


class TestMergeLlmInheritance:
    def test_none_value_inherits_from_below(self):
        result = merge_llm(
            defaults={"model": "gpt-4"},
            global_override={"model": None},
        )
        assert result["model"] == "gpt-4"

    def test_empty_string_inherits_from_below(self):
        result = merge_llm(
            defaults={"model": "gpt-4"},
            global_override={"model": ""},
        )
        assert result["model"] == "gpt-4"

    def test_zero_inherits_from_below(self):
        result = merge_llm(
            defaults={"max_tokens": 4096},
            global_override={"max_tokens": 0},
        )
        assert result["max_tokens"] == 4096

    def test_persona_none_inherits_from_global(self):
        result = merge_llm(
            defaults={"model": "gpt-4"},
            global_override={"model": "claude-sonnet"},
            persona_override={"model": None},
        )
        assert result["model"] == "claude-sonnet"

    def test_persona_empty_string_inherits_from_global(self):
        result = merge_llm(
            defaults={"model": "gpt-4"},
            global_override={"model": "claude-sonnet"},
            persona_override={"model": ""},
        )
        assert result["model"] == "claude-sonnet"

    def test_false_is_not_empty(self):
        """False is a meaningful value, not treated as empty."""
        result = merge_llm(
            defaults={"thinking_enabled": True},
            global_override={"thinking_enabled": False},
        )
        assert result["thinking_enabled"] is False

    def test_float_zero_is_not_empty(self):
        result = merge_llm(
            defaults={"temperature": 0.7},
            global_override={"temperature": 0.0},
        )
        assert result["temperature"] == 0.0

    def test_negative_number_is_not_empty(self):
        """Negative numbers are meaningful, not treated as empty."""
        result = merge_llm(
            defaults={"temperature": 0.7},
            global_override={"temperature": -1},
        )
        assert result["temperature"] == -1

    def test_empty_list_is_not_empty(self):
        """Empty list is a meaningful value (clear the list)."""
        result = merge_llm(
            defaults={"stop_sequences": ["END"]},
            global_override={"stop_sequences": []},
        )
        assert result["stop_sequences"] == []


# ---------------------------------------------------------------------------
# merge_llm — security keys
# ---------------------------------------------------------------------------


class TestMergeLlmSecurityKeys:
    def test_allowed_tools_dropped_from_global(self, caplog):
        with caplog.at_level(logging.WARNING, logger="niuu.domain.llm_merge"):
            result = merge_llm(
                defaults={"model": "gpt-4"},
                global_override={"allowed_tools": ["bash", "read"]},
            )
        assert "allowed_tools" not in result
        assert "dropping security key" in caplog.text

    def test_forbidden_tools_dropped_from_global(self, caplog):
        with caplog.at_level(logging.WARNING, logger="niuu.domain.llm_merge"):
            result = merge_llm(
                defaults={"model": "gpt-4"},
                global_override={"forbidden_tools": ["rm"]},
            )
        assert "forbidden_tools" not in result
        assert "dropping security key" in caplog.text

    def test_allowed_tools_dropped_from_persona(self, caplog):
        with caplog.at_level(logging.WARNING, logger="niuu.domain.llm_merge"):
            result = merge_llm(
                defaults={"model": "gpt-4"},
                persona_override={"allowed_tools": ["bash"]},
            )
        assert "allowed_tools" not in result
        assert "dropping security key" in caplog.text

    def test_forbidden_tools_dropped_from_persona(self, caplog):
        with caplog.at_level(logging.WARNING, logger="niuu.domain.llm_merge"):
            result = merge_llm(
                defaults={"model": "gpt-4"},
                persona_override={"forbidden_tools": ["rm"]},
            )
        assert "forbidden_tools" not in result
        assert "dropping security key" in caplog.text

    def test_security_keys_in_defaults_are_kept(self):
        """Defaults (persona config) ARE allowed to set security keys."""
        result = merge_llm(
            defaults={"allowed_tools": ["bash", "read"], "forbidden_tools": ["rm"]},
        )
        assert result["allowed_tools"] == ["bash", "read"]
        assert result["forbidden_tools"] == ["rm"]

    def test_non_security_keys_pass_through(self, caplog):
        with caplog.at_level(logging.WARNING, logger="niuu.domain.llm_merge"):
            result = merge_llm(
                defaults={"model": "gpt-4"},
                global_override={"model": "claude-sonnet", "thinking_enabled": True},
                persona_override={"max_tokens": 16000},
            )
        assert result == {"model": "claude-sonnet", "thinking_enabled": True, "max_tokens": 16000}
        assert "dropping security key" not in caplog.text


# ---------------------------------------------------------------------------
# merge_llm — full matrix combinations
# ---------------------------------------------------------------------------


class TestMergeLlmFullMatrix:
    def test_string_field_all_three_layers(self):
        result = merge_llm(
            defaults={"primary_alias": "default"},
            global_override={"primary_alias": "balanced"},
            persona_override={"primary_alias": "powerful"},
        )
        assert result["primary_alias"] == "powerful"

    def test_int_field_all_three_layers(self):
        result = merge_llm(
            defaults={"max_tokens": 4096},
            global_override={"max_tokens": 8192},
            persona_override={"max_tokens": 16000},
        )
        assert result["max_tokens"] == 16000

    def test_bool_field_all_three_layers(self):
        result = merge_llm(
            defaults={"thinking_enabled": False},
            global_override={"thinking_enabled": True},
            persona_override={"thinking_enabled": False},
        )
        assert result["thinking_enabled"] is False

    def test_dict_field_replaced_not_deep_merged(self):
        """Dict values are replaced wholesale, not deep-merged."""
        result = merge_llm(
            defaults={"provider": {"adapter": "openai", "base_url": "https://api.openai.com"}},
            global_override={"provider": {"adapter": "anthropic"}},
        )
        assert result["provider"] == {"adapter": "anthropic"}

    def test_multiple_fields_across_layers(self):
        result = merge_llm(
            defaults={"model": "gpt-4", "max_tokens": 4096, "thinking_enabled": False},
            global_override={"model": "claude-sonnet", "thinking_enabled": True},
            persona_override={"max_tokens": 16000},
        )
        assert result == {
            "model": "claude-sonnet",
            "max_tokens": 16000,
            "thinking_enabled": True,
        }

    def test_partial_override_preserves_defaults(self):
        result = merge_llm(
            defaults={
                "model": "gpt-4",
                "max_tokens": 4096,
                "temperature": 0.7,
                "thinking_enabled": False,
            },
            persona_override={"thinking_enabled": True},
        )
        assert result == {
            "model": "gpt-4",
            "max_tokens": 4096,
            "temperature": 0.7,
            "thinking_enabled": True,
        }


# ---------------------------------------------------------------------------
# merge_llm — does not mutate inputs
# ---------------------------------------------------------------------------


class TestMergeLlmImmutability:
    def test_defaults_not_mutated(self):
        defaults = {"model": "gpt-4", "max_tokens": 4096}
        original = dict(defaults)
        merge_llm(defaults=defaults, global_override={"model": "claude-sonnet"})
        assert defaults == original

    def test_global_override_not_mutated(self):
        override = {"model": "claude-sonnet"}
        original = dict(override)
        merge_llm(defaults={"model": "gpt-4"}, global_override=override)
        assert override == original

    def test_persona_override_not_mutated(self):
        override = {"model": "claude-opus"}
        original = dict(override)
        merge_llm(defaults={"model": "gpt-4"}, persona_override=override)
        assert override == original


# ---------------------------------------------------------------------------
# concat_prompt_extras
# ---------------------------------------------------------------------------


class TestConcatPromptExtras:
    def test_single_extra(self):
        assert concat_prompt_extras("Be thorough.") == "Be thorough."

    def test_two_extras(self):
        result = concat_prompt_extras("Persona template.", "Global extra.")
        assert result == "Persona template.\n\nGlobal extra."

    def test_three_extras_preserves_order(self):
        result = concat_prompt_extras("Persona.", "Global.", "Per-persona.")
        assert result == "Persona.\n\nGlobal.\n\nPer-persona."

    def test_none_values_filtered(self):
        result = concat_prompt_extras("A.", None, "C.")
        assert result == "A.\n\nC."

    def test_empty_string_filtered(self):
        result = concat_prompt_extras("A.", "", "C.")
        assert result == "A.\n\nC."

    def test_whitespace_only_filtered(self):
        result = concat_prompt_extras("A.", "   ", "C.")
        assert result == "A.\n\nC."

    def test_all_none(self):
        assert concat_prompt_extras(None, None, None) == ""

    def test_all_empty(self):
        assert concat_prompt_extras("", "", "") == ""

    def test_no_args(self):
        assert concat_prompt_extras() == ""

    def test_strips_whitespace_from_parts(self):
        result = concat_prompt_extras("  A.  ", "  B.  ")
        assert result == "A.\n\nB."
