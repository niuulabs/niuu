"""A2A for residents: Agent Card, and SendMessage/GetTask/CancelTask over channels."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from volundr.adapters.inbound.auth import extract_principal
from volundr.adapters.inbound.rest_resident_a2a import (
    A2A_CARD_ENDPOINT_KIND,
    A2A_ENDPOINT_KIND,
    create_resident_a2a_router,
    with_a2a_endpoints,
)
from volundr.adapters.inbound.rest_resident_runtimes import create_resident_runtimes_router
from volundr.domain.models import (
    Principal,
    ResidentBackend,
    ResidentCapability,
    ResidentEngine,
    ResidentRuntime,
    ResidentSession,
    ResidentSessionStatus,
)
from volundr.domain.ports import ResidentChatConnection, SessionPersona
from volundr.domain.services.resident_runtime import ResidentRuntimeNotFoundError

_PRINCIPAL = Principal(
    user_id="user-a",
    email="user-a@example.test",
    tenant_id="tenant-a",
    roles=["volundr:developer"],
)
_BASE = "https://guild.example"
_HEADERS = {"A2A-Version": "1.0"}
_A2A_CAPABILITIES = [
    ResidentCapability.CHAT,
    ResidentCapability.SESSION_LIST,
    ResidentCapability.SESSION_CREATE,
]


def _runtime(**overrides: Any) -> ResidentRuntime:
    values: dict[str, Any] = {
        "id": uuid4(),
        "owner_id": "user-a",
        "tenant_id": "tenant-a",
        "name": "Muninn",
        "persona_name": "product-steward",
        "backend": ResidentBackend.HELMRELEASE,
        "engine": ResidentEngine.RAVN,
        "profile_id": "ravn-k8s",
        "capabilities": _A2A_CAPABILITIES,
    }
    values.update(overrides)
    return ResidentRuntime(**values)


def _session(runtime: ResidentRuntime, **overrides: Any) -> ResidentSession:
    values: dict[str, Any] = {"id": uuid4(), "resident_id": runtime.id, "title": "Channel"}
    values.update(overrides)
    return ResidentSession(**values)


class _Chat(ResidentChatConnection):
    """Scripted resident chat: history first, then the frames a reply produces."""

    def __init__(
        self,
        turns: list[dict[str, Any]] | None = None,
        reply: list[dict[str, Any]] | None = None,
    ) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self._frames: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._frames.put_nowait({"type": "capabilities"})
        self._frames.put_nowait({"type": "conversation_history", "turns": turns or []})
        self._reply = reply or []

    async def receive(self) -> dict[str, Any]:
        return await self._frames.get()

    async def send(self, frame: dict[str, Any]) -> None:
        self.sent.append(frame)
        if frame.get("type") == "user":
            for item in self._reply:
                self._frames.put_nowait(item)

    async def close(self) -> None:
        self.closed = True


def _reply_frames(text: str) -> list[dict[str, Any]]:
    return [
        {"type": "user_confirmed", "id": "m1"},
        {"type": "assistant", "message": {"role": "assistant", "content": []}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text[:3]}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text[3:]}},
        {"type": "result", "result": ""},
    ]


def _service(
    runtime: ResidentRuntime,
    *,
    sessions: list[ResidentSession] | None = None,
    chats: list[_Chat] | None = None,
) -> Mock:
    service = Mock()
    service.get = AsyncMock(return_value=runtime)
    service.list = AsyncMock(return_value=[runtime])
    service.list_sessions = AsyncMock(return_value=list(sessions or []))
    created = _session(runtime, title="new")

    async def create_session(principal: Principal, runtime_id: UUID, *, title: str):
        session = created.model_copy(update={"title": title})
        service.list_sessions.return_value = [*service.list_sessions.return_value, session]
        return session

    service.create_session = AsyncMock(side_effect=create_session)
    service.connect_chat = AsyncMock(side_effect=list(chats or []))
    return service


def _client(
    service: Mock,
    *,
    persona_provider: Any = None,
    reply_timeout_seconds: float = 2.0,
) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_resident_a2a_router(
            service,
            persona_provider=persona_provider,
            public_base_url=_BASE,
            reply_timeout_seconds=reply_timeout_seconds,
        )
    )
    app.dependency_overrides[extract_principal] = lambda: _PRINCIPAL
    return TestClient(app)


def _rpc(client: TestClient, runtime: ResidentRuntime, method: str, params: dict) -> dict:
    response = client.post(
        f"/api/v1/forge/resident-runtimes/{runtime.id}/a2a",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        headers=_HEADERS,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _send(
    client: TestClient,
    runtime: ResidentRuntime,
    text: str,
    **message: Any,
) -> dict:
    configuration = message.pop("configuration", None)
    params: dict[str, Any] = {
        "message": {
            "messageId": message.pop("messageId", "msg-1"),
            "role": "ROLE_USER",
            "parts": [{"text": text}],
            **message,
        }
    }
    if configuration:
        params["configuration"] = configuration
    return _rpc(client, runtime, "SendMessage", params)


def _artifact_text(task: dict) -> str:
    return task["artifacts"][0]["parts"][0]["text"]


# -- Agent Card -------------------------------------------------------- #


def test_card_projects_resident_and_persona_onto_a2a_card() -> None:
    runtime = _runtime()
    persona_provider = Mock()
    persona_provider.get = AsyncMock(
        return_value=SessionPersona(
            name="product-steward",
            system_prompt="## Identity\n\nYou are a resident product steward.\n\n## More\n\nx",
        )
    )

    response = _client(_service(runtime), persona_provider=persona_provider).get(
        f"/api/v1/forge/resident-runtimes/{runtime.id}/a2a/.well-known/agent-card.json"
    )

    assert response.status_code == 200
    card = response.json()
    assert card["name"] == "Muninn"
    assert card["description"] == "You are a resident product steward."
    assert card["supportedInterfaces"] == [
        {
            "url": f"{_BASE}/api/v1/forge/resident-runtimes/{runtime.id}/a2a",
            "protocolBinding": "JSONRPC",
            "protocolVersion": "1.0",
        }
    ]
    assert [skill["id"] for skill in card["skills"]] == ["product-steward"]
    assert card["skills"][0]["tags"] == ["resident", "ravn", "product-steward"]
    assert card["capabilities"].get("pushNotifications", False) is False
    assert "platformBearer" in card["securitySchemes"]
    persona_provider.get.assert_awaited_once_with("user-a", "product-steward")


def test_card_is_hidden_for_resident_without_channels() -> None:
    runtime = _runtime(capabilities=[ResidentCapability.CHAT])

    response = _client(_service(runtime)).get(
        f"/api/v1/forge/resident-runtimes/{runtime.id}/a2a/.well-known/agent-card.json"
    )

    assert response.status_code == 404


def test_card_of_invisible_resident_is_not_found() -> None:
    runtime = _runtime()
    service = _service(runtime)
    service.get = AsyncMock(side_effect=ResidentRuntimeNotFoundError("Resident runtime not found"))

    response = _client(service).get(
        f"/api/v1/forge/resident-runtimes/{runtime.id}/a2a/.well-known/agent-card.json"
    )

    assert response.status_code == 404


# -- SendMessage ------------------------------------------------------- #


def test_send_without_context_opens_a_channel_and_returns_the_reply() -> None:
    runtime = _runtime()
    chat = _Chat(reply=_reply_frames("Hello there"))
    service = _service(runtime, chats=[chat])

    body = _send(_client(service), runtime, "What is the plan?", messageId="m-42")

    task = body["result"]["task"]
    session = service.list_sessions.return_value[-1]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["contextId"] == str(session.id)
    assert task["id"].startswith(f"{session.id}:0:")
    assert _artifact_text(task) == "Hello there"
    assert task["metadata"]["sessionId"] == str(session.id)
    service.create_session.assert_awaited_once()
    assert service.create_session.await_args.kwargs["title"] == "A2A · What is the plan?"
    assert chat.sent == [{"type": "user", "content": "What is the plan?", "request_id": "m-42"}]
    assert chat.closed


def test_send_with_session_context_continues_that_channel() -> None:
    runtime = _runtime()
    session = _session(runtime)
    chat = _Chat(
        turns=[{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}],
        reply=_reply_frames("again"),
    )
    service = _service(runtime, sessions=[session], chats=[chat])

    task = _send(_client(service), runtime, "next", contextId=str(session.id))["result"]["task"]

    service.create_session.assert_not_awaited()
    service.connect_chat.assert_awaited_once_with(_PRINCIPAL, runtime.id, session.id)
    assert task["contextId"] == str(session.id)
    assert task["id"].startswith(f"{session.id}:2:")


def test_send_with_foreign_context_names_one_channel_for_it() -> None:
    runtime = _runtime()
    named = _session(runtime, title="A2A · ctx-from-peer")
    service = _service(
        runtime,
        sessions=[named],
        chats=[_Chat(reply=_reply_frames("one")), _Chat(reply=_reply_frames("two"))],
    )
    client = _client(service)

    first = _send(client, runtime, "hi", contextId="ctx-from-peer")["result"]["task"]
    service.list_sessions.return_value = []
    second = _send(client, runtime, "hi", contextId="other-ctx", messageId="m2")["result"]["task"]

    assert first["contextId"] == "ctx-from-peer"
    assert first["metadata"]["sessionId"] == str(named.id)
    assert second["contextId"] == "other-ctx"
    assert service.create_session.await_args.kwargs["title"] == "A2A · other-ctx"


def test_follow_up_on_a_task_continues_its_channel() -> None:
    runtime = _runtime()
    session = _session(runtime)
    service = _service(
        runtime,
        sessions=[session],
        chats=[_Chat(turns=[{"role": "user", "content": "x"}], reply=_reply_frames("ok"))],
    )

    task = _send(_client(service), runtime, "more", taskId=f"{session.id}:0:abc")["result"]["task"]

    service.create_session.assert_not_awaited()
    assert task["contextId"] == str(session.id)


def test_follow_up_on_unknown_task_is_not_found() -> None:
    runtime = _runtime()
    service = _service(runtime)

    body = _send(_client(service), runtime, "more", taskId=f"{uuid4()}:0:abc")

    assert body["error"]["code"] == -32001
    service.connect_chat.assert_not_awaited()


def test_slow_reply_returns_working_task() -> None:
    runtime = _runtime()
    session = _session(runtime)
    chat = _Chat(reply=[{"type": "user_confirmed"}])
    service = _service(runtime, sessions=[session], chats=[chat])

    task = _send(
        _client(service, reply_timeout_seconds=0.05),
        runtime,
        "think hard",
        contextId=str(session.id),
    )["result"]["task"]

    assert task["status"]["state"] == "TASK_STATE_WORKING"
    assert "artifacts" not in task
    assert chat.closed


def test_return_immediately_does_not_wait_for_the_reply() -> None:
    runtime = _runtime()
    session = _session(runtime)
    service = _service(runtime, sessions=[session], chats=[_Chat(reply=_reply_frames("late"))])

    task = _send(
        _client(service),
        runtime,
        "go",
        contextId=str(session.id),
        configuration={"returnImmediately": True},
    )["result"]["task"]

    assert task["status"]["state"] == "TASK_STATE_WORKING"


def test_engine_error_fails_the_task_but_unsupported_commands_do_not() -> None:
    runtime = _runtime()
    session = _session(runtime)
    chat = _Chat(
        reply=[
            {"type": "error", "error": "Unsupported", "command": "set_model"},
            {"type": "error", "error": "model unavailable"},
        ]
    )
    service = _service(runtime, sessions=[session], chats=[chat])

    task = _send(_client(service), runtime, "hi", contextId=str(session.id))["result"]["task"]

    assert task["status"]["state"] == "TASK_STATE_FAILED"
    assert task["metadata"]["error"] == "model unavailable"


def test_empty_message_is_invalid() -> None:
    runtime = _runtime()

    body = _send(_client(_service(runtime)), runtime, "   ")

    assert body["error"]["code"] == -32602


# -- GetTask / CancelTask ---------------------------------------------- #


def _task_id(session: ResidentSession, index: int, prompt: str) -> str:
    return f"{session.id}:{index}:{hashlib.sha256(prompt.encode()).hexdigest()[:12]}"


def test_get_task_reads_the_reply_from_channel_history() -> None:
    runtime = _runtime()
    session = _session(runtime)
    turns = [
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "sure"},
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer"},
    ]
    service = _service(runtime, sessions=[session], chats=[_Chat(turns=turns)])

    task = _rpc(_client(service), runtime, "GetTask", {"id": _task_id(session, 2, "question")})[
        "result"
    ]

    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert _artifact_text(task) == "answer"
    assert task["contextId"] == str(session.id)


def test_get_task_refinds_a_shifted_turn_by_its_prompt() -> None:
    runtime = _runtime()
    session = _session(runtime)
    turns = [
        {"role": "user", "content": "interleaved"},
        {"role": "assistant", "content": "x"},
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "later"},
    ]
    service = _service(runtime, sessions=[session], chats=[_Chat(turns=turns)])

    task = _rpc(_client(service), runtime, "GetTask", {"id": _task_id(session, 0, "question")})[
        "result"
    ]

    assert _artifact_text(task) == "answer"


def test_get_task_is_working_while_the_channel_runs() -> None:
    runtime = _runtime()
    session = _session(runtime, status=ResidentSessionStatus.RUNNING)
    service = _service(
        runtime,
        sessions=[session],
        chats=[_Chat(turns=[{"role": "user", "content": "q"}])],
    )

    task = _rpc(_client(service), runtime, "GetTask", {"id": _task_id(session, 0, "q")})["result"]

    assert task["status"]["state"] == "TASK_STATE_WORKING"


def test_get_task_falls_back_to_the_turn_position_when_the_prompt_was_reworded() -> None:
    runtime = _runtime()
    session = _session(runtime)
    turns = [
        {"role": "user", "content": "[peer] question"},
        {"role": "assistant", "content": "answer"},
    ]
    service = _service(runtime, sessions=[session], chats=[_Chat(turns=turns)])

    task = _rpc(_client(service), runtime, "GetTask", {"id": _task_id(session, 0, "question")})[
        "result"
    ]

    assert _artifact_text(task) == "answer"


def test_get_task_for_a_turn_that_is_not_a_prompt_is_not_found() -> None:
    runtime = _runtime()
    session = _session(runtime)
    turns = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]
    service = _service(runtime, sessions=[session], chats=[_Chat(turns=turns)])

    body = _rpc(_client(service), runtime, "GetTask", {"id": _task_id(session, 1, "other")})

    assert body["error"]["code"] == -32001


def test_get_task_of_unknown_channel_is_not_found() -> None:
    runtime = _runtime()
    service = _service(runtime)

    body = _rpc(_client(service), runtime, "GetTask", {"id": f"{uuid4()}:0:abc"})

    assert body["error"]["code"] == -32001


@pytest.mark.parametrize("task_id", ["nope", "a:b:c", "00000000-0000-4000-8000-000000000001:x:abc"])
def test_get_task_with_malformed_id_is_not_found(task_id: str) -> None:
    runtime = _runtime()

    body = _rpc(_client(_service(runtime)), runtime, "GetTask", {"id": task_id})

    assert body["error"]["code"] == -32001


def test_cancel_interrupts_a_running_turn() -> None:
    runtime = _runtime()
    session = _session(runtime, status=ResidentSessionStatus.RUNNING)
    interrupt = _Chat()
    service = _service(
        runtime,
        sessions=[session],
        chats=[_Chat(turns=[{"role": "user", "content": "q"}]), interrupt],
    )

    task = _rpc(_client(service), runtime, "CancelTask", {"id": _task_id(session, 0, "q")})[
        "result"
    ]

    assert task["status"]["state"] == "TASK_STATE_CANCELED"
    assert interrupt.sent == [{"type": "interrupt"}]


def test_cancel_of_answered_turn_is_refused() -> None:
    runtime = _runtime()
    session = _session(runtime)
    service = _service(
        runtime,
        sessions=[session],
        chats=[
            _Chat(turns=[{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}])
        ],
    )

    body = _rpc(_client(service), runtime, "CancelTask", {"id": _task_id(session, 0, "q")})

    assert body["error"]["code"] == -32002


def test_push_configuration_is_unsupported() -> None:
    runtime = _runtime()

    body = _rpc(
        _client(_service(runtime)),
        runtime,
        "CreateTaskPushNotificationConfig",
        {"taskId": "t", "id": "c", "url": "https://cb.example/push"},
    )

    assert body["error"]["code"] == -32004


# -- Directory publication --------------------------------------------- #


def test_with_a2a_endpoints_publishes_card_and_endpoint() -> None:
    runtime = _runtime()

    published = with_a2a_endpoints(runtime, f"{_BASE}/")

    kinds = {endpoint.kind: endpoint for endpoint in published.endpoints}
    base = f"{_BASE}/api/v1/forge/resident-runtimes/{runtime.id}"
    assert kinds[A2A_CARD_ENDPOINT_KIND].url == f"{base}/a2a/.well-known/agent-card.json"
    assert kinds[A2A_ENDPOINT_KIND].url == f"{base}/a2a"
    assert kinds[A2A_ENDPOINT_KIND].protocol == "jsonrpc"
    # Idempotent: publishing twice does not duplicate.
    assert len(with_a2a_endpoints(published, _BASE).endpoints) == 2


@pytest.mark.parametrize(
    ("base_url", "capabilities"),
    [("", _A2A_CAPABILITIES), (_BASE, [ResidentCapability.CHAT])],
)
def test_with_a2a_endpoints_needs_public_origin_and_channels(
    base_url: str,
    capabilities: list[ResidentCapability],
) -> None:
    runtime = _runtime(capabilities=capabilities)

    assert with_a2a_endpoints(runtime, base_url).endpoints == []


def test_resident_list_and_get_publish_a2a_endpoints_when_configured() -> None:
    runtime = _runtime()
    service = _service(runtime)
    app = FastAPI()
    app.include_router(create_resident_runtimes_router(service, a2a_public_base_url=_BASE))
    app.dependency_overrides[extract_principal] = lambda: _PRINCIPAL
    client = TestClient(app)

    listed = client.get("/api/v1/forge/resident-runtimes").json()[0]
    fetched = client.get(f"/api/v1/forge/resident-runtimes/{runtime.id}").json()

    for payload in (listed, fetched):
        assert {endpoint["kind"] for endpoint in payload["endpoints"]} == {"a2a-card", "a2a"}
