"""Forge session tokens on Volundr: verification, precedence, allow-list and binding."""

from __future__ import annotations

import time
import uuid

import jwt
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from niuu.domain.services.forge_session_token import LAUNCH_ID_CLAIM, SESSION_ID_CLAIM
from niuu.domain.services.token_scope import (
    FORGE_NOTIFY_SCOPE,
    FORGE_SESSION_LIFECYCLE_SCOPE,
    FORGE_SESSION_MESSAGE_SCOPE,
    FORGE_SESSION_READ_SCOPE,
)
from tests.support.forge_session import (
    OWNER,
    PREFIX,
    TENANT,
    bearer,
    build_forge_app,
    token_issuer,
    token_service,
)
from volundr.adapters.inbound.auth import extract_principal
from volundr.adapters.inbound.forge_session_auth import (
    ROUTE_POLICIES,
    Binding,
    ForgeSessionAuthMiddleware,
    match_policy,
    require_bound_session,
    session_token_in,
)
from volundr.domain.models import SessionStatus


@pytest.fixture
def forge():
    return build_forge_app()


NOTIFY = {"kind": "milestone", "title": "Tests green", "idempotency_key": "k-1"}


class TestPolicyTable:
    @pytest.mark.parametrize(
        ("method", "path", "scope", "binding"),
        [
            ("GET", "/notifications", FORGE_SESSION_READ_SCOPE, Binding.NONE),
            ("PUT", "/notifications/read-state", FORGE_SESSION_READ_SCOPE, Binding.NONE),
            ("PUT", "/notifications/N/read", FORGE_SESSION_READ_SCOPE, Binding.NONE),
            ("GET", "/sessions/S/notifications", FORGE_SESSION_READ_SCOPE, Binding.OWNED),
            ("POST", "/sessions/S/notifications", FORGE_NOTIFY_SCOPE, Binding.OWN),
            ("GET", "/sessions", FORGE_SESSION_READ_SCOPE, Binding.NONE),
            ("GET", "/sessions/S", FORGE_SESSION_READ_SCOPE, Binding.OWNED),
            ("GET", "/sessions/S/conversation", FORGE_SESSION_READ_SCOPE, Binding.OWNED),
            ("GET", "/sessions/S/log", FORGE_SESSION_READ_SCOPE, Binding.OWNED),
            ("GET", "/sessions/S/transcript", FORGE_SESSION_READ_SCOPE, Binding.OWNED),
            (
                "GET",
                "/sessions/S/message-deliveries/r-1",
                FORGE_SESSION_READ_SCOPE,
                Binding.OWNED,
            ),
            ("POST", "/sessions/S/messages", FORGE_SESSION_MESSAGE_SCOPE, Binding.PEER),
            ("POST", "/sessions", FORGE_SESSION_LIFECYCLE_SCOPE, Binding.NONE),
            ("POST", "/sessions/S/start", FORGE_SESSION_LIFECYCLE_SCOPE, Binding.PEER),
            ("POST", "/sessions/S/stop", FORGE_SESSION_LIFECYCLE_SCOPE, Binding.PEER),
            ("POST", "/sessions/S/log", FORGE_NOTIFY_SCOPE, Binding.OWN),
            ("POST", "/sessions/S/activity", FORGE_NOTIFY_SCOPE, Binding.OWN),
            ("POST", "/sessions/S/usage", FORGE_NOTIFY_SCOPE, Binding.OWN),
            ("POST", "/mcp", None, Binding.NONE),
        ],
    )
    def test_allowed_routes(self, method, path, scope, binding) -> None:
        session_id = str(uuid.uuid4())
        policy, params = match_policy(method, f"{PREFIX}{path.replace('/S', '/' + session_id)}")
        assert (policy.scope, policy.binding) == (scope, binding)
        if "/S" in path:
            assert params["session_id"] == session_id

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("DELETE", "/sessions/S"),
            ("PATCH", "/sessions/S/archive"),
            ("PUT", "/sessions/S"),
            ("GET", "/notifications/rules"),
            ("POST", "/notifications/rules"),
            ("PUT", "/notifications/rules/r"),
            ("DELETE", "/notifications/rules/r"),
            ("GET", "/notifications/sinks"),
            ("GET", "/notifications/n/deliveries"),
            ("GET", "/sessions/stream"),
            ("GET", "/sessions/S/logs"),
            ("GET", "/sessions/S/files"),
            ("POST", "/sessions/S/message-deliveries/r/claim"),
            ("GET", "/admin/settings"),
            ("POST", "/sessions/import"),
            ("POST", "/sessions/archive-stopped"),
            ("GET", "/sessions/"),
        ],
    )
    def test_everything_else_is_denied(self, method, path) -> None:
        session_id = str(uuid.uuid4())
        assert match_policy(method, f"{PREFIX}{path.replace('/S', '/' + session_id)}") is None
        assert match_policy(method, f"{PREFIX}{path}") is None

    @pytest.mark.parametrize(
        "path",
        ["/api/v1/tokens", "/api/v1/credentials/user", "/s/S/api/message", "/api/v1/volundr/x"],
    )
    def test_other_apis_are_denied(self, path) -> None:
        assert match_policy("GET", path) is None and match_policy("POST", path) is None

    def test_every_policy_names_a_known_scope(self) -> None:
        scopes = {
            FORGE_NOTIFY_SCOPE,
            FORGE_SESSION_READ_SCOPE,
            FORGE_SESSION_MESSAGE_SCOPE,
            FORGE_SESSION_LIFECYCLE_SCOPE,
            None,
        }
        assert {policy.scope for policy in ROUTE_POLICIES} <= scopes


