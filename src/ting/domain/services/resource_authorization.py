"""Request-bound authorization around persisted Ting resources.

Background services retain their repositories. HTTP composition supplies these
ports so every lookup (including identifiers used before external side effects)
uses stored ownership and tenant attribution.
"""

from contextlib import asynccontextmanager

from identity.models import Resource
from identity.ports import AuthorizationDeniedError
from ting.domain.models import RunStatus
from ting.ports.saga_repository import SagaRepository
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository
from ting.ports.workflow_repository import WorkflowRepository


class _Policy:
    def __init__(self, repo, authorization, principal, *, read_action="read"):
        self.repo = repo
        self.authorization = authorization
        self.principal = principal
        self.read_action = read_action

    def resource(self, item, kind):
        attrs = {"owner_id": item.owner_id or "", "tenant_id": item.tenant_id}
        if kind == "workflow":
            attrs["scope"] = item.scope.value
        return Resource(kind, str(item.id), attrs)

    async def visible(self, item, kind):
        if item is None:
            return None
        if await self.authorization.is_allowed(
            self.principal, self.read_action, self.resource(item, kind)
        ):
            return item
        return None

    async def check(self, item, kind, action):
        if item is None or not await self.authorization.is_allowed(
            self.principal, action, self.resource(item, kind)
        ):
            raise AuthorizationDeniedError("Resource operation denied")

    async def save_check(self, item, existing, kind):
        if existing is not None:
            await self.check(existing, kind, "update")
            if (item.owner_id, item.tenant_id) != (existing.owner_id, existing.tenant_id):
                raise AuthorizationDeniedError("Resource ownership is immutable")
        await self.check(item, kind, "create" if existing is None else "update")


class AuthorizedWorkflowRepository(_Policy, WorkflowRepository):
    async def list_workflows(self, *, owner_id, scope=None):
        items = await self.repo.list_workflows(owner_id=owner_id, scope=scope)
        return [item for item in items if await self.visible(item, "workflow")]

    async def get_workflow(self, workflow_id):
        return await self.visible(await self.repo.get_workflow(workflow_id), "workflow")

    async def save_workflow(self, workflow):
        await self.save_check(workflow, await self.repo.get_workflow(workflow.id), "workflow")
        return await self.repo.save_workflow(workflow)

    async def delete_workflow(self, workflow_id):
        await self.check(await self.repo.get_workflow(workflow_id), "workflow", "delete")
        return await self.repo.delete_workflow(workflow_id)


class AuthorizedCampaignRepository(_Policy, WorkflowCampaignRepository):
    async def list_campaigns(self, *, owner_id):
        items = await self.repo.list_campaigns(owner_id=owner_id)
        return [item for item in items if await self.visible(item, "campaign")]

    async def list_active_campaigns(self):
        items = await self.repo.list_active_campaigns()
        return [item for item in items if await self.visible(item, "campaign")]

    async def get_campaign(self, campaign_id):
        return await self.visible(await self.repo.get_campaign(campaign_id), "campaign")

    async def get_campaign_by_slug(self, slug, *, owner_id=None):
        return await self.visible(
            await self.repo.get_campaign_by_slug(slug, owner_id=owner_id), "campaign"
        )

    async def save_campaign(self, campaign):
        await self.save_check(campaign, await self.repo.get_campaign(campaign.id), "campaign")
        return await self.repo.save_campaign(campaign)

    async def delete_campaign(self, campaign_id):
        await self.check(await self.repo.get_campaign(campaign_id), "campaign", "delete")
        return await self.repo.delete_campaign(campaign_id)


