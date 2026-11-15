"""LLM config merge utilities shared by Volundr and Ting.

Merge semantics (three layers, last wins):
  1. PersonaConfig.llm defaults
  2. workload_config.llm_config  (global override)
  3. workload_config.personas[i].llm  (per-persona override)

Rules:
  - Empty string / zero / ``None`` on an override means "inherit from
    the layer below" (the key is skipped, not overwritten).
  - ``system_prompt_extra`` is **concatenated** across layers, not replaced.
  - ``allowed_tools`` / ``forbidden_tools`` are not supported: per-harness tool
    restrictions are not available yet, so any layer that carries one raises.
"""

from __future__ import annotations

from typing import Any

#: Keys of the removed per-harness tool restrictions; a layer that carries one is refused.
REMOVED_TOOL_RESTRICTION_KEYS: tuple[str, ...] = ("allowed_tools", "forbidden_tools")


def is_empty(value: object) -> bool:
    """Return True when *value* should be treated as "inherit from below".

    ``None``, empty string, and integer zero mean "inherit".  ``False`` is
    a meaningful value and is **not** treated as empty.
    """
    if value is None:
        return True
    if value == "":
        return True
    if type(value) is int and value == 0:
        return True
    return False


def _refuse_tool_restrictions(layer_name: str, layer: dict[str, Any]) -> None:
    present = [key for key in REMOVED_TOOL_RESTRICTION_KEYS if key in layer]
    if not present:
        return
    raise ValueError(
        f"llm_merge: {layer_name} sets {', '.join(present)}: per-harness tool restrictions "
        "are not available yet; remove allowed_tools (and forbidden_tools) from the config"
    )


def merge_llm(
    *,
    defaults: dict[str, Any] | None = None,
    global_override: dict[str, Any] | None = None,
    persona_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the effective LLM config after merging three layers.

    Each layer is a flat dict.  Non-empty values in higher layers replace
    lower ones.  A layer that carries ``allowed_tools`` or ``forbidden_tools`` raises
    :class:`ValueError`: per-harness tool restrictions are not available yet.
    """
    result: dict[str, Any] = dict(defaults or {})
    _refuse_tool_restrictions("defaults", result)

    for layer_name, layer in (
        ("global_override", global_override),
        ("persona_override", persona_override),
    ):
        if not layer:
            continue
        _refuse_tool_restrictions(layer_name, layer)
        for key, value in layer.items():
            if is_empty(value):
                continue
            result[key] = value

    return result


def concat_prompt_extras(*extras: str | None) -> str:
    """Concatenate prompt extras, filtering out empty/None values.

    Order: persona template extra → global extra → per-persona extra.
    Parts are joined with double-newline separators.
    """
    parts = [e.strip() for e in extras if e and e.strip()]
    return "\n\n".join(parts)
