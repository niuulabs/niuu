"""Contract tests for the Hermes API-server resident adapter."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from aiohttp import web

from niuu.adapters.memory_credential_store import MemoryCredentialStore
from niuu.domain.models import SecretType
from niuu.ports.session_proxy import SessionProxyTarget
from volundr.adapters.outbound.hermes_gateway import (
    HERMES_CREDENTIAL_NAME,
    HERMES_LEGACY_CREDENTIAL_NAME,
    HermesGatewayError,
    HermesResidentSessionController,
    _session_uuid,
    ensure_hermes_api_key,
    normalize_hermes_model_id,
)
from volundr.domain.models import ResidentBackend, ResidentEngine, ResidentRuntime


class _Route:
    def __init__(self, port: int) -> None:
        self.port = port

    def resident_proxy_target(self, _runtime: ResidentRuntime) -> SessionProxyTarget:
        return SessionProxyTarget(
            service_url=f"http://hermes.test:{self.port}",
            connect_host="127.0.0.1",
            connect_port=self.port,
        )


def _runtime() -> ResidentRuntime:
    return ResidentRuntime(
        id=UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"),
        owner_id="user-a",
        tenant_id="tenant-a",
        name="Hermes",
        model="niuu/Qwen/Qwen3.6-35B-A3B-FP8",
        backend=ResidentBackend.OPENSHELL,
        engine=ResidentEngine.HERMES,
        profile_id="nemohermes-openshell",
    )


@asynccontextmanager
async def _serve(app: web.Application) -> AsyncIterator[int]:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = site._server.sockets
    try:
        yield sockets[0].getsockname()[1]
    finally:
        await runner.cleanup()


def _assert_auth(request: web.Request, token: str) -> None:
    assert request.headers["Authorization"] == f"Bearer {token}"
    assert request.headers["Host"].startswith("hermes.test:")


@pytest.mark.asyncio
async def test_api_key_is_generated_once_and_is_the_only_stored_secret() -> None:
    store = MemoryCredentialStore()
    runtime = _runtime()

    first = await ensure_hermes_api_key(store, runtime)
    second = await ensure_hermes_api_key(store, runtime)

    assert first == second
    assert await store.get_value("resident", str(runtime.id), HERMES_CREDENTIAL_NAME) == {
        "api_key": first
    }


@pytest.mark.asyncio
async def test_legacy_dashboard_token_is_migrated_to_api_credential() -> None:
    store = MemoryCredentialStore()
    runtime = _runtime()
    await store.store(
        "resident",
        str(runtime.id),
        HERMES_LEGACY_CREDENTIAL_NAME,
        SecretType.API_KEY,
        {"session_token": "legacy-machine-secret"},
    )

    api_key = await ensure_hermes_api_key(store, runtime)

    assert api_key == "legacy-machine-secret"
    assert await store.get_value("resident", str(runtime.id), HERMES_LEGACY_CREDENTIAL_NAME) is None
    assert await store.get_value("resident", str(runtime.id), HERMES_CREDENTIAL_NAME) == {
        "api_key": api_key
    }


@pytest.mark.asyncio
async def test_incomplete_api_credential_fails_closed() -> None:
    store = MemoryCredentialStore()
    runtime = _runtime()
    await store.store(
        "resident",
        str(runtime.id),
        HERMES_CREDENTIAL_NAME,
        SecretType.API_KEY,
        {"not_the_key": "value"},
    )

    with pytest.raises(RuntimeError, match="credential is incomplete"):
        await ensure_hermes_api_key(store, runtime)


def test_niuu_virtual_provider_prefix_is_removed_for_hermes() -> None:
    assert normalize_hermes_model_id("niuu/Qwen/Qwen3.6-35B-A3B-FP8") == (
        "Qwen/Qwen3.6-35B-A3B-FP8"
    )
    assert normalize_hermes_model_id("openrouter/anthropic/claude-sonnet-4.6") == (
        "openrouter/anthropic/claude-sonnet-4.6"
    )


@pytest.mark.asyncio
async def test_session_lifecycle_uses_authenticated_resource_api() -> None:
    persistent_key = "api_existing"
    created_key = "api_created"
    sessions: dict[str, dict[str, Any]] = {
        persistent_key: {
            "id": persistent_key,
            "title": "Persistent work",
            "model": "Qwen/Qwen3.6-35B-A3B-FP8",
            "started_at": datetime(2026, 7, 11, tzinfo=UTC).timestamp(),
            "message_count": 4,
        }
    }
    requests: list[tuple[str, str]] = []
    store = MemoryCredentialStore()
    token = await ensure_hermes_api_key(store, _runtime())

    async def list_sessions(request: web.Request) -> web.Response:
        _assert_auth(request, token)
        requests.append((request.method, request.path))
        return web.json_response(
            {"object": "list", "data": list(sessions.values()), "has_more": False}
        )

    async def create_session(request: web.Request) -> web.Response:
        _assert_auth(request, token)
        requests.append((request.method, request.path))
        body = await request.json()
        assert body == {
            "title": "New work",
            "model": "Qwen/Qwen3.6-35B-A3B-FP8",
        }
        session = {"id": created_key, **body, "started_at": 1, "message_count": 0}
        sessions[created_key] = session
        return web.json_response({"object": "hermes.session", "session": session}, status=201)

    async def delete_session(request: web.Request) -> web.Response:
        _assert_auth(request, token)
        requests.append((request.method, request.path))
        sessions.pop(request.match_info["session_id"])
        return web.json_response({"deleted": True})

    app = web.Application()
    app.router.add_get("/api/sessions", list_sessions)
    app.router.add_post("/api/sessions", create_session)
    app.router.add_delete("/api/sessions/{session_id}", delete_session)

    async with _serve(app) as port:
        controller = HermesResidentSessionController(_Route(port), store)
        runtime = _runtime()

        listed = await controller.list_sessions(runtime)
        created = await controller.create_session(
            runtime,
            title="New work",
            model=runtime.model,
        )
        await controller.delete_session(runtime, listed[0].id)

    assert listed[0].id == _session_uuid(persistent_key)
    assert listed[0].model == runtime.model
    assert created.id == _session_uuid(created_key)
    assert persistent_key not in sessions
    assert requests == [
        ("GET", "/api/sessions"),
        ("POST", "/api/sessions"),
        ("DELETE", f"/api/sessions/{persistent_key}"),
    ]


@pytest.mark.asyncio
async def test_session_model_cannot_diverge_from_resident_model() -> None:
    controller = HermesResidentSessionController(_Route(1), MemoryCredentialStore())

    with pytest.raises(HermesGatewayError, match="use the resident model"):
        await controller.create_session(
            _runtime(),
            title="Different model",
            model="niuu/gpt-5.6-sol",
        )


@pytest.mark.asyncio
async def test_chat_stream_normalizes_history_approval_tools_and_usage() -> None:
    session_key = "api_chat"
    run_id = "run_approval"
    approval = asyncio.Event()
    store = MemoryCredentialStore()
    runtime = _runtime()
    token = await ensure_hermes_api_key(store, runtime)

    async def list_sessions(request: web.Request) -> web.Response:
        _assert_auth(request, token)
        return web.json_response(
            {"data": [{"id": session_key, "model": normalize_hermes_model_id(runtime.model)}]}
        )

    async def get_session(request: web.Request) -> web.Response:
        _assert_auth(request, token)
        return web.json_response(
            {
                "session": {
                    "id": session_key,
                    "model": normalize_hermes_model_id(runtime.model),
                }
            }
        )

    async def get_messages(request: web.Request) -> web.Response:
        _assert_auth(request, token)
        return web.json_response(
            {
                "data": [
                    {"id": "m1", "role": "user", "content": "Earlier", "timestamp": 1},
                    {"id": "m2", "role": "assistant", "content": "Reply", "timestamp": 2},
                ]
            }
        )

    async def create_run(request: web.Request) -> web.Response:
        _assert_auth(request, token)
        assert await request.json() == {
            "input": "Run the command",
            "session_id": session_key,
            "model": normalize_hermes_model_id(runtime.model),
        }
        return web.json_response({"run_id": run_id, "status": "started"}, status=202)

    async def run_events(request: web.Request) -> web.StreamResponse:
        _assert_auth(request, token)
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)

        async def emit(event: dict[str, Any]) -> None:
            await response.write(f"data: {json.dumps(event)}\n\n".encode())

        await emit(
            {
                "event": "approval.request",
                "run_id": run_id,
                "command": "printf PROOF",
                "description": "Run proof command",
                "allow_permanent": True,
            }
        )
        await approval.wait()
        await emit({"event": "tool.started", "run_id": run_id, "tool": "terminal"})
        await emit({"event": "tool.completed", "run_id": run_id, "tool": "terminal"})
        await emit({"event": "message.delta", "run_id": run_id, "delta": "API_OK"})
        await emit(
            {
                "event": "run.completed",
                "run_id": run_id,
                "output": "API_OK",
                "usage": {"input_tokens": 8, "output_tokens": 2, "total_tokens": 10},
            }
        )
        return response

    async def approve_run(request: web.Request) -> web.Response:
        _assert_auth(request, token)
        assert await request.json() == {"choice": "once"}
        approval.set()
        return web.json_response({"resolved": 1})

    app = web.Application()
    app.router.add_get("/api/sessions", list_sessions)
    app.router.add_get("/api/sessions/{session_id}", get_session)
    app.router.add_get("/api/sessions/{session_id}/messages", get_messages)
    app.router.add_post("/v1/runs", create_run)
    app.router.add_get("/v1/runs/{run_id}/events", run_events)
    app.router.add_post("/v1/runs/{run_id}/approval", approve_run)

    async with _serve(app) as port:
        controller = HermesResidentSessionController(_Route(port), store)
        chat = await controller.connect_chat(runtime, _session_uuid(session_key))
        assert await chat.receive() == {
            "type": "capabilities",
            "interrupt": True,
            "steer": False,
            "set_model": False,
            "set_thinking_tokens": False,
        }
        history = await chat.receive()
        assert [turn["content"] for turn in history["turns"]] == ["Earlier", "Reply"]

        await chat.send({"type": "user", "request_id": "u1", "content": "Run the command"})
        assert (await chat.receive())["type"] == "user_confirmed"
        control = await chat.receive()
        assert control["type"] == "control_request"
        assert control["input"]["command"] == "printf PROOF"
        await chat.send(
            {
                "type": "permission_response",
                "request_id": control["request_id"],
                "behavior": "allowOnce",
            }
        )
        post_approval = [await chat.receive() for _ in range(6)]
        assert {frame["type"] for frame in post_approval} == {
            "permission_resolved",
            "tool_start",
            "tool_result",
            "assistant",
            "content_block_delta",
            "result",
        }
        delta = next(frame for frame in post_approval if frame["type"] == "content_block_delta")
        assert delta["delta"]["text"] == "API_OK"
        result = next(frame for frame in post_approval if frame["type"] == "result")
        assert result["type"] == "result"
        assert result["usage"]["total_tokens"] == 10
        await chat.close()


@pytest.mark.asyncio
async def test_interrupt_uses_run_stop_api_and_steer_is_not_advertised() -> None:
    session_key = "api_interrupt"
    run_id = "run_interrupt"
    stopped = asyncio.Event()
    store = MemoryCredentialStore()
    runtime = _runtime()
    token = await ensure_hermes_api_key(store, runtime)

    async def sessions(request: web.Request) -> web.Response:
        _assert_auth(request, token)
        return web.json_response({"data": [{"id": session_key}]})

    async def detail(request: web.Request) -> web.Response:
        _assert_auth(request, token)
        return web.json_response({"session": {"id": session_key}})

    async def messages(request: web.Request) -> web.Response:
        _assert_auth(request, token)
        return web.json_response({"data": []})

    async def create_run(request: web.Request) -> web.Response:
        _assert_auth(request, token)
        return web.json_response({"run_id": run_id}, status=202)

    async def events(request: web.Request) -> web.StreamResponse:
        _assert_auth(request, token)
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await stopped.wait()
        await response.write(
            f"data: {json.dumps({'event': 'run.cancelled', 'run_id': run_id})}\n\n".encode()
        )
        return response

    async def stop(request: web.Request) -> web.Response:
        _assert_auth(request, token)
        stopped.set()
        return web.json_response({"run_id": run_id, "status": "stopping"})

    app = web.Application()
    app.router.add_get("/api/sessions", sessions)
    app.router.add_get("/api/sessions/{session_id}", detail)
    app.router.add_get("/api/sessions/{session_id}/messages", messages)
    app.router.add_post("/v1/runs", create_run)
    app.router.add_get("/v1/runs/{run_id}/events", events)
    app.router.add_post("/v1/runs/{run_id}/stop", stop)

    async with _serve(app) as port:
        chat = await HermesResidentSessionController(_Route(port), store).connect_chat(
            runtime, _session_uuid(session_key)
        )
        await chat.receive()
        await chat.receive()
        await chat.send({"type": "user", "content": "Long task"})
        await chat.receive()
        await chat.send({"type": "interrupt"})
        result = await chat.receive()
        assert result == {"type": "result", "result": "", "interrupted": True}
        with pytest.raises(HermesGatewayError, match="does not support steering"):
            await chat.send({"type": "steer", "content": "Change direction"})
        await chat.close()
