"""Per-message-type room-role authorization for browser WebSocket traffic.

Widening "attach" to any active participant handed an observer nearly the
whole broker control surface (set_permission_mode, terminal input, slash
commands, ...) unless every message TYPE is separately gated. This module
covers the classification table exhaustively (one assertion per case label
in Broker._dispatch_browser_message's match statement), a drift guard that
fails if a new case is added without being classified, and a handful of
end-to-end dispatch tests proving the wiring (channel role lookup -> rank
comparison -> short-circuit) actually runs.
"""

from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from niuu.ports.cli.transport import TransportCapabilities
from skuld.broker import (
    _APPROVER_ONLY_MESSAGE_TYPES,
    _OWNER_ONLY_MESSAGE_TYPES,
    _ROOM_ROLE_RANK,
    _VIEWER_MESSAGE_TYPES,
    Broker,
)
from skuld.channels import WebSocketChannel
from skuld.config import SkuldSettings

_ALL_CAPABILITIES = TransportCapabilities(
    **dict.fromkeys([f.name for f in fields(TransportCapabilities)], True)
)

# The exact allowlist the security review specified, restated here so this
# test fails loudly if broker.py's classification ever silently drifts from
# the reviewed policy (rather than only drifting from its own case labels,
# which the parity test below catches separately).
_EXPECTED_VIEWER = {
    "directed_message",
    "steer_active_turn",
    "set_internal_visibility",
    "discover_slash_commands",
    "get_effort",
    "get_runtime_options",
}
_EXPECTED_APPROVER_ONLY = {"permission_response", "ask_user_answer"}
_EXPECTED_OWNER_ONLY = {
    "set_permission_mode",
    "mcp_set_servers",
    "terminal_input",
    "terminal_key",
    "terminal_resize",
    "slash_command",
    "interrupt",
    "set_model",
    "set_effort",
    "set_runtime_options",
    "set_max_thinking_tokens",
    "rewind_files",
    "resend_initial_prompt",
    "publish_event",
}


def test_classification_matches_the_reviewed_allowlist():
    assert _VIEWER_MESSAGE_TYPES == _EXPECTED_VIEWER
    assert _APPROVER_ONLY_MESSAGE_TYPES == _EXPECTED_APPROVER_ONLY
    assert _OWNER_ONLY_MESSAGE_TYPES == _EXPECTED_OWNER_ONLY
    # No overlap: every type has exactly one tier. A three-way intersection
    # alone would miss a type present in exactly two of the three sets (the
    # triple-AND is empty whenever ANY pair disagrees, so it can never
    # actually catch that case) — check every pair explicitly.
    assert not (_VIEWER_MESSAGE_TYPES & _APPROVER_ONLY_MESSAGE_TYPES), (
        "type(s) classified as both viewer and approver-only: "
        f"{_VIEWER_MESSAGE_TYPES & _APPROVER_ONLY_MESSAGE_TYPES}"
    )
    assert not (_VIEWER_MESSAGE_TYPES & _OWNER_ONLY_MESSAGE_TYPES), (
        "type(s) classified as both viewer and owner-only: "
        f"{_VIEWER_MESSAGE_TYPES & _OWNER_ONLY_MESSAGE_TYPES}"
    )
    assert not (_APPROVER_ONLY_MESSAGE_TYPES & _OWNER_ONLY_MESSAGE_TYPES), (
        "type(s) classified as both approver-only and owner-only: "
        f"{_APPROVER_ONLY_MESSAGE_TYPES & _OWNER_ONLY_MESSAGE_TYPES}"
    )


