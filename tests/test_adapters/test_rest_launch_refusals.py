"""LEX-1858: a refused session launch (409) says why in the journal, not only the response."""

from __future__ import annotations

from uuid import UUID

from volundr.adapters.inbound.rest import _refused_session_launch
from volundr.domain.models import SessionStatus
from volundr.domain.ports import SessionCapacity
from volundr.domain.services.session import SessionCapacityError, SessionStateError


def test_a_slot_cap_refusal_is_logged_with_its_numbers(caplog) -> None:
    caplog.set_level("WARNING", logger="volundr.adapters.inbound.rest")
    error = SessionCapacityError(
        SessionCapacity(limit=32, active=32, remedy="raise pod_manager.max_concurrent")
    )

    refused = _refused_session_launch(error)

    assert refused.status_code == 409
    assert refused.detail == str(error)  # the response keeps the reason
    assert "Session launch refused (409, slot cap): 32 of 32 running" in caplog.text


def test_a_session_state_refusal_is_logged(caplog) -> None:
    caplog.set_level("WARNING", logger="volundr.adapters.inbound.rest")
    session_id = UUID("6a3f2b1c-0d9e-4f8a-b7c6-5d4e3f2a1b0c")
    error = SessionStateError(session_id, "start", SessionStatus.STOPPING)

    refused = _refused_session_launch(error)

    assert (refused.status_code, refused.detail) == (409, str(error))
    assert f"Session launch refused (409, session state): Cannot start session {session_id}" in (
        caplog.text
    )


async def test_a_project_conflict_is_logged(caplog) -> None:
    import pytest
    from fastapi import HTTPException

    from volundr.adapters.inbound.rest_projects import project_result
    from volundr.domain.project_ports import ProjectConflictError

    caplog.set_level("WARNING", logger="volundr.adapters.inbound.rest_projects")

    async def conflicting():
        raise ProjectConflictError("Project context changed; refresh its revision before dispatch")

    with pytest.raises(HTTPException) as refused:
        await project_result(conflicting())

    assert refused.value.status_code == 409
    assert "Project request refused (409): Project context changed" in caplog.text
