"""Per-sidecar persona override application for Ravn flock deployments.

When Volundr dispatches a flock, it embeds per-persona overrides (system
prompt extra text, iteration budget) into each sidecar's YAML config under
the ``persona_overrides`` key.  This module reads those overrides from
``Settings.persona_overrides`` and applies them to the resolved
``PersonaConfig`` before the agent loop starts.

Concatenation order for system_prompt_extra (lowest → highest priority):
  1. Persona's built-in system_prompt_template
  2. persona_overrides.system_prompt_extra (injected by Volundr)

Security keys (``allowed_tools``, ``forbidden_tools``) are silently dropped
with a WARN log — they are not overridable at the workload_config layer.
"""

from __future__ import annotations

import dataclasses
import logging

from niuu.domain.llm_merge import _SECURITY_KEYS, concat_prompt_extras
from ravn.adapters.personas.loader import PersonaConfig

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

    # Security keys are not overridable at the workload_config layer.
    for key in _SECURITY_KEYS:
        if key in overrides:
            logger.warning(
                "persona_overrides: dropping security key %r — "
                "allowed_tools/forbidden_tools are not overridable at the "
                "workload_config layer",
                key,
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

    return persona