class TestTokenDetection:
    def _scope(self, *, header: str = "", query: str = "") -> dict:
        headers = [(b"authorization", header.encode())] if header else []
        return {"headers": headers, "query_string": query.encode()}

    def test_header_and_query(self, forge) -> None:
        token = forge.tokens.mint(
            session_id=uuid.uuid4(),
            session_name="x",
            owner_id=OWNER,
            tenant_id=None,
            grants=(),
            launch_id="l",
        ).token
        assert session_token_in(self._scope(header=f"Bearer {token}")) == token
        assert session_token_in(self._scope(header=f"bearer {token}")) == token
        assert session_token_in(self._scope(query=f"token={token}")) == token
        # a PAT in the header does not hide a session token in the query
        assert session_token_in(self._scope(header="Bearer pat", query=f"token={token}")) == token
        assert session_token_in(self._scope(header="Bearer pat")) == ""
        assert session_token_in(self._scope(header="Basic abc")) == ""


class TestPrincipalPrecedence:
    async def test_valid_token_beats_allow_all(self, forge) -> None:
        own = await forge.session("own")
        other_owner = await forge.session("theirs", owner="someone-else")
        token = (await forge.start(own)).token

        listed = forge.client.get(f"{PREFIX}/sessions", headers=bearer(token))
        assert listed.status_code == 200
        assert [s["id"] for s in listed.json()] == [str(own.id)]  # owner-scoped, not admin

        admin = {
            "x-auth-user-id": "admin",
            "x-auth-tenant": TENANT,
            "x-auth-roles": "volundr:admin",
        }
        everyone = forge.client.get(f"{PREFIX}/sessions", headers=admin)
        assert {s["id"] for s in everyone.json()} == {str(own.id), str(other_owner.id)}

    async def test_token_wins_over_forwarded_identity_headers(self, forge) -> None:
        own = await forge.session("own")
        await forge.session("theirs", owner="someone-else")
        token = (await forge.start(own)).token
        spoofed = {
            **bearer(token),
            "x-auth-user-id": "someone-else",
            "x-auth-roles": "volundr:admin",
        }
        listed = forge.client.get(f"{PREFIX}/sessions", headers=spoofed)
        assert [s["id"] for s in listed.json()] == [str(own.id)]

    def test_invalid_token_is_401_never_anonymous(self, forge) -> None:
        forged = jwt.encode(
            {
                "sub": OWNER,
                "token_use": "forge_session",
                "scopes": [FORGE_SESSION_READ_SCOPE],
                SESSION_ID_CLAIM: str(uuid.uuid4()),
                LAUNCH_ID_CLAIM: "l",
            },
            "not-the-forge-key-but-32-bytes-long",
            algorithm="HS256",
        )
        response = forge.client.get(f"{PREFIX}/sessions", headers=bearer(forged))
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        # the same forgery in the query string is caught too
        assert forge.client.get(f"{PREFIX}/sessions", params={"token": forged}).status_code == 401

    async def test_expired_token_is_401(self, forge) -> None:
        session = await forge.session()
        await forge.start(session)
        stored = await forge.sessions.get(session.id)
        launch = stored.workload_config["forge_mcp"]["launch_id"]
        from niuu.domain.services.forge_session_token import ForgeSessionTokenService

        expired = ForgeSessionTokenService(
            forge.tokens._issuer, audiences=["volundr-api"], ttl_seconds=-60, key_source="t"
        ).mint(
            session_id=session.id,
            session_name="x",
            owner_id=OWNER,
            tenant_id=TENANT,
            grants=(),
            launch_id=launch,
        )
        response = forge.client.get(f"{PREFIX}/sessions", headers=bearer(expired.token))
        assert response.status_code == 401 and "expired" in response.json()["detail"]

    async def test_a_token_from_another_forge_is_401(self, forge) -> None:
        session = await forge.session()
        await forge.start(session)
        stored = await forge.sessions.get(session.id)
        stranger = token_service(token_issuer()).mint(
            session_id=session.id,
            session_name="x",
            owner_id=OWNER,
            tenant_id=TENANT,
            grants=(),
            launch_id=stored.workload_config["forge_mcp"]["launch_id"],
        )
        assert (
            forge.client.get(f"{PREFIX}/sessions", headers=bearer(stranger.token)).status_code
            == 401
        )

    async def test_without_a_token_service_session_tokens_are_refused(self) -> None:
        minting = build_forge_app()
        session = await minting.session()
        token = (await minting.start(session)).token
        refusing = build_forge_app(tokens=False)
        response = refusing.client.get(f"{PREFIX}/sessions", headers=bearer(token))
        assert response.status_code == 401
        assert "not enabled" in response.json()["detail"]

    def test_other_callers_are_untouched(self, forge) -> None:
        pat = jwt.encode({"sub": "u", "type": "pat"}, "k" * 32, algorithm="HS256")
        assert forge.client.get(f"{PREFIX}/sessions", headers=bearer(pat)).status_code == 200
        headers = {"x-auth-user-id": "alice", "x-auth-roles": "volundr:developer"}
        assert forge.client.get(f"{PREFIX}/sessions", headers=headers).status_code == 200
        assert forge.client.get(f"{PREFIX}/sessions").status_code == 200

    async def test_unverified_token_without_middleware_is_401(self) -> None:
        app = FastAPI()

        @app.get("/who")
        async def who(request: Request) -> dict:
            principal = await extract_principal(request)
            return {"user": principal.user_id}

        forge = build_forge_app()
        session = await forge.session()
        token = (await forge.start(session)).token
        client = TestClient(app)
        assert client.get("/who", headers=bearer(token)).status_code == 401


