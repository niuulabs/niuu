"""A session launched with another session's credential inherits that session's connections.

The Connections lookup here is the real shared adapter: ``HTTPIntegrationRepository``
forwarding the caller's bearer through ``RequestBearerTokenAuthAdapter``. A launch made
with a ``forge_session`` credential carries no owner bearer, so any lookup it made would
fail with "Shared integrations require an authenticated caller bearer token" (the
orchestrator launch failure). These tests prove such a launch never looks up again.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from starlette.requests import Request

from niuu.adapters.http_integrations import HTTPIntegrationRepository
from niuu.adapters.inbound.auth_context import extract_bearer_token
from niuu.domain.models import project_owner_id
from niuu.domain.services.token_scope import (
    FORGE_NOTIFY_SCOPE,
    FORGE_SESSION_LIFECYCLE_SCOPE,
    FORGE_SESSION_READ_SCOPE,
)
from tests.support.forge_session import OWNER, PREFIX, TENANT, build_forge_app
from volundr.domain.models import (
    GitSource,
    IntegrationConnection,
    IntegrationType,
    Principal,
    SessionStatus,
)
from volundr.domain.ports import SessionContext, SessionContribution, SessionContributor
from volundr.domain.session_connections import (
    RESOLVED_CONNECTIONS_KEY,
    ResolvedConnectionsRecordError,
    resolved_connections_of,
    with_resolved_connections,
    without_resolved_connections,
)

NOW = datetime(2026, 11, 19, tzinfo=UTC).isoformat()
OWNER_TOKEN = "owner-bearer"
RUN = str(uuid4())
USER = Principal(user_id=OWNER, email="", tenant_id=TENANT, roles=["volundr:developer"])


def _record(connection_id: str, integration_type: str = "ai_provider") -> dict:
    return {
        "id": connection_id,
        "owner_id": OWNER,
        "integration_type": integration_type,
        "adapter": "some.adapter",
        "credential_name": f"{connection_id}-credential",
        "config": {"region": "eu"},
        "enabled": True,
        "created_at": NOW,
        "updated_at": NOW,
        "slug": connection_id,
    }


class ConnectionsApi:
    """The central Connections API: answers only a caller presenting the owner's bearer."""

    def __init__(self, *records: dict) -> None:
        self.records = {record["id"]: record for record in records}
        self.calls: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        if request.headers.get("authorization") != f"Bearer {OWNER_TOKEN}":
            return httpx.Response(401)
        connection_id = request.url.path.rsplit("/", 1)[-1]
        if connection_id == "integrations":
            return httpx.Response(200, json=list(self.records.values()))
        record = self.records.get(connection_id)
        return httpx.Response(200, json=record) if record else httpx.Response(404)


def _shared_repository(api: ConnectionsApi) -> HTTPIntegrationRepository:
    repository = HTTPIntegrationRepository(
        "https://connections.test",
        api_prefix="/api/v1/integrations",
        auth_adapter="niuu.adapters.outbound.http_auth.RequestBearerTokenAuthAdapter",
    )
    repository._client = httpx.AsyncClient(
        base_url="https://connections.test", transport=httpx.MockTransport(api.handle)
    )
    return repository


def _present_bearer(token: str | None) -> None:
    """What Forge's auth does for a request: hold its bearer for outbound calls."""
    headers = [(b"authorization", f"Bearer {token}".encode())] if token else []
    extract_bearer_token(Request({"type": "http", "query_string": b"", "headers": headers}))


class Recorder(SessionContributor):
    def __init__(self) -> None:
        self.seen: dict = {}

    @property
    def name(self) -> str:
        return "recorder"

    async def contribute(self, session, context: SessionContext) -> SessionContribution:
        self.seen[session.id] = context
        return SessionContribution()


def _session_credential(caller_id) -> Principal:
    """The principal Forge derives from a verified ``forge_session`` token of ``caller_id``."""
    return Principal(
        user_id=OWNER,
        email="",
        tenant_id=TENANT,
        roles=["volundr:developer"],
        token_use="forge_session",
        scopes=(FORGE_NOTIFY_SCOPE, FORGE_SESSION_READ_SCOPE, FORGE_SESSION_LIFECYCLE_SCOPE),
        bound_session_id=str(caller_id),
    )


