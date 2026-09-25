"""Tests for bifrost.pricing — cost calculation and YAML pricing file loading."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from bifrost.domain.models import TokenUsage
from bifrost.pricing import BUILTIN_PRICING, ModelPricing, calculate_cost, load_pricing_from_yaml


class TestBuiltinPricing:
    def test_anthropic_models_present(self):
        assert "claude-opus-4-6" in BUILTIN_PRICING
        assert "claude-sonnet-4-6" in BUILTIN_PRICING
        assert "claude-haiku-4-5-20251001" in BUILTIN_PRICING

    def test_openai_models_present(self):
        assert "gpt-4o" in BUILTIN_PRICING
        assert "gpt-4o-mini" in BUILTIN_PRICING

    def test_latest_claude_and_gpt_6_models_priced(self):
        assert BUILTIN_PRICING["claude-opus-5-5"] == ModelPricing(
            input_per_million=4.00,
            output_per_million=20.00,
            cache_creation_per_million=5.00,
            cache_read_per_million=0.20,
        )
        assert BUILTIN_PRICING["claude-fable-5-1"].output_per_million == 50.00
        assert BUILTIN_PRICING["gpt-6-sol"] == ModelPricing(
            input_per_million=2.00, output_per_million=10.00, cache_read_per_million=0.20
        )
        assert BUILTIN_PRICING["gpt-6-luna"].output_per_million == 0.50
        assert BUILTIN_PRICING["gpt-6-astra"].input_per_million == 10.00

    def test_ollama_free(self):
        pricing = BUILTIN_PRICING["llama3.1:8b"]
        assert pricing.input_per_million == 0.0
        assert pricing.output_per_million == 0.0

    def test_opus_more_expensive_than_sonnet(self):
        opus = BUILTIN_PRICING["claude-opus-4-6"]
        sonnet = BUILTIN_PRICING["claude-sonnet-4-6"]
        assert opus.output_per_million > sonnet.output_per_million


class TestCalculateCost:
    def test_zero_usage_returns_zero(self):
        usage = TokenUsage()
        cost = calculate_cost("claude-sonnet-4-6", usage)
        assert cost == 0.0

    def test_input_tokens_only(self):
        usage = TokenUsage(input_tokens=1_000_000)
        cost = calculate_cost("claude-sonnet-4-6", usage)
        expected = BUILTIN_PRICING["claude-sonnet-4-6"].input_per_million
        assert abs(cost - expected) < 1e-9

    def test_output_tokens_only(self):
        usage = TokenUsage(output_tokens=1_000_000)
        cost = calculate_cost("claude-opus-4-6", usage)
        expected = BUILTIN_PRICING["claude-opus-4-6"].output_per_million
        assert abs(cost - expected) < 1e-9

    def test_combined_tokens(self):
        usage = TokenUsage(input_tokens=100_000, output_tokens=50_000)
        pricing = BUILTIN_PRICING["claude-sonnet-4-6"]
        expected = (
            100_000 * pricing.input_per_million / 1_000_000
            + 50_000 * pricing.output_per_million / 1_000_000
        )
        cost = calculate_cost("claude-sonnet-4-6", usage)
        assert abs(cost - expected) < 1e-9

    def test_cache_tokens_included(self):
        usage = TokenUsage(
            input_tokens=0,
            output_tokens=0,
            cache_creation_input_tokens=1_000_000,
            cache_read_input_tokens=1_000_000,
        )
        pricing = BUILTIN_PRICING["claude-sonnet-4-6"]
        expected = pricing.cache_creation_per_million + pricing.cache_read_per_million
        cost = calculate_cost("claude-sonnet-4-6", usage)
        assert abs(cost - expected) < 1e-9

    def test_reasoning_tokens_do_not_add_extra_cost(self):
        usage_no_reasoning = TokenUsage(input_tokens=1000, output_tokens=500)
        usage_with_reasoning = TokenUsage(
            input_tokens=1000, output_tokens=500, reasoning_tokens=100
        )
        cost_no = calculate_cost("claude-sonnet-4-6", usage_no_reasoning)
        cost_with = calculate_cost("claude-sonnet-4-6", usage_with_reasoning)
        # reasoning_tokens are billed as output tokens; no separate line.
        assert abs(cost_no - cost_with) < 1e-9

    def test_unknown_model_returns_zero(self):
        usage = TokenUsage(input_tokens=1000, output_tokens=500)
        assert calculate_cost("totally-unknown-model", usage) == 0.0

    def test_free_model_returns_zero(self):
        usage = TokenUsage(input_tokens=500_000, output_tokens=200_000)
        assert calculate_cost("llama3.1:8b", usage) == 0.0

    def test_overrides_take_precedence(self):
        usage = TokenUsage(input_tokens=1_000_000)
        override = {"claude-sonnet-4-6": ModelPricing(input_per_million=99.0)}
        cost = calculate_cost("claude-sonnet-4-6", usage, overrides=override)
        assert abs(cost - 99.0) < 1e-9

    def test_overrides_can_add_new_model(self):
        usage = TokenUsage(output_tokens=1_000_000)
        override = {"custom-model": ModelPricing(output_per_million=7.5)}
        cost = calculate_cost("custom-model", usage, overrides=override)
        assert abs(cost - 7.5) < 1e-9

    def test_override_does_not_affect_other_models(self):
        usage = TokenUsage(input_tokens=1_000_000)
        override = {"claude-opus-4-6": ModelPricing(input_per_million=999.0)}
        cost = calculate_cost("claude-sonnet-4-6", usage, overrides=override)
        expected = BUILTIN_PRICING["claude-sonnet-4-6"].input_per_million
        assert abs(cost - expected) < 1e-9

    @pytest.mark.parametrize(
        "model,input_tok,output_tok",
        [
            ("claude-haiku-4-5-20251001", 10_000, 5_000),
            ("gpt-4o-mini", 20_000, 10_000),
            ("gpt-4o", 500, 200),
        ],
    )
    def test_parametrised_models(self, model: str, input_tok: int, output_tok: int):
        usage = TokenUsage(input_tokens=input_tok, output_tokens=output_tok)
        pricing = BUILTIN_PRICING[model]
        expected = (
            input_tok * pricing.input_per_million / 1_000_000
            + output_tok * pricing.output_per_million / 1_000_000
        )
        cost = calculate_cost(model, usage)
        assert abs(cost - expected) < 1e-9


class TestLoadPricingFromYaml:
    def test_empty_path_returns_empty(self):
        result = load_pricing_from_yaml("")
        assert result == {}

    def test_nonexistent_file_returns_empty(self):
        result = load_pricing_from_yaml("/does/not/exist.yaml")
        assert result == {}

    def test_loads_valid_yaml(self, tmp_path: Path):
        yaml_content = textwrap.dedent("""
            my-custom-model:
              input_per_million: 1.00
              output_per_million: 5.00
              cache_creation_per_million: 1.25
              cache_read_per_million: 0.10
        """)
        pricing_file = tmp_path / "pricing.yaml"
        pricing_file.write_text(yaml_content)

        result = load_pricing_from_yaml(str(pricing_file))
        assert "my-custom-model" in result
        p = result["my-custom-model"]
        assert p.input_per_million == 1.00
        assert p.output_per_million == 5.00
        assert p.cache_creation_per_million == 1.25
        assert p.cache_read_per_million == 0.10

    def test_partial_fields_default_to_zero(self, tmp_path: Path):
        yaml_content = "my-model:\n  output_per_million: 3.00\n"
        pricing_file = tmp_path / "pricing.yaml"
        pricing_file.write_text(yaml_content)

        result = load_pricing_from_yaml(str(pricing_file))
        p = result["my-model"]
        assert p.input_per_million == 0.0
        assert p.output_per_million == 3.00

    def test_multiple_models(self, tmp_path: Path):
        yaml_content = textwrap.dedent("""
            model-a:
              input_per_million: 2.00
              output_per_million: 10.00
            model-b:
              input_per_million: 0.50
              output_per_million: 2.00
        """)
        pricing_file = tmp_path / "pricing.yaml"
        pricing_file.write_text(yaml_content)

        result = load_pricing_from_yaml(str(pricing_file))
        assert len(result) == 2
        assert result["model-a"].input_per_million == 2.00
        assert result["model-b"].output_per_million == 2.00

    def test_yaml_pricing_used_in_cost_calculation(self, tmp_path: Path):
        yaml_content = "special-model:\n  input_per_million: 50.00\n  output_per_million: 100.00\n"
        pricing_file = tmp_path / "pricing.yaml"
        pricing_file.write_text(yaml_content)

        overrides = load_pricing_from_yaml(str(pricing_file))
        usage = TokenUsage(input_tokens=1_000_000)
        cost = calculate_cost("special-model", usage, overrides=overrides)
        assert abs(cost - 50.0) < 1e-9

    def test_empty_file_returns_empty(self, tmp_path: Path):
        pricing_file = tmp_path / "pricing.yaml"
        pricing_file.write_text("")
        result = load_pricing_from_yaml(str(pricing_file))
        assert result == {}
