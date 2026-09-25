"""The broker's own gate-resolve route requires the stamped room role.

Defense-in-depth: a caller who reaches this route via the generic session-proxy
HTTP passthrough (/s/{id}/api/...) only needed "attach" to get there — any
active participant, not just an approver/owner. This route additionally
requires the session-proxy-verified x-niuu-room-role header to be owner or
approver, independent of Volundr's own REST-layer Cedar check (which stamps
its own "approver" value on its direct-to-broker call; see
volundr.adapters.inbound.rest.ROOM_ROLE_HEADER).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from skuld.broker_api import (
    WORKFLOW_GATE_INTENT_HEADER,
    WORKFLOW_GATE_INTENT_RESOLVE,
    _WorkflowGateResolveRequest,
    resolve_workflow_gate,
)


def _request(room_role: str | None, *, intent: str | None = WORKFLOW_GATE_INTENT_RESOLVE):
    headers = {}
    if intent is not None:
        headers[WORKFLOW_GATE_INTENT_HEADER] = intent
    if room_role is not None:
        headers["x-niuu-room-role"] = room_role
    return SimpleNamespace(headers=headers)


def _body() -> _WorkflowGateResolveRequest:
    return _WorkflowGateResolveRequest(decision="APPROVE")


@pytest.mark.parametrize("role", ["viewer", "not-a-real-role", "", None])
async def test_insufficient_or_missing_room_role_is_refused(role):
    with pytest.raises(HTTPException) as exc_info:
        await resolve_workflow_gate(_request(role), "gate-1", _body())
    assert exc_info.value.status_code == 403


@pytest.mark.parametrize("role", ["owner", "approver"])
async def test_owner_or_approver_room_role_reaches_the_broker(role, monkeypatch):
    fake_broker = SimpleNamespace(
        resolve_workflow_gate=AsyncMock(return_value={"id": "gate-1", "status": "resolved"})
    )
    monkeypatch.setattr("skuld.broker_api.broker", fake_broker)
    result = await resolve_workflow_gate(_request(role), "gate-1", _body())
    assert result["status"] == "resolved"
    fake_broker.resolve_workflow_gate.assert_awaited_once()


async def test_intent_header_check_runs_before_the_room_role_check():
    """Both guards are independent; a request missing both fails on intent
    first (428), not room role (403) — the code checks intent, then role."""
    with pytest.raises(HTTPException) as exc_info:
        await resolve_workflow_gate(_request(None, intent=None), "gate-1", _body())
    assert exc_info.value.status_code == 428


async def test_case_insensitive_role_matching():
    fake_broker = SimpleNamespace(
        resolve_workflow_gate=AsyncMock(return_value={"id": "gate-1", "status": "resolved"})
    )
    import skuld.broker_api as broker_api_module

    original = broker_api_module.broker
    broker_api_module.broker = fake_broker
    try:
        result = await resolve_workflow_gate(_request("APPROVER"), "gate-1", _body())
        assert result["status"] == "resolved"
    finally:
        broker_api_module.broker = original
