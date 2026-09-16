"""Native text must precede the tool whose hook arrives before MessageDisplay."""

import asyncio
import json

import pytest

from skuld.transports.tmux_interactive import TmuxInteractiveTransport

NATIVE = "13c6aa82-a33e-49c3-834f-2b4d3a43e54e"


def native_row(identifier, parent, blocks, *, message_id="message-a", session=NATIVE, child=False):
    return {
        "type": "assistant",
        "uuid": identifier,
        "parentUuid": parent,
        "sessionId": session,
        "isSidechain": child,
        "message": {"id": message_id, "role": "assistant", "content": blocks},
    }


def write_native(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def tool(identifier="tool-a"):
    return {"type": "tool_use", "id": identifier, "name": "Bash", "input": {"command": "true"}}


async def probe(tmp_path):
    transport = TmuxInteractiveTransport(str(tmp_path), sdk_port=8081)
    transport._claude_native_session_id = NATIVE
    events = []

    async def capture(event):
        events.append(event)

    transport.on_event(capture)
    return transport, events


def pretool(path, identifier="tool-a", **extra):
    return {
        "hook_event_name": "PreToolUse",
        "session_id": NATIVE,
        "transcript_path": str(path),
        "tool_use_id": identifier,
        "tool_name": "Bash",
        "tool_input": {"command": "true"},
        **extra,
    }


def display(text, identifier="display-a"):
    return {
        "hook_event_name": "MessageDisplay",
        "session_id": NATIVE,
        "message_id": identifier,
        "final": True,
        "index": 0,
        "delta": text,
    }


def blocks(events):
    return [
        block
        for event in events
        if event.get("type") == "assistant"
        for block in event.get("message", {}).get("content", [])
    ]


async def test_native_text_precedes_pretool_then_late_display_is_one_echo(tmp_path):
    transport, events = await probe(tmp_path)
    path = tmp_path / f"{NATIVE}.jsonl"
    text = "Checking café 東京.\n"
    write_native(
        path,
        [
            native_row("text-row", None, [{"type": "text", "text": text}]),
            native_row("tool-row", "text-row", [tool()]),
        ],
    )
    await transport.handle_claude_hook(pretool(path))
    await transport.handle_claude_hook(display(text))
    observed = blocks(events)
    assert [b["type"] for b in observed] == ["text", "tool_use"]
    assert observed[0]["text"] == text
    assert "phase" not in observed[0]


async def test_identical_later_native_text_and_display_are_distinct_occurrences(tmp_path):
    transport, events = await probe(tmp_path)
    path = tmp_path / f"{NATIVE}.jsonl"
    rows = [
        native_row("text-a", None, [{"type": "text", "text": "Checking."}]),
        native_row("tool-a", "text-a", [tool()]),
    ]
    write_native(path, rows)
    await transport.handle_claude_hook(pretool(path))
    await transport.handle_claude_hook(display("Checking.", "display-a"))
    rows += [
        native_row(
            "text-b", "tool-a", [{"type": "text", "text": "Checking."}], message_id="message-b"
        ),
        native_row("tool-b", "text-b", [tool("tool-b")], message_id="message-b"),
    ]
    write_native(path, rows)
    await transport.handle_claude_hook(pretool(path, "tool-b"))
    await transport.handle_claude_hook(display("Checking.", "display-b"))
    assert [b["type"] for b in blocks(events)] == ["text", "tool_use", "text", "tool_use"]
    await transport.handle_claude_hook(display("Checking.", "display-c"))
    await transport.handle_claude_hook(display("Checking.", "display-c"))
    assert [b["text"] for b in blocks(events) if b["type"] == "text"] == ["Checking."] * 3


async def test_display_before_pretool_keeps_one_text_without_reordering(tmp_path):
    transport, events = await probe(tmp_path)
    path = tmp_path / f"{NATIVE}.jsonl"
    write_native(
        path,
        [
            native_row("text", None, [{"type": "text", "text": "First."}]),
            native_row("tool", "text", [tool()]),
        ],
    )
    await transport.handle_claude_hook(display("First."))
    await transport.handle_claude_hook(pretool(path))
    assert [b["type"] for b in blocks(events)] == ["text", "tool_use"]


@pytest.mark.parametrize(
    "case",
    [
        "foreign_session",
        "foreign_path",
        "absent_tool",
        "after_tool",
        "child",
        "nested_child",
        "read_budget",
        "malformed_parent",
    ],
)
async def test_unproved_native_prefix_never_invents_text_or_delays_tool(tmp_path, case):
    transport, events = await probe(tmp_path)
    path = tmp_path / f"{NATIVE}.jsonl"
    rows = [
        native_row("text", None, [{"type": "text", "text": "PRIVATE_PREFIX"}]),
        native_row("tool", "text", [tool()]),
    ]
    hook = pretool(path)
    if case == "foreign_session":
        for row in rows:
            row["sessionId"] = "different-session"
    elif case == "foreign_path":
        path = tmp_path / "foreign.jsonl"
        hook["transcript_path"] = str(path)
    elif case == "absent_tool":
        hook["tool_use_id"] = "absent"
    elif case == "after_tool":
        rows = [native_row("tool", None, [tool(), {"type": "text", "text": "AFTER"}])]
    elif case == "child":
        hook["agent_id"] = "worker"
    elif case == "nested_child":
        transport._active_subagent_stack.append("worker-task")
    elif case == "malformed_parent":
        rows[1]["parentUuid"] = []
    elif case == "read_budget":
        rows[0]["message"]["content"][0]["text"] = "x" * (2 * 1024 * 1024)
    write_native(path, rows)
    await transport.handle_claude_hook(hook)
    assert [b["type"] for b in blocks(events)] == ["tool_use"]


async def test_concurrent_late_display_waits_for_native_prefix_proof(tmp_path, monkeypatch):
    transport, events = await probe(tmp_path)
    path = tmp_path / f"{NATIVE}.jsonl"
    write_native(
        path,
        [
            native_row("text", None, [{"type": "text", "text": "First."}]),
            native_row("tool", "text", [tool()]),
        ],
    )
    entered, release = asyncio.Event(), asyncio.Event()
    original = transport._emit_native_text_before_tool

    async def suspended(payload):
        entered.set()
        await release.wait()
        await original(payload)

    monkeypatch.setattr(transport, "_emit_native_text_before_tool", suspended)
    pre = asyncio.create_task(transport.handle_claude_hook(pretool(path)))
    await entered.wait()
    late = asyncio.create_task(transport.handle_claude_hook(display("First.")))
    await asyncio.sleep(0)
    assert not late.done()
    release.set()
    await asyncio.gather(pre, late)
    assert [b["type"] for b in blocks(events)] == ["text", "tool_use"]


async def test_native_file_append_becomes_visible_before_bounded_deadline(tmp_path):
    transport, events = await probe(tmp_path)
    path = tmp_path / f"{NATIVE}.jsonl"
    path.write_text("")

    async def append_native():
        await asyncio.sleep(0.02)
        write_native(
            path,
            [
                native_row("text", None, [{"type": "text", "text": "Now visible."}]),
                native_row("tool", "text", [tool()]),
            ],
        )

    await asyncio.gather(transport.handle_claude_hook(pretool(path)), append_native())
    assert [b["type"] for b in blocks(events)] == ["text", "tool_use"]
    assert blocks(events)[0]["text"] == "Now visible."


async def test_missing_native_proof_has_finite_fallback_and_no_extra_text(tmp_path):
    transport, events = await probe(tmp_path)
    path = tmp_path / f"{NATIVE}.jsonl"
    path.write_text("")
    # This models a native writer waiting on the PreToolUse response itself.
    # A timeout around the caller catches any circular wait; no elapsed-sleep assertion.
    async with asyncio.timeout(0.5):
        await transport.handle_claude_hook(pretool(path))
    assert [b["type"] for b in blocks(events)] == ["tool_use"]
    assert not transport._text_hook_lock.locked()


async def test_cancelled_native_proof_releases_hook_lock_for_next_display(tmp_path, monkeypatch):
    transport, events = await probe(tmp_path)
    path = tmp_path / f"{NATIVE}.jsonl"
    path.write_text("")
    entered = asyncio.Event()
    original = transport._emit_native_text_before_tool

    async def notify(payload):
        entered.set()
        await original(payload)

    monkeypatch.setattr(transport, "_emit_native_text_before_tool", notify)
    task = asyncio.create_task(transport.handle_claude_hook(pretool(path)))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with asyncio.timeout(0.5):
        await transport.handle_claude_hook(display("Still connected."))
    assert [b["text"] for b in blocks(events)] == ["Still connected."]
    assert not transport._text_hook_lock.locked()
