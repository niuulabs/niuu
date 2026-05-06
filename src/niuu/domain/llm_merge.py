"""LLM config merge utilities shared by Volundr and Tyr.

Merge semantics (three layers, last wins):
  1. PersonaConfig.llm defaults
  2. workload_config.llm_config  (global override)
  3. workload_config.personas[i].llm  (per-persona override)

Rules:
  - Empty string / zero / ``None`` on an override means "inherit from
    the layer below" (the key is skipped, not overwritten).
  - ``system_prompt_extra`` is **concatenated** across layers, not replaced.
  - ``allowed_tools`` / ``forbidden_tools`` are security boundaries and
    **cannot** be overridden at the workload_config layer.  Attempts are
    logged at WARN and silently dropped.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_SECURITY_KEYS = frozenset({"allowed_tools", "forbidden_tools"})


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


def merge_llm(
    *,
    defaults: dict[str, Any] | None = None,
    global_override: dict[str, Any] | None = None,
    persona_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the effective LLM config after merging three layers.

    Each layer is a flat dict.  Non-empty values in higher layers replace
    lower ones.  Security keys (``allowed_tools``, ``forbidden_tools``) in
    *global_override* or *persona_override* are dropped with a WARN log.
    """
    result: dict[str, Any] = dict(defaults or {})

    for layer_name, layer in (
        ("global_override", global_override),
        ("persona_override", persona_override),
    ):
        if not layer:
            continue
        for key, value in layer.items():
            if key in _SECURITY_KEYS:
                logger.warning(
                    "llm_merge: dropping security key %r from %s — "
                    "allowed_tools/forbidden_tools are not overridable at "
                    "the workload_config layer",
                    key,
                    layer_name,
                )
                continue
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
