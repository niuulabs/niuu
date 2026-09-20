from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from volundr.adapters.outbound.contributors.workflow_execution_credentials import (
    WorkflowExecutionCredentialContributor,
)
from volundr.domain.models import PodSpecAdditions, Session
from volundr.domain.ports import SessionContext
from volundr.ports.workflow_execution_credentials import ExecutionCredentialProjection


@pytest.mark.asyncio
async def test_contributor_exposes_only_path_and_mount_contract() -> None:
    service = AsyncMock()
    service.project.return_value = ExecutionCredentialProjection(
        token_file="/run/developer/token",
        pod_spec=PodSpecAdditions(volumes=({"name": "credential"},)),
    )
    contributor = WorkflowExecutionCredentialContributor(execution_credential_service=service)
    session = Session(name="developer")

    result = await contributor.contribute(session, SessionContext())

    assert result.values == {"workflowExecutionCredential": {"tokenFile": "/run/developer/token"}}
    assert result.values["workflowExecutionCredential"] == {"tokenFile": "/run/developer/token"}
    assert len(result.values["workflowExecutionCredential"]) == 1
    assert result.pod_spec is not None
    await contributor.cleanup(session, SessionContext())
    service.remove.assert_awaited_once_with(session.id)
