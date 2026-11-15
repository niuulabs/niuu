"""Per-workload persona override application.

A workload's configuration carries per-persona overrides (system prompt extra
text, iteration budget, consumed event types) under the ``persona_overrides``
key.  This module applies them to the resolved ``PersonaConfig`` before the
worker starts.

Concatenation order for system_prompt_extra (lowest → highest priority):
  1. Persona's built-in system_prompt_template
  2. persona_overrides.system_prompt_extra (injected by Volundr)

``allowed_tools`` and ``forbidden_tools`` are refused: per-harness tool
restrictions are not available yet.
"""

from __future__ import annotations

import dataclasses
import logging

from niuu.domain.llm_merge import REMOVED_TOOL_RESTRICTION_KEYS, concat_prompt_extras
from personas.domain.persona import PersonaConfig, PersonaConsumes

logger = logging.getLogger(__name__)


def apply_config_overrides(persona: PersonaConfig, overrides: dict) -> PersonaConfig:
    """Apply ``persona_overrides`` block from sidecar YAML to a loaded PersonaConfig.

    Args:
        persona:   The resolved PersonaConfig from the persona registry.
        overrides: The ``persona_overrides`` dict from the sidecar YAML config.
                   Typically obtained via
                   ``settings.persona_overrides.model_dump(exclude_defaults=True)``.

    Returns:
        A new (frozen) PersonaConfig with overrides applied.  The original
        is never mutated.
    """
    if not overrides:
        return persona

    refused = [key for key in REMOVED_TOOL_RESTRICTION_KEYS if key in overrides]
    if refused:
        raise ValueError(
            f"persona_overrides for persona {persona.name!r} set {', '.join(refused)}: "
            "per-harness tool restrictions are not available yet; remove allowed_tools "
            "(and forbidden_tools) from the persona_overrides block."
        )

    # Concatenate system_prompt_extra onto the persona's base template.
    extra = overrides.get("system_prompt_extra") or ""
    if extra.strip():
        new_prompt = concat_prompt_extras(persona.system_prompt_template, extra)
        logger.info(
            "persona_overrides: appending system_prompt_extra to persona %r (+%d chars)",
            persona.name,
            len(extra),
        )
        persona = dataclasses.replace(persona, system_prompt_template=new_prompt)

    # Override iteration_budget when a non-zero value is provided.
    budget = overrides.get("iteration_budget") or 0
    if budget:
        logger.info(
            "persona_overrides: setting iteration_budget=%d for persona %r",
            budget,
            persona.name,
        )
        persona = dataclasses.replace(persona, iteration_budget=int(budget))

    consumes_event_types = overrides.get("consumes_event_types") or []
    if consumes_event_types:
        logger.info(
            "persona_overrides: replacing consumes.event_types for persona %r -> %s",
            persona.name,
            consumes_event_types,
        )
        persona = dataclasses.replace(
            persona,
            consumes=PersonaConsumes(
                event_types=list(consumes_event_types),
                injects=list(persona.consumes.injects),
                schema=dict(persona.consumes.schema),
            ),
        )

    if "executor" in overrides:
        raise ValueError(
            f"persona_overrides for persona {persona.name!r} set 'executor'. A persona no "
            "longer names the harness that runs it; remove the 'executor' key from the "
            "persona_overrides block."
        )

    return persona
