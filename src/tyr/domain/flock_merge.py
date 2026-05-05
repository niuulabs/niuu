"""Flock-flow persona merge helpers (NIU-644).

Shared by ``pipeline_executor`` and ``dispatch_service`` — do not import
heavier orchestration modules from here.
"""

from __future__ import annotations

import logging

from niuu.domain.llm_merge import concat_prompt_extras, is_empty, merge_llm
from tyr.domain.templates import TemplateRaid
from tyr.ports.flock_flow import FlockFlowProvider

logger = logging.getLogger(__name__)


def merge_persona_override(flow_persona: dict, stage_override: dict) -> dict:
    """Apply a stage-level ``persona_overrides`` dict onto a flow-level persona dict.

    Merge rules:

    - ``llm``: merged via :func:`niuu.domain.llm_merge.merge_llm` (non-empty
      stage values replace flow values; security keys are dropped with a warning).
    - ``system_prompt_extra``: concatenated across layers (flow then stage).
    - Other non-empty, non-reserved fields: stage override wins.
    - ``name`` is always preserved from *flow_persona*.

    :param flow_persona: A persona dict from ``FlockFlowConfig.personas[i].to_dict()``.
    :param stage_override: The raw ``persona_overrides`` dict from the template YAML.
    :returns: Merged persona dict.
    """
    result = dict(flow_persona)

    # Merge LLM config
    stage_llm = stage_override.get("llm") or {}
    if stage_llm:
        result["llm"] = merge_llm(
            defaults=result.get("llm"),
            persona_override=stage_llm,
        )

    # Concatenate system_prompt_extra
    combined = concat_prompt_extras(
        result.get("system_prompt_extra"),
        stage_override.get("system_prompt_extra"),
    )
    if combined:
        result["system_prompt_extra"] = combined
    else:
        result.pop("system_prompt_extra", None)

    # Apply other non-empty fields (name, llm, system_prompt_extra already handled)
    for k, v in stage_override.items():
        if k in ("name", "llm", "system_prompt_extra"):
            continue
        if not is_empty(v):
            result[k] = v

    return result


def build_flock_workload_config(
    flow_name: str,
    tpl_raid: TemplateRaid,
    flow_provider: FlockFlowProvider | None,
    initial_prompt: str,
) -> dict | None:
    """Build a ``workload_config`` dict for a flock session dispatch.

    Resolves *flow_name* via *flow_provider*, applies any
    ``tpl_raid.persona_overrides`` to the matching persona, and packages
    the result for use in a :class:`~tyr.ports.volundr.SpawnRequest`.

    Merge order (last wins):
    ``PersonaConfig defaults`` ← ``FlockFlowConfig.personas[matching]``
    ← ``pipeline.stage.persona_overrides``

    :param flow_name: Named flock flow from the saga template.
    :param tpl_raid: The raid being dispatched (provides persona name and overrides).
    :param flow_provider: Provider used to resolve the flow definition.
    :param initial_prompt: The raid prompt, stored as ``initiative_context``.
    :returns: ``workload_config`` dict, or ``None`` when the flow cannot be
        resolved (caller should fall back to a solo dispatch).
    """
    if not flow_name or flow_provider is None:
        return None

    flow = flow_provider.get(flow_name)
    if flow is None:
        logger.warning(
            "flock_merge: flow %r not found — dispatching raid %r without flock config",
            flow_name,
            tpl_raid.name,
        )
        return None

    # Start with the full flow persona list
    personas: list[dict] = [p.to_dict() for p in flow.personas]

    # Apply per-raid overrides to the matching persona
    if tpl_raid.persona_overrides and tpl_raid.persona:
        personas = [
            merge_persona_override(p, tpl_raid.persona_overrides)
            if p.get("name") == tpl_raid.persona
            else p
            for p in personas
        ]

    workload_config: dict = {
        "personas": personas,
        "initiative_context": initial_prompt,
        "mesh_transport": flow.mesh_transport,
    }
    if flow.mimir:
        workload_config["mimir"] = dict(flow.mimir)
    if flow.mimir_hosted_url:
        workload_config["mimir_hosted_url"] = flow.mimir_hosted_url
    if flow.sleipnir_publish_urls:
        workload_config["sleipnir_publish_urls"] = list(flow.sleipnir_publish_urls)

    return workload_config
