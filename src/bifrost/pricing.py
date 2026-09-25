"""Per-model pricing table and cost calculation.

Prices are USD per million tokens (input / output / cache variants).
The built-in snapshot can be extended or overridden via ``BifrostConfig``
or an external YAML pricing file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from bifrost.domain.models import TokenUsage


@dataclass(frozen=True)
class ModelPricing:
    """USD cost per one million tokens for a single model."""

    input_per_million: float = 0.0
    output_per_million: float = 0.0
    cache_creation_per_million: float = 0.0
    cache_read_per_million: float = 0.0


# ---------------------------------------------------------------------------
# Built-in pricing snapshot — USD/million tokens as of 2026-04, with the
# Claude Fable 5.1 / Opus 5.5 and GPT-6 rows added 2026-09.
# These can be overridden or extended through BifrostConfig.pricing.
# ---------------------------------------------------------------------------
BUILTIN_PRICING: dict[str, ModelPricing] = {
    # Anthropic ──────────────────────────────────────────────────────────────
    "claude-fable-5-1": ModelPricing(
        input_per_million=10.00,
        output_per_million=50.00,
        cache_creation_per_million=12.50,
        cache_read_per_million=0.25,
    ),
    "claude-opus-5-5": ModelPricing(
        input_per_million=4.00,
        output_per_million=20.00,
        cache_creation_per_million=5.00,
        cache_read_per_million=0.20,
    ),
    "claude-opus-4-6": ModelPricing(
        input_per_million=15.00,
        output_per_million=75.00,
        cache_creation_per_million=18.75,
        cache_read_per_million=1.50,
    ),
    "claude-sonnet-4-6": ModelPricing(
        input_per_million=3.00,
        output_per_million=15.00,
        cache_creation_per_million=3.75,
        cache_read_per_million=0.30,
    ),
    "claude-haiku-4-5-20251001": ModelPricing(
        input_per_million=0.80,
        output_per_million=4.00,
        cache_creation_per_million=1.00,
        cache_read_per_million=0.08,
    ),
    # OpenAI ─────────────────────────────────────────────────────────────────
    # OpenAI bills no cache writes; cached input reads are 90% off.
    "gpt-6-astra": ModelPricing(
        input_per_million=10.00, output_per_million=50.00, cache_read_per_million=1.00
    ),
    "gpt-6-sol": ModelPricing(
        input_per_million=2.00, output_per_million=10.00, cache_read_per_million=0.20
    ),
    "gpt-6-luna": ModelPricing(
        input_per_million=0.10, output_per_million=0.50, cache_read_per_million=0.01
    ),
    "gpt-4o": ModelPricing(input_per_million=2.50, output_per_million=10.00),
    "gpt-4o-mini": ModelPricing(input_per_million=0.15, output_per_million=0.60),
    # Local / free (Ollama) ──────────────────────────────────────────────────
    "llama3.1:8b": ModelPricing(),
}


def load_pricing_from_yaml(path: str) -> dict[str, ModelPricing]:
    """Load a pricing table from a YAML file.

    The file format is a mapping of model identifier to pricing fields::

        claude-sonnet-4-6:
          input_per_million: 3.00
          output_per_million: 15.00
          cache_creation_per_million: 3.75
          cache_read_per_million: 0.30

    Returns an empty dict if *path* is empty or the file does not exist.
    """
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    raw: dict = yaml.safe_load(p.read_text()) or {}
    result: dict[str, ModelPricing] = {}
    for model, fields in raw.items():
        if not isinstance(fields, dict):
            continue
        result[model] = ModelPricing(
            input_per_million=float(fields.get("input_per_million", 0.0)),
            output_per_million=float(fields.get("output_per_million", 0.0)),
            cache_creation_per_million=float(fields.get("cache_creation_per_million", 0.0)),
            cache_read_per_million=float(fields.get("cache_read_per_million", 0.0)),
        )
    return result


def calculate_cost(
    model: str,
    usage: TokenUsage,
    overrides: dict[str, ModelPricing] | None = None,
) -> float:
    """Return the USD cost for *usage* on *model*.

    Pricing is looked up in *overrides* first, then ``BUILTIN_PRICING``.
    Returns ``0.0`` when the model has no pricing entry.

    Args:
        model:     Canonical model identifier.
        usage:     Token counts from the completed request.
        overrides: Optional per-model overrides from config.

    Returns:
        Cost in USD (may be 0.0 for unknown / free models).
    """
    pricing = (overrides or {}).get(model) or BUILTIN_PRICING.get(model)
    if pricing is None:
        return 0.0

    return (
        usage.input_tokens * pricing.input_per_million / 1_000_000
        + usage.output_tokens * pricing.output_per_million / 1_000_000
        + usage.cache_creation_input_tokens * pricing.cache_creation_per_million / 1_000_000
        + usage.cache_read_input_tokens * pricing.cache_read_per_million / 1_000_000
        # reasoning_tokens are already counted in output_tokens by Anthropic;
        # no separate billing line is needed.
    )
