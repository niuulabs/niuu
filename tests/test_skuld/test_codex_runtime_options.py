"""Native catalog/settings controls; all infrastructure is explicitly mocked."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from niuu.ports.cli import TransportCapabilities
from skuld import broker_api
from skuld.broker import Broker
from skuld.config import SkuldSettings
from skuld.transports.codex_ws import CodexWebSocketTransport


@pytest.fixture(autouse=True)
def no_subprocesses(monkeypatch):
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(side_effect=AssertionError))
    monkeypatch.setattr(asyncio, "create_subprocess_shell", AsyncMock(side_effect=AssertionError))


def model(name="gpt-6-astra", levels=None):
    return {
        "id": name,
        "model": name,
        "displayName": name,
        "description": "Native model",
        "isDefault": True,
        "hidden": False,
        "defaultReasoningEffort": "high",
        "supportedReasoningEfforts": [{"reasoningEffort": e} for e in levels or ["high", "xhigh"]],
        "serviceTiers": [{"id": "priority", "name": "Fast"}],
        "inputModalities": ["text", "image"],
    }


@pytest.fixture
def transport(tmp_path):
    t = CodexWebSocketTransport(str(tmp_path), model="gpt-6-astra", reasoning_effort="xhigh")
    t._alive = True
    t._thread_id = "thread"
    t._send_rpc = AsyncMock(return_value={"data": [model()], "nextCursor": None})
    return t


async def test_discovery_paginates_caches_and_returns_defensive_copy(transport):
    t = transport
    t._send_rpc.side_effect = [
        {"data": [model()], "nextCursor": "second"},
        {"data": [model("future", ["high"])], "nextCursor": None},
    ]
    state = await t.get_runtime_options()
    assert [m["model"] for m in state["models"]] == ["gpt-6-astra", "future"]
    t._send_rpc.assert_awaited_with("model/list", {"cursor": "second"})
    state["models"].clear()
    assert len((await t.get_runtime_options())["models"]) == 2
    assert t._send_rpc.await_count == 2
    assert (await t.get_effort())["source"] == "native"
    assert (await t.get_effort())["levels"] == ["high", "xhigh"]


async def test_catalog_failure_does_not_substitute_static_values(transport):
    await transport.get_runtime_options()
    transport._send_rpc.side_effect = RuntimeError("disconnected")
    with pytest.raises(RuntimeError, match="disconnected"):
        await transport.get_runtime_options(refresh=True)
    with pytest.raises(RuntimeError):
        await transport.send_control("set_effort", effort="high")
    assert transport._reasoning_effort == "xhigh"


@pytest.mark.parametrize("response", [{}, {"data": [None]}, {"data": [], "nextCursor": 9}])
async def test_invalid_native_catalog_is_explicit(transport, response):
    transport._send_rpc.return_value = response
    with pytest.raises(RuntimeError):
        await transport.get_runtime_options()


async def test_pagination_cycle_is_explicit(transport):
    transport._send_rpc.return_value = {"data": [], "nextCursor": "again"}
    with pytest.raises(RuntimeError, match="advance"):
        await transport.get_runtime_options()


async def test_model_effort_tier_are_one_acknowledged_native_update(transport):
    t = transport
    t._current_turn_id = "active"
    t._turn_model = "gpt-6-astra"
    t._send_rpc.side_effect = [
        {"data": [model("future", ["high"])], "nextCursor": None},
        {},
    ]
    await t.send_control(
        "set_runtime_options",
        options={"model": "future", "effort": "high", "service_tier": "priority"},
    )
    t._send_rpc.assert_awaited_with(
        "thread/settings/update",
        {"threadId": "thread", "model": "future", "effort": "high", "serviceTier": "priority"},
    )
    state = await t.get_runtime_options()
    assert state["current"] == {"model": "future", "effort": "high", "service_tier": "priority"}
    assert state["active_turn"] == {"id": "active", "model": "gpt-6-astra"}
    t._send_rpc.side_effect = None
    t._send_rpc.return_value = {"data": [model("future", ["high"])], "nextCursor": None}
    await t.send_control("set_runtime_options", options={"service_tier": None})
    assert (await t.get_runtime_options())["current"]["service_tier"] is None


@pytest.mark.parametrize(
    "options",
    [
        {},
        None,
        {"permission": "never"},
        {"model": "unknown"},
        {"effort": "ultra"},
        {"effort": None},
        {"service_tier": "unknown"},
    ],
)
async def test_invalid_selection_never_reaches_mutation(transport, options):
    before = deepcopy((await transport.get_runtime_options())["current"])
    with pytest.raises(ValueError):
        await transport.send_control("set_runtime_options", options=options)
    assert (await transport.get_runtime_options())["current"] == before
    assert all(call.args[0] == "model/list" for call in transport._send_rpc.await_args_list)


async def test_failed_native_update_leaves_requested_settings_unchanged(transport):
    transport._send_rpc.side_effect = [
        {"data": [model()], "nextCursor": None},
        RuntimeError("timeout"),
    ]
    with pytest.raises(RuntimeError, match="timeout"):
        await transport.send_control("set_model", model="gpt-6-astra")
    assert transport._reasoning_effort == "xhigh"
    assert transport._service_tier is None


async def test_disconnected_catalog_and_settings_reject(transport):
    transport._alive = False
    with pytest.raises(RuntimeError):
        await transport.get_runtime_options()
    with pytest.raises(RuntimeError):
        await transport.send_control("set_runtime_options", options={"effort": "high"})


@pytest.fixture
def broker(tmp_path, transport):
    b = Broker(
        SkuldSettings(
            session={"id": "runtime-test", "model": "gpt-6-astra", "workspace_dir": str(tmp_path)}
        )
    )
    b._conversation_history_path = lambda: tmp_path / "conversation.json"
    b._transport = transport
    b._emit_broker_frame = AsyncMock()
    return b


async def test_broker_persists_exact_settings_and_restores_launch_overrides(broker):
    b = broker
    b._transport._send_rpc.return_value = {"data": [model("future")], "nextCursor": None}
    result = await b.handle_runtime_options(
        {"model": "future", "effort": "high", "service_tier": "priority"}, request_id="control"
    )
    assert result["request_id"] == "control"
    assert b.model == "future"
    b.model = "gpt-6-astra"
    b._restore_runtime_options()
    assert b.model == "future"
    assert b._restored_effort() == "high"
    assert b._build_transport_kwargs()["service_tier"] == "priority"
    b._settings.session.model = "operator-changed"
    b.model = "operator-changed"
    b._restore_runtime_options()
    assert b.model == "operator-changed"


async def test_broker_failed_control_does_not_persist_or_broadcast(broker):
    broker._transport._send_rpc.side_effect = RuntimeError("offline")
    with pytest.raises(RuntimeError):
        await broker.handle_runtime_options({"effort": "high"})
    assert not broker._effort_state_path().exists()
    broker._emit_broker_frame.assert_not_awaited()


async def test_existing_model_and_new_controls_share_validated_path(broker):
    await broker._dispatch_browser_message(
        {"type": "set_model", "model": "gpt-6-astra", "request_id": "old-client"}
    )
    assert broker._emit_broker_frame.await_args.args[0]["request_id"] == "old-client"
    await broker._dispatch_browser_message({"type": "get_runtime_options"})
    await broker._dispatch_browser_message(
        {"type": "set_runtime_options", "options": {"effort": "high"}}
    )
    assert broker._restored_effort() == "high"


async def test_http_discovery_and_acknowledged_setting(broker, monkeypatch):
    monkeypatch.setattr(broker_api, "broker", broker)
    assert (await broker_api.get_runtime_options())["source"] == "native"
    result = await broker_api.set_runtime_options(
        broker_api._RuntimeOptionsRequest(options={"effort": "high"}, request_id="http")
    )
    assert result["current"]["effort"] == "high"
    assert result["request_id"] == "http"
    with pytest.raises(HTTPException) as error:
        await broker_api.set_runtime_options(
            broker_api._RuntimeOptionsRequest(options={"effort": "ultra"})
        )
    assert error.value.status_code == 400
    broker._transport._send_rpc.side_effect = RuntimeError("native unavailable")
    with pytest.raises(HTTPException) as error:
        await broker_api.get_runtime_options(refresh=True)
    assert error.value.status_code == 502
    with pytest.raises(HTTPException) as error:
        await broker_api.set_runtime_options(
            broker_api._RuntimeOptionsRequest(options={"effort": "high"})
        )
    assert error.value.status_code == 502


async def test_http_and_broker_unsupported_and_unavailable(broker, monkeypatch):
    monkeypatch.setattr(broker_api, "broker", broker)
    broker._transport = None
    for operation in (
        broker_api.get_runtime_options,
        lambda: broker_api.set_runtime_options(broker_api._RuntimeOptionsRequest(options={})),
    ):
        with pytest.raises(HTTPException) as error:
            await operation()
        assert error.value.status_code == 503
    with pytest.raises(RuntimeError):
        await broker.handle_runtime_options()

    class Unsupported:
        capabilities = TransportCapabilities()

    broker._transport = Unsupported()
    for operation in (
        broker_api.get_runtime_options,
        lambda: broker_api.set_runtime_options(broker_api._RuntimeOptionsRequest(options={})),
    ):
        with pytest.raises(HTTPException) as error:
            await operation()
        assert error.value.status_code == 409
    with pytest.raises(ValueError):
        await broker.handle_runtime_options()
