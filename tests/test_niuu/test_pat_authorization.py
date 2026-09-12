"""Exercise real Cedar authorization at the PAT HTTP and service boundaries."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.cedar import CedarAuthorizationAdapter
from identity.ports import AuthorizationEvaluationError
from niuu.adapters.inbound.rest_pats import create_pats_router
from niuu.domain.models import PersonalAccessToken, Principal
from niuu.domain.services.pat import PATService
from niuu.ports.token_issuer import IssuedToken


def app_for(principal, authorization=None):
    repo = AsyncMock()
    issuer = AsyncMock()
    token = PersonalAccessToken(
        id=uuid4(), owner_id=principal.user_id, name="test", created_at=datetime.now(UTC)
    )
    repo.create.return_value = token
    repo.list.return_value = [token]
    repo.delete.return_value = "hash"
    issuer.issue_token.return_value = IssuedToken(
        "signed-token", "id", principal.user_id, 9999999999
    )
    service = PATService(repo, issuer, authorization=authorization or CedarAuthorizationAdapter())
    app = FastAPI()
    app.state.pat_service = service
    app.state.identity = AsyncMock()

    async def identity():
        return principal

    app.include_router(create_pats_router(identity))
    return TestClient(app), repo, issuer, token


@pytest.mark.parametrize(
    "roles,tenant", [(["volundr:viewer"], "acme"), ([], "acme"), (["volundr:admin"], "")]
)
def test_denied_pat_create_never_issues_or_stores_a_token(roles, tenant):
    client, repo, issuer, _ = app_for(Principal("alice", "", tenant, roles))
    response = client.post("/api/v1/tokens", json={"name": "denied"})
    assert response.status_code == 403
    issuer.issue_token.assert_not_called()
    repo.create.assert_not_called()


def test_owner_can_create_list_and_revoke_under_cedar():
    client, repo, issuer, token = app_for(Principal("alice", "", "acme", ["volundr:developer"]))
    assert client.post("/api/v1/tokens", json={"name": "test"}).status_code == 201
    assert client.get("/api/v1/tokens").json()[0]["id"] == str(token.id)
    assert client.delete(f"/api/v1/tokens/{token.id}").status_code == 204
    repo.delete.assert_awaited_once_with(token.id, "alice")
    issuer.issue_token.assert_awaited_once()


def test_viewer_cannot_see_or_revoke_pat_metadata():
    client, repo, _, token = app_for(Principal("alice", "", "acme", ["volundr:viewer"]))
    assert client.get("/api/v1/tokens").json() == []
    assert client.delete(f"/api/v1/tokens/{token.id}").status_code == 403
    repo.delete.assert_not_called()


@pytest.mark.parametrize("method", ["post", "get", "delete"])
def test_policy_failure_returns_unavailable_without_mutation(method):
    authorization = AsyncMock()
    authorization.is_allowed.side_effect = AuthorizationEvaluationError("failed")
    authorization.filter_allowed.side_effect = AuthorizationEvaluationError("failed")
    client, repo, issuer, token = app_for(
        Principal("alice", "", "acme", ["volundr:developer"]), authorization
    )
    path = f"/api/v1/tokens/{token.id}" if method == "delete" else "/api/v1/tokens"
    kwargs = {"json": {"name": "test"}} if method == "post" else {}
    assert getattr(client, method)(path, **kwargs).status_code == 503
    repo.create.assert_not_called()
    repo.delete.assert_not_called()
    issuer.issue_token.assert_not_called()


def test_list_authorizes_persisted_owner_not_requested_owner():
    client, repo, _, _ = app_for(Principal("alice", "", "acme", ["volundr:admin"]))
    repo.list.return_value = [PersonalAccessToken(uuid4(), "bob", "private", datetime.now(UTC))]
    assert client.get("/api/v1/tokens").json() == []


def test_explicit_disabled_auth_accepts_http_without_credentials():
    from identity.adapters.authorization import AllowAllAuthorizationAdapter
    from identity.adapters.http_auth import extract_principal
    from identity.adapters.identity import AllowAllIdentityAdapter

    repo = AsyncMock()
    repo.list.return_value = []
    app = FastAPI()
    app.state.identity = AllowAllIdentityAdapter(user_repository=AsyncMock())
    app.state.pat_service = PATService(
        repo, AsyncMock(), authorization=AllowAllAuthorizationAdapter()
    )
    app.include_router(create_pats_router(extract_principal))
    with TestClient(app) as client:
        assert client.get("/api/v1/tokens").status_code == 200
    repo.list.assert_awaited_once_with("dev-user")
