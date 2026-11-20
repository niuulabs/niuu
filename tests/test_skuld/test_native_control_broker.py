"""LEX-1936 at the broker: an explicit refusal, urgent attention, an audited dismiss route."""

from __future__ import annotations

import asyncio
import importlib
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from skuld.broker import Broker
from skuld.broker_api import (
    _NativeControlDismissRequest,
    dismiss_native_control,
    list_native_controls,
)
from skuld.config import SkuldSettings
from skuld.control_errors import NativeControlsUnsupportedError
from skuld.delivery_errors import NativeControlPendingError
from skuld.transports import TransportCapabilities


@pytest.fixture(autouse=True)
def delivery_claim_api(monkeypatch):
    module = importlib.import_module("skuld.broker")

    async def claim(*args, **kwargs):
        return {"claimed": True, "status": "pending", "request_id": kwargs["request_id"]}

    monkeypatch.setattr(module, "claim_message", claim)
    monkeypatch.setattr(module, "settle_message", AsyncMock())
    client = AsyncMock()
    client.post.return_value = MagicMock(status_code=204)
    monkeypatch.setattr(Broker, "_get_http_client", AsyncMock(return_value=client))


def _broker(tmp_path, *, native_controls: bool = True) -> Broker:
    settings = SkuldSettings(
        session={"id": "test-session", "workspace_dir": str(tmp_path)},
        transport="sdk",
        delivery={"max_attempts": 3, "initial_backoff_seconds": 0.0, "max_backoff_seconds": 0.0},
    )
    b = Broker(settings=settings)
    b._transport = AsyncMock()
    b._transport.capabilities = TransportCapabilities(native_controls=native_controls)
    b._transport.is_turn_active = False
    b._apply_retrieval_reflex = AsyncMock(side_effect=lambda m: m)
    b._save_conversation_history = MagicMock()
    return b


def _pending_error() -> NativeControlPendingError:
    return NativeControlPendingError(
        "Claude is waiting on a native control (tty-1-c3b257b9, permission, menu live)",
        control_request_id="tty-1-c3b257b9",
        control_kind="permission",
        opened_at="2026-11-05T20:36:56.590000+00:00",
        menu_state="live",
    )


async def test_a_live_native_control_refuses_at_once_with_its_card(tmp_path):
    """5 Nov: the API sender got 202 pending while the retry loop hid the refusal."""
    b = _broker(tmp_path)
    b._transport.send_message = AsyncMock(side_effect=_pending_error())
    frames: list[dict] = []
    b._emit_broker_frame = AsyncMock(side_effect=frames.append)

    await b._deliver_user_message_and_ack("disk headroom", request_id="req-1", msg_id="m1")

    assert b._transport.send_message.await_count == 1  # never retried
    [ack] = [f for f in frames if f.get("type") == "user_delivery_failed"]
    assert ack["request_id"] == "req-1"
    assert ack["code"] == "native_control_pending"
    assert ack["control_request_id"] == "tty-1-c3b257b9"
    assert ack["control_kind"] == "permission"
    assert ack["menu_state"] == "live"
    assert ack["control_opened_at"] == "2026-11-05T20:36:56.590000+00:00"
    [error] = [f for f in frames if f.get("type") == "error"]
    assert error["reason_code"] == "native_control_pending"
    assert error["control_request_id"] == "tty-1-c3b257b9"
    assert not any(f.get("type") == "user_delivery_pending" for f in frames)