def test_case_labels_in_dispatch_are_all_classified():
    """Drift guard: a new `case "foo":` in _dispatch_browser_message that is
    not added to one of the three classification sets must fail this test,
    not silently default to viewer (unsafe) or be caught only in review."""
    source = Path("src/skuld/broker.py").read_text()
    start = source.index("async def _dispatch_browser_message")
    end = source.index("\n    def ", start)
    body = source[start:end]

    clause_pattern = re.compile(r'case ((?:"[a-z_]+"(?: \| )?)+):')
    case_labels: set[str] = set()
    for match in clause_pattern.finditer(body):
        for literal in re.findall(r'"([a-z_]+)"', match.group(1)):
            case_labels.add(literal)

    # A case clause the pattern above can't parse (a guard, a binding, a
    # class pattern — anything other than one-or-more quoted string
    # literals ORed together) would match ZERO labels and simply vanish
    # from case_labels rather than failing loud. Count every raw `case `
    # line independently and require it to match the number of clauses the
    # label-extracting regex actually recognized, so a shape drift is
    # caught here instead of silently exempting a message type from
    # classification. `case _:` (the untyped default-chat fallback) is the
    # one deliberate exception: it carries no literal to classify.
    all_case_lines = [line for line in body.splitlines() if re.match(r"\s*case ", line)]
    wildcard_lines = [line for line in all_case_lines if re.match(r"\s*case _:\s*$", line)]
    recognized_clauses = len(clause_pattern.findall(body))
    assert len(wildcard_lines) == 1, (
        f"expected exactly one `case _:` default fallback, found {len(wildcard_lines)}"
    )
    assert recognized_clauses == len(all_case_lines) - len(wildcard_lines), (
        "a `case` clause in _dispatch_browser_message has a shape this test's regex "
        "cannot parse (found "
        f"{len(all_case_lines)} raw `case ` lines, {len(wildcard_lines)} wildcard, "
        f"but only recognized {recognized_clauses} as classifiable literals) — "
        "update this test's clause_pattern to cover the new shape, or simplify the "
        "new case back to plain string literals."
    )

    classified = _VIEWER_MESSAGE_TYPES | _APPROVER_ONLY_MESSAGE_TYPES | _OWNER_ONLY_MESSAGE_TYPES
    assert case_labels, "regex found no case labels; the dispatcher body shape changed"
    assert case_labels <= classified, f"unclassified case labels: {case_labels - classified}"
    stale = classified - case_labels
    assert not stale, f"classified types no longer in the dispatcher: {stale}"


@pytest.mark.parametrize("msg_type", sorted(_EXPECTED_VIEWER) + [None, "unrecognized_future_type"])
def test_viewer_role_requirement(msg_type):
    from skuld.broker import _message_role_requirement

    assert _message_role_requirement(msg_type) == "viewer"


@pytest.mark.parametrize("msg_type", sorted(_EXPECTED_APPROVER_ONLY))
def test_approver_role_requirement(msg_type):
    from skuld.broker import _message_role_requirement

    assert _message_role_requirement(msg_type) == "approver"


@pytest.mark.parametrize("msg_type", sorted(_EXPECTED_OWNER_ONLY))
def test_owner_role_requirement(msg_type):
    from skuld.broker import _message_role_requirement

    assert _message_role_requirement(msg_type) == "owner"


def test_role_rank_is_strictly_ordered():
    assert _ROOM_ROLE_RANK["viewer"] < _ROOM_ROLE_RANK["approver"] < _ROOM_ROLE_RANK["owner"]


# --- End-to-end: the dispatcher actually enforces the resolved channel role --


def _broker(tmp_path) -> Broker:
    b = Broker(
        settings=SkuldSettings(
            session={"id": "gating-session", "workspace_dir": str(tmp_path)},
            transport="sdk",
        )
    )
    b._transport = AsyncMock()
    b._transport.capabilities = _ALL_CAPABILITIES
    b._transport.is_turn_active = False
    return b


def _connect(broker: Broker, role: str) -> AsyncMock:
    ws = AsyncMock()
    broker._channels.add(WebSocketChannel(ws, room_role=role))
    return ws


async def test_viewer_may_steer_the_active_turn(tmp_path):
    broker = _broker(tmp_path)
    ws = _connect(broker, "viewer")

    await broker._dispatch_browser_message(
        {"type": "steer_active_turn", "content": "go left"}, sender_ws=ws
    )

    broker._transport.send_control.assert_awaited_once_with("steer", content="go left")
    ws.send_json.assert_not_called()


async def test_viewer_denied_interrupt(tmp_path):
    broker = _broker(tmp_path)
    ws = _connect(broker, "viewer")

    await broker._dispatch_browser_message({"type": "interrupt"}, sender_ws=ws)

    broker._transport.send_control.assert_not_called()
    sent = ws.send_json.await_args.args[0]
    assert sent["type"] == "error" and sent["code"] == "forbidden"
    assert "owner" in sent["content"]


