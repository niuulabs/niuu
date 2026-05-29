"""Tests for server-backed permission auto approval policy."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import InMemorySessionRepository, MockPodManager
from volundr.adapters.inbound.rest import create_router
from volundr.config import PermissionAutoApprovalConfig
from volundr.domain.models import Session, SessionStatus
from volundr.domain.services.permission_auto_approval import (
    evaluate_permission_auto_approval,
)
from volundr.domain.services.session import SessionService


def test_permission_auto_approval_allows_default_dev_flow_commands() -> None:
    policy = PermissionAutoApprovalConfig()

    start = evaluate_permission_auto_approval(
        input={"command": "./start-dev"},
        policy=policy,
    )
    stop = evaluate_permission_auto_approval(
        input={"command": "./stop-dev"},
        policy=policy,
    )

    assert start.can_auto_approve is True
    assert start.reason == "allowed"
    assert stop.can_auto_approve is True
    assert stop.reason == "allowed"


def test_permission_auto_approval_allows_shell_wrapped_dev_flow_commands() -> None:
    policy = PermissionAutoApprovalConfig()

    decision = evaluate_permission_auto_approval(
        input={"command": "/bin/zsh -lc ./start-dev"},
        policy=policy,
    )

    assert decision.can_auto_approve is True
    assert decision.reason == "allowed"
    assert decision.command == "./start-dev"


def test_permission_auto_approval_denies_shell_wrapped_dangerous_command() -> None:
    policy = PermissionAutoApprovalConfig(
        allowlist=[r".*"],
        denylist=[r"^rm\s+-rf"],
    )

    decision = evaluate_permission_auto_approval(
        input={"command": "/bin/zsh -lc 'rm -rf /tmp/volundr-test'"},
        policy=policy,
    )

    assert decision.can_auto_approve is False
    assert decision.reason == "denylist"
    assert decision.command == "rm -rf /tmp/volundr-test"


def test_permission_auto_approval_denies_before_allowing() -> None:
    policy = PermissionAutoApprovalConfig(
        allowlist=[r".*"],
        denylist=[r"rm\s+-rf"],
    )

    decision = evaluate_permission_auto_approval(
        command="rm -rf /tmp/volundr-test",
        policy=policy,
    )

    assert decision.can_auto_approve is False
    assert decision.reason == "denylist"


@pytest.mark.asyncio
async def test_permission_auto_approval_endpoint_checks_configured_policy() -> None:
    repository = InMemorySessionRepository()
    service = SessionService(
        repository=repository,
        pod_manager=MockPodManager(),
        validate_repos=False,
    )
    session = await repository.create(Session(name="policy-test", status=SessionStatus.RUNNING))

    app = FastAPI()
    app.state.settings = SimpleNamespace(
        permission_auto_approval=PermissionAutoApprovalConfig(
            enabled=True,
            delay_seconds=3,
            allowlist=[r"^echo\b"],
            denylist=[r"rm\s+-rf"],
        )
    )
    app.include_router(create_router(session_service=service))
    client = TestClient(app)

    allowed = client.post(
        f"/api/v1/forge/sessions/{session.id}/permissions/auto-approval/evaluate",
        json={
            "request_id": "perm-1",
            "tool_name": "Bash",
            "description": "echo ok",
            "input": {"command": "echo ok"},
        },
    )
    denied = client.post(
        f"/api/v1/forge/sessions/{session.id}/permissions/auto-approval/evaluate",
        json={
            "request_id": "perm-2",
            "tool_name": "Bash",
            "description": "rm -rf /tmp/volundr-test",
            "command": "rm -rf /tmp/volundr-test",
        },
    )

    assert allowed.status_code == 200
    assert allowed.json()["can_auto_approve"] is True
    assert allowed.json()["delay_seconds"] == 3
    assert denied.status_code == 200
    assert denied.json()["can_auto_approve"] is False
    assert denied.json()["reason"] == "denylist"
