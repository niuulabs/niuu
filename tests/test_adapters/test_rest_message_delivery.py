"""Delivery claims are owner-authorized and cannot be changed ambiguously."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.domain.models import Principal
from volundr.adapters.inbound.rest_message_delivery import create_message_delivery_router
from volundr.domain.message_delivery import MessageDelivery, MessageDeliveryConflictError
from volundr.domain.services.session import SessionAccessDeniedError


@pytest.fixture
def delivery_api():
    repo, service = AsyncMock(), AsyncMock()
    app = FastAPI()
    app.include_router(create_message_delivery_router(repo, service))
    sid = uuid4()
    principal = Principal(
        user_id="owner", email="owner@example.test", tenant_id="default", roles=[]
    )
    with patch(
        "volundr.adapters.inbound.rest_message_delivery.extract_principal",
        new=AsyncMock(return_value=principal),
    ):
        yield (
            TestClient(app),
            repo,
            service,
            f"/api/v1/forge/sessions/{sid}/message-deliveries/req-1",
        )


def test_claim_and_read_hide_claim_token(delivery_api):
    client, repo, _, path = delivery_api
    repo.claim.return_value = MessageDelivery("req-1", "pending", claimed=True)
    token = uuid4()
    response = client.post(
        path + "/claim", json={"payload_hash": "a" * 64, "claim_token": str(token)}
    )
    assert response.status_code == 200
    assert response.json() == {
        "request_id": "req-1",
        "status": "pending",
        "claimed": True,
        "error": None,
    }
    assert repo.claim.await_args.args[3] == token
    repo.get.return_value = MessageDelivery("req-1", "delivered")
    assert client.get(path).json()["status"] == "delivered"


@pytest.mark.parametrize("method, suffix", [("get", ""), ("post", "/claim"), ("post", "/settle")])
def test_foreign_owner_cannot_read_or_write(delivery_api, method, suffix):
    client, repo, service, path = delivery_api
    service._check_access.side_effect = SessionAccessDeniedError(uuid4(), "foreign-user")
    body = {"payload_hash": "a" * 64, "claim_token": str(uuid4()), "status": "delivered"}
    response = client.request(method, path + suffix, json=body)
    assert response.status_code == 403
    repo.claim.assert_not_awaited()
    repo.get.assert_not_awaited()
    repo.settle.assert_not_awaited()


def test_missing_session_and_claim_are_distinct(delivery_api):
    client, repo, service, path = delivery_api
    repo.get.return_value = None
    assert client.get(path).status_code == 404
    service.get_session.return_value = None
    response = client.post(
        path + "/claim", json={"payload_hash": "b" * 64, "claim_token": str(uuid4())}
    )
    assert response.status_code == 404
    repo.claim.assert_not_awaited()


def test_changed_payload_and_stolen_settlement_return_conflict(delivery_api):
    client, repo, _, path = delivery_api
    repo.claim.side_effect = MessageDeliveryConflictError("different content")
    repo.settle.side_effect = MessageDeliveryConflictError("different claimant")
    token = str(uuid4())
    assert (
        client.post(
            path + "/claim", json={"payload_hash": "c" * 64, "claim_token": token}
        ).status_code
        == 409
    )
    assert (
        client.post(
            path + "/settle", json={"status": "delivered", "claim_token": token}
        ).status_code
        == 409
    )


def test_settlement_is_terminal_and_request_id_is_bounded(delivery_api):
    client, repo, _, path = delivery_api
    token = str(uuid4())
    assert (
        client.post(path + "/settle", json={"status": "pending", "claim_token": token}).status_code
        == 422
    )
    assert client.get(path.rsplit("/", 1)[0] + "/" + "x" * 201).status_code == 422
    repo.settle.return_value = MessageDelivery("req-1", "failed", error="native stopped")
    response = client.post(
        path + "/settle", json={"status": "failed", "claim_token": token, "error": "native stopped"}
    )
    assert response.json()["error"] == "native stopped"
    repo.settle.side_effect = LookupError("missing claim")
    assert (
        client.post(path + "/settle", json={"status": "failed", "claim_token": token}).status_code
        == 404
    )


@pytest.mark.parametrize("operation", ["claim", "settle"])
@pytest.mark.parametrize("caller, expected", [("rightful-owner", 200), ("foreign-developer", 403)])
def test_actual_role_policy_enforces_delivery_write_ownership(operation, caller, expected):
    from types import SimpleNamespace

    from volundr.adapters.outbound.authorization import SimpleRoleAuthorizationAdapter
    from volundr.adapters.outbound.identity import AllowAllIdentityAdapter
    from volundr.domain.services.session import SessionService

    sid = uuid4()
    service = SessionService.__new__(SessionService)
    service._authorization = SimpleRoleAuthorizationAdapter()
    service.get_session = AsyncMock(
        return_value=SimpleNamespace(id=sid, owner_id="rightful-owner", tenant_id="tenant-a")
    )
    repo = AsyncMock()
    repo.claim.return_value = MessageDelivery("r", "pending", claimed=True)
    repo.settle.return_value = MessageDelivery("r", "delivered")
    app = FastAPI()
    app.state.identity = AllowAllIdentityAdapter(user_repository=AsyncMock())
    app.include_router(create_message_delivery_router(repo, service))
    response = TestClient(app).post(
        f"/api/v1/forge/sessions/{sid}/message-deliveries/r/{operation}",
        headers={
            "x-auth-user-id": caller,
            "x-auth-tenant": "tenant-a",
            "x-auth-roles": "volundr:developer",
        },
        json={"payload_hash": "a" * 64, "claim_token": str(uuid4()), "status": "delivered"},
    )
    assert response.status_code == expected
    assert getattr(repo, operation).await_count == (1 if expected == 200 else 0)
