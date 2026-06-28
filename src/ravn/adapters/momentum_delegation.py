"""Static Momentum delegation target catalog adapter."""

from __future__ import annotations

from ravn.momentum.models import MomentumDelegationTarget

_DEFAULT_TARGETS = [
    {
        "target_id": "human",
        "target_kind": "human",
        "display_name": "Human operator",
        "supported_proposal_kinds": ["human_question", "no_delegation_needed"],
        "authority_boundary": "human_review_required",
        "risk_level": "operator_review",
        "notes": "Use for clarification or explicit human decision requests.",
    },
    {
        "target_id": "codex",
        "target_kind": "codex",
        "display_name": "Codex coding agent",
        "supported_proposal_kinds": ["codex_task"],
        "authority_boundary": "proposal_only_no_execution",
        "risk_level": "workspace_change_proposal",
        "notes": "Prepare coding work only; do not create or start a task.",
    },
    {
        "target_id": "ravn",
        "target_kind": "ravn",
        "display_name": "Ravn action",
        "supported_proposal_kinds": ["ravn_action_request"],
        "authority_boundary": "proposal_only_no_execution",
        "risk_level": "resident_action_proposal",
        "notes": "Prepare a resident action request artifact only.",
    },
    {
        "target_id": "ting",
        "target_kind": "ting",
        "display_name": "Ting workflow",
        "supported_proposal_kinds": ["ting_workflow_proposal"],
        "authority_boundary": "proposal_only_no_execution",
        "risk_level": "workflow_proposal",
        "notes": "Prepare workflow intent only; do not create a workflow.",
    },
    {
        "target_id": "skuld",
        "target_kind": "skuld",
        "display_name": "Skuld huddle",
        "supported_proposal_kinds": ["skuld_huddle"],
        "authority_boundary": "proposal_only_no_execution",
        "risk_level": "coordination_proposal",
        "notes": "Prepare huddle intent only; do not contact anyone.",
    },
    {
        "target_id": "capability_proposal",
        "target_kind": "capability_proposal",
        "display_name": "Capability proposal",
        "supported_proposal_kinds": ["capability_proposal"],
        "authority_boundary": "proposal_only_no_registration",
        "risk_level": "capability_candidate",
        "notes": "Record a capability gap/proposal only; do not register it.",
    },
    {
        "target_id": "none",
        "target_kind": "none",
        "display_name": "No delegation needed",
        "supported_proposal_kinds": ["no_delegation_needed"],
        "authority_boundary": "no_execution",
        "risk_level": "none",
        "notes": "Use when the judgment only updates memory or is not actionable.",
    },
]


class StaticMomentumDelegationTargetCatalog:
    """Config-backed read-only target catalog."""

    def __init__(self, targets: list[dict] | None = None) -> None:
        self._targets = [
            MomentumDelegationTarget.model_validate(item)
            for item in (targets or _DEFAULT_TARGETS)
        ]

    async def list_targets(self) -> list[MomentumDelegationTarget]:
        return list(self._targets)
