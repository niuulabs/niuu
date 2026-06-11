"""Tests for PersistentSubprocessTransport.

The transport spawns ``claude -p --input-format stream-json`` once per
session and reuses it across turns. Tests stub the asyncio subprocess so
we exercise the protocol logic (stdin write, stdout demultiplex, turn
completion via ``result`` events) without a real Claude CLI.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from skuld.transports.persistent_subprocess import PersistentSubprocessTransport


class _StubStream:
    """Async stream stub backed by a list of byte lines."""

    def __init__(self, lines: list[bytes]) -> None:
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        for line in lines:
            self._queue.put_nowait(line)

    def push(self, line: bytes) -> None:
        self._queue.put_nowait(line)

    def close(self) -> None:
        # Sentinel: empty bytes means EOF for readline.
        self._queue.put_nowait(b"")

    async def readline(self) -> bytes:
        return await self._queue.get()


class _StubStdin:
    def __init__(self) -> None:
        self.buf = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.buf.extend(data)

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def close(self) -> None:
        self.closed = True

    def is_closing(self) -> bool:
        return self.closed


def _make_proc(stdout_lines: list[bytes], pid: int = 1234) -> MagicMock:
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = None
    proc.stdout = _StubStream(stdout_lines)
    proc.stderr = _StubStream([b""])
    proc.stdin = _StubStdin()
    proc.terminate = MagicMock()
    proc.wait = AsyncMock(return_value=0)
    return proc


def _result_line(text: str) -> bytes:
    return json.dumps({"type": "result", "subtype": "success", "result": text}).encode() + b"\n"


def _assistant_line(text: str) -> bytes:
    return (
        json.dumps(
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
            }
        ).encode()
        + b"\n"
    )


def _system_line(session_id: str) -> bytes:
    return (
        json.dumps({"type": "system", "subtype": "init", "session_id": session_id}).encode() + b"\n"
    )


@pytest.mark.asyncio
async def test_send_message_returns_when_result_event_arrives(tmp_path) -> None:
    """send_message blocks until the corresponding result event."""
    proc = _make_proc(
        [
            _system_line("sess-abc"),
            _assistant_line("ALPHA"),
            _result_line("ALPHA"),
        ]
    )
    transport = PersistentSubprocessTransport(str(tmp_path), initial_prompt="")

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        await transport.send_message("hello")

    assert b'"content": "hello"' in proc.stdin.buf or b'"content":"hello"' in proc.stdin.buf
    assert transport.session_id == "sess-abc"
    assert transport.last_result is not None
    assert transport.last_result.get("result") == "ALPHA"
    await transport.stop()


@pytest.mark.asyncio
async def test_two_turns_reuse_same_process(tmp_path) -> None:
    """Two send_message calls write to the same stdin without respawning."""
    proc = _make_proc(
        [
            _system_line("sess-multi"),
            _assistant_line("ALPHA"),
            _result_line("ALPHA"),
            # Second turn arrives later
        ]
    )
    spawn = AsyncMock(return_value=proc)
    transport = PersistentSubprocessTransport(str(tmp_path), initial_prompt="")

    with patch("asyncio.create_subprocess_exec", spawn):
        await transport.send_message("first")
        # Now push the second turn's events
        proc.stdout.push(_assistant_line("BETA"))
        proc.stdout.push(_result_line("BETA"))
        await transport.send_message("second")

    assert spawn.await_count == 1, "should spawn Claude only once across turns"
    body = bytes(proc.stdin.buf)
    assert b'"content": "first"' in body or b'"content":"first"' in body
    assert b'"content": "second"' in body or b'"content":"second"' in body
    await transport.stop()


@pytest.mark.asyncio
async def test_events_fan_out_to_callback(tmp_path) -> None:
    """Assistant and other events get forwarded to the on_event callback."""
    proc = _make_proc(
        [
            _system_line("sess-cb"),
            _assistant_line("hi"),
            _result_line("hi"),
        ]
    )
    received: list[dict] = []

    async def cb(event: dict) -> None:
        received.append(event)

    transport = PersistentSubprocessTransport(str(tmp_path), initial_prompt="")
    transport.on_event(cb)

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        await transport.send_message("hi")

    types = [e.get("type") for e in received]
    assert "system" in types
    assert "assistant" in types
    assert "result" in types
    await transport.stop()


@pytest.mark.asyncio
async def test_start_sends_initial_prompt(tmp_path) -> None:
    """start() with an initial_prompt writes it to stdin and waits for result."""
    proc = _make_proc(
        [
            _system_line("sess-init"),
            _assistant_line("ack"),
            _result_line("ack"),
        ]
    )
    transport = PersistentSubprocessTransport(str(tmp_path), initial_prompt="run setup")

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        await transport.start()

    body = bytes(proc.stdin.buf)
    assert b"run setup" in body
    await transport.stop()


@pytest.mark.asyncio
async def test_start_without_initial_prompt_does_not_write(tmp_path) -> None:
    """start() with no initial_prompt only spawns; no stdin write."""
    proc = _make_proc([])
    transport = PersistentSubprocessTransport(str(tmp_path), initial_prompt="")

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        await transport.start()

    assert proc.stdin.buf == bytearray()
    await transport.stop()


@pytest.mark.asyncio
async def test_start_injects_tracker_shim_env(tmp_path) -> None:
    proc = _make_proc([])
    transport = PersistentSubprocessTransport(str(tmp_path), initial_prompt="")
    shim_env = {
        "PATH": f"{tmp_path}/.skuld-tools/bin:/usr/bin",
        "RAVN_WORKSPACE_DIR": str(tmp_path),
    }

    with (
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)) as spawn,
        patch(
            "skuld.transports.persistent_subprocess.ensure_codex_tool_shims",
            return_value=(tmp_path / ".skuld-tools" / "bin", shim_env),
        ),
    ):
        await transport.start()

    env = spawn.call_args.kwargs["env"]
    assert env["PATH"] == shim_env["PATH"]
    assert env["RAVN_WORKSPACE_DIR"] == str(tmp_path)
    await transport.stop()


@pytest.mark.asyncio
async def test_send_message_concurrency_is_serialized(tmp_path) -> None:
    """Two concurrent send_message calls run sequentially under the lock."""
    proc = _make_proc(
        [
            _system_line("sess-concurrent"),
            _result_line("first done"),
        ]
    )
    transport = PersistentSubprocessTransport(str(tmp_path), initial_prompt="")

    async def send_second() -> None:
        # Tiny delay so the first call grabs the lock; then queue events
        # for the second turn after a moment.
        await asyncio.sleep(0.01)
        await transport.send_message("second")

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        first = asyncio.create_task(transport.send_message("first"))
        second = asyncio.create_task(send_second())
        # Wait until first turn is awaiting the result event, then push it
        # plus the result event for the second turn.
        await asyncio.sleep(0.05)
        proc.stdout.push(_result_line("second done"))
        await asyncio.gather(first, second)

    # Both writes landed in order.
    body = bytes(proc.stdin.buf)
    first_idx = body.find(b'"first"')
    second_idx = body.find(b'"second"')
    assert 0 <= first_idx < second_idx, "first should be written before second"
    await transport.stop()


@pytest.mark.asyncio
async def test_stop_closes_stdin(tmp_path) -> None:
    proc = _make_proc([])
    transport = PersistentSubprocessTransport(str(tmp_path), initial_prompt="")

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        await transport.start()
        await transport.stop()

    assert proc.stdin.closed is True


@pytest.mark.asyncio
async def test_capabilities() -> None:
    transport = PersistentSubprocessTransport("/tmp", initial_prompt="")
    caps = transport.capabilities
    assert caps.session_resume is True
    assert caps.interrupt is False
    assert caps.cli_websocket is False


def test_seeded_resume_id_makes_first_spawn_resume() -> None:
    """An imported session id is passed as ``--resume`` on the very first spawn."""
    transport = PersistentSubprocessTransport(
        "/tmp",
        system_prompt="ignored when resuming",
        resume_session_id="2e877b9f-4b8a-4d46-8f00-03f6163addd5",
    )

    cmd = transport._build_command()

    resume_index = cmd.index("--resume")
    assert cmd[resume_index + 1] == "2e877b9f-4b8a-4d46-8f00-03f6163addd5"
    assert "--append-system-prompt" not in cmd
    assert transport.session_id == "2e877b9f-4b8a-4d46-8f00-03f6163addd5"


def test_no_resume_flag_without_seed() -> None:
    transport = PersistentSubprocessTransport("/tmp", system_prompt="be helpful")

    cmd = transport._build_command()

    assert "--resume" not in cmd
    assert "--append-system-prompt" in cmd


@pytest.mark.asyncio
async def test_seeded_resume_skips_initial_prompt(tmp_path) -> None:
    """On resume the prior conversation already contains the initial prompt —
    replaying it would double-seed history."""
    proc = _make_proc([])
    transport = PersistentSubprocessTransport(
        str(tmp_path),
        initial_prompt="kick off the task",
        resume_session_id="sess-resume-1",
    )

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        await transport.start()

    assert proc.stdin.buf == bytearray()
    await transport.stop()


def test_flag_off_keeps_bypass_permissions_and_no_control_protocol() -> None:
    """Default: classic bypassPermissions behavior, no stdio permission tool."""
    transport = PersistentSubprocessTransport("/tmp", skip_permissions=True)

    cmd = transport._build_command()

    assert "--permission-mode" in cmd
    assert "bypassPermissions" in cmd
    assert "--permission-prompt-tool" not in cmd


def test_flag_on_routes_permissions_over_stdio() -> None:
    """SKULD__ASK_USER_QUESTION_ENABLED routes permissions over the control
    protocol so AskUserQuestion reaches a human; bypassPermissions would
    auto-dismiss it."""
    transport = PersistentSubprocessTransport(
        "/tmp",
        skip_permissions=True,
        ask_user_question_enabled=True,
    )

    cmd = transport._build_command()

    assert "--permission-prompt-tool" in cmd
    assert "stdio" in cmd
    assert "--permission-mode" not in cmd


class TestPermissionControlProtocol:
    """The stdio control protocol behind SKULD__ASK_USER_QUESTION_ENABLED."""

    def _transport_with_stdin(self, tmp_path) -> tuple[PersistentSubprocessTransport, MagicMock]:
        transport = PersistentSubprocessTransport(str(tmp_path), ask_user_question_enabled=True)
        proc = _make_proc([])
        transport._process = proc
        return transport, proc

    @pytest.mark.asyncio
    async def test_can_use_tool_allows_ordinary_tools(self, tmp_path) -> None:
        transport, proc = self._transport_with_stdin(tmp_path)

        await transport._handle_control_request(
            {
                "type": "control_request",
                "request_id": "req-1",
                "request": {
                    "subtype": "can_use_tool",
                    "tool_name": "Write",
                    "input": {"file_path": "/tmp/x"},
                },
            }
        )

        written = json.loads(bytes(proc.stdin.buf).decode())
        assert written["response"]["subtype"] == "success"
        assert written["response"]["response"]["behavior"] == "allow"
        assert written["response"]["response"]["updatedInput"] == {"file_path": "/tmp/x"}

    @pytest.mark.asyncio
    async def test_unsupported_subtype_returns_error(self, tmp_path) -> None:
        transport, proc = self._transport_with_stdin(tmp_path)

        await transport._handle_control_request(
            {"request_id": "req-2", "request": {"subtype": "hook_callback"}}
        )

        written = json.loads(bytes(proc.stdin.buf).decode())
        assert written["response"]["subtype"] == "error"
        assert "Unsupported" in written["response"]["error"]

    @pytest.mark.asyncio
    async def test_ask_user_question_blocks_until_answered(self, tmp_path) -> None:
        """The full HITL loop: question emitted to clients, turn blocks, the
        ask_user_answer control resolves it as a deny-with-message the model
        reads."""
        transport, proc = self._transport_with_stdin(tmp_path)
        events: list[dict] = []

        async def collect(event: dict) -> None:
            events.append(event)

        transport.on_event(collect)

        task = asyncio.create_task(
            transport._handle_control_request(
                {
                    "request_id": "req-3",
                    "request": {
                        "subtype": "can_use_tool",
                        "tool_name": "AskUserQuestion",
                        "tool_use_id": "toolu_1",
                        "input": {
                            "questions": [
                                {
                                    "header": "Color",
                                    "question": "Pick one",
                                    "options": ["red", "blue"],
                                }
                            ]
                        },
                    },
                }
            )
        )
        for _ in range(50):
            await asyncio.sleep(0)
            if events:
                break
        question = events[0]
        assert question["type"] == "ask_user_question"
        assert not task.done(), "the turn must block until a client answers"

        await transport.send_control(
            "ask_user_answer",
            request_id=question["request_id"],
            answers=[{"answer": "blue"}],
        )
        await asyncio.wait_for(task, timeout=2)

        written = json.loads(bytes(proc.stdin.buf).decode())
        response = written["response"]["response"]
        assert response["behavior"] == "deny"
        assert "Color: blue" in response["message"]

    @pytest.mark.asyncio
    async def test_ask_user_question_without_questions_denies(self, tmp_path) -> None:
        transport, _ = self._transport_with_stdin(tmp_path)

        response = await transport._answer_ask_user_question({}, "toolu_2")

        assert response["behavior"] == "deny"
        assert "No questions" in response["message"]

    @pytest.mark.asyncio
    async def test_unknown_answer_request_id_is_ignored(self, tmp_path) -> None:
        transport, _ = self._transport_with_stdin(tmp_path)
        await transport.send_control("ask_user_answer", request_id="nope", answers=[])

    @pytest.mark.asyncio
    async def test_initialize_handshake_resolves_on_control_response(self, tmp_path) -> None:
        transport, proc = self._transport_with_stdin(tmp_path)

        task = asyncio.create_task(transport._send_initialize())
        for _ in range(50):
            await asyncio.sleep(0)
            if proc.stdin.buf:
                break
        sent = json.loads(bytes(proc.stdin.buf).decode())
        assert sent["request"]["subtype"] == "initialize"

        transport._handle_control_response(
            {
                "type": "control_response",
                "response": {"subtype": "success", "request_id": sent["request_id"]},
            }
        )
        await asyncio.wait_for(task, timeout=2)

    def test_control_response_error_sets_exception(self, tmp_path) -> None:
        transport = PersistentSubprocessTransport(str(tmp_path))
        loop = asyncio.new_event_loop()
        try:
            fut = loop.create_future()
            transport._pending_control["req-err"] = fut
            transport._handle_control_response(
                {"response": {"subtype": "error", "request_id": "req-err", "error": "boom"}}
            )
            assert fut.done() and isinstance(fut.exception(), Exception)
        finally:
            loop.close()


class TestAnswerFormatting:
    def test_multi_question_multi_answer(self) -> None:
        from skuld.transports.persistent_subprocess import _format_answer_message

        text = _format_answer_message(
            [
                {"header": "Color", "question": "Pick"},
                {"question": "Size?"},
                {"header": "Extras"},
            ],
            [{"answer": "blue"}, {"answer": ["s", "m"]}],
        )

        assert "- Color: blue" in text
        assert "- Size?: s, m" in text
        assert "- Extras: (no answer)" in text

    def test_non_list_answers_tolerated(self) -> None:
        from skuld.transports.persistent_subprocess import _format_answer_message

        text = _format_answer_message([{"header": "Q"}], "garbage")
        assert "- Q: (no answer)" in text
