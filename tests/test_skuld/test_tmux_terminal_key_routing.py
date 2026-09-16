"""Terminal keys must survive the real broker -> tmux adapter boundary.

Only tmux execution is replaced with a test runner. No live session, process,
socket, provider or permission prompt is touched.
"""

import pytest

from tests.test_skuld.test_delivery_failed_steering_state import _broker
from tests.test_skuld.test_tmux_interactive_transport import (
    FakeTmuxInteractiveTransport,
    _collect_events,
)


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"key": "enter"}, ["Enter"]),
        ({"key": "escape"}, ["Escape"]),
        ({"key": "Up", "keys": []}, ["Up"]),
        ({"key": "Down", "keys": None}, ["Down"]),
        ({"keys": ["down", "enter"]}, ["Down", "Enter"]),
        ({"key": "escape", "keys": ["enter"]}, ["Enter"]),
        ({"keys": "escape"}, ["Escape"]),
        ({"keys": []}, []),
        ({}, []),
    ],
)
async def test_broker_delivers_exact_requested_keys_without_answering_other_controls(
    tmp_path, payload, expected
):
    broker = _broker(tmp_path)
    transport = FakeTmuxInteractiveTransport(str(tmp_path))
    events = await _collect_events(transport)
    await transport.start()
    broker._transport = transport
    # A raw key is an explicit terminal operation, not evidence of native
    # permission acceptance or completion of an unrelated structured question.
    pending = {"kind": "permission", "questions": []}
    transport._pending_tty_prompts["still-pending"] = pending
    transport.commands.clear()
    events.clear()
    try:
        await broker._dispatch_browser_message({"type": "terminal_key", "pane_id": "%1", **payload})

        commands = [args for args, _ in transport.commands if args[0] == "send-keys"]
        assert commands == [("send-keys", "-t", "%1", key) for key in expected]
        sent = [event for event in events if event["type"] == "terminal_key_sent"]
        assert [(event["pane_id"], event["key"]) for event in sent] == [
            ("%1", key) for key in expected
        ]
        assert transport._pending_tty_prompts["still-pending"] is pending
        assert not any(event["type"] == "ask_user_resolved" for event in events)
        assert not transport.loaded_buffers  # Never manufacture a chat message.
    finally:
        await transport.stop()