async def test_viewer_denied_permission_response(tmp_path):
    broker = _broker(tmp_path)
    ws = _connect(broker, "viewer")

    await broker._dispatch_browser_message(
        {"type": "permission_response", "request_id": "r1", "behavior": "allow"}, sender_ws=ws
    )

    broker._transport.send_control_response.assert_not_called()
    sent = ws.send_json.await_args.args[0]
    assert sent["type"] == "error" and sent["code"] == "forbidden"


async def test_approver_may_answer_permission_response(tmp_path):
    broker = _broker(tmp_path)
    ws = _connect(broker, "approver")

    await broker._dispatch_browser_message(
        {"type": "permission_response", "request_id": "r1", "behavior": "allow"}, sender_ws=ws
    )

    broker._transport.send_control_response.assert_awaited_once()


async def test_approver_permission_response_strips_updated_input_and_permissions(tmp_path):
    """An approver may allow/deny a pending tool call, but must not also
    rewrite it: updated_input could swap in a different command, and
    updated_permissions could grant a broader mode (e.g. bypassPermissions)
    than a single yes/no decision implies. Only the owner may set either."""
    broker = _broker(tmp_path)
    ws = _connect(broker, "approver")

    await broker._dispatch_browser_message(
        {
            "type": "permission_response",
            "request_id": "r1",
            "behavior": "allow",
            "updated_input": {"command": "rm -rf /"},
            "updated_permissions": {"mode": "bypassPermissions"},
        },
        sender_ws=ws,
    )

    broker._transport.send_control_response.assert_awaited_once()
    sent_response = broker._transport.send_control_response.await_args.args[1]
    assert sent_response["updatedInput"] == {}
    assert "updatedPermissions" not in sent_response


async def test_owner_permission_response_keeps_updated_input_and_permissions(tmp_path):
    broker = _broker(tmp_path)
    ws = _connect(broker, "owner")

    await broker._dispatch_browser_message(
        {
            "type": "permission_response",
            "request_id": "r1",
            "behavior": "allow",
            "updated_input": {"command": "ls"},
            "updated_permissions": {"mode": "bypassPermissions"},
        },
        sender_ws=ws,
    )

    broker._transport.send_control_response.assert_awaited_once()
    sent_response = broker._transport.send_control_response.await_args.args[1]
    assert sent_response["updatedInput"] == {"command": "ls"}
    assert sent_response["updatedPermissions"] == {"mode": "bypassPermissions"}


async def test_approver_denied_owner_only_interrupt(tmp_path):
    broker = _broker(tmp_path)
    ws = _connect(broker, "approver")

    await broker._dispatch_browser_message({"type": "interrupt"}, sender_ws=ws)

    broker._transport.send_control.assert_not_called()
    sent = ws.send_json.await_args.args[0]
    assert sent["type"] == "error" and sent["code"] == "forbidden"


async def test_owner_may_interrupt(tmp_path):
    broker = _broker(tmp_path)
    ws = _connect(broker, "owner")

    await broker._dispatch_browser_message({"type": "interrupt"}, sender_ws=ws)

    broker._transport.send_control.assert_awaited_once_with("interrupt")


async def test_owner_may_set_permission_mode_bypass(tmp_path):
    broker = _broker(tmp_path)
    ws = _connect(broker, "owner")

    await broker._dispatch_browser_message(
        {"type": "set_permission_mode", "mode": "bypassPermissions"}, sender_ws=ws
    )

    broker._transport.send_control.assert_awaited_once_with(
        "set_permission_mode", permissionMode="bypassPermissions"
    )


async def test_default_chat_is_viewer_level(tmp_path):
    import asyncio

    broker = _broker(tmp_path)
    ws = _connect(broker, "viewer")

    await broker._dispatch_browser_message({"content": "hello"}, sender_ws=ws)
    # Delivery runs in a background task; pump the loop so it executes.
    await asyncio.sleep(0)

    broker._transport.send_message.assert_called_once()


