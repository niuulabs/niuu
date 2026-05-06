"""Tests for VolundrHTTPAdapter with respx-mocked httpx calls."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from tyr.adapters.volundr_http import VolundrHTTPAdapter
from tyr.ports.volundr import SpawnRequest

BASE_URL = "http://volundr.test:8000"
SESSIONS_URL = f"{BASE_URL}/api/v1/forge/sessions"


@pytest.fixture
def adapter() -> VolundrHTTPAdapter:
    return VolundrHTTPAdapter(base_url=BASE_URL, timeout=5.0, name="test-cluster")


# -------------------------------------------------------------------
# _headers
# -------------------------------------------------------------------


class TestAuthHeaders:
    def test_no_token_by_default(self, adapter: VolundrHTTPAdapter):
        assert adapter._headers() == {}

    def test_auth_token_provides_header(self, adapter: VolundrHTTPAdapter):
        headers = adapter._headers(auth_token="tok-123")
        assert headers["Authorization"] == "Bearer tok-123"

    def test_api_key_provides_header(self):
        adapter = VolundrHTTPAdapter(base_url=BASE_URL, api_key="pat-abc")
        assert adapter._headers()["Authorization"] == "Bearer pat-abc"

    def test_auth_token_overrides_api_key(self):
        adapter = VolundrHTTPAdapter(base_url=BASE_URL, api_key="pat-abc")
        assert adapter._headers(auth_token="runtime-tok")["Authorization"] == "Bearer runtime-tok"

    def test_none_auth_token_falls_back_to_api_key(self):
        adapter = VolundrHTTPAdapter(base_url=BASE_URL, api_key="pat-abc")
        assert adapter._headers(auth_token=None)["Authorization"] == "Bearer pat-abc"

    def test_no_auth_token_no_api_key(self, adapter: VolundrHTTPAdapter):
        assert adapter._headers(auth_token=None) == {}


# -------------------------------------------------------------------
# spawn_session
# -------------------------------------------------------------------


class TestSpawnSession:
    @pytest.mark.asyncio
    @respx.mock
    async def test_success(self, adapter: VolundrHTTPAdapter):
        respx.post(SESSIONS_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "ses-1",
                    "name": "my-session",
                    "status": "creating",
                    "tracker_issue_id": "ALPHA-1",
                },
            )
        )

        req = SpawnRequest(
            name="my-session",
            repo="org/repo",
            branch="feat/alpha",
            model="claude-sonnet-4-6",
            tracker_issue_id="ALPHA-1",
            tracker_issue_url="https://linear.app/i-1",
            system_prompt="Be helpful.",
            initial_prompt="Do the thing.",
            base_branch="dev",
        )
        session = await adapter.spawn_session(req)

        assert session.id == "ses-1"
        assert session.name == "my-session"
        assert session.status == "creating"
        assert session.tracker_issue_id == "ALPHA-1"
        assert session.cluster_name == "test-cluster"

    @pytest.mark.asyncio
    @respx.mock
    async def test_sends_correct_payload(self, adapter: VolundrHTTPAdapter):
        route = respx.post(SESSIONS_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "ses-2",
                    "name": "n",
                    "status": "creating",
                },
            )
        )

        req = SpawnRequest(
            name="n",
            repo="org/repo",
            branch="main",
            model="claude-opus-4-6",
            tracker_issue_id="X-1",
            tracker_issue_url="https://example.com/X-1",
            system_prompt="prompt",
            initial_prompt="go",
            base_branch="dev",
            definition="skuldCodex",
        )
        await adapter.spawn_session(req)

        sent = route.calls[0].request
        import json

        body = json.loads(sent.content)
        assert body["name"] == "n"
        assert body["model"] == "claude-opus-4-6"
        assert body["source"]["type"] == "git"
        assert body["source"]["repo"] == "org/repo"
        assert body["source"]["branch"] == "main"
        assert body["system_prompt"] == "prompt"
        assert body["initial_prompt"] == "go"
        assert body["issue_id"] == "X-1"
        assert body["definition"] == "skuldCodex"
        assert body["issue_url"] == "https://example.com/X-1"

    @pytest.mark.asyncio
    @respx.mock
    async def test_sends_auth_token_header(self, adapter: VolundrHTTPAdapter):
        route = respx.post(SESSIONS_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "ses-3",
                    "name": "n",
                    "status": "creating",
                },
            )
        )

        req = SpawnRequest(
            name="n",
            repo="r",
            branch="b",
            model="m",
            tracker_issue_id="X",
            tracker_issue_url="",
            system_prompt="",
            initial_prompt="",
            base_branch="dev",
        )
        await adapter.spawn_session(req, auth_token="my-token")

        sent = route.calls[0].request
        assert sent.headers["Authorization"] == "Bearer my-token"

    @pytest.mark.asyncio
    @respx.mock
    async def test_raises_on_error(self, adapter: VolundrHTTPAdapter):
        respx.post(SESSIONS_URL).mock(return_value=httpx.Response(500, text="Internal error"))

        req = SpawnRequest(
            name="n",
            repo="r",
            branch="b",
            model="m",
            tracker_issue_id="X",
            tracker_issue_url="",
            system_prompt="",
            initial_prompt="",
            base_branch="dev",
        )
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.spawn_session(req)


# -------------------------------------------------------------------
# get_session
# -------------------------------------------------------------------


class TestGetSession:
    @pytest.mark.asyncio
    @respx.mock
    async def test_found(self, adapter: VolundrHTTPAdapter):
        respx.get(f"{SESSIONS_URL}/ses-1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "ses-1",
                    "name": "my-session",
                    "status": "running",
                    "tracker_issue_id": "ALPHA-1",
                },
            )
        )

        session = await adapter.get_session("ses-1")
        assert session is not None
        assert session.id == "ses-1"
        assert session.name == "my-session"
        assert session.status == "running"
        assert session.tracker_issue_id == "ALPHA-1"

    @pytest.mark.asyncio
    @respx.mock
    async def test_not_found(self, adapter: VolundrHTTPAdapter):
        respx.get(f"{SESSIONS_URL}/nonexistent").mock(return_value=httpx.Response(404))

        session = await adapter.get_session("nonexistent")
        assert session is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_raises_on_server_error(self, adapter: VolundrHTTPAdapter):
        respx.get(f"{SESSIONS_URL}/ses-1").mock(return_value=httpx.Response(500, text="error"))

        with pytest.raises(httpx.HTTPStatusError):
            await adapter.get_session("ses-1")

    @pytest.mark.asyncio
    @respx.mock
    async def test_no_tracker_issue_id(self, adapter: VolundrHTTPAdapter):
        respx.get(f"{SESSIONS_URL}/ses-2").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "ses-2",
                    "name": "plain",
                    "status": "running",
                },
            )
        )

        session = await adapter.get_session("ses-2")
        assert session is not None
        assert session.tracker_issue_id is None


# -------------------------------------------------------------------
# list_sessions
# -------------------------------------------------------------------


class TestListSessions:
    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_sessions(self, adapter: VolundrHTTPAdapter):
        respx.get(SESSIONS_URL).mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "id": "ses-1",
                        "name": "first",
                        "status": "running",
                        "tracker_issue_id": "A-1",
                    },
                    {
                        "id": "ses-2",
                        "name": "second",
                        "status": "stopped",
                    },
                ],
            )
        )

        sessions = await adapter.list_sessions()
        assert len(sessions) == 2
        assert sessions[0].id == "ses-1"
        assert sessions[0].tracker_issue_id == "A-1"
        assert sessions[1].id == "ses-2"
        assert sessions[1].tracker_issue_id is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_list(self, adapter: VolundrHTTPAdapter):
        respx.get(SESSIONS_URL).mock(return_value=httpx.Response(200, json=[]))

        sessions = await adapter.list_sessions()
        assert sessions == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_raises_on_error(self, adapter: VolundrHTTPAdapter):
        respx.get(SESSIONS_URL).mock(return_value=httpx.Response(503, text="unavailable"))

        with pytest.raises(httpx.HTTPStatusError):
            await adapter.list_sessions()

    @pytest.mark.asyncio
    @respx.mock
    async def test_sends_auth_token_header(self, adapter: VolundrHTTPAdapter):
        route = respx.get(SESSIONS_URL).mock(return_value=httpx.Response(200, json=[]))

        await adapter.list_sessions(auth_token="list-tok")

        sent = route.calls[0].request
        assert sent.headers["Authorization"] == "Bearer list-tok"


# -------------------------------------------------------------------
# get_pr_status
# -------------------------------------------------------------------


class TestGetPRStatus:
    @pytest.mark.asyncio
    @respx.mock
    async def test_success(self, adapter: VolundrHTTPAdapter):
        respx.get(f"{SESSIONS_URL}/ses-1/pr").mock(
            return_value=httpx.Response(
                200,
                json={
                    "pr_id": "42",
                    "url": "https://github.com/org/repo/pull/42",
                    "state": "open",
                    "mergeable": True,
                    "ci_passed": True,
                },
            )
        )

        pr_status = await adapter.get_pr_status("ses-1")
        assert pr_status.pr_id == "42"
        assert pr_status.url == "https://github.com/org/repo/pull/42"
        assert pr_status.state == "open"
        assert pr_status.mergeable is True
        assert pr_status.ci_passed is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_ci_passed_none(self, adapter: VolundrHTTPAdapter):
        respx.get(f"{SESSIONS_URL}/ses-1/pr").mock(
            return_value=httpx.Response(
                200,
                json={
                    "pr_id": "pr-1",
                    "state": "open",
                    "mergeable": False,
                },
            )
        )

        pr_status = await adapter.get_pr_status("ses-1")
        assert pr_status.ci_passed is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_raises_on_error(self, adapter: VolundrHTTPAdapter):
        respx.get(f"{SESSIONS_URL}/ses-1/pr").mock(return_value=httpx.Response(500, text="error"))

        with pytest.raises(httpx.HTTPStatusError):
            await adapter.get_pr_status("ses-1")


# -------------------------------------------------------------------
# get_chronicle_summary
# -------------------------------------------------------------------


class TestGetChronicleSummary:
    @pytest.mark.asyncio
    @respx.mock
    async def test_success(self, adapter: VolundrHTTPAdapter):
        respx.get(f"{SESSIONS_URL}/ses-1/chronicle").mock(
            return_value=httpx.Response(
                200,
                json={"summary": "All tests pass"},
            )
        )

        summary = await adapter.get_chronicle_summary("ses-1")
        assert summary == "All tests pass"

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_summary(self, adapter: VolundrHTTPAdapter):
        respx.get(f"{SESSIONS_URL}/ses-1/chronicle").mock(return_value=httpx.Response(200, json={}))

        summary = await adapter.get_chronicle_summary("ses-1")
        assert summary == ""

    @pytest.mark.asyncio
    @respx.mock
    async def test_raises_on_error(self, adapter: VolundrHTTPAdapter):
        respx.get(f"{SESSIONS_URL}/ses-1/chronicle").mock(
            return_value=httpx.Response(503, text="unavailable")
        )

        with pytest.raises(httpx.HTTPStatusError):
            await adapter.get_chronicle_summary("ses-1")


# -------------------------------------------------------------------
# Constructor
# -------------------------------------------------------------------


class TestConstructor:
    def test_strips_trailing_slash(self):
        adapter = VolundrHTTPAdapter(base_url="http://example.com/")
        assert adapter._base_url == "http://example.com"

    def test_default_timeout(self):
        adapter = VolundrHTTPAdapter(base_url="http://example.com")
        assert adapter._timeout == 30.0

    def test_custom_timeout(self):
        adapter = VolundrHTTPAdapter(base_url="http://example.com", timeout=10.0)
        assert adapter._timeout == 10.0

    def test_default_api_key_is_none(self):
        adapter = VolundrHTTPAdapter(base_url="http://example.com")
        assert adapter._api_key is None

    def test_custom_api_key(self):
        adapter = VolundrHTTPAdapter(base_url="http://example.com", api_key="pat-xyz")
        assert adapter._api_key == "pat-xyz"

    def test_api_key_with_timeout(self):
        adapter = VolundrHTTPAdapter(base_url="http://example.com", api_key="pat-xyz", timeout=15.0)
        assert adapter._api_key == "pat-xyz"
        assert adapter._timeout == 15.0

    def test_default_name_is_empty(self):
        adapter = VolundrHTTPAdapter(base_url="http://example.com")
        assert adapter._name == ""

    def test_custom_name(self):
        adapter = VolundrHTTPAdapter(base_url="http://example.com", name="production")
        assert adapter._name == "production"


# -------------------------------------------------------------------
# send_message
# -------------------------------------------------------------------


class TestSendMessage:
    @pytest.mark.asyncio
    @respx.mock
    async def test_success(self, adapter: VolundrHTTPAdapter):
        route = respx.post(f"{SESSIONS_URL}/ses-1/messages").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )

        await adapter.send_message("ses-1", "Fix the test")

        sent = route.calls[0].request
        import json

        body = json.loads(sent.content)
        assert body["content"] == "Fix the test"

    @pytest.mark.asyncio
    @respx.mock
    async def test_sends_auth_token(self, adapter: VolundrHTTPAdapter):
        route = respx.post(f"{SESSIONS_URL}/ses-1/messages").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )

        await adapter.send_message("ses-1", "hello", auth_token="pat-abc")

        sent = route.calls[0].request
        assert sent.headers["Authorization"] == "Bearer pat-abc"

    @pytest.mark.asyncio
    @respx.mock
    async def test_raises_on_error(self, adapter: VolundrHTTPAdapter):
        respx.post(f"{SESSIONS_URL}/ses-1/messages").mock(
            return_value=httpx.Response(500, text="error")
        )

        with pytest.raises(httpx.HTTPStatusError):
            await adapter.send_message("ses-1", "hello")


class TestStopSession:
    @pytest.mark.asyncio
    @respx.mock
    async def test_success(self, adapter: VolundrHTTPAdapter):
        route = respx.delete(f"{SESSIONS_URL}/ses-1").mock(return_value=httpx.Response(204))

        await adapter.stop_session("ses-1")

        assert route.called

    @pytest.mark.asyncio
    @respx.mock
    async def test_ignores_not_found(self, adapter: VolundrHTTPAdapter):
        respx.delete(f"{SESSIONS_URL}/missing").mock(return_value=httpx.Response(404))

        await adapter.stop_session("missing")

    @pytest.mark.asyncio
    @respx.mock
    async def test_sends_auth_token(self, adapter: VolundrHTTPAdapter):
        route = respx.delete(f"{SESSIONS_URL}/ses-2").mock(return_value=httpx.Response(204))

        await adapter.stop_session("ses-2", auth_token="runtime-token")

        sent = route.calls[0].request
        assert sent.headers["Authorization"] == "Bearer runtime-token"


class TestIntegrationsAndRepos:
    @pytest.mark.asyncio
    @respx.mock
    async def test_list_integration_ids_filters_disabled_connections(
        self, adapter: VolundrHTTPAdapter
    ):
        route = respx.get(f"{BASE_URL}/api/v1/integrations").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"id": "git", "enabled": True},
                    {"id": "slack", "enabled": False},
                    {"id": "jira"},
                ],
            )
        )

        ids = await adapter.list_integration_ids(auth_token="pat-1")

        assert ids == ["git", "jira"]
        sent = route.calls[0].request
        assert sent.headers["Authorization"] == "Bearer pat-1"

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_repos_flattens_provider_buckets(self, adapter: VolundrHTTPAdapter):
        respx.get(f"{BASE_URL}/api/v1/niuu/repos").mock(
            return_value=httpx.Response(
                200,
                json={
                    "github": [
                        {
                            "org": "niuulabs",
                            "name": "volundr",
                            "url": "https://github.com/niuulabs/volundr",
                        }
                    ],
                    "gitlab": [
                        {
                            "org": "niuulabs",
                            "name": "niuu",
                            "url": "https://gitlab.com/niuulabs/niuu",
                        }
                    ],
                },
            )
        )

        repos = await adapter.list_repos(auth_token="pat-2")

        assert repos == [
            {"org": "niuulabs", "name": "volundr", "url": "https://github.com/niuulabs/volundr"},
            {"org": "niuulabs", "name": "niuu", "url": "https://gitlab.com/niuulabs/niuu"},
        ]

    @pytest.mark.asyncio
    async def test_resolve_repo_url_matches_repo(self, adapter: VolundrHTTPAdapter, monkeypatch):
        async def fake_list_repos(*, auth_token=None):
            assert auth_token == "runtime"
            return [
                {
                    "org": "niuulabs",
                    "name": "volundr",
                    "url": "https://github.com/niuulabs/volundr",
                },
            ]

        monkeypatch.setattr(adapter, "list_repos", fake_list_repos)

        resolved = await adapter._resolve_repo_url("niuulabs/volundr", auth_token="runtime")

        assert resolved == "https://github.com/niuulabs/volundr"

    @pytest.mark.asyncio
    async def test_resolve_repo_url_returns_none_for_invalid_shorthand(
        self, adapter: VolundrHTTPAdapter, monkeypatch
    ):
        async def fake_list_repos(*, auth_token=None):
            return [
                {"org": "niuulabs", "name": "volundr", "url": "https://github.com/niuulabs/volundr"}
            ]

        monkeypatch.setattr(adapter, "list_repos", fake_list_repos)

        assert await adapter._resolve_repo_url("not-a-repo") is None

    @pytest.mark.asyncio
    async def test_resolve_repo_url_handles_repo_lookup_errors(
        self, adapter: VolundrHTTPAdapter, monkeypatch
    ):
        async def broken_list_repos(*, auth_token=None):
            raise RuntimeError("boom")

        monkeypatch.setattr(adapter, "list_repos", broken_list_repos)

        assert await adapter._resolve_repo_url("niuulabs/volundr") is None


class TestConversation:
    @pytest.mark.asyncio
    @respx.mock
    async def test_get_conversation(self, adapter: VolundrHTTPAdapter):
        respx.get(f"{SESSIONS_URL}/ses-1/conversation").mock(
            return_value=httpx.Response(200, json={"turns": [{"role": "user", "content": "hi"}]})
        )

        conversation = await adapter.get_conversation("ses-1")

        assert conversation["turns"][0]["content"] == "hi"

    @pytest.mark.asyncio
    async def test_get_last_assistant_message_prefers_recent_json_assessment(
        self, adapter: VolundrHTTPAdapter, monkeypatch
    ):
        async def fake_conversation(session_id: str):
            assert session_id == "ses-1"
            return {
                "turns": [
                    {"role": "assistant", "content": "plain response"},
                    {"role": "assistant", "content": '{"confidence": 0.92, "summary": "ready"}'},
                    {"role": "assistant", "content": "latest plain response"},
                ]
            }

        monkeypatch.setattr(adapter, "get_conversation", fake_conversation)

        content = await adapter.get_last_assistant_message("ses-1")

        assert '"confidence": 0.92' in content

    @pytest.mark.asyncio
    async def test_get_last_assistant_message_falls_back_to_latest_assistant(
        self, adapter: VolundrHTTPAdapter, monkeypatch
    ):
        async def fake_conversation(session_id: str):
            return {
                "turns": [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "latest assistant reply"},
                ]
            }

        monkeypatch.setattr(adapter, "get_conversation", fake_conversation)

        content = await adapter.get_last_assistant_message("ses-2")

        assert content == "latest assistant reply"

    @pytest.mark.asyncio
    async def test_get_last_assistant_message_raises_when_missing(
        self, adapter: VolundrHTTPAdapter, monkeypatch
    ):
        async def fake_conversation(session_id: str):
            return {"turns": [{"role": "user", "content": "hi"}]}

        monkeypatch.setattr(adapter, "get_conversation", fake_conversation)

        with pytest.raises(ValueError):
            await adapter.get_last_assistant_message("ses-3")


class _FakeLineIterator:
    def __init__(self, lines: list[str]) -> None:
        self._iter = iter(lines)

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _FakeStreamResponse:
    def __init__(self, lines: list[str], status_code: int = 200) -> None:
        self._lines = lines
        self.status_code = status_code
        self.request = httpx.Request("GET", f"{BASE_URL}/api/v1/forge/sessions/stream")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "stream failed",
                request=self.request,
                response=httpx.Response(self.status_code, request=self.request),
            )

    def aiter_lines(self) -> _FakeLineIterator:
        return _FakeLineIterator(self._lines)


class _FakeAsyncClient:
    def __init__(self, response: _FakeStreamResponse) -> None:
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def stream(self, method: str, url: str, headers: dict[str, str]):
        assert method == "GET"
        assert url == f"{BASE_URL}/api/v1/forge/sessions/stream"
        assert headers == {}
        return self._response


class TestSubscribeActivity:
    @pytest.mark.asyncio
    async def test_yields_activity_and_terminal_session_updates(
        self, adapter: VolundrHTTPAdapter, monkeypatch
    ):
        activity_event = {
            "session_id": "ses-1",
            "state": "running",
            "metadata": {"step": "plan"},
            "owner_id": "user-1",
        }
        session_updated_event = {
            "id": "ses-2",
            "status": "stopped",
            "owner_id": "user-2",
        }
        response = _FakeStreamResponse(
            [
                "event: session_activity",
                f"data: {json.dumps(activity_event)}",
                "",
                "event: session_updated",
                f"data: {json.dumps(session_updated_event)}",
                "",
                "event: session_updated",
                f"data: {json.dumps({'id': 'ses-3', 'status': 'running', 'owner_id': 'user-3'})}",
                "",
                "event: session_activity",
                "data: not-json",
                "",
            ]
        )

        monkeypatch.setattr(httpx, "AsyncClient", lambda timeout=None: _FakeAsyncClient(response))

        events = [event async for event in adapter.subscribe_activity()]

        assert len(events) == 2
        assert events[0].session_id == "ses-1"
        assert events[0].state == "running"
        assert events[0].metadata == {"step": "plan"}
        assert events[1].session_id == "ses-2"
        assert events[1].session_status == "stopped"