class TestRevocation:
    async def test_restart_revokes_the_previous_launch(self, forge) -> None:
        session = await forge.session()
        first = await forge.start(session)
        await forge.set_status(session.id, SessionStatus.STOPPED)
        second = await forge.start(session)
        assert first.launch_id != second.launch_id
        stale = forge.client.get(f"{PREFIX}/sessions", headers=bearer(first.token))
        assert stale.status_code == 401 and "restarted" in stale.json()["detail"]
        assert (
            forge.client.get(f"{PREFIX}/sessions", headers=bearer(second.token)).status_code == 200
        )

    @pytest.mark.parametrize("status", [SessionStatus.STOPPED, SessionStatus.ARCHIVED])
    async def test_stopped_or_archived_session_revokes(self, forge, status) -> None:
        session = await forge.session()
        token = (await forge.start(session)).token
        await forge.set_status(session.id, status)
        response = forge.client.get(f"{PREFIX}/sessions", headers=bearer(token))
        assert response.status_code == 401 and status.value in response.json()["detail"]

    async def test_failed_session_keeps_its_token(self, forge) -> None:
        # The liveness heuristic can mark a live broker failed; its launch is unchanged.
        session = await forge.session()
        token = (await forge.start(session)).token
        await forge.set_status(session.id, SessionStatus.FAILED)
        assert forge.client.get(f"{PREFIX}/sessions", headers=bearer(token)).status_code == 200

    async def test_deleted_session_revokes(self, forge) -> None:
        session = await forge.session()
        token = (await forge.start(session)).token
        await forge.sessions.delete(session.id)
        response = forge.client.get(f"{PREFIX}/sessions", headers=bearer(token))
        assert response.status_code == 401 and "gone" in response.json()["detail"]

    async def test_owner_change_revokes(self, forge) -> None:
        session = await forge.session()
        token = (await forge.start(session)).token
        stored = await forge.sessions.get(session.id)
        await forge.sessions.update(stored.model_copy(update={"owner_id": "new-owner"}))
        assert forge.client.get(f"{PREFIX}/sessions", headers=bearer(token)).status_code == 401


