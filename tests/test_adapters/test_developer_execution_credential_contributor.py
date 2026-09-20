from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from volundr.adapters.outbound.contributors.developer_execution_credentials import (
    DeveloperExecutionCredentialContributor,
)
from volundr.domain.models import PodSpecAdditions, Session
from volundr.domain.ports import SessionContext
from volundr.ports.developer_execution_credentials import DeveloperCredentialProjection


@pytest.mark.asyncio
async def test_contributor_exposes_only_path_and_mount_contract() -> None:
    service = AsyncMock()
    service.project.return_value = DeveloperCredentialProjection(
        token_file="/run/developer/token",
        pod_spec=PodSpecAdditions(volumes=({"name": "credential"},)),
    )
    contributor = DeveloperExecutionCredentialContributor(developer_credential_service=service)
    session = Session(name="developer")

    result = await contributor.contribute(session, SessionContext())

    assert result.values == {"developerExecutionCredential": {"tokenFile": "/run/developer/token"}}
    assert result.values["developerExecutionCredential"] == {"tokenFile": "/run/developer/token"}
    assert len(result.values["developerExecutionCredential"]) == 1
    assert result.pod_spec is not None
    await contributor.cleanup(session, SessionContext())
    service.remove.assert_awaited_once_with(session.id)