async def test_unregistered_connection_defaults_to_viewer_not_owner(tmp_path):
    """A live sender_ws with no matching channel (torn down mid-dispatch, or
    never registered) must get the LEAST privilege, never the most."""
    broker = _broker(tmp_path)
    ws = AsyncMock()  # not registered in broker._channels

    await broker._dispatch_browser_message({"type": "interrupt"}, sender_ws=ws)

    broker._transport.send_control.assert_not_called()
    sent = ws.send_json.await_args.args[0]
    assert sent["type"] == "error" and sent["code"] == "forbidden"


async def test_no_sender_ws_is_trusted_as_owner(tmp_path):
    """Internal/synthetic dispatch (no live browser socket) is a call site
    that already established its own authority before reaching here."""
    broker = _broker(tmp_path)

    await broker._dispatch_browser_message({"type": "interrupt"})

    broker._transport.send_control.assert_awaited_once_with("interrupt")


class TestSanitizeDirectedMetadata:
    """skuld.broker._sanitize_directed_metadata: the shared filter behind
    both the WS directed_message/default-chat dispatch paths and (a
    separate copy of the same policy) skuld.broker_api's HTTP room/direct
    route."""

    def test_none_passes_through(self):
        from skuld.broker import _sanitize_directed_metadata

        assert _sanitize_directed_metadata("viewer", None) is None

    def test_owner_metadata_passes_through_unchanged(self):
        from skuld.broker import _sanitize_directed_metadata

        metadata = {"participant_id": "ravn-peer-x", "reply_context": {"case": "1"}, "source": "x"}
        assert _sanitize_directed_metadata("owner", metadata) == metadata

    @pytest.mark.parametrize("role", ["viewer", "approver"])
    def test_sub_owner_strips_participant_id_reply_context_and_source(self, role):
        from skuld.broker import _sanitize_directed_metadata

        metadata = {
            "participant_id": "ravn-peer-x",
            "reply_context": {"case": "1"},
            "source": "sneaky",
            "thread_id": "keep-me",
        }
        result = _sanitize_directed_metadata(role, metadata)
        assert result == {"thread_id": "keep-me"}


async def test_viewer_directed_message_cannot_impersonate_another_participant(tmp_path):
    """The reviewer's exploit pattern: a viewer sets metadata.participant_id
    to a Ravn peer's id, hoping handle_directed_room_message (which reads
    participant_id from metadata, not a separate argument) attributes the
    message to that peer instead of the viewer who actually sent it."""
    broker = _broker(tmp_path)
    ws = _connect(broker, "viewer")
    broker._room_bridge = MagicMock(pending_reply_peer_ids=lambda: ())
    broker.handle_directed_room_message = AsyncMock(return_value="msg-1")

    await broker._dispatch_browser_message(
        {
            "type": "directed_message",
            "targetPeerId": "peer-1",
            "content": "hello",
            "metadata": {
                "participant_id": "ravn-peer-x",
                "reply_context": {"forged": "context"},
                "source": "sneaky",
                "thread_id": "keep-me",
            },
        },
        sender_ws=ws,
    )

    broker.handle_directed_room_message.assert_awaited_once()
    call = broker.handle_directed_room_message.await_args
    assert call.kwargs["metadata"] == {"thread_id": "keep-me"}
    assert call.kwargs["source"] == "browser"


async def test_owner_directed_message_keeps_participant_id_and_reply_context(tmp_path):
    broker = _broker(tmp_path)
    ws = _connect(broker, "owner")
    broker._room_bridge = MagicMock(pending_reply_peer_ids=lambda: ())
    broker.handle_directed_room_message = AsyncMock(return_value="msg-1")

    metadata = {
        "participant_id": "ravn-peer-x",
        "reply_context": {"case": "1"},
        "thread_id": "keep-me",
    }
    await broker._dispatch_browser_message(
        {
            "type": "directed_message",
            "targetPeerId": "peer-1",
            "content": "hello",
            "metadata": dict(metadata),
        },
        sender_ws=ws,
    )

    broker.handle_directed_room_message.assert_awaited_once()
    call = broker.handle_directed_room_message.await_args
    assert call.kwargs["metadata"] == metadata
