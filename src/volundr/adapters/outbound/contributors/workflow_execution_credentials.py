"""Session contribution for rotated workflow-execution credentials."""

from __future__ import annotations

from volundr.domain.models import Session
from volundr.domain.ports import SessionContext, SessionContribution, SessionContributor
from volundr.domain.services.workflow_execution_credentials import (
    WorkflowExecutionCredentialService,
)


class WorkflowExecutionCredentialContributor(SessionContributor):
    """Materialize the initial token before starting a coordinator runtime."""

    def __init__(
        self,
        *,
        execution_credential_service: WorkflowExecutionCredentialService,
        **_extra: object,
    ) -> None:
        self._service = execution_credential_service

    @property
    def name(self) -> str:
        return "workflow_execution_credentials"

    async def contribute(
        self,
        session: Session,
        context: SessionContext,
    ) -> SessionContribution:
        projection = await self._service.project(session)
        if projection is None:
            return SessionContribution()
        return SessionContribution(
            values={
                "workflowExecutionCredential": {
                    "tokenFile": projection.token_file,
                }
            },
            pod_spec=projection.pod_spec,
        )

    async def cleanup(self, session: Session, context: SessionContext) -> None:
        await self._service.remove(session.id)
