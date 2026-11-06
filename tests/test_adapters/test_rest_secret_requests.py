"""REST contract for secure secret requests (under /api/v1/forge)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from niuu.adapters.memory_credential_store import MemoryCredentialStore
from niuu.domain.models import SecretType
from niuu.domain.services.token_scope import (
    FORGE_NOTIFY_SCOPE,
    VALKYRIE_BUILD_TOKEN_USE,
)
from niuu.forge_mcp import credentials as session_credentials
from tests.support.forge_session import OWNER, PREFIX, TENANT, bearer, build_forge_app
from tests.support.secret_requests import InMemorySecretRequestRepository
from volundr.adapters.inbound.rest_secret_requests import create_secret_requests_router
from volundr.domain.models import Session, SessionStatus
from volundr.domain.secret_requests import session_credential_name
from volundr.domain.services.secret_requests import CREDENTIAL_OWNER_TYPE, SecretRequestService

SECRET = "glpat-super-secret-value"
OTHER_USER = "owner-b"


def owner_headers(user: str = OWNER) -> dict[str, str]:
    return {
        "x-auth-user-id": user,
        "x-auth-tenant": TENANT,
        "x-auth-roles": "volundr:developer",
    }


class Env:
    """A Forge app slice (real session-token middleware and minting) with the secret
    request routes mounted over in-memory stores."""

    def __init__(self) -> None:
        self.forge = build_forge_app()
        self.credentials = MemoryCredentialStore()
        self.repository = InMemorySecretRequestRepository()
        self.service = SecretRequestService(self.repository, self.credentials)
        self.forge.app.include_router(
            create_secret_requests_router(self.service, self.forge.session_service, prefix=PREFIX)
        )
        self.client = self.forge.client
        self.tokens: dict[UUID, dict[str, str]] = {}

    async def session(self, status: SessionStatus = SessionStatus.RUNNING) -> Session:
        """A started session (so its token is live), then set to ``status``."""
        session = await self.forge.session(owner=OWNER)
        credential = await self.forge.start(session)
        self.tokens[session.id] = bearer(credential.token)
        if status is not SessionStatus.RUNNING:
            await self.forge.set_status(session.id, status)
        return session

    def token(self, session: Session) -> dict[str, str]:
        return self.tokens[session.id]

    def url(self, session: Session, *tail: object) -> str:
        return "/".join([f"{PREFIX}/sessions/{session.id}/secret-requests", *map(str, tail)])

    def ask(self, session: Session, name: str = "GITLAB_TOKEN", **kwargs):
        headers = kwargs.pop("headers", None) or self.token(session)
        body = {"name": name, "purpose": "push the branch", "target": "gitlab.com", **kwargs}
        return self.client.post(self.url(session), json=body, headers=headers)

    def fulfill(self, session: Session, request_id: str, keep: str = "session", **kwargs):
        headers = kwargs.pop("headers", None) or owner_headers()
        return self.client.post(
            self.url(session, request_id, "fulfill"),
            json={"value": SECRET, "keep": keep},
            headers=headers,
        )


@pytest.fixture
def env() -> Env:
    return Env()


class TestCreate:
    async def test_creates_then_returns_the_open_request(self, env):
        session = await env.session()

        first = env.ask(session)
        again = env.ask(session)

        assert first.status_code == 201
        assert again.status_code == 200
        assert again.json()["id"] == first.json()["id"]
        body = first.json()
        assert set(body) == {
            "id",
            "session_id",
            "name",
            "purpose",
            "target",
            "status",
            "keep",
            "credential_name",
            "created_at",
            "resolved_at",
            "delivered_at",
        }
        assert (body["status"], body["keep"], body["credential_name"]) == ("pending", None, None)
        assert body["session_id"] == str(session.id)

    async def test_another_name_is_another_request(self, env):
        session = await env.session()
        assert env.ask(session, "A_KEY").json()["id"] != env.ask(session, "B_KEY").json()["id"]

    async def test_a_closed_request_is_replaced_by_a_new_one(self, env):
        session = await env.session()
        first = env.ask(session).json()
        env.client.post(env.url(session, first["id"], "cancel"), headers=env.token(session))

        again = env.ask(session)

        assert again.status_code == 201
        assert again.json()["id"] != first["id"]

    @pytest.mark.parametrize("name", ["gitlab_token", "1TOKEN", "HAS-DASH", "", "A" * 256])
    async def test_rejects_an_illegal_name(self, env, name):
        session = await env.session()
        assert env.ask(session, name).status_code == 422

    async def test_accepts_the_longest_name(self, env):
        session = await env.session()
        assert env.ask(session, "A" * 255).status_code == 201

    async def test_a_purpose_is_required(self, env):
        session = await env.session()
        response = env.client.post(
            env.url(session), json={"name": "A_KEY"}, headers=env.token(session)
        )
        assert response.status_code == 422

    async def test_the_owner_may_create_when_session_tokens_are_off(self, env):
        session = await env.session()
        assert env.ask(session, headers=owner_headers()).status_code == 201

    async def test_refuses_an_ended_session(self, env):
        session = await env.session(SessionStatus.STOPPED)
        # the session's own token is revoked at stop (401); its owner is told it ended
        assert env.ask(session).status_code == 401
        assert env.ask(session, headers=owner_headers()).status_code == 409

    async def test_unknown_session_is_404(self, env):
        ghost = Session(name="ghost", owner_id=OWNER, tenant_id="tenant-a")
        assert env.ask(ghost, headers=owner_headers()).status_code == 404


class TestAccess:
    async def test_a_token_without_the_scope_is_refused(self, env, monkeypatch):
        monkeypatch.setattr(session_credentials, "DEFAULT_SESSION_SCOPES", (FORGE_NOTIFY_SCOPE,))
        session = await env.session()
        assert env.ask(session).status_code == 403

    async def test_a_token_bound_to_another_session_is_refused(self, env):
        mine, other = await env.session(), await env.session()
        assert env.ask(mine, headers=env.token(other)).status_code == 403

    async def test_another_users_is_refused(self, env):
        session = await env.session()
        assert env.ask(session, headers=owner_headers(OTHER_USER)).status_code == 403

    async def test_a_credential_scoped_to_something_else_is_refused(self, env):
        session = await env.session()
        headers = {**owner_headers(), "x-auth-token-use": VALKYRIE_BUILD_TOKEN_USE}
        assert env.ask(session, headers=headers).status_code == 403

    async def test_every_session_route_checks_the_scope(self, env, monkeypatch):
        monkeypatch.setattr(session_credentials, "DEFAULT_SESSION_SCOPES", (FORGE_NOTIFY_SCOPE,))
        session = await env.session()
        rid = env.ask(session, headers=owner_headers()).json()["id"]
        calls = [
            ("GET", env.url(session)),
            ("GET", env.url(session, rid)),
            ("POST", env.url(session, rid, "claim")),
            ("POST", env.url(session, rid, "cancel")),
        ]
        for method, url in calls:
            response = env.client.request(method, url, headers=env.token(session))
            assert response.status_code == 403, url
            assert "forge:session:secret" in response.json()["detail"], url


class TestReads:
    async def test_lists_newest_first_and_filters_by_status(self, env):
        session = await env.session()
        first = env.ask(session, "A_KEY").json()
        second = env.ask(session, "B_KEY").json()
        env.client.post(env.url(session, first["id"], "cancel"), headers=env.token(session))
        headers = env.token(session)

        everything = env.client.get(env.url(session), headers=headers).json()
        pending = env.client.get(env.url(session), params={"status": "pending"}, headers=headers)

        assert [r["id"] for r in everything] == [second["id"], first["id"]]
        assert [r["id"] for r in pending.json()] == [second["id"]]

    async def test_gets_one_and_not_another_sessions(self, env):
        session, other = await env.session(), await env.session()
        request = env.ask(session).json()

        found = env.client.get(env.url(session, request["id"]), headers=env.token(session))
        stray = env.client.get(env.url(other, request["id"]), headers=env.token(other))
        missing = env.client.get(env.url(session, uuid4()), headers=env.token(session))

        assert found.json()["id"] == request["id"]
        assert stray.status_code == 404
        assert missing.status_code == 404


class TestFulfill:
    async def test_stores_the_value_and_never_returns_it(self, env):
        session = await env.session()
        request = env.ask(session).json()

        response = env.fulfill(session, request["id"])

        assert response.status_code == 200
        assert SECRET not in response.text
        body = response.json()
        assert (body["status"], body["keep"], body["credential_name"]) == (
            "fulfilled",
            "session",
            None,
        )
        assert body["resolved_at"] is not None
        name = session_credential_name(UUID(request["id"]))
        assert await env.credentials.get_value(CREDENTIAL_OWNER_TYPE, OWNER, name) == {
            "value": SECRET
        }
        stored = await env.credentials.get(CREDENTIAL_OWNER_TYPE, OWNER, name)
        assert stored.metadata == {
            "source": "secret_request",
            "session_id": str(session.id),
            "request_id": request["id"],
        }

    async def test_a_saved_value_is_stored_under_the_kebab_name_and_overwrites(self, env):
        session = await env.session()
        await env.credentials.store(
            CREDENTIAL_OWNER_TYPE, OWNER, "gitlab-token", SecretType.GENERIC, {"value": "old"}
        )
        request = env.ask(session).json()

        response = env.fulfill(session, request["id"], keep="saved")

        assert response.json()["credential_name"] == "gitlab-token"
        assert response.json()["keep"] == "saved"
        assert await env.credentials.get_value(CREDENTIAL_OWNER_TYPE, OWNER, "gitlab-token") == {
            "value": SECRET
        }

    async def test_only_a_pending_request_can_be_fulfilled(self, env):
        session = await env.session()
        request = env.ask(session).json()
        assert env.fulfill(session, request["id"]).status_code == 200
        assert env.fulfill(session, request["id"]).status_code == 409

    async def test_a_session_token_can_never_fulfill(self, env):
        session = await env.session()
        request = env.ask(session).json()

        response = env.fulfill(session, request["id"], headers=env.token(session))

        assert response.status_code == 403
        assert SECRET not in response.text
        assert env.repository.rows[UUID(request["id"])].status.value == "pending"
        assert len(env.credentials._values) == 0

    async def test_any_scoped_credential_header_is_refused(self, env):
        session = await env.session()
        request = env.ask(session).json()
        headers = {**owner_headers(), "x-auth-token-use": VALKYRIE_BUILD_TOKEN_USE}
        assert env.fulfill(session, request["id"], headers=headers).status_code == 403

    async def test_another_user_cannot_fulfill(self, env):
        session = await env.session()
        request = env.ask(session).json()
        response = env.fulfill(session, request["id"], headers=owner_headers(OTHER_USER))
        assert response.status_code == 403

    async def test_a_bad_body_is_422_and_does_not_echo_the_value(self, env):
        session = await env.session()
        request = env.ask(session).json()

        wrong_keep = env.client.post(
            env.url(session, request["id"], "fulfill"),
            json={"value": SECRET, "keep": "forever"},
            headers=owner_headers(),
        )
        empty = env.client.post(
            env.url(session, request["id"], "fulfill"),
            json={"value": "", "keep": "session"},
            headers=owner_headers(),
        )
        not_json = env.client.post(
            env.url(session, request["id"], "fulfill"),
            content=SECRET,
            headers=owner_headers(),
        )

        assert [wrong_keep.status_code, empty.status_code, not_json.status_code] == [422] * 3
        for response in (wrong_keep, empty, not_json):
            assert SECRET not in response.text
        assert len(env.credentials._values) == 0


class TestDecline:
    async def test_declines_a_pending_request_once(self, env):
        session = await env.session()
        request = env.ask(session).json()
        url = env.url(session, request["id"], "decline")

        declined = env.client.post(url, headers=owner_headers())
        again = env.client.post(url, headers=owner_headers())

        assert declined.status_code == 200
        assert declined.json()["status"] == "declined"
        assert again.status_code == 409

    async def test_a_session_token_can_never_decline(self, env):
        session = await env.session()
        request = env.ask(session).json()
        response = env.client.post(
            env.url(session, request["id"], "decline"), headers=env.token(session)
        )
        assert response.status_code == 403
        assert env.repository.rows[UUID(request["id"])].status.value == "pending"


class TestClaim:
    async def test_delivers_the_value_once_and_deletes_a_session_only_one(self, env):
        session = await env.session()
        request = env.ask(session).json()
        env.fulfill(session, request["id"])
        url = env.url(session, request["id"], "claim")
        headers = env.token(session)

        claimed = env.client.post(url, headers=headers)
        again = env.client.post(url, headers=headers)

        assert claimed.status_code == 200
        assert claimed.json() == {"name": "GITLAB_TOKEN", "value": SECRET}
        assert claimed.headers["cache-control"] == "no-store"
        assert again.status_code == 409
        assert SECRET not in again.text
        assert len(env.credentials._values) == 0
        shown = env.client.get(env.url(session, request["id"]), headers=headers).json()
        assert shown["status"] == "delivered"
        assert shown["delivered_at"] is not None

    async def test_a_saved_value_stays_in_the_owners_credentials(self, env):
        session = await env.session()
        request = env.ask(session).json()
        env.fulfill(session, request["id"], keep="saved")

        claimed = env.client.post(
            env.url(session, request["id"], "claim"), headers=env.token(session)
        )

        assert claimed.json()["value"] == SECRET
        assert await env.credentials.get_value(CREDENTIAL_OWNER_TYPE, OWNER, "gitlab-token")

    async def test_only_a_fulfilled_request_can_be_claimed(self, env):
        session = await env.session()
        request = env.ask(session).json()
        response = env.client.post(
            env.url(session, request["id"], "claim"), headers=env.token(session)
        )
        assert response.status_code == 409

    async def test_a_value_gone_from_the_store_is_an_error_not_an_empty_secret(self, env):
        session = await env.session()
        request = env.ask(session).json()
        env.fulfill(session, request["id"])
        await env.credentials.delete(
            CREDENTIAL_OWNER_TYPE, OWNER, session_credential_name(UUID(request["id"]))
        )

        response = env.client.post(
            env.url(session, request["id"], "claim"), headers=env.token(session)
        )

        assert response.status_code == 409
        assert "cancel" in response.json()["detail"]


class TestCancel:
    async def test_cancels_a_pending_request(self, env):
        session = await env.session()
        request = env.ask(session).json()
        response = env.client.post(
            env.url(session, request["id"], "cancel"), headers=env.token(session)
        )
        assert response.json()["status"] == "cancelled"

    async def test_cancelling_a_fulfilled_request_deletes_its_session_only_value(self, env):
        session = await env.session()
        request = env.ask(session).json()
        env.fulfill(session, request["id"])
        assert len(env.credentials._values) == 1

        response = env.client.post(
            env.url(session, request["id"], "cancel"), headers=owner_headers()
        )

        assert response.json()["status"] == "cancelled"
        assert len(env.credentials._values) == 0

    async def test_cancelling_a_saved_request_keeps_what_the_owner_saved(self, env):
        session = await env.session()
        request = env.ask(session).json()
        env.fulfill(session, request["id"], keep="saved")

        env.client.post(env.url(session, request["id"], "cancel"), headers=owner_headers())

        assert await env.credentials.get_value(CREDENTIAL_OWNER_TYPE, OWNER, "gitlab-token")

    async def test_a_closed_request_cannot_be_cancelled(self, env):
        session = await env.session()
        request = env.ask(session).json()
        url = env.url(session, request["id"], "cancel")
        headers = env.token(session)

        env.client.post(url, headers=headers)

        assert env.client.post(url, headers=headers).status_code == 409
