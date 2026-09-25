"""Tests for VolundrHTTPAdapter with respx-mocked httpx calls."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest
import respx

from ravn.adapters.personas.loader import PersonaConfig
from ravn.domain.persona_document import portable_persona_from_config
from ting.adapters.volundr_http import VolundrHTTPAdapter
from ting.ports.volundr import SpawnRequest

BASE_URL = "http://volundr.test:8000"
SESSIONS_URL = f"{BASE_URL}/api/v1/forge/sessions"


@pytest.fixture
def adapter() -> VolundrHTTPAdapter:
    # These fixtures exercise adapter *behavior* (request shape, response
    # parsing, error propagation), not the guild_transport policy itself —
    # see test_volundr_http_transport_security.py for that — so a non-loopback
    # http:// fixture URL opts into plaintext explicitly, matching the
    # pattern used by other Guild-outbound test suites (e.g.
    # test_rest_volundr.py's _instance helper).
    return VolundrHTTPAdapter(
        base_url=BASE_URL, timeout=5.0, name="test-cluster", config={"allow_plaintext": True}
    )


class StaticAuth:
    def __init__(self, headers: dict[str, str]) -> None:
        self._headers = headers

    def headers(self) -> dict[str, str]:
        return dict(self._headers)


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

    def test_service_auth_headers_used_when_no_runtime_or_user_token(self):
        adapter = VolundrHTTPAdapter(
            base_url=BASE_URL,
            auth=StaticAuth({"Authorization": "Bearer service-token"}),
        )

        assert adapter._headers()["Authorization"] == "Bearer service-token"

    def test_auth_token_overrides_service_auth(self):
        adapter = VolundrHTTPAdapter(
            base_url=BASE_URL,
            auth=StaticAuth({"Authorization": "Bearer service-token", "x-service": "ting"}),
        )

        headers = adapter._headers(auth_token="runtime-tok")

        assert headers["Authorization"] == "Bearer runtime-tok"
        assert headers["x-service"] == "ting"

    def test_api_key_overrides_service_auth(self):
        adapter = VolundrHTTPAdapter(
            base_url=BASE_URL,
            api_key="pat-abc",
            auth=StaticAuth({"Authorization": "Bearer service-token"}),
        )

        assert adapter._headers()["Authorization"] == "Bearer pat-abc"


@pytest.mark.asyncio
@respx.mock
async def test_get_public_session_log_page_keeps_internal_visibility_hidden(
    adapter: VolundrHTTPAdapter,
) -> None:
    route = respx.get(f"{SESSIONS_URL}/ses-1/log/page").mock(
        return_value=httpx.Response(
            200,
            json={
                "entries": [
                    {
                        "session_id": "ses-1",
                        "seq": 7,
                        "kind": "room_outcome",
                        "role": None,
                        "request_id": "request-1",
                        "payload": {"eventType": "review.completed", "fields": {}},
                        "ts": "2026-09-19T22:00:00+00:00",
                    }
                ],
                "scannedThrough": 9,
                "hasMore": True,
            },
        )
    )

    page = await adapter.get_public_session_log_page(
        "ses-1",
        after=4,
        limit=25,
        auth_token="owner-token",
    )

    assert dict(route.calls[0].request.url.params) == {
        "after": "4",
        "limit": "25",
        "show_internal": "false",
    }
    assert route.calls[0].request.headers["authorization"] == "Bearer owner-token"
    assert page.scanned_through == 9
    assert page.has_more is True
    assert page.entries[0].seq == 7
    assert page.entries[0].ts == datetime.fromisoformat("2026-09-19T22:00:00+00:00")
    assert page.entries[0].payload["eventType"] == "review.completed"


class TestPortablePersonas:
    @pytest.mark.asyncio
    @respx.mock
    async def test_get_current_source_uses_owner_authorization(
        self,
        adapter: VolundrHTTPAdapter,
    ) -> None:
        document = portable_persona_from_config(
            PersonaConfig(name="custom-agent", system_prompt_template="Owner prompt")
        )
        route = respx.get(f"{BASE_URL}/api/v1/personas/custom-agent/portable").mock(
            return_value=httpx.Response(200, json=document.to_dict())
        )

        loaded = await adapter.get_current_portable_persona(
            "custom-agent",
            auth_token="owner-token",
        )

        assert loaded == document
        assert route.calls[0].request.headers["Authorization"] == "Bearer owner-token"

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_current_source_returns_none_for_missing_persona(
        self,
        adapter: VolundrHTTPAdapter,
    ) -> None:
        respx.get(f"{BASE_URL}/api/v1/personas/missing/portable").mock(
            return_value=httpx.Response(404)
        )

        assert await adapter.get_current_portable_persona("missing") is None


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
    async def test_rewrites_loopback_chat_endpoint_to_adapter_origin(self):
        adapter = VolundrHTTPAdapter(
            base_url="https://volundr.valhalla.asgard.niuu.world",
            timeout=5.0,
            name="Valhalla",
        )
        respx.post("https://volundr.valhalla.asgard.niuu.world/api/v1/forge/sessions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "ses-1",
                    "name": "my-session",
                    "status": "creating",
                    "chat_endpoint": "ws://localhost:8080/s/ses-1/session",
                },
            )
        )

        session = await adapter.spawn_session(
            SpawnRequest(
                name="my-session",
                repo="org/repo",
                branch="main",
                model="gpt-5.5",
                tracker_issue_id="",
                tracker_issue_url="",
                system_prompt="",
                initial_prompt="go",
                base_branch="main",
            )
        )

        assert session.chat_endpoint == "wss://volundr.valhalla.asgard.niuu.world/s/ses-1/session"

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
    async def test_local_repo_path_sends_local_mount(
        self,
        adapter: VolundrHTTPAdapter,
        tmp_path: Path,
    ):
        local_repo = tmp_path / "repo"
        local_repo.mkdir()
        route = respx.post(SESSIONS_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "ses-local",
                    "name": "n",
                    "status": "creating",
                    "source": {
                        "type": "local_mount",
                        "local_path": str(local_repo.resolve()),
                    },
                },
            )
        )

        req = SpawnRequest(
            name="n",
            repo=str(local_repo),
            branch="feature",
            model="claude-opus-4-6",
            tracker_issue_id="X-1",
            tracker_issue_url="",
            system_prompt="",
            initial_prompt="go",
            base_branch="dev",
            workload_type="ravn_flock",
        )
        session = await adapter.spawn_session(req)

        body = json.loads(route.calls[0].request.content)
        assert body["source"] == {
            "type": "local_mount",
            "local_path": str(local_repo.resolve()),
        }
        assert body["workload_type"] == "ravn_flock"
        assert session.repo == str(local_repo.resolve())

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
    async def test_get_session_rewrites_loopback_chat_endpoint(self, adapter: VolundrHTTPAdapter):
        respx.get(f"{SESSIONS_URL}/ses-1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "ses-1",
                    "name": "my-session",
                    "status": "running",
                    "chat_endpoint": "ws://127.0.0.1:8080/s/ses-1/session",
                },
            )
        )

        session = await adapter.get_session("ses-1")

        assert session is not None
        assert session.chat_endpoint == "ws://volundr.test:8000/s/ses-1/session"

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
        assert sessions[0].chat_endpoint is None
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

    @pytest.mark.asyncio
    @respx.mock
    async def test_send_directed_room_message(self, adapter: VolundrHTTPAdapter):
        respx.get(f"{SESSIONS_URL}/ses-1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "ses-1",
                    "name": "research-council",
                    "status": "running",
                    "chat_endpoint": "http://volundr.test:8000/s/ses-1/chat",
                },
            )
        )
        route = respx.post("http://volundr.test:8000/s/ses-1/chat/api/room/direct").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )

        await adapter.send_directed_room_message(
            "ses-1",
            "flock-council-chair",
            "Please prefer the staged rollout option.",
        )

        sent = route.calls[0].request
        body = json.loads(sent.content)
        assert body["target_peer_id"] == "flock-council-chair"
        assert body["content"] == "Please prefer the staged rollout option."
        assert body["source"] == "ting"

    @pytest.mark.asyncio
    @respx.mock
    async def test_publish_workflow_event(self, adapter: VolundrHTTPAdapter):
        respx.get(f"{SESSIONS_URL}/ses-1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "ses-1",
                    "name": "developer-delivery",
                    "status": "running",
                    "chat_endpoint": "http://volundr.test:8000/s/ses-1/chat",
                },
            )
        )
        route = respx.post("http://volundr.test:8000/s/ses-1/chat/api/room/workflow-events").mock(
            return_value=httpx.Response(200, json={"status": "published"})
        )

        await adapter.publish_workflow_event(
            "ses-1",
            "developer.children.verified",
            '{"generation":1}',
            payload={"generation": 1},
            request_id="continuation-1",
        )

        body = json.loads(route.calls[0].request.content)
        assert body == {
            "event_type": "developer.children.verified",
            "content": '{"generation":1}',
            "payload": {"generation": 1},
            "request_id": "continuation-1",
            "source": "ting",
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("operation", ["workflow", "direct"])
    @respx.mock
    async def test_room_control_uses_configured_gateway_origin(self, adapter, operation):
        respx.get(f"{SESSIONS_URL}/ses-1").respond(
            200,
            json={
                "id": "ses-1",
                "name": "developer-delivery",
                "status": "running",
                "chat_endpoint": "wss://browser-only.example/s/ses-1/session",
            },
        )
        suffix = "workflow-events" if operation == "workflow" else "direct"
        route = respx.post(f"{BASE_URL}/s/ses-1/api/room/{suffix}").respond(200, json={})
        if operation == "workflow":
            await adapter.publish_workflow_event(
                "ses-1", "test.observed", "observation", request_id="continuation-1"
            )
        else:
            await adapter.send_directed_room_message("ses-1", "flock-coordinator", "reply")
        assert route.call_count == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_room_control_preserves_direct_skuld_origin(self, adapter):
        respx.get(f"{SESSIONS_URL}/ses-1").respond(
            200,
            json={
                "id": "ses-1",
                "name": "direct-session",
                "status": "running",
                "chat_endpoint": "wss://session-ingress.example/session",
            },
        )
        route = respx.post("https://session-ingress.example/api/room/workflow-events").respond(
            200, json={}
        )
        await adapter.publish_workflow_event(
            "ses-1", "test.observed", "observation", request_id="continuation-1"
        )
        assert route.call_count == 1


class TestStopSession:
    @pytest.mark.asyncio
    @respx.mock
    async def test_success(self, adapter: VolundrHTTPAdapter):
        route = respx.post(f"{SESSIONS_URL}/ses-1/stop").mock(
            return_value=httpx.Response(200, json={"id": "ses-1", "status": "stopped"})
        )

        await adapter.stop_session("ses-1")

        assert route.called
        assert route.calls[0].request.method == "POST"

    @pytest.mark.asyncio
    @respx.mock
    async def test_ignores_not_found(self, adapter: VolundrHTTPAdapter):
        respx.post(f"{SESSIONS_URL}/missing/stop").mock(return_value=httpx.Response(404))

        await adapter.stop_session("missing")

    @pytest.mark.asyncio
    @respx.mock
    async def test_sends_auth_token(self, adapter: VolundrHTTPAdapter):
        route = respx.post(f"{SESSIONS_URL}/ses-2/stop").mock(
            return_value=httpx.Response(200, json={"id": "ses-2", "status": "stopped"})
        )

        await adapter.stop_session("ses-2", auth_token="runtime-token")

        sent = route.calls[0].request
        assert sent.headers["Authorization"] == "Bearer runtime-token"

    @pytest.mark.asyncio
    @respx.mock
    async def test_already_stopped_conflict_is_confirmed_by_get(self, adapter: VolundrHTTPAdapter):
        stop = respx.post(f"{SESSIONS_URL}/ses-stopped/stop").mock(
            return_value=httpx.Response(409, json={"detail": "cannot stop from stopped"})
        )
        current = respx.get(f"{SESSIONS_URL}/ses-stopped").mock(
            return_value=httpx.Response(
                200,
                json={"id": "ses-stopped", "name": "done", "status": "stopped"},
            )
        )

        await adapter.stop_session("ses-stopped", auth_token="runtime-token")

        assert stop.called
        assert current.called
        assert current.calls[0].request.headers["Authorization"] == "Bearer runtime-token"

    @pytest.mark.asyncio
    @respx.mock
    async def test_conflict_is_not_success_when_session_is_not_stopped(
        self, adapter: VolundrHTTPAdapter
    ):
        respx.post(f"{SESSIONS_URL}/ses-running/stop").mock(
            return_value=httpx.Response(409, json={"detail": "stop conflict"})
        )
        respx.get(f"{SESSIONS_URL}/ses-running").mock(
            return_value=httpx.Response(
                200,
                json={"id": "ses-running", "name": "live", "status": "running"},
            )
        )

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await adapter.stop_session("ses-running")

        assert exc_info.value.response.status_code == 409

    @pytest.mark.asyncio
    @respx.mock
    async def test_conflict_confirmation_failure_is_propagated(self, adapter: VolundrHTTPAdapter):
        respx.post(f"{SESSIONS_URL}/ses-unknown/stop").mock(
            return_value=httpx.Response(409, json={"detail": "stop conflict"})
        )
        respx.get(f"{SESSIONS_URL}/ses-unknown").mock(return_value=httpx.Response(503))

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await adapter.stop_session("ses-unknown")

        assert exc_info.value.response.status_code == 503

    @pytest.mark.asyncio
    @respx.mock
    async def test_stop_failure_is_propagated(self, adapter: VolundrHTTPAdapter):
        respx.post(f"{SESSIONS_URL}/ses-failed/stop").mock(return_value=httpx.Response(500))

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await adapter.stop_session("ses-failed")

        assert exc_info.value.response.status_code == 500


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
        async def fake_list_repos(*, auth_token=None, principal=None):
            assert auth_token == "runtime"
            assert principal is None
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
        async def fake_list_repos(*, auth_token=None, principal=None):
            return [
                {"org": "niuulabs", "name": "volundr", "url": "https://github.com/niuulabs/volundr"}
            ]

        monkeypatch.setattr(adapter, "list_repos", fake_list_repos)

        assert await adapter._resolve_repo_url("not-a-repo") is None

    @pytest.mark.asyncio
    async def test_resolve_repo_url_handles_repo_lookup_errors(
        self, adapter: VolundrHTTPAdapter, monkeypatch
    ):
        async def broken_list_repos(*, auth_token=None, principal=None):
            raise RuntimeError("boom")

        monkeypatch.setattr(adapter, "list_repos", broken_list_repos)

        assert await adapter._resolve_repo_url("niuulabs/volundr") is None


class TestConversation:
    @pytest.mark.asyncio
    @respx.mock
    async def test_get_conversation(self, adapter: VolundrHTTPAdapter):
        route = respx.get(f"{SESSIONS_URL}/ses-1/conversation").mock(
            return_value=httpx.Response(200, json={"turns": [{"role": "user", "content": "hi"}]})
        )

        conversation = await adapter.get_conversation("ses-1", auth_token="tok-123")

        assert conversation["turns"][0]["content"] == "hi"
        assert route.calls.last.request.headers["Authorization"] == "Bearer tok-123"

    @pytest.mark.asyncio
    async def test_get_last_assistant_message_prefers_recent_json_assessment(
        self, adapter: VolundrHTTPAdapter, monkeypatch
    ):
        async def fake_conversation(session_id: str, **kwargs):
            assert session_id == "ses-1"
            assert kwargs["auth_token"] == "tok-123"
            return {
                "turns": [
                    {"role": "assistant", "content": "plain response"},
                    {"role": "assistant", "content": '{"confidence": 0.92, "summary": "ready"}'},
                    {"role": "assistant", "content": "latest plain response"},
                ]
            }

        monkeypatch.setattr(adapter, "get_conversation", fake_conversation)

        content = await adapter.get_last_assistant_message("ses-1", auth_token="tok-123")

        assert '"confidence": 0.92' in content

    @pytest.mark.asyncio
    async def test_get_last_assistant_message_falls_back_to_latest_assistant(
        self, adapter: VolundrHTTPAdapter, monkeypatch
    ):
        async def fake_conversation(session_id: str, **kwargs):
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
        async def fake_conversation(session_id: str, **kwargs):
            return {"turns": [{"role": "user", "content": "hi"}]}

        monkeypatch.setattr(adapter, "get_conversation", fake_conversation)

        with pytest.raises(ValueError):
            await adapter.get_last_assistant_message("ses-3")


class TestWorkflowGates:
    @pytest.mark.asyncio
    @respx.mock
    async def test_get_workflow_gates_returns_gate_list(self, adapter: VolundrHTTPAdapter):
        respx.get(f"{SESSIONS_URL}/ses-1/workflow/gates").mock(
            return_value=httpx.Response(
                200,
                json={"gates": [{"id": "gate-1", "node_id": "plan-brief-gate"}]},
            )
        )

        gates = await adapter.get_workflow_gates("ses-1")

        assert gates == [{"id": "gate-1", "node_id": "plan-brief-gate"}]

    @pytest.mark.asyncio
    @respx.mock
    async def test_resolve_workflow_gate_encodes_id_and_sends_intent(
        self, adapter: VolundrHTTPAdapter
    ):
        route = respx.post(
            f"{SESSIONS_URL}/ses-1/workflow/gates/plan%20brief%3Fstep%3D1/resolve"
        ).mock(return_value=httpx.Response(200, json={"status": "resolved"}))

        result = await adapter.resolve_workflow_gate(
            "ses-1",
            "plan brief?step=1",
            "APPROVE",
            notes="Looks bounded.",
            source="ting.plan",
            auth_token="tok-123",
        )

        assert result == {"status": "resolved"}
        assert route.calls.last.request.headers["Authorization"] == "Bearer tok-123"
        assert route.calls.last.request.headers["x-niuu-workflow-gate-intent"] == "resolve"
        assert json.loads(route.calls.last.request.content) == {
            "decision": "APPROVE",
            "notes": "Looks bounded.",
            "source": "ting.plan",
        }


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
    def __init__(self, response: _FakeStreamResponse, expected_headers: dict[str, str]) -> None:
        self._response = response
        self._expected_headers = expected_headers
        # VolundrHTTPAdapter._client() reads .timeout.connect back off the
        # client build_guild_httpx_client() returned (to strip the read
        # timeout for this streaming call) — a real httpx.AsyncClient always
        # has one; this fake needs the same shape.
        self.timeout = httpx.Timeout(30.0, connect=5.0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def stream(self, method: str, url: str, headers: dict[str, str]):
        assert method == "GET"
        assert url == f"{BASE_URL}/api/v1/forge/sessions/stream"
        assert headers == self._expected_headers
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

        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda *args, **kwargs: _FakeAsyncClient(response, expected_headers={}),
        )

        events = [event async for event in adapter.subscribe_activity()]

        assert len(events) == 2
        assert events[0].session_id == "ses-1"
        assert events[0].state == "running"
        assert events[0].metadata == {"step": "plan"}
        assert events[1].session_id == "ses-2"
        assert events[1].session_status == "stopped"

    @pytest.mark.asyncio
    async def test_uses_service_auth_headers(self, monkeypatch):
        adapter = VolundrHTTPAdapter(
            base_url=BASE_URL,
            auth=StaticAuth({"Authorization": "Bearer service-token"}),
            config={"allow_plaintext": True},
        )
        response = _FakeStreamResponse(
            [
                "event: session_updated",
                f"data: {json.dumps({'id': 'ses-1', 'status': 'failed', 'owner_id': 'user-1'})}",
                "",
            ]
        )
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda *args, **kwargs: _FakeAsyncClient(
                response,
                expected_headers={"Authorization": "Bearer service-token"},
            ),
        )

        events = [event async for event in adapter.subscribe_activity()]

        assert events[0].session_id == "ses-1"
