"""Forge side of LEX-1936: an explicit 409 for a native control, and an audited dismiss."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.domain.models import Principal
from niuu.domain.services.forge_session_policy import ROUTE_POLICIES
from tests.conftest import (
    InMemoryPricingProvider,
    InMemorySessionRepository,
    InMemoryStatsRepository,
    make_session_participant_service,
)
from tests.test_adapters import test_rest as rest_tests
from volundr.adapters.inbound.rest import create_router
from volundr.domain.models import GitSource, SessionStatus
from volundr.domain.services import StatsService
from volundr.domain.services.session import SessionAccessDeniedError, SessionService

OWNER = Principal(user_id="dfagnou", email="", tenant_id="default", roles=[])
SESSION_TOKEN = Principal(
    user_id="dfagnou",
    email="",
    tenant_id="default",
    roles=[],
    token_use="forge_session",
)


@pytest.fixture
def service(repository: InMemorySessionRepository, pod_manager) -> SessionService:
    return SessionService(repository, pod_manager)


@pytest.fixture
def client(service: SessionService):
    app = FastAPI()
    app.include_router(
        create_router(
            service,
            StatsService(InMemoryStatsRepository(), service),
            pricing_provider=InMemoryPricingProvider(),
            session_participant_service=make_session_participant_service(service),
        )
    )
    with TestClient(app) as test_client:
        yield test_client


def _principal(principal: Principal):
    return patch(
        "volundr.adapters.inbound.rest.extract_principal", new=AsyncMock(return_value=principal)
    )


def test_native_control_refusal_is_409_with_the_card_not_202(client, repository):
    """5 Nov: coordinator POSTs got 202 pending while the app refused the same steer."""
    fake_connect = rest_tests.TestSessionMessages._ack_connect(
        {
            "type": "user_delivery_failed",
            "status": "failed",
            "id": "m1",
            "error": "Claude is waiting on a native control (tty-1-c3b257b9, permission)",
            "code": "native_control_pending",
            "control_request_id": "tty-1-c3b257b9",
            "control_kind": "permission",
            "control_opened_at": "2026-11-05T20:36:56.590000+00:00",
            "menu_state": "live",
        }
    )
    response = rest_tests.TestSessionMessages()._post_message(client, repository, fake_connect)
    assert response.status_code == 409
    body = response.json()
    assert body["status"] == "failed" and body["delivery"] == "failed"
    assert body["detail"] == {
        "code": "native_control_pending",
        "message": "Claude is waiting on a native control (tty-1-c3b257b9, permission)",
        "control_request_id": "tty-1-c3b257b9",
        "control_kind": "permission",
        "control_opened_at": "2026-11-05T20:36:56.590000+00:00",
        "menu_state": "live",
    }


def test_a_menu_without_a_card_has_no_control_request_id(client, repository):
    fake_connect = rest_tests.TestSessionMessages._ack_connect(
        {
            "type": "user_delivery_failed",
            "status": "failed",
            "error": "A Claude menu is on screen",
            "code": "native_control_pending",
            "control_request_id": "",
            "control_kind": "unknown",
            "menu_state": "live",
        }
    )
    response = rest_tests.TestSessionMessages()._post_message(client, repository, fake_connect)
    assert response.status_code == 409
    assert response.json()["detail"]["control_request_id"] is None


async def _running(service: SessionService):
    session = await service.create_session(
        "native",
        "claude-sonnet-4",
        source=GitSource(repo="https://github.com/org/repo", branch="main"),
    )
    await service.start_session(session.id)
    return await service.get_session(session.id)


def _broker_reply(status_code: int, payload: dict):
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.json.return_value = payload
    return response


async def test_owner_dismiss_proxies_to_the_broker_with_who_and_is_audited(client, service, caplog):
    session = await _running(service)
    caplog.set_level(logging.WARNING)
    with (
        _principal(OWNER),
        patch("volundr.adapters.inbound.rest.httpx.AsyncClient") as client_cls,
    ):
        broker = AsyncMock()
        broker.request.return_value = _broker_reply(
            200,
            {
                "status": "dismissed",
                "control": {"request_id": "tty-1", "dismiss_reason": "dismissed_by_operator"},
            },
        )
        client_cls.return_value.__aenter__.return_value = broker
        response = client.post(
            f"/api/v1/forge/sessions/{session.id}/native-controls/tty-1/dismiss",
            headers={"Authorization": "Bearer t"},
        )
    assert response.status_code == 200
    assert response.json()["control"]["dismiss_reason"] == "dismissed_by_operator"
    method, url = broker.request.await_args.args
    assert method == "POST"
    assert url.endswith(f"/s/{session.id}/api/native-controls/tty-1/dismiss")
    kwargs = broker.request.await_args.kwargs
    assert kwargs["json"] == {"dismissed_by": "dfagnou"}
    assert kwargs["headers"]["x-niuu-room-role"] == "owner"
    assert kwargs["headers"]["Authorization"] == "Bearer t"
    assert "AUDIT native control tty-1" in caplog.text and "dfagnou" in caplog.text


async def test_broker_status_is_passed_through(client, service):
    session = await _running(service)
    with (
        _principal(OWNER),
        patch("volundr.adapters.inbound.rest.httpx.AsyncClient") as client_cls,
    ):
        broker = AsyncMock()
        broker.request.return_value = _broker_reply(404, {"detail": "No open native control"})
        client_cls.return_value.__aenter__.return_value = broker
        response = client.post(f"/api/v1/forge/sessions/{session.id}/native-controls/tty-9/dismiss")
    assert response.status_code == 404


async def test_list_native_controls_proxies_get(client, service):
    session = await _running(service)
    with (
        _principal(OWNER),
        patch("volundr.adapters.inbound.rest.httpx.AsyncClient") as client_cls,
    ):
        broker = AsyncMock()
        broker.request.return_value = _broker_reply(200, {"controls": [], "counts": {}})
        client_cls.return_value.__aenter__.return_value = broker
        response = client.get(f"/api/v1/forge/sessions/{session.id}/native-controls")
    assert response.status_code == 200 and response.json() == {"controls": [], "counts": {}}
    assert broker.request.await_args.args[0] == "GET"


async def test_a_session_credential_may_not_dismiss(client, service):
    session = await _running(service)
    with _principal(SESSION_TOKEN):
        response = client.post(f"/api/v1/forge/sessions/{session.id}/native-controls/tty-1/dismiss")
    assert response.status_code == 403


def test_session_token_allow_list_does_not_name_native_controls():
    assert not any("native-controls" in policy.template for policy in ROUTE_POLICIES)


async def test_a_non_owner_may_not_dismiss(client, service):
    session = await _running(service)
    with (
        _principal(OWNER),
        patch.object(
            SessionService,
            "_check_access",
            new=AsyncMock(side_effect=SessionAccessDeniedError(session.id, "x")),
        ),
    ):
        response = client.post(f"/api/v1/forge/sessions/{session.id}/native-controls/tty-1/dismiss")
    assert response.status_code == 403


async def test_unknown_or_stopped_sessions(client, service):
    with _principal(OWNER):
        missing = client.post(
            "/api/v1/forge/sessions/00000000-0000-0000-0000-000000000000"
            "/native-controls/tty-1/dismiss"
        )
    assert missing.status_code == 404
    session = await service.create_session(
        "stopped",
        "claude-sonnet-4",
        source=GitSource(repo="https://github.com/org/repo", branch="main"),
    )
    assert session.status is not SessionStatus.RUNNING
    with _principal(OWNER):
        stopped = client.post(f"/api/v1/forge/sessions/{session.id}/native-controls/tty-1/dismiss")
    assert stopped.status_code == 409


async def test_unreachable_broker_reconciles_and_409s(client, service):
    session = await _running(service)
    request = httpx.Request("POST", "http://x")
    with (
        _principal(OWNER),
        patch("volundr.adapters.inbound.rest.httpx.AsyncClient") as client_cls,
    ):
        broker = AsyncMock()
        broker.request.side_effect = httpx.ConnectError("refused", request=request)
        client_cls.return_value.__aenter__.return_value = broker
        response = client.post(f"/api/v1/forge/sessions/{session.id}/native-controls/tty-1/dismiss")
    assert response.status_code == 409
    assert "no longer reachable" in response.json()["detail"]


@pytest.mark.parametrize("body", [b"not json"])
async def test_a_non_json_broker_reply_is_wrapped(client, service, body):
    session = await _running(service)
    reply = MagicMock(spec=httpx.Response)
    reply.status_code = 502
    reply.json.side_effect = ValueError("no json")
    reply.text = body.decode()
    with (
        _principal(OWNER),
        patch("volundr.adapters.inbound.rest.httpx.AsyncClient") as client_cls,
    ):
        broker = AsyncMock()
        broker.request.return_value = reply
        client_cls.return_value.__aenter__.return_value = broker
        response = client.get(f"/api/v1/forge/sessions/{session.id}/native-controls")
    assert response.status_code == 502 and response.json() == {"detail": "not json"}


async def test_owner_reconcile_proxies_to_the_broker_and_is_audited(client, service, caplog):
    """LEX-1979: an owner checks the session's open questions against its CLI session file."""
    session = await _running(service)
    caplog.set_level(logging.WARNING)
    outcome = {"checked": 1, "closed": [{"tool_use_id": "toolu_x", "reason": "never_shown"}]}
    with (
        _principal(OWNER),
        patch("volundr.adapters.inbound.rest.httpx.AsyncClient") as client_cls,
    ):
        broker = AsyncMock()
        broker.request.return_value = _broker_reply(200, {**outcome, "kept": []})
        client_cls.return_value.__aenter__.return_value = broker
        response = client.post(f"/api/v1/forge/sessions/{session.id}/native-controls/reconcile")
    assert response.status_code == 200
    assert response.json()["closed"] == outcome["closed"]
    method, url = broker.request.await_args.args
    assert method == "POST"
    assert url.endswith(f"/s/{session.id}/api/native-controls/reconcile")
    assert broker.request.await_args.kwargs["headers"]["x-niuu-room-role"] == "owner"
    assert "question reconcile requested by dfagnou" in caplog.text


async def test_a_session_credential_may_not_reconcile(client, service):
    session = await _running(service)
    with _principal(SESSION_TOKEN):
        response = client.post(f"/api/v1/forge/sessions/{session.id}/native-controls/reconcile")
    assert response.status_code == 403
