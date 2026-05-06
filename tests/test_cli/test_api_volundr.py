"""Tests for cli.api.volundr — Volundr REST API methods."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from cli.api.client import APIClient
from cli.api.volundr import VolundrAPI

BASE = "http://volundr.test"
V1 = "/api/v1/forge"


@pytest.fixture
def api() -> VolundrAPI:
    return VolundrAPI(APIClient(base_url=BASE, access_token="t"))


class TestListSessions:
    async def test_returns_sessions(self, api: VolundrAPI) -> None:
        with respx.mock:
            respx.get(f"{BASE}{V1}/sessions").mock(
                return_value=httpx.Response(
                    200,
                    json=[
                        {"id": "s1", "name": "alpha", "status": "running"},
                        {"id": "s2", "name": "beta", "status": "stopped"},
                    ],
                )
            )
            sessions = await api.list_sessions()
        assert len(sessions) == 2
        assert sessions[0].id == "s1"
        assert sessions[1].status == "stopped"

    async def test_empty_list(self, api: VolundrAPI) -> None:
        with respx.mock:
            respx.get(f"{BASE}{V1}/sessions").mock(return_value=httpx.Response(200, json=[]))
            sessions = await api.list_sessions()
        assert sessions == []


class TestCreateSession:
    async def test_creates_session(self, api: VolundrAPI) -> None:
        with respx.mock:
            respx.post(f"{BASE}{V1}/sessions").mock(
                return_value=httpx.Response(
                    201,
                    json={
                        "id": "new-id",
                        "name": "test",
                        "status": "pending",
                        "source": {"repo": "org/repo", "branch": "main", "base_branch": "main"},
                    },
                )
            )
            session = await api.create_session("test", repo="org/repo", branch="main")
        assert session.id == "new-id"
        assert session.repo == "org/repo"

    async def test_create_sends_payload(self, api: VolundrAPI) -> None:
        with respx.mock:
            route = respx.post(f"{BASE}{V1}/sessions").mock(
                return_value=httpx.Response(
                    201,
                    json={
                        "id": "x",
                        "name": "n",
                        "status": "pending",
                    },
                )
            )
            await api.create_session("n", model="opus", initial_prompt="go")
            body = json.loads(route.calls[0].request.content)
        assert body["name"] == "n"
        assert body["model"] == "opus"
        assert body["initial_prompt"] == "go"


class TestGetSession:
    async def test_returns_session(self, api: VolundrAPI) -> None:
        with respx.mock:
            respx.get(f"{BASE}{V1}/sessions/s1").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "id": "s1",
                        "name": "a",
                        "status": "running",
                        "source": {"repo": "r", "branch": "b", "base_branch": "m"},
                    },
                )
            )
            session = await api.get_session("s1")
        assert session is not None
        assert session.branch == "b"

    async def test_returns_none_for_404(self, api: VolundrAPI) -> None:
        with respx.mock:
            respx.get(f"{BASE}{V1}/sessions/missing").mock(return_value=httpx.Response(404))
            session = await api.get_session("missing")
        assert session is None


class TestStartStopDelete:
    async def test_start_session(self, api: VolundrAPI) -> None:
        with respx.mock:
            route = respx.post(f"{BASE}{V1}/sessions/s1/start").mock(
                return_value=httpx.Response(200)
            )
            await api.start_session("s1")
        assert route.called

    async def test_stop_session(self, api: VolundrAPI) -> None:
        with respx.mock:
            respx.post(f"{BASE}{V1}/sessions/s1/stop").mock(return_value=httpx.Response(200))
            await api.stop_session("s1")

    async def test_stop_session_404_is_ok(self, api: VolundrAPI) -> None:
        with respx.mock:
            respx.post(f"{BASE}{V1}/sessions/gone/stop").mock(return_value=httpx.Response(404))
            await api.stop_session("gone")

    async def test_delete_session(self, api: VolundrAPI) -> None:
        with respx.mock:
            respx.delete(f"{BASE}{V1}/sessions/s1").mock(return_value=httpx.Response(204))
            await api.delete_session("s1")


class TestChronicleTimelineStats:
    async def test_get_chronicle(self, api: VolundrAPI) -> None:
        with respx.mock:
            respx.get(f"{BASE}{V1}/sessions/s1/chronicle").mock(
                return_value=httpx.Response(200, json={"summary": "Did things."})
            )
            summary = await api.get_chronicle("s1")
        assert summary == "Did things."

    async def test_get_timeline(self, api: VolundrAPI) -> None:
        with respx.mock:
            respx.get(f"{BASE}{V1}/chronicles/s1/timeline").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "events": [{"t": 12, "type": "session", "label": "started"}],
                        "files": [],
                        "commits": [],
                        "token_burn": [],
                    },
                )
            )
            timeline = await api.get_timeline("s1")
        assert len(timeline) == 1
        assert timeline[0].timestamp == "12"
        assert timeline[0].event == "session"
        assert timeline[0].details["label"] == "started"

    async def test_get_stats(self, api: VolundrAPI) -> None:
        with respx.mock:
            respx.get(f"{BASE}{V1}/stats").mock(
                return_value=httpx.Response(
                    200,
                    json={"active_sessions": 1, "total_sessions": 2, "tokens_today": 300},
                )
            )
            stats = await api.get_stats("s1")
        assert stats["tokens_today"] == 300
