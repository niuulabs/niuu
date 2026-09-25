"""Bounded Forge adapter over current/retained gateways and archive reads."""

import copy
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.domain.models import Principal
from skuld.conversation_snapshot import prepare_history_page
from tests.conftest import (
    InMemorySessionRepository,
    MockPodManager,
    make_session_participant_service,
)
from tests.test_skuld.test_history_paging import rows
from volundr.adapters.inbound import rest
from volundr.domain.models import Session, SessionStatus
from volundr.domain.services import SessionService


async def setup_api(monkeypatch, *, mode="archive", source=None, upstream_status=200):
    session = Session(
        id=uuid4(),
        name="synthetic-history",
        model="test",
        status=SessionStatus.ARCHIVED if mode == "archive" else SessionStatus.RUNNING,
        chat_endpoint=None if mode == "archive" else "ws://gateway.test/session",
    )
    repo = InMemorySessionRepository()
    await repo.create(session)
    sessions = SessionService(repository=repo, pod_manager=MockPodManager(), validate_repos=False)
    source = source if source is not None else rows(50)
    archive = AsyncMock()
    archive.get_transcript.side_effect = lambda sid: {"turns": copy.deepcopy(source)}
    archive.latest_event_seq.return_value = 0
    app = FastAPI()
    app.include_router(
        rest.create_router(
            sessions,
            archive_service=archive,
            history_max_turns=5,
            history_max_bytes=8192,
            session_participant_service=make_session_participant_service(sessions),
        )
    )
    captures = []

    def upstream(request):
        captures.append(dict(request.url.params))
        if upstream_status != 200:
            return httpx.Response(
                upstream_status,
                json={
                    "detail": {
                        "code": "history_cursor_invalid"
                        if upstream_status == 409
                        else "history_busy",
                        "recovery": "recent" if upstream_status == 409 else "retry",
                    }
                },
            )
        payload = {"turns": copy.deepcopy(source), "is_active": False}
        if mode == "gateway" and request.url.params.get("history_protocol") == "2":
            payload = prepare_history_page(
                {
                    **payload,
                    "history_source": "gateway",
                    "history_settled_tail_id": source[-1]["id"],
                },
                session_id=str(session.id),
                max_turns=int(request.url.params["limit"]),
                max_bytes=int(request.url.params["max_bytes"]),
                cursor=request.url.params.get("cursor"),
            )
        return httpx.Response(200, json=payload)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        rest.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(upstream)),
    )
    return app, session, sessions, archive, source, captures


@pytest.mark.parametrize("mode", ["archive", "legacy_gateway", "gateway"])
async def test_actual_http_pages_survive_appends_and_refresh_without_duplicates(monkeypatch, mode):
    app, session, _, archive, source, captures = await setup_api(monkeypatch, mode=mode)
    url = f"/api/v1/forge/sessions/{session.id}/conversation"
    params = {"history_protocol": 2, "limit": 99999, "max_bytes": 999999999}
    with TestClient(app) as client:
        first_response = client.get(url, params=params)
        assert first_response.status_code == 200
        assert len(first_response.content) <= 8192
        first = first_response.json()
        assert first["history_protocol"] == 2 and len(first["turns"]) == 5
        assert first["window_offset"] == 45 and first["total_turns"] == 50
        assert first["history_source"] == mode
        source.extend([{**row, "id": f"new-{i}"} for i, row in enumerate(rows(3))])
        source[48]["metadata"] = {"status": "completed"}
        older = client.get(url, params={**params, "cursor": first["older_cursor"]}).json()
        assert [t["id"] for t in older["turns"]] == list(map(str, range(40, 45)))
        refresh = client.get(url, params={**params, "cursor": first["refresh_cursor"]}).json()
        assert [t["id"] for t in refresh["turns"]] == list(map(str, range(45, 50)))
        assert refresh["turns"][3]["metadata"]["status"] == "completed"
        if mode != "archive":
            assert captures[0] == {
                "detail": "full",
                "history_protocol": "2",
                "limit": "5",
                "max_bytes": "8192",
            }
            assert captures[1]["cursor"] == first["older_cursor"]
            archive.get_transcript.assert_not_awaited()


@pytest.mark.parametrize("mode", ["archive", "legacy_gateway"])
async def test_cursor_failure_cannot_silently_return_different_page(monkeypatch, mode):
    app, session, _, _, source, _ = await setup_api(monkeypatch, mode=mode)
    url = f"/api/v1/forge/sessions/{session.id}/conversation"
    with TestClient(app) as client:
        first = client.get(url, params={"history_protocol": 2}).json()
        source[0]["id"] = "resegmented"
        bad = client.get(url, params={"history_protocol": 2, "cursor": first["older_cursor"]})
        assert bad.status_code == 409
        assert bad.json()["detail"]["code"] == "history_cursor_invalid"
        assert client.get(url, params={"history_protocol": 2, "cursor": "%%%"}).status_code == 400
        assert client.get(url, params={"cursor": first["older_cursor"]}).status_code == 400
        assert client.get(url, params={"history_protocol": 2, "before": 3}).status_code == 400


@pytest.mark.parametrize("upstream_status", [409, 503])
async def test_new_gateway_gap_or_busy_cannot_switch_to_stale_archive(monkeypatch, upstream_status):
    app, session, _, archive, _, _ = await setup_api(
        monkeypatch, mode="gateway", upstream_status=upstream_status
    )
    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/forge/sessions/{session.id}/conversation", params={"history_protocol": 2}
        )
    assert response.status_code == upstream_status
    archive.get_transcript.assert_not_awaited()


@pytest.mark.parametrize("mode", ["archive", "legacy_gateway"])
async def test_huge_item_preview_then_explicit_full_expansion(monkeypatch, mode):
    source = rows(1, content="😀" * 100_000)
    app, session, _, _, _, _ = await setup_api(monkeypatch, mode=mode, source=source)
    url = f"/api/v1/forge/sessions/{session.id}/conversation"
    with TestClient(app) as client:
        response = client.get(url, params={"history_protocol": 2})
        assert len(response.content) <= 8192
        preview = response.json()["turns"][0]
        assert preview["history_ref"] == {"turn_id": "0"}
        full = client.get(url + "/turns/0")
        assert full.status_code == 200
        assert full.json()["turn"] == source[0]
        assert client.get(url + "/turns/missing").status_code == 404


async def test_history_and_expansion_use_existing_authorization_adapter(monkeypatch):
    app, session, sessions, archive, _, captures = await setup_api(monkeypatch, mode="gateway")
    sessions._authorization = AsyncMock()
    sessions._authorization.is_allowed.return_value = False
    app.state.identity = AsyncMock()
    principal = Principal(
        user_id="not-owner", email="test@example.com", tenant_id="other", roles=[]
    )
    monkeypatch.setattr(
        "volundr.adapters.inbound.auth.extract_principal", AsyncMock(return_value=principal)
    )
    with TestClient(app) as client:
        url = f"/api/v1/forge/sessions/{session.id}/conversation"
        assert client.get(url, params={"history_protocol": 2}).status_code == 403
        assert client.get(url + "/turns/0").status_code == 403
    assert captures == []
    archive.get_transcript.assert_not_awaited()