async def test_a_person_only_card_raises_urgent_attention_naming_the_timeout(tmp_path):
    b = _broker(tmp_path)
    b._channels = MagicMock()
    b._channels.count = 0
    b._channels.broadcast = AsyncMock()
    with patch.object(b, "_report_activity_state", new_callable=AsyncMock) as report:
        await b._handle_cli_event(
            {
                "type": "ask_user_question",
                "request_id": "tty-1-c3b257b9",
                "questions": [{"header": "Bash", "question": "Run `rm $V/$n`?"}],
                "metadata": {
                    "source": "tmux_tty_bridge",
                    "control_kind": "permission",
                    "person_only": True,
                    "auto_deny_after_seconds": 120.0,
                },
            }
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    report.assert_awaited()
    (state,) = report.call_args.args
    extra = report.call_args.kwargs["extra_metadata"]
    assert state == "awaiting_input"
    assert extra["kind"] == "permission"
    assert extra["urgent"] is True and extra["person_only"] is True
    assert extra["auto_deny_after_seconds"] == 120.0
    assert "Only a person may approve" in extra["prompt"]
    assert "120 seconds" in extra["prompt"] and "Run `rm $V/$n`?" in extra["prompt"]
    assert b._pending_attention == {"tty-1-c3b257b9": "permission"}


def test_person_only_card_without_a_timeout_is_refused():
    with pytest.raises(ValueError, match="auto_deny_after_seconds"):
        Broker._person_only_attention({"metadata": {"person_only": True}})
    assert Broker._person_only_attention({"metadata": {}}) is None


async def test_dismissal_from_the_terminal_leaves_attention(tmp_path):
    b = _broker(tmp_path)
    b._pending_attention = {"tty-1": "permission"}
    b._pending_ask_user_questions["tty-1"] = {"request_id": "tty-1"}
    b._activity_state = "awaiting_input"
    with patch.object(b, "_report_activity_state", new_callable=AsyncMock) as report:
        await b._handle_cli_event(
            {
                "type": "ask_user_resolved",
                "request_id": "tty-1",
                "decision": "completed_elsewhere",
                "accepted": False,
                "dismissed": True,
                "dismiss_reason": "resolved_in_terminal",
            }
        )
        await asyncio.sleep(0)
    assert "tty-1" not in b._pending_ask_user_questions
    assert b._pending_attention == {}
    report.assert_awaited_with("active")


async def test_broker_dismiss_is_audited_and_unsupported_transports_refuse(tmp_path, caplog):
    b = _broker(tmp_path)
    b._transport.dismiss_native_control = AsyncMock(return_value={"request_id": "tty-1"})
    b._transport.list_native_controls = MagicMock(return_value={"controls": []})
    caplog.set_level(logging.WARNING)
    assert await b.dismiss_native_control("tty-1", dismissed_by="dfagnou") == {
        "request_id": "tty-1"
    }
    b._transport.dismiss_native_control.assert_awaited_once_with("tty-1", dismissed_by="dfagnou")
    assert "dismissed by operator dfagnou" in caplog.text
    b._transport.open_question_tool_use_ids = MagicMock(return_value=set())
    assert b.list_native_controls() == {
        "controls": [],
        "record_questions": [],
        "reconcile_counts": {},
    }

    plain = _broker(tmp_path, native_controls=False)
    with pytest.raises(NativeControlsUnsupportedError):
        await plain.dismiss_native_control("tty-1", dismissed_by="x")
    with pytest.raises(NativeControlsUnsupportedError):
        plain.list_native_controls()


# -- Broker HTTP routes ------------------------------------------------------------------


def _request(room_role: str | None, **headers: str):
    if room_role is not None:
        headers["x-niuu-room-role"] = room_role
    return SimpleNamespace(headers=headers, state=SimpleNamespace(room_role=room_role))


def _fake_broker(**methods):
    return SimpleNamespace(
        _settings=SimpleNamespace(
            ws_auth=SimpleNamespace(
                user_id_header="x-auth-user-id",
                tenant_header="x-auth-tenant",
                roles_header="x-auth-roles",
            )
        ),
        **methods,
    )


@pytest.mark.parametrize("role", ["viewer", "approver"])
async def test_only_the_owner_may_dismiss_or_list(role, monkeypatch):
    fake = _fake_broker(dismiss_native_control=AsyncMock(), list_native_controls=MagicMock())
    monkeypatch.setattr("skuld.broker_api.broker", fake)
    with pytest.raises(HTTPException) as refused:
        await dismiss_native_control(_request(role), "tty-1", _NativeControlDismissRequest())
    assert refused.value.status_code == 403
    with pytest.raises(HTTPException) as refused:
        await list_native_controls(_request(role))
    assert refused.value.status_code == 403
    fake.dismiss_native_control.assert_not_awaited()


async def test_owner_dismiss_names_the_verified_identity_over_the_body(monkeypatch):
    fake = _fake_broker(dismiss_native_control=AsyncMock(return_value={"request_id": "tty-1"}))
    monkeypatch.setattr("skuld.broker_api.broker", fake)
    result = await dismiss_native_control(
        _request("owner", **{"x-auth-user-id": "dfagnou"}),
        "tty-1",
        _NativeControlDismissRequest(dismissed_by="someone-else"),
    )
    assert result == {"status": "dismissed", "control": {"request_id": "tty-1"}}
    fake.dismiss_native_control.assert_awaited_once_with("tty-1", dismissed_by="dfagnou")


@pytest.mark.parametrize(
    ("body", "expected"),
    [("forge-user", "forge-user"), ("", "session owner")],
)
async def test_owner_dismiss_without_identity_headers(monkeypatch, body, expected):
    fake = _fake_broker(dismiss_native_control=AsyncMock(return_value={}))
    monkeypatch.setattr("skuld.broker_api.broker", fake)
    await dismiss_native_control(
        _request("owner"), "tty-1", _NativeControlDismissRequest(dismissed_by=body)
    )
    fake.dismiss_native_control.assert_awaited_once_with("tty-1", dismissed_by=expected)


@pytest.mark.parametrize(
    ("error", "status"),
    [(LookupError("No open native control tty-9"), 404), (NativeControlsUnsupportedError(), 409)],
)
async def test_dismiss_errors_map_to_status(monkeypatch, error, status):
    fake = _fake_broker(dismiss_native_control=AsyncMock(side_effect=error))
    monkeypatch.setattr("skuld.broker_api.broker", fake)
    with pytest.raises(HTTPException) as refused:
        await dismiss_native_control(_request("owner"), "tty-9", _NativeControlDismissRequest())
    assert refused.value.status_code == status


async def test_owner_lists_native_controls(monkeypatch):
    fake = _fake_broker(list_native_controls=MagicMock(return_value={"controls": [], "counts": {}}))
    monkeypatch.setattr("skuld.broker_api.broker", fake)
    assert await list_native_controls(_request("owner")) == {"controls": [], "counts": {}}
    fake.list_native_controls = MagicMock(side_effect=NativeControlsUnsupportedError("no"))
    with pytest.raises(HTTPException) as refused:
        await list_native_controls(_request("owner"))
    assert refused.value.status_code == 409


def test_the_dismiss_route_is_owner_only_through_the_session_proxy():
    from niuu.room_access import required_role_for_route

    assert required_role_for_route("POST", "native-controls/tty-1/dismiss") == "owner"
    assert required_role_for_route("GET", "native-controls") == "owner"
