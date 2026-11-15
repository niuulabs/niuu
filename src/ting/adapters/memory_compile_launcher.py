"""Starts the bundled memory-compile workflow for a project, as the person who set up the import.

An import runs in the background, so there is no request to launch from. The run is started
the way Ting starts a ticket's run from the background: its campaign is built here, pinned to
the workflow's snapshot, and handed to the step engine. Its sessions run on the owner's own
Forge connection, in the project, so the project's memory layer is theirs to write to; the
owner must still be an owner of the project on that Forge.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from niuu.domain.models import Principal
from personas.ports.persona import PersonaPort
from ting.api.workflows import fail_unstarted_campaign
from ting.domain.memory_imports import (
    COMPILE_CONTEXT_SOURCE_IDS,
    COMPILE_PROMPT,
    MEMORY_COMPILE_WORKFLOW_ID,
    CompileLaunchError,
)
from ting.domain.models import WorkflowCampaign, WorkflowCampaignStatus
from ting.domain.services.run_thread import RUN_PROJECT_KEY
from ting.domain.services.workflow_runs import WorkflowRuns
from ting.domain.workflow_snapshot import build_workflow_snapshot
from ting.ports.memory_imports import CompileLauncher
from ting.ports.volundr import VolundrFactory, VolundrPort
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository
from ting.ports.workflow_repository import WorkflowRepository

logger = logging.getLogger(__name__)

#: ``surface`` of a run this launcher started.
COMPILE_SURFACE = "memory.compile"
#: Role on a Forge project that may set up and compile its memory.
PROJECT_OWNER_ROLE = "owner"


class WorkflowCompileLauncher(CompileLauncher):
    def __init__(
        self,
        *,
        workflows: WorkflowRepository,
        campaigns: WorkflowCampaignRepository,
        runs: Callable[[], WorkflowRuns],
        volundr_factory: VolundrFactory,
        persona_source: PersonaPort | None,
    ) -> None:
        self._workflows = workflows
        self._campaigns = campaigns
        self._runs = runs
        self._volundr_factory = volundr_factory
        self._persona_source = persona_source

    async def launch(
        self,
        *,
        project_id: UUID,
        tenant_id: str,
        owner_id: str,
        source_ids: Sequence[str],
    ) -> str:
        if not source_ids:
            raise CompileLaunchError("a compile run needs at least one source")
        workflow = await self._workflows.get_workflow(MEMORY_COMPILE_WORKFLOW_ID)
        if workflow is None:
            raise CompileLaunchError(
                f"the bundled memory-compile workflow {MEMORY_COMPILE_WORKFLOW_ID} is not in "
                "this Ting's catalogue; set workflow_repository.seed_bundled: true"
            )
        try:
            snapshot = build_workflow_snapshot(workflow, persona_source=self._persona_source)
        except ValueError as exc:
            raise CompileLaunchError(
                f"the memory-compile workflow cannot be pinned for a run: {exc}"
            ) from exc
        principal = Principal(user_id=owner_id, email="", tenant_id=tenant_id, roles=[])
        forge = await self._owners_forge(project_id, principal)
        campaign_id = uuid4()
        slug = f"memory-compile-{campaign_id.hex[:12]}"
        now = datetime.now(UTC)
        context = {
            COMPILE_CONTEXT_SOURCE_IDS: list(source_ids),
            "project_id": str(project_id),
            "layer": f"project:{project_id}",
        }
        brief = _brief(workflow.name, slug, context)
        campaign = WorkflowCampaign(
            id=campaign_id,
            tenant_id=tenant_id,
            slug=slug,
            name=f"Compile {len(source_ids)} source(s) into project memory",
            owner_id=owner_id,
            workflow_id=workflow.id,
            workflow_version=workflow.version,
            workflow_name=workflow.name,
            workflow_snapshot=snapshot,
            session_id="",
            session_name="",
            status=WorkflowCampaignStatus.PENDING,
            active_stage_id=None,
            stage_state=[],
            metadata={
                "surface": COMPILE_SURFACE,
                "launch_slug": slug,
                RUN_PROJECT_KEY: str(project_id),
                "prompt": COMPILE_PROMPT,
                "context": context,
            },
            created_at=now,
            updated_at=now,
            last_activity_at=now,
            connection_id=forge.target_id or None,
        )
        saved = await self._campaigns.save_campaign(campaign)
        payload = {"prompt": COMPILE_PROMPT, "initiative_context": brief, "context": context}
        try:
            await self._runs().launch(saved, payload)
        except Exception as exc:
            logger.error("Memory compile run %s could not start: %s", slug, exc, exc_info=True)
            await fail_unstarted_campaign(self._campaigns, saved, str(exc))
            raise CompileLaunchError(f"the compile run {slug} could not start: {exc}") from exc
        logger.info(
            "Started compile run %s for %d source(s) of project %s as %s",
            slug,
            len(source_ids),
            project_id,
            owner_id,
        )
        return str(saved.id)

    async def _owners_forge(self, project_id: UUID, principal: Principal) -> VolundrPort:
        """The owner's Forge that has the project, once they are an owner of it there."""
        forges = await self._volundr_factory.for_principal(principal)
        if not forges:
            raise CompileLaunchError(
                f"{principal.user_id} has no Forge connection, so the compile run has nowhere "
                "to start; connect a Forge for them in Guild"
            )
        for forge in forges:
            role = await forge.get_project_role(str(project_id), principal=principal)
            if role is None:
                continue
            if role != PROJECT_OWNER_ROLE:
                raise CompileLaunchError(
                    f"{principal.user_id} is a {role} of project {project_id}, no longer an "
                    "owner; only an owner's imports compile into the project's memory"
                )
            return forge
        raise CompileLaunchError(
            f"project {project_id} is not on any Forge {principal.user_id} is connected to"
        )


def _brief(workflow_name: str, slug: str, context: dict) -> str:
    return "\n".join(
        [
            "# Workflow Launch",
            "",
            f"Workflow: {workflow_name}",
            f"Slug: {slug}",
            "",
            "## Prompt",
            COMPILE_PROMPT,
            "",
            "## Launch Context",
            json.dumps(context, indent=2, sort_keys=True),
        ]
    )
