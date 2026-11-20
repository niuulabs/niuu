"""Bounded Forge adapter over current/retained gateways and archive reads."""

import copy
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.domain.models import Principal
from niuu.domain.session_endpoint import BROKER_UNAVAILABLE_HEADER
from skuld.conversation_snapshot import prepare_history_page
from tests.conftest import (
    InMemorySessionRepository,
    MockPodManager,
    make_session_participant_service,
)
from tests.test_skuld.test_history_paging import rows, tool_turn
from volundr.adapters.inbound import rest
from volundr.domain.models import Session, SessionStatus
from volundr.domain.services import SessionService


async def setup_api(
    monkeypatch, *, mode="archive", source=None, upstream_status=200, unavailable=None
):
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
        if unavailable is not None:
            # The session proxy's own answer: the broker is not serving.
            return httpx.Response(
                504 if unavailable == "timeout" else 502,
                json={"detail": "Skuld broker not ready"},
                headers={BROKER_UNAVAILABLE_HEADER: unavailable},
            )
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
                part_paging=request.url.params.get("part_paging") == "1",
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


@pytest.mark.parametrize("mode", ["archive", "legacy_gateway", "gateway"])
async def test_huge_turn_is_whole_or_paged_by_complete_parts_never_previewed(monkeypatch, mode):
    source = [*rows(2), tool_turn(60)]
    app, session, _, _, _, captures = await setup_api(monkeypatch, mode=mode, source=source)
    url = f"/api/v1/forge/sessions/{session.id}/conversation"
    with TestClient(app) as client:
        whole = client.get(url, params={"history_protocol": 2}).json()
        assert whole["turns"] == [source[-1]]
        assert "history_preview" not in whole and "history_preview" not in whole["turns"][0]
        params = {"history_protocol": 2, "part_paging": 1}
        current = client.get(url, params=params)
        parts, pages = [], 0
        while True:
            pages += 1
            assert current.status_code == 200 and len(current.content) <= 8192
            body = current.json()
            turn = body["turns"][-1]
            assert turn["id"] == "long" and "history_preview" not in turn
            parts = turn["parts"] + parts
            if not turn.get("part_window") or turn["part_window"]["offset"] == 0:
                break
            current = client.get(url, params={**params, "cursor": body["older_cursor"]})
        assert pages > 2
        assert parts == source[-1]["parts"]
        assert turn.get("content", "final answer") == "final answer"
        full = client.get(url + "/turns/long")
        assert full.status_code == 200 and full.json()["turn"] == source[-1]
        assert client.get(url, params={"part_paging": 1}).status_code == 400
    if mode == "gateway":
        assert any(capture.get("part_paging") == "1" for capture in captures)


async def test_shallow_item_read_keeps_heavy_tool_results_lazy(monkeypatch):
    source = [tool_turn(3, result_bytes=5000)]
    app, session, _, _, _, _ = await setup_api(monkeypatch, source=source)
    url = f"/api/v1/forge/sessions/{session.id}/conversation"
    with TestClient(app) as client:
        item = client.get(url, params={"turn_id": "long", "detail": "shallow"}).json()["turn"]
        full = client.get(url, params={"turn_id": "long"}).json()["turn"]
    results = [part for part in item["parts"] if part["type"] == "tool_result"]
    assert len(results) == 3
    assert all(part["truncated"] and "content" not in part for part in results)
    assert full == source[0]


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


@pytest.mark.parametrize("mode", ["archive", "legacy_gateway", "gateway"])
async def test_source_alias_resolves_exact_absolute_index_on_every_read_path(monkeypatch, mode):
    from niuu.domain.conversation_timeline import project_timeline
    from tests.test_niuu.test_conversation_timeline import _assistant, _part, _user

    source = _assistant([_part(2), _part(6)])
    source["id"] = "durable-final"
    source["metadata"] = {"final_output": True}
    source.pop("in_progress")
    projected = project_timeline([_user(1), _user(5), source], "fixture")
    app, session, _, _, _, _ = await setup_api(monkeypatch, mode=mode, source=projected)
    with TestClient(app) as client:
        url = f"/api/v1/forge/sessions/{session.id}/conversation"
        reply = client.get(url + "/turns/durable-final")
        assert reply.status_code == 200
        anchor = reply.json()
        assert anchor["requested_turn_id"] == "durable-final"
        assert anchor["index"] == 3 and anchor["total_turns"] == 4
        assert anchor["turn"]["id"] == projected[-1]["id"]
        page = client.get(url, params={"detail": "shallow", "after": anchor["index"] - 1}).json()
        assert page["turns"][0]["id"] == anchor["turn"]["id"]


@pytest.mark.parametrize("how", ["connect", "transport", "timeout"])
async def test_a_first_page_while_the_broker_is_not_serving_comes_from_the_log(monkeypatch, how):
    """A session starting or restarting has no broker for a few seconds (Thor 5xx,
    2026-11-04): its first page is the durable log's, as an unpaged read already is."""
    app, session, _, archive, _, _ = await setup_api(monkeypatch, mode="gateway", unavailable=how)
    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/forge/sessions/{session.id}/conversation",
            params={"history_protocol": 2, "limit": 5},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["history_protocol"] == 2 and body["total_turns"] == 50
    assert [t["id"] for t in body["turns"]] == list(map(str, range(45, 50)))
    archive.get_transcript.assert_awaited()


async def test_a_cursor_page_while_the_broker_is_not_serving_is_a_typed_retry(monkeypatch):
    app, session, _, archive, _, _ = await setup_api(monkeypatch, mode="gateway")
    url = f"/api/v1/forge/sessions/{session.id}/conversation"
    with TestClient(app) as client:
        first = client.get(url, params={"history_protocol": 2}).json()
    monkeypatch.undo()  # a second Forge, whose broker is not serving
    app2, session2, _, archive2, _, _ = await setup_api(
        monkeypatch, mode="gateway", unavailable="connect"
    )
    with TestClient(app2) as client:
        response = client.get(
            f"/api/v1/forge/sessions/{session2.id}/conversation",
            params={"history_protocol": 2, "cursor": first["older_cursor"]},
        )
    assert response.status_code == 503
    assert response.json()["detail"] == {"code": "history_busy", "recovery": "retry"}
    assert response.headers["retry-after"] == "1"
    archive2.get_transcript.assert_not_awaited()


async def test_a_broker_answer_without_the_marker_still_passes_through(monkeypatch):
    """Only the proxy's own marker means "not serving": a broker's 502 is its answer."""
    app, session, _, archive, _, _ = await setup_api(
        monkeypatch, mode="gateway", upstream_status=502
    )
    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/forge/sessions/{session.id}/conversation", params={"history_protocol": 2}
        )
    assert response.status_code == 502
    archive.get_transcript.assert_not_awaited()