class AuthorizedSagaRepository(_Policy, SagaRepository):
    async def list_sagas(self, *, owner_id=None):
        items = await self.repo.list_sagas(owner_id=owner_id)
        return [item for item in items if await self.visible(item, "saga")]

    async def get_saga(self, saga_id, *, owner_id=None):
        return await self.visible(await self.repo.get_saga(saga_id, owner_id=owner_id), "saga")

    async def get_saga_by_slug(self, slug):
        return await self.visible(await self.repo.get_saga_by_slug(slug), "saga")

    async def save_saga(self, saga, *, conn=None):
        await self.save_check(
            saga,
            await self.repo.get_saga(saga.id, **({"conn": conn} if conn is not None else {})),
            "saga",
        )
        return await self.repo.save_saga(saga, conn=conn)

    async def delete_saga(self, saga_id, *, owner_id=None):
        await self.check(await self.repo.get_saga(saga_id), "saga", "delete")
        return await self.repo.delete_saga(saga_id, owner_id=owner_id)

    async def update_saga_status(self, saga_id, status):
        await self.check(await self.repo.get_saga(saga_id), "saga", "update")
        return await self.repo.update_saga_status(saga_id, status)

    async def update_saga_workflow(self, saga_id, **kwargs):
        await self.check(await self.repo.get_saga(saga_id), "saga", "update")
        return await self.repo.update_saga_workflow(saga_id, **kwargs)

    async def update_saga_target(self, saga_id, **kwargs):
        await self.check(await self.repo.get_saga(saga_id), "saga", "update")
        return await self.repo.update_saga_target(saga_id, **kwargs)

    async def get_phase(self, phase_id):
        phase = await self.repo.get_phase(phase_id)
        if phase is None or await self.get_saga(phase.saga_id) is None:
            return None
        return phase

    async def get_run(self, run_id):
        run = await self.repo.get_run(run_id)
        if run is None or await self.get_phase(run.phase_id) is None:
            return None
        return run

    async def get_runs_by_phase(self, phase_id):
        if await self.get_phase(phase_id) is None:
            return []
        return await self.repo.get_runs_by_phase(phase_id)

    async def get_phases_by_saga(self, saga_id):
        if await self.get_saga(saga_id) is None:
            return []
        return await self.repo.get_phases_by_saga(saga_id)

    async def save_phase(self, phase, *, conn=None):
        await self.check(
            await self.repo.get_saga(phase.saga_id, **({"conn": conn} if conn is not None else {})),
            "saga",
            "update",
        )
        existing = await self.repo.get_phase(
            phase.id, **({"conn": conn} if conn is not None else {})
        )
        if existing is not None and existing.saga_id != phase.saga_id:
            raise AuthorizationDeniedError("Phase parent is immutable")
        return await self.repo.save_phase(phase, conn=conn)

    async def save_run(self, run, *, conn=None):
        phase = await self.repo.get_phase(
            run.phase_id, **({"conn": conn} if conn is not None else {})
        )
        await self.check(
            await self.repo.get_saga(phase.saga_id, **({"conn": conn} if conn is not None else {}))
            if phase
            else None,
            "saga",
            "update",
        )
        existing = await self.repo.get_run(run.id, **({"conn": conn} if conn is not None else {}))
        if existing is not None and existing.phase_id != run.phase_id:
            raise AuthorizationDeniedError("Run parent is immutable")
        return await self.repo.save_run(run, conn=conn)

    async def update_run_outcome(self, run_id, outcome, event_type, status):
        run = await self.repo.get_run(run_id)
        phase = await self.repo.get_phase(run.phase_id) if run else None
        await self.check(
            await self.repo.get_saga(phase.saga_id) if phase else None, "saga", "update"
        )
        return await self.repo.update_run_outcome(run_id, outcome, event_type, status)

    async def count_by_status(self):
        counts = {status.value: 0 for status in RunStatus}
        for saga in await self.list_sagas():
            for phase in await self.repo.get_phases_by_saga(saga.id):
                for run in await self.repo.get_runs_by_phase(phase.id):
                    counts[run.status.value] += 1
        return counts

    @asynccontextmanager
    async def begin(self):
        async with self.repo.begin() as conn:
            yield conn