class Forge:
    def __init__(self, *records: dict) -> None:
        self.api = ConnectionsApi(*records)
        self.recorder = Recorder()
        self.app = build_forge_app(
            integration_repo=_shared_repository(self.api), contributors=[self.recorder]
        )

    async def launch(self, name: str, principal: Principal, bearer: str | None, **kwargs):
        session = await self.app.session(name, **kwargs.pop("session", {}))
        _present_bearer(bearer)
        service = self.app.session_service
        await service.start_session(session.id, principal=principal, **kwargs)
        task = service._provisioning_tasks.get(session.id)
        if task is not None:
            await task
        return await self.app.sessions.get(session.id)

    async def orchestrator(self, **kwargs):
        return await self.launch("orchestrator", USER, OWNER_TOKEN, **kwargs)

    async def child_of(self, caller, **kwargs):
        return await self.launch("child", _session_credential(caller.id), None, **kwargs)

    def attached(self, session) -> list[str]:
        return [c.id for c in self.recorder.seen[session.id].integration_connections]


async def test_a_user_launch_without_a_bearer_cannot_look_connections_up():
    """The adapter the orchestrator failed on: no bearer, no lookup, the launch fails."""
    forge = Forge(_record("claude"))
    started = await forge.launch("anonymous", USER, None)
    assert started.status == SessionStatus.FAILED
    assert "authenticated caller bearer token" in started.error


async def test_a_session_launched_by_a_session_inherits_its_connections():
    forge = Forge(_record("claude"), _record("linear", "issue_tracker"))
    orchestrator = await forge.orchestrator()
    assert forge.attached(orchestrator) == ["claude", "linear"]
    lookups = len(forge.api.calls)

    child = await forge.child_of(orchestrator)

    assert child.status != SessionStatus.FAILED, child.error
    assert forge.attached(child) == ["claude", "linear"]
    assert len(forge.api.calls) == lookups, "the child launch must not call the Connections API"
    assert child.workload_config["integration_ids"] == ["claude", "linear"]
    context = forge.recorder.seen[child.id]
    assert [c.credential_name for c in context.integration_connections] == [
        "claude-credential",
        "linear-credential",
    ]
    assert context.integration_connections[0].config == {"region": "eu"}


async def test_a_child_may_select_a_subset_of_its_callers_connections():
    forge = Forge(_record("claude"), _record("linear", "issue_tracker"))
    orchestrator = await forge.orchestrator()

    child = await forge.child_of(orchestrator, integration_ids=["linear"])

    assert child.status != SessionStatus.FAILED, child.error
    assert forge.attached(child) == ["linear"]


async def test_a_child_selecting_its_callers_connections_keeps_the_callers_sources():
    forge = Forge(_record("claude"), _record("github", "source_control"))
    orchestrator = await forge.orchestrator()

    child = await forge.child_of(
        orchestrator,
        integration_ids=["claude"],
        session={"source": GitSource(repo="https://github.com/niuulabs/niuu", branch="dev")},
    )

    assert child.status != SessionStatus.FAILED, child.error
    assert forge.attached(child) == ["claude", "github"]


async def test_a_child_cannot_select_a_connection_its_caller_was_not_given():
    forge = Forge(_record("claude"), _record("linear", "issue_tracker"))
    orchestrator = await forge.orchestrator(integration_ids=["claude"])
    assert forge.attached(orchestrator) == ["claude"]

    child = await forge.child_of(orchestrator, integration_ids=["linear"])

    assert child.status == SessionStatus.FAILED
    assert "['linear'] are not among those session" in child.error
    assert str(orchestrator.id) in child.error


async def test_a_caller_launched_before_recording_must_be_restarted():
    forge = Forge(_record("claude"))
    orchestrator = await forge.orchestrator()
    unrecorded = orchestrator.model_copy(
        update={"workload_config": without_resolved_connections(orchestrator.workload_config)}
    )
    await forge.app.sessions.update(unrecorded)

    child = await forge.child_of(orchestrator)

    assert child.status == SessionStatus.FAILED
    assert "was started before Forge recorded the connections" in child.error
    assert "restart it" in child.error


async def test_a_caller_with_no_connections_launches_children_with_none():
    forge = Forge()
    orchestrator = await forge.orchestrator()
    assert resolved_connections_of(orchestrator.workload_config) == []

    child = await forge.child_of(orchestrator)

    assert child.status != SessionStatus.FAILED, child.error
    assert forge.attached(child) == []


async def test_a_request_cannot_supply_the_record():
    forge = Forge(_record("claude"))
    forged = with_resolved_connections({}, [IntegrationConnection(**_parsed(_record("stolen")))])

    orchestrator = await forge.orchestrator(workload_config=forged)

    assert [c.id for c in resolved_connections_of(orchestrator.workload_config)] == ["claude"]


async def test_contributors_never_see_the_record():
    forge = Forge(_record("claude"))
    orchestrator = await forge.orchestrator()

    assert RESOLVED_CONNECTIONS_KEY in orchestrator.workload_config
    assert RESOLVED_CONNECTIONS_KEY not in forge.recorder.seen[orchestrator.id].workload_config


