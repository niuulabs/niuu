"""Where the step engine finds what a run executes: the campaign's pinned snapshot.

A run is a campaign, and the campaign holds the workflow exactly as it was pinned
when the run was created: the resolved graph, the persona documents its stage
members run as, and the pins those documents were checked against. The engine reads
that, never the live workflow, so editing a workflow cannot change a run in flight.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from ting.domain.exceptions import WorkflowDocumentError
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository
from ting.ports.workflow_steps import PersonaRef, StepRunDefinition, StepRunDefinitions


class CampaignStepRunDefinitions(StepRunDefinitions):
    """The definition of a run, from the snapshot its campaign carries."""

    def __init__(self, campaigns: WorkflowCampaignRepository) -> None:
        self._campaigns = campaigns

    async def get(self, run_id: UUID) -> StepRunDefinition:
        campaign = await self._campaigns.get_campaign(run_id)
        if campaign is None:
            raise LookupError(f"workflow run {run_id} has no campaign, so it has no definition")
        return definition_of_snapshot(campaign.workflow_snapshot, run_id=run_id)


def definition_of_snapshot(snapshot: Mapping[str, Any], *, run_id: UUID) -> StepRunDefinition:
    """The engine's definition of a pinned workflow snapshot."""
    graph = snapshot.get("graph")
    if not isinstance(graph, Mapping):
        raise WorkflowDocumentError(f"run {run_id}: its pinned workflow snapshot has no graph")
    definitions = snapshot.get("persona_definitions")
    dependencies = snapshot.get("persona_dependencies")
    persona_definitions = (
        {str(alias): document for alias, document in definitions.items()}
        if isinstance(definitions, Mapping)
        else {}
    )
    persona_refs: dict[str, PersonaRef] = {}
    if isinstance(dependencies, Mapping):
        for alias, pin in dependencies.items():
            if not isinstance(pin, Mapping):
                raise WorkflowDocumentError(
                    f"run {run_id}: persona dependency {alias!r} of its pinned workflow "
                    "snapshot is not an object"
                )
            persona_refs[str(alias)] = PersonaRef(
                alias=str(alias),
                id=str(pin.get("id") or ""),
                revision=str(pin.get("revision") or ""),
                digest=str(pin.get("digest") or ""),
            )
    return StepRunDefinition(
        graph=graph,
        persona_definitions=persona_definitions,
        persona_refs=persona_refs,
        workflow_name=str(snapshot.get("name") or ""),
    )