class TestRouteFamilies:
    async def _pair(self, forge, *grants):
        """The caller's own session (started, with ``grants``) and a peer of the same owner."""
        own = await forge.session("own")
        peer = await forge.session("peer")
        if grants:
            stored = await forge.sessions.get(own.id)
            config = {"forge_mcp": {"grants": list(grants)}}
            await forge.sessions.update(stored.model_copy(update={"workload_config": config}))
        credential = await forge.start(own)
        return own, peer, credential.token

    async def test_session_reads(self, forge) -> None:
        own, peer, token = await self._pair(forge)
        foreign = await forge.session("foreign", owner="someone-else")
        assert (
            forge.client.get(f"{PREFIX}/sessions/{peer.id}", headers=bearer(token)).status_code
            == 200
        )
        assert (
            forge.client.get(f"{PREFIX}/sessions/{own.id}", headers=bearer(token)).status_code
            == 200
        )
        response = forge.client.get(f"{PREFIX}/sessions/{foreign.id}", headers=bearer(token))
        assert response.status_code == 403 and "same owner" in response.json()["detail"]
        missing = forge.client.get(f"{PREFIX}/sessions/{uuid.uuid4()}", headers=bearer(token))
        assert missing.status_code == 404
        bad = forge.client.get(f"{PREFIX}/sessions/not-a-uuid", headers=bearer(token))
        assert bad.status_code == 403
        log = forge.client.get(f"{PREFIX}/sessions/{peer.id}/log", headers=bearer(token))
        assert log.status_code == 200
        head = forge.client.get(f"{PREFIX}/sessions/{peer.id}/log/head", headers=bearer(token))
        assert head.status_code == 200

    async def test_notification_feed_and_read_state(self, forge) -> None:
        own, peer, token = await self._pair(forge)
        feed = forge.client.get(f"{PREFIX}/notifications", headers=bearer(token))
        assert feed.status_code == 200 and feed.json()["items"] == []
        state = forge.client.get(f"{PREFIX}/notifications/read-state", headers=bearer(token))
        assert state.status_code == 200
        moved = forge.client.put(
            f"{PREFIX}/notifications/read-state",
            json={"read_through_seq": 0, "expected_revision": 0},
            headers=bearer(token),
        )
        assert moved.status_code == 200
        per_session = forge.client.get(
            f"{PREFIX}/sessions/{peer.id}/notifications", headers=bearer(token)
        )
        assert per_session.status_code == 200

    async def test_direct_submit_is_agent_for_itself_and_denied_for_others(self, forge) -> None:
        own, peer, token = await self._pair(forge)
        created = forge.client.post(
            f"{PREFIX}/sessions/{own.id}/notifications", json=NOTIFY, headers=bearer(token)
        )
        assert created.status_code == 201
        assert created.json()["source"] == "agent"
        again = forge.client.post(
            f"{PREFIX}/sessions/{own.id}/notifications", json=NOTIFY, headers=bearer(token)
        )
        assert again.status_code == 200 and again.json()["id"] == created.json()["id"]
        other = forge.client.post(
            f"{PREFIX}/sessions/{peer.id}/notifications", json=NOTIFY, headers=bearer(token)
        )
        assert other.status_code == 403 and "bound to another session" in other.json()["detail"]
        # the owner submitting with their own identity is an operator
        operator = forge.client.post(
            f"{PREFIX}/sessions/{own.id}/notifications",
            json=NOTIFY,
            headers={"x-auth-user-id": OWNER, "x-auth-tenant": TENANT},
        )
        assert operator.status_code == 201 and operator.json()["source"] == "operator"
        assert operator.json()["id"] != created.json()["id"]

    async def test_rules_sinks_and_deliveries_are_denied(self, forge) -> None:
        _, _, token = await self._pair(forge)
        rule = {"name": "r", "sink": "ops"}
        for method, path, body in [
            ("GET", "/notifications/rules", None),
            ("POST", "/notifications/rules", rule),
            ("PUT", f"/notifications/rules/{uuid.uuid4()}", rule),
            ("DELETE", f"/notifications/rules/{uuid.uuid4()}", None),
            ("GET", "/notifications/sinks", None),
            ("GET", f"/notifications/{uuid.uuid4()}/deliveries", None),
        ]:
            response = forge.client.request(
                method, f"{PREFIX}{path}", json=body, headers=bearer(token)
            )
            assert response.status_code == 403, (method, path)

    async def test_message_needs_the_grant_and_never_targets_itself(self, forge) -> None:
        own, peer, token = await self._pair(forge)
        denied = forge.client.post(
            f"{PREFIX}/sessions/{peer.id}/messages", json={"content": "hi"}, headers=bearer(token)
        )
        assert denied.status_code == 403
        assert FORGE_SESSION_MESSAGE_SCOPE in denied.json()["detail"]

        forge2 = build_forge_app()
        own2, peer2, granted = await self._pair(forge2, "message")
        to_self = forge2.client.post(
            f"{PREFIX}/sessions/{own2.id}/messages", json={"content": "hi"}, headers=bearer(granted)
        )
        assert to_self.status_code == 403 and "itself" in to_self.json()["detail"]
        # to a peer the policy admits it; the route then fails on the missing pod
        to_peer = forge2.client.post(
            f"{PREFIX}/sessions/{peer2.id}/messages",
            json={"content": "hi"},
            headers=bearer(granted),
        )
        assert to_peer.status_code not in (401, 403)

    async def test_lifecycle_needs_the_grant(self, forge) -> None:
        own, peer, token = await self._pair(forge)
        for path in (f"/sessions/{peer.id}/start", f"/sessions/{peer.id}/stop", "/sessions"):
            response = forge.client.post(
                f"{PREFIX}{path}", json={"name": "child"}, headers=bearer(token)
            )
            assert response.status_code == 403, path

    async def test_lifecycle_with_the_grant(self, forge) -> None:
        own, peer, token = await self._pair(forge, "lifecycle")
        await forge.set_status(peer.id, SessionStatus.RUNNING)
        stopped = forge.client.post(f"{PREFIX}/sessions/{peer.id}/stop", headers=bearer(token))
        assert stopped.status_code == 200
        started = forge.client.post(f"{PREFIX}/sessions/{peer.id}/start", headers=bearer(token))
        assert started.status_code == 200
        created = forge.client.post(
            f"{PREFIX}/sessions", json={"name": "child"}, headers=bearer(token)
        )
        assert created.status_code == 201
        child = await forge.sessions.get(uuid.UUID(created.json()["id"]))
        assert child.owner_id == OWNER  # acts as the owner
        own_stop = forge.client.post(f"{PREFIX}/sessions/{own.id}/stop", headers=bearer(token))
        assert own_stop.status_code == 403 and "itself" in own_stop.json()["detail"]

    async def test_delete_archive_and_admin_are_denied_even_with_every_grant(self, forge) -> None:
        own, peer, token = await self._pair(forge, "lifecycle", "message")
        for method, path in [
            ("DELETE", f"/sessions/{peer.id}"),
            ("PATCH", f"/sessions/{peer.id}/archive"),
            ("PUT", f"/sessions/{peer.id}"),
            ("POST", "/sessions/archive-stopped"),
            ("GET", "/sessions/stream"),
            ("GET", "/feature-flags"),
        ]:
            response = forge.client.request(method, f"{PREFIX}{path}", headers=bearer(token))
            assert response.status_code == 403, (method, path)
        for path in ("/api/v1/tokens", "/api/v1/credentials/user"):
            assert forge.client.get(path, headers=bearer(token)).status_code == 403

    async def test_session_bound_writes(self, forge) -> None:
        own, peer, token = await self._pair(forge)
        frame = {"entries": [{"seq": 1, "kind": "assistant", "payload": {"n": 1}}]}
        own_log = forge.client.post(
            f"{PREFIX}/sessions/{own.id}/log", json=frame, headers=bearer(token)
        )
        assert own_log.status_code == 201
        for suffix, body in (
            ("log", frame),
            ("activity", {"state": "idle"}),
            ("usage", {"provider": "cloud", "tokens": 1, "model": "m"}),
        ):
            response = forge.client.post(
                f"{PREFIX}/sessions/{peer.id}/{suffix}", json=body, headers=bearer(token)
            )
            assert response.status_code == 403, suffix
            assert "bound to another session" in response.json()["detail"]

    async def test_websockets_are_closed(self, forge) -> None:
        own, _, token = await self._pair(forge)
        from starlette.websockets import WebSocketDisconnect

        with (
            pytest.raises(WebSocketDisconnect) as closed,
            forge.client.websocket_connect(
                f"{PREFIX}/sessions/{own.id}/replay", headers=bearer(token)
            ),
        ):
            pass
        assert closed.value.code == 4403