def _parsed(record: dict) -> dict:
    return {
        **record,
        "integration_type": IntegrationType(record["integration_type"]),
        "created_at": datetime.fromisoformat(record["created_at"]),
        "updated_at": datetime.fromisoformat(record["updated_at"]),
    }


def _written(connection_id: str) -> dict:
    connection = IntegrationConnection(**_parsed(_record(connection_id)))
    return with_resolved_connections({}, [connection])[RESOLVED_CONNECTIONS_KEY][0]


class TestRecord:
    def test_round_trips_every_field(self):
        project_id = "11111111-1111-1111-1111-111111111111"
        connection = IntegrationConnection(
            **{**_parsed(_record("team")), "owner_id": project_owner_id(project_id)},
            project_id=project_id,
            connected_by="someone",
        )

        config = with_resolved_connections({"other": 1}, [connection])

        assert config["other"] == 1
        assert resolved_connections_of(config) == [connection]

    def test_no_record_is_none_and_an_empty_one_is_empty(self):
        assert resolved_connections_of(None) is None
        assert resolved_connections_of({"integration_ids": ["x"]}) is None
        assert resolved_connections_of(with_resolved_connections({}, [])) == []

    @pytest.mark.parametrize(
        "record",
        [
            "not-a-list",
            ["not-a-mapping"],
            [{"id": "missing-fields"}],
            [{**_written("bad"), "integration_type": "no-such-type"}],
            [{**_written("bad"), "created_at": "yesterday"}],
        ],
    )
    def test_a_record_forge_did_not_write_raises(self, record):
        with pytest.raises(ResolvedConnectionsRecordError, match="restart the session"):
            resolved_connections_of({RESOLVED_CONNECTIONS_KEY: record})


async def test_restarting_a_session_keeps_its_own_connections():
    """A peer the orchestrator restarts keeps what it had, even what the caller lacks."""
    forge = Forge(_record("claude"), _record("linear", "issue_tracker"))
    orchestrator = await forge.orchestrator(integration_ids=["claude"])
    peer = await forge.launch("peer", USER, OWNER_TOKEN, integration_ids=["linear"])
    assert forge.attached(peer) == ["linear"]
    await forge.app.set_status(peer.id, SessionStatus.STOPPED)
    lookups = len(forge.api.calls)

    _present_bearer(None)
    service = forge.app.session_service
    await service.start_session(peer.id, principal=_session_credential(orchestrator.id))
    await service._provisioning_tasks[peer.id]

    restarted = await forge.app.sessions.get(peer.id)
    assert restarted.status != SessionStatus.FAILED, restarted.error
    assert forge.attached(restarted) == ["linear"]
    assert len(forge.api.calls) == lookups


async def test_a_forge_without_connections_launches_session_children_with_none():
    app = build_forge_app()
    caller = await app.session("orchestrator")
    child = await app.session("child")
    await app.session_service.start_session(child.id, principal=_session_credential(caller.id))
    await app.session_service._provisioning_tasks[child.id]

    stored = await app.sessions.get(child.id)
    assert stored.status != SessionStatus.FAILED, stored.error
    assert resolved_connections_of(stored.workload_config) == []


async def test_a_credential_naming_no_session_is_refused_by_name():
    forge = Forge(_record("claude"))
    principal = _session_credential("not-a-session")
    started = await forge.launch("child", principal, None)
    assert started.status == SessionStatus.FAILED
    assert "must name the session it belongs to, got 'not-a-session'" in started.error


def test_a_held_member_cannot_be_created_with_a_record():
    """A hosted member is stored at create and built later; a request cannot plant one."""
    forge = build_forge_app()
    forged = with_resolved_connections({}, [IntegrationConnection(**_parsed(_record("stolen")))])
    root = forge.client.post(
        f"{PREFIX}/sessions",
        json={
            "name": "wf-run",
            "hosts_members": True,
            "workflow_run_id": RUN,
            "workflow_run_room": True,
            "workflow_orchestration": "engine",
        },
    ).json()
    response = forge.client.post(
        f"{PREFIX}/sessions",
        json={
            "name": "member",
            "hosted_by": root["id"],
            "workflow_run_id": RUN,
            "workflow_member": "coder",
            "definition": "skuldClaude",
            "workload_config": forged,
        },
    )
    assert response.status_code == 201, response.text

    member = asyncio.run(forge.sessions.get(UUID(response.json()["id"])))
    assert RESOLVED_CONNECTIONS_KEY not in member.workload_config
