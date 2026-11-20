"""Reasoning vocabularies verified against the deployed native harnesses.

Catalog defaults are explicit product configuration. Native adapters additionally
validate model capabilities where their protocol publishes them (PI and Grok).
Unknown models have no assumed effort support.
"""

CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")
CODEX_EFFORTS = (*CLAUDE_EFFORTS, "ultra")
MUSE_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "ultra")

MODEL_EFFORTS: dict[str, tuple[str, ...]] = {
    "claude-fable-5-2": CLAUDE_EFFORTS,
    "claude-fable-5-1": CLAUDE_EFFORTS,
    "claude-fable-5": CLAUDE_EFFORTS,
    "claude-opus-5-5": CLAUDE_EFFORTS,
    "claude-opus-5": CLAUDE_EFFORTS,
    "claude-opus-4-8": CLAUDE_EFFORTS,
    "claude-sonnet-5": CLAUDE_EFFORTS,
    "gpt-6.2-astra": CODEX_EFFORTS,
    "gpt-6.2-sol": CODEX_EFFORTS,
    "gpt-6.1-sol": CODEX_EFFORTS,
    "gpt-6.1-terra": CODEX_EFFORTS,
    "gpt-6-astra": CODEX_EFFORTS,
    "gpt-6-sol": CODEX_EFFORTS,
    "gpt-6-luna": CLAUDE_EFFORTS,
    "gpt-5.6-sol": CODEX_EFFORTS,
    "gpt-5.6-terra": CODEX_EFFORTS,
    "gpt-5.6-luna": CLAUDE_EFFORTS,
    "gpt-reserve": CLAUDE_EFFORTS,
    "gpt-5.5": ("none", "low", "medium", "high", "xhigh"),
    "gpt-5.4": ("none", "low", "medium", "high", "xhigh"),
    "grok-4.7": ("low", "medium", "high", "xhigh"),
    "grok-4.6": ("low", "medium", "high", "xhigh"),
    "grok-4.5": ("low", "medium", "high"),
    "muse-spark-1.3": MUSE_EFFORTS,
    "muse-spark-1.2": MUSE_EFFORTS,
    "muse-spark-1.3-contributor": MUSE_EFFORTS,
    "openai-codex/gpt-6-astra": ("minimal", "low", "medium", "high", "xhigh", "max"),
    "openai-codex/gpt-5.6-sol": ("off", "minimal", "low", "medium", "high", "xhigh", "max"),
}


# A model whose own default effort is the launch effort, instead of extra high.
# Claude Fable 5.2 defaults to high (LEX-2021).
MODEL_DEFAULT_EFFORTS: dict[str, str] = {
    "claude-fable-5-2": "high",
}


def preferred_effort(levels: tuple[str, ...] | list[str], model: str = "") -> str:
    """The model's own default when it has one; else extra high, else the highest level."""
    default = MODEL_DEFAULT_EFFORTS.get(model)
    if default is not None and default in levels:
        return default
    return "xhigh" if "xhigh" in levels else next(iter(reversed(levels)), "")


def normalize_effort(value: str) -> str:
    raw = value.strip().lower().replace("_", "-")
    return {"extra": "xhigh", "extra-high": "xhigh", "x-high": "xhigh"}.get(raw, raw)


def validate_effort(value: str, levels: tuple[str, ...] | list[str]) -> str:
    effort = normalize_effort(value)
    if effort not in levels:
        choices = ", ".join(levels) or "no configurable levels"
        raise ValueError(f"Unsupported effort {value!r}. Available: {choices}")
    return effort