class TestRequireBoundSession:
    def _request(self, headers: dict[str, str], claims=None) -> Request:
        scope = {
            "type": "http",
            "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
            "state": {"forge_session": claims} if claims else {},
        }
        return Request(scope)

    def test_openshell_binding_is_kept(self) -> None:
        from fastapi import HTTPException

        sid = uuid.uuid4()
        ok = {"x-auth-token-use": "openshell_session", "x-auth-workload-session-id": str(sid)}
        require_bound_session(self._request(ok), sid)
        with pytest.raises(HTTPException) as denied:
            require_bound_session(self._request(ok), uuid.uuid4())
        assert denied.value.status_code == 403
        require_bound_session(self._request({}), sid)  # other callers are not restricted


class TestCreateWithGrants:
    async def test_grants_persist_and_are_minted(self, forge) -> None:
        response = forge.client.post(
            f"{PREFIX}/sessions",
            json={"name": "granted", "forge_mcp": {"grants": ["message"]}},
            headers={"x-auth-user-id": OWNER, "x-auth-tenant": TENANT},
        )
        assert response.status_code == 201
        session_id = uuid.UUID(response.json()["id"])
        task = forge.session_service._provisioning_tasks.get(session_id)
        if task is not None:
            await task
        stored = await forge.sessions.get(session_id)
        assert stored.workload_config["forge_mcp"]["grants"] == ["message"]
        credential = forge.pod_manager.start_calls[-1][1].forge_session
        assert FORGE_SESSION_MESSAGE_SCOPE in credential.scopes

    def test_forge_mcp_in_workload_config_is_refused(self, forge) -> None:
        response = forge.client.post(
            f"{PREFIX}/sessions",
            json={"name": "x", "workload_config": {"forge_mcp": {"grants": ["message"]}}},
        )
        assert response.status_code == 422

    def test_unknown_grant_is_refused(self, forge) -> None:
        response = forge.client.post(
            f"{PREFIX}/sessions", json={"name": "x", "forge_mcp": {"grants": ["root"]}}
        )
        assert response.status_code == 422

    async def test_a_session_cannot_grant_what_it_lacks(self, forge) -> None:
        own = await forge.session("own")
        stored = await forge.sessions.get(own.id)
        await forge.sessions.update(
            stored.model_copy(update={"workload_config": {"forge_mcp": {"grants": ["lifecycle"]}}})
        )
        token = (await forge.start(own)).token
        before = len(await forge.sessions.list())
        response = forge.client.post(
            f"{PREFIX}/sessions",
            json={"name": "child", "forge_mcp": {"grants": ["message"]}},
            headers=bearer(token),
        )
        assert response.status_code == 403 and "message" in response.json()["detail"]
        assert len(await forge.sessions.list()) == before  # refused before any row exists
        same = forge.client.post(
            f"{PREFIX}/sessions",
            json={"name": "child", "forge_mcp": {"grants": ["lifecycle"]}},
            headers=bearer(token),
        )
        assert same.status_code == 201


class TestMiddlewareOutsideHttp:
    async def test_lifespan_passes_through(self) -> None:
        seen = []

        async def app(scope, receive, send):
            seen.append(scope["type"])

        await ForgeSessionAuthMiddleware(app)({"type": "lifespan"}, None, None)
        assert seen == ["lifespan"]

    async def test_not_ready_is_503(self) -> None:
        forge = build_forge_app()
        session = await forge.session()
        token = (await forge.start(session)).token
        forge.app.state.session_service = None
        response = forge.client.get(f"{PREFIX}/sessions", headers=bearer(token))
        assert response.status_code == 503

    def test_expiry_is_a_backstop(self, forge) -> None:
        # Minted tokens live for the configured TTL; the launch binding revokes earlier.
        issued = forge.tokens.mint(
            session_id=uuid.uuid4(),
            session_name="x",
            owner_id=OWNER,
            tenant_id=None,
            grants=(),
            launch_id="l",
        )
        assert issued.expires_at - int(time.time()) > 0
