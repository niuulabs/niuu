"""Operator route for a session's Forge MCP grants, and grants by project role (LEX-1506)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from niuu.domain.services.token_scope import (
    FORGE_NOTIFY_SCOPE,
    FORGE_SESSION_LIFECYCLE_SCOPE,
    FORGE_SESSION_MESSAGE_SCOPE,
    FORGE_SESSION_READ_SCOPE,
)
from niuu.forge_mcp.credentials import effective_grants
from niuu.forge_mcp.models import ForgeMcpGrant
from sleipnir.domain import registry
from tests.support.forge_session import OWNER, PREFIX, TENANT, bearer, build_forge_app
from volundr.config import ForgeMcpConfig
from volundr.domain.models import Principal, SessionStatus
from volundr.domain.projects import SessionCoordination
from volundr.domain.services.forge_session_launch import (
    ForgeMcpGrantChangeRefusedError,
    ForgeMcpGrantEscalationError,
)

COORDINATOR_GRANTS = {"coordinator": ["lifecycle", "message"]}


class InMemoryAudit:
    def __init__(self) -> None:
        self.events: list = []

    async def append(self, event) -> None:
        self.events.append(event)

    async def query(self, q):  # pragma: no cover - not used
        raise AssertionError("not used")

    async def purge_expired(self) -> int:  # pragma: no cover - not used
        raise AssertionError("not used")


class FailingAudit(InMemoryAudit):
    async def append(self, event) -> None:
        raise ConnectionError("audit store unreachable")


def _forge(audit=None, **kwargs):
    kwargs.setdefault("forge_mcp_role_grants", COORDINATOR_GRANTS)
    return build_forge_app(audit_repository=audit or InMemoryAudit(), **kwargs)


def _filed(role: str) -> dict:
    return {"coordination": SessionCoordination(project_id=uuid4(), role=role)}


def _session_principal(*scopes: str, token_use: str = "forge_session") -> Principal:
    return Principal(
        user_id=OWNER,
        email="",
        tenant_id=TENANT,
        roles=["volundr:developer"],
        token_use=token_use,
        scopes=(FORGE_NOTIFY_SCOPE, FORGE_SESSION_READ_SCOPE, *scopes),
        bound_session_id="caller",
    )


class TestOperatorRoute:
    async def test_put_replaces_stored_grants_and_keeps_the_launch(self) -> None:
        audit = InMemoryAudit()
        forge = _forge(audit)
        session = await forge.session()
        first = await forge.start(session)

        response = forge.client.put(
            f"{PREFIX}/sessions/{session.id}/forge-mcp", json={"grants": ["message", "lifecycle"]}
        )

        assert response.status_code == 200, response.text
        assert response.json() == {
            "session_id": str(session.id),
            "grants": ["lifecycle", "message"],
            "default_grants": [],
            "role": None,
            "role_grants": [],
            "effective_grants": ["lifecycle", "message"],
            "applies_at": "next_start",
        }
        stored = await forge.sessions.get(session.id)
        assert stored.workload_config["forge_mcp"] == {
            "grants": ["lifecycle", "message"],
            "launch_id": first.launch_id,  # the running launch keeps its credential
        }

    async def test_next_start_mints_the_new_grants(self) -> None:
        forge = _forge()
        session = await forge.session()
        before = await forge.start(session)
        assert effective_grants(before.token, None) == frozenset()

        forge.client.put(f"{PREFIX}/sessions/{session.id}/forge-mcp", json={"grants": ["message"]})
        await forge.set_status(session.id, SessionStatus.STOPPED)
        after = await forge.start(session)

        assert FORGE_SESSION_MESSAGE_SCOPE in after.scopes
        # what the Forge MCP environment tool reports (it reads the token's scopes)
        assert effective_grants(after.token, None) == {ForgeMcpGrant.MESSAGE}

    async def test_empty_list_removes_grants(self) -> None:
        forge = _forge()
        session = await forge.session()
        forge.client.put(f"{PREFIX}/sessions/{session.id}/forge-mcp", json={"grants": ["message"]})
        response = forge.client.put(
            f"{PREFIX}/sessions/{session.id}/forge-mcp", json={"grants": []}
        )
        assert response.json()["grants"] == []
        stored = await forge.sessions.get(session.id)
        assert stored.workload_config["forge_mcp"]["grants"] == []

    async def test_get_reports_grants_by_source(self) -> None:
        forge = _forge(forge_mcp_default_grants=["message"])
        session = await forge.session(**_filed("coordinator"))
        forge.client.put(
            f"{PREFIX}/sessions/{session.id}/forge-mcp", json={"grants": ["lifecycle"]}
        )

        response = forge.client.get(f"{PREFIX}/sessions/{session.id}/forge-mcp")

        assert response.status_code == 200
        body = response.json()
        assert body["grants"] == ["lifecycle"]
        assert body["default_grants"] == ["message"]
        assert body["role"] == "coordinator"
        assert body["role_grants"] == ["lifecycle", "message"]
        assert body["effective_grants"] == ["lifecycle", "message"]

    async def test_change_is_audited(self) -> None:
        audit = InMemoryAudit()
        forge = _forge(audit)
        session = await forge.session()
        forge.client.put(f"{PREFIX}/sessions/{session.id}/forge-mcp", json={"grants": ["message"]})
        forge.client.put(
            f"{PREFIX}/sessions/{session.id}/forge-mcp", json={"grants": ["lifecycle"]}
        )

        assert [e.event_type for e in audit.events] == [
            registry.VOLUNDR_SESSION_FORGE_MCP_GRANTS_CHANGED
        ] * 2
        last = audit.events[-1]
        assert last.payload["session_id"] == str(session.id)
        assert last.payload["owner_id"] == OWNER
        assert last.payload["previous_grants"] == ["message"]
        assert last.payload["grants"] == ["lifecycle"]
        assert last.payload["actor_token_use"] == ""
        assert last.correlation_id == str(session.id)
        assert last.tenant_id == TENANT

    async def test_unknown_grant_is_422(self) -> None:
        audit = InMemoryAudit()
        forge = _forge(audit)
        session = await forge.session()
        response = forge.client.put(
            f"{PREFIX}/sessions/{session.id}/forge-mcp", json={"grants": ["root"]}
        )
        assert response.status_code == 422
        assert audit.events == []

    async def test_missing_session_is_404(self) -> None:
        forge = _forge()
        missing = uuid4()
        put = forge.client.put(f"{PREFIX}/sessions/{missing}/forge-mcp", json={"grants": []})
        get = forge.client.get(f"{PREFIX}/sessions/{missing}/forge-mcp")
        assert put.status_code == 404 and get.status_code == 404

    async def test_failed_audit_write_raises(self) -> None:
        forge = _forge(FailingAudit())
        session = await forge.session()
        with pytest.raises(ConnectionError):
            await forge.session_service.set_forge_mcp_grants(session.id, ["message"], None)

    async def test_without_an_audit_repository_nothing_changes(self) -> None:
        forge = build_forge_app()
        session = await forge.session()
        with pytest.raises(RuntimeError, match="audit"):
            await forge.session_service.set_forge_mcp_grants(session.id, ["message"], None)
        stored = await forge.sessions.get(session.id)
        assert "forge_mcp" not in stored.workload_config


class TestEscalationRefusal:
    async def test_session_credential_cannot_widen_its_own_grants(self) -> None:
        audit = InMemoryAudit()
        forge = _forge(audit)
        session = await forge.session()
        credential = await forge.start(session)

        response = forge.client.put(
            f"{PREFIX}/sessions/{session.id}/forge-mcp",
            json={"grants": ["message", "lifecycle"]},
            headers=bearer(credential.token),
        )

        assert response.status_code == 403
        stored = await forge.sessions.get(session.id)
        assert stored.workload_config["forge_mcp"]["grants"] == []
        assert audit.events == []

    async def test_fully_granted_session_cannot_change_a_peer(self) -> None:
        audit = InMemoryAudit()
        forge = _forge(audit)
        caller = await forge.session("caller")
        forge.client.put(
            f"{PREFIX}/sessions/{caller.id}/forge-mcp", json={"grants": ["message", "lifecycle"]}
        )
        credential = await forge.start(caller)
        peer = await forge.session("peer")

        for method in ("get", "put"):
            response = forge.client.request(
                method.upper(),
                f"{PREFIX}/sessions/{peer.id}/forge-mcp",
                json={"grants": ["message"]} if method == "put" else None,
                headers=bearer(credential.token),
            )
            assert response.status_code == 403, method
        stored = await forge.sessions.get(peer.id)
        assert "forge_mcp" not in stored.workload_config
        assert len(audit.events) == 1  # the operator's own change only

    @pytest.mark.parametrize("token_use", ["forge_session", "valkyrie_build"])
    async def test_service_refuses_any_scoped_credential(self, token_use: str) -> None:
        forge = _forge()
        session = await forge.session()
        principal = _session_principal(
            FORGE_SESSION_LIFECYCLE_SCOPE, FORGE_SESSION_MESSAGE_SCOPE, token_use=token_use
        )
        with pytest.raises(ForgeMcpGrantChangeRefusedError, match=token_use):
            await forge.session_service.set_forge_mcp_grants(session.id, ["message"], principal)
        with pytest.raises(ForgeMcpGrantChangeRefusedError):
            await forge.session_service.get_forge_mcp_grants(session.id, principal)

    def test_cannot_file_a_peer_under_a_role_it_does_not_hold(self) -> None:
        forge = _forge()
        lifecycle_only = _session_principal(FORGE_SESSION_LIFECYCLE_SCOPE)
        with pytest.raises(ForgeMcpGrantEscalationError, match="message"):
            forge.session_service.check_forge_mcp_grants(
                lifecycle_only, [], launch_spec=None, role="coordinator"
            )
        # a worker carries no role grants, and a full coordinator may create one of its own
        forge.session_service.check_forge_mcp_grants(
            lifecycle_only, [], launch_spec=None, role="worker"
        )
        full = _session_principal(FORGE_SESSION_LIFECYCLE_SCOPE, FORGE_SESSION_MESSAGE_SCOPE)
        forge.session_service.check_forge_mcp_grants(full, [], launch_spec=None, role="coordinator")

    async def test_create_route_refuses_a_coordinator_child_beyond_the_caller(self) -> None:
        forge = _forge()
        caller = await forge.session("caller")
        forge.client.put(f"{PREFIX}/sessions/{caller.id}/forge-mcp", json={"grants": ["lifecycle"]})
        credential = await forge.start(caller)
        response = forge.client.post(
            f"{PREFIX}/sessions",
            json={
                "name": "child",
                "coordination": {"project_id": str(uuid4()), "role": "coordinator"},
            },
            headers=bearer(credential.token),
        )
        assert response.status_code == 403
        assert "message" in response.json()["detail"]


class TestRoleGrants:
    async def test_coordinator_gets_lifecycle_and_message(self) -> None:
        forge = _forge()
        session = await forge.session(**_filed("coordinator"))
        credential = await forge.start(session)
        assert set(credential.grants) == {ForgeMcpGrant.LIFECYCLE, ForgeMcpGrant.MESSAGE}
        assert effective_grants(credential.token, None) == {
            ForgeMcpGrant.LIFECYCLE,
            ForgeMcpGrant.MESSAGE,
        }
        # role grants apply at the mint; they are not copied onto the row
        stored = await forge.sessions.get(session.id)
        assert stored.workload_config["forge_mcp"]["grants"] == []

    @pytest.mark.parametrize("extra", [_filed("worker"), _filed("reviewer"), {}])
    async def test_workers_unknown_roles_and_unfiled_sessions_get_none(self, extra) -> None:
        forge = _forge()
        credential = await forge.start(await forge.session(**extra))
        assert credential.grants == ()

    async def test_role_grants_are_configurable(self) -> None:
        forge = _forge(forge_mcp_role_grants={"coordinator": ["message"], "lead": ["lifecycle"]})
        coordinator = await forge.start(await forge.session("c", **_filed("coordinator")))
        lead = await forge.start(await forge.session("l", **_filed("lead")))
        assert coordinator.grants == (ForgeMcpGrant.MESSAGE,)
        assert lead.grants == (ForgeMcpGrant.LIFECYCLE,)

    async def test_empty_role_grants_grant_nothing_by_role(self) -> None:
        forge = _forge(forge_mcp_role_grants={})
        credential = await forge.start(await forge.session(**_filed("coordinator")))
        assert credential.grants == ()

    def test_config_default_is_coordinator_lifecycle_and_message(self) -> None:
        config = ForgeMcpConfig()
        assert config.role_grants == {
            "coordinator": [ForgeMcpGrant.LIFECYCLE, ForgeMcpGrant.MESSAGE]
        }
        assert ForgeMcpConfig.model_validate({"role_grants": {}}).role_grants == {}
        with pytest.raises(ValueError):
            ForgeMcpConfig.model_validate({"role_grants": {"coordinator": ["root"]}})
