"""PI coding agent's native JSONL RPC transport.

PI owns the agent loop, tools and native session file. Skuld owns admission,
capture and delivery. A prompt acknowledgement is NOT a consumption receipt:
only a native user message marks steering consumed. Text content indices are
scoped to their native assistant message, preserving text/tool interleaving.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from niuu.domain.reasoning import validate_effort
from niuu.ports.cli import CLITransport, TransportCapabilities

_TOOLS = {
    "bash": "Bash",
    "read": "Read",
    "write": "Write",
    "edit": "Edit",
    "grep": "Grep",
    "find": "Glob",
    "ls": "LS",
}


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        b["text"] for b in content if isinstance(b, dict) and isinstance(b.get("text"), str)
    )


class PiProtocolError(RuntimeError):
    """A rejected command or broken native RPC connection."""


class PiRpcTransport(CLITransport):
    """One persistent ``pi --mode rpc`` process per Forge session."""

    def __init__(
        self,
        workspace_dir: str,
        model: str = "",
        session_id: str | None = None,
        resume_session_id: str | None = None,
        initial_prompt: str = "",
        system_prompt: str = "",
        session_name: str = "",
        reasoning_effort: str = "",
        pi_bin: str = "pi",
        pi_agent_dir: str = "",
        pi_session_dir: str = "",
        pi_command_timeout_s: float = 30,
        pi_turn_timeout_s: float = 1800,
        pi_shutdown_timeout_s: float = 5,
        live_frame_max_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        super().__init__()
        self._workspace = workspace_dir
        self._model = model
        self._session_id = session_id or str(uuid.uuid4())
        self._resume = resume_session_id
        self._initial = initial_prompt
        self._system = system_prompt
        self._name = session_name
        self._effort = reasoning_effort
        self._binary = pi_bin
        self._agent_dir = pi_agent_dir
        self._session_dir = pi_session_dir or str(
            Path(workspace_dir) / ".skuld" / "pi" / self._session_id
        )
        self._command_timeout = pi_command_timeout_s
        self._turn_timeout = pi_turn_timeout_s
        self._shutdown_timeout = pi_shutdown_timeout_s
        self._max_frame = live_frame_max_bytes
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task | None = None
        self._stderr: asyncio.Task | None = None
        self._watchdog: asyncio.Task | None = None
        self._start_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future] = {}
        self._admissions: list[tuple[str, str | None, str | None]] = []
        self._questions: dict[str, dict] = {}
        self._turn_done: asyncio.Future | None = None
        self._active = False
        self._stopping = False
        self._ready = False
        self._last_result: dict | None = None
        self._message_id = ""
        self._tools: dict[str, str] = {}
        self._usage: dict[str, int | float] = {}
        self._error = ""
        self._final_text = ""

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def last_result(self) -> dict | None:
        return self._last_result

    @property
    def is_alive(self) -> bool:
        return self._process is not None and self._process.returncode is None

    @property
    def is_turn_active(self) -> bool:
        return self._active

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(
            session_resume=True,
            interrupt=True,
            set_effort=True,
            steer=True,
            steering_mode="native",
            set_model=True,
            permission_requests=True,
            slash_commands=True,
            skills=True,
        )

    async def start(self) -> None:
        async with self._start_lock:
            if self._ready and self.is_alive:
                return
            self._stopping = False
            Path(self._session_dir).mkdir(parents=True, exist_ok=True)
            command = [self._binary, "--mode", "rpc", "--session-dir", self._session_dir]
            command += (
                ["--session", self._resume] if self._resume else ["--session-id", self._session_id]
            )
            if self._model:
                command += ["--model", self._model]
            if self._system:
                command += ["--append-system-prompt", self._system]
            if self._name:
                command += ["--name", self._name]
            env = dict(os.environ)
            if self._agent_dir:
                env["PI_CODING_AGENT_DIR"] = self._agent_dir
            try:
                self._process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=self._workspace,
                    env=env,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    limit=self._max_frame,
                )
                self._reader = asyncio.create_task(self._read_loop())
                self._stderr = asyncio.create_task(self._drain_stderr())
                state = await self._command("get_state")
                native_model = state.get("model") or {}
                if not native_model:
                    raise PiProtocolError(
                        "PI has no authenticated model. Configure PI provider login."
                    )
                actual_model = f"{native_model['provider']}/{native_model['id']}"
                if self._model and actual_model != self._model:
                    raise PiProtocolError(f"PI selected {actual_model}, expected {self._model}")
                self._model = actual_model
                self._session_id = state["sessionId"]
                if self._effort:
                    await self.send_control("set_effort", effort=self._effort)
                self._ready = True
                await self._emit(
                    {
                        "type": "system",
                        "subtype": "init",
                        "session_id": self._session_id,
                        "model": self._model,
                        "tools": list(_TOOLS.values()),
                        "pi_session_file": state.get("sessionFile"),
                    }
                )
            except Exception as exc:
                await self._finish(str(exc), force=True)
                await self.stop()
                raise
        if self._initial:
            initial, self._initial = self._initial, ""
            await self.send_message(initial)

    async def _write(self, frame: dict) -> None:
        if not self.is_alive or not self._process or not self._process.stdin:
            raise PiProtocolError("PI RPC process is not running")
        encoded = (json.dumps(frame, ensure_ascii=False) + "\n").encode()
        if len(encoded) > self._max_frame:
            raise PiProtocolError("PI command exceeds configured frame limit")
        async with self._write_lock:
            self._process.stdin.write(encoded)
            await self._process.stdin.drain()

    async def _command(self, kind: str, **payload: Any) -> dict:
        request_id = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._write({"id": request_id, "type": kind, **payload})
            return await asyncio.wait_for(future, self._command_timeout)
        finally:
            self._pending.pop(request_id, None)

    async def _drain_stderr(self) -> None:
        # Drain bounded chunks without copying provider credentials/extension diagnostics
        # into the shared transcript. Protocol failures are surfaced on the result path.
        assert self._process and self._process.stderr
        while await self._process.stderr.read(4096):
            pass

    async def _read_loop(self) -> None:
        assert self._process and self._process.stdout
        error = "PI RPC stream closed unexpectedly"
        try:
            while line := await self._process.stdout.readline():
                frame = json.loads(line)
                if not isinstance(frame, dict):
                    raise PiProtocolError("PI RPC frame must be an object")
                if frame.get("type") == "response":
                    future = self._pending.get(frame.get("id"))
                    if future is None or future.done():
                        continue
                    if frame.get("success") is True:
                        future.set_result(frame.get("data") or {})
                        continue
                    future.set_exception(
                        PiProtocolError(str(frame.get("error") or "PI command rejected"))
                    )
                    continue
                await self._event(frame)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = f"PI RPC failed: {type(exc).__name__}"
        finally:
            self._ready = False
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(PiProtocolError(error))
            if not self._stopping:
                try:
                    await self._finish(error, force=self._active)
                finally:
                    # A malformed/closed stream can leave the OS process alive.
                    # Never spawn a replacement while that orphan still owns tools.
                    await self.stop()

    def _begin(self) -> None:
        if self._active:
            return
        self._active = True
        self._error = ""
        self._final_text = ""
        self._usage = {}
        self._tools = {}
        self._turn_done = asyncio.get_running_loop().create_future()
        self._watchdog = asyncio.create_task(self._watch_turn(self._turn_done))

    async def _watch_turn(self, done: asyncio.Future) -> None:
        try:
            await asyncio.wait_for(asyncio.shield(done), self._turn_timeout)
        except TimeoutError:
            await self._finish("PI turn exceeded configured timeout")
            await self.stop()

    async def _submit(
        self, content: str, msg_id: str | None, request_id: str | None
    ) -> asyncio.Future:
        if not self._ready or not self.is_alive:
            await self.start()
        self._begin()
        done = self._turn_done
        assert done is not None
        admission = (content, msg_id, request_id)
        self._admissions.append(admission)
        try:
            await self._command("prompt", message=content, streamingBehavior="steer")
        except Exception as exc:
            if admission in self._admissions:
                self._admissions.remove(admission)
            await self._finish(f"PI prompt was not accepted: {exc}")
            await self.stop()
            raise
        return done

    async def send_message(
        self, content: str, *, msg_id: str | None = None, request_id: str | None = None
    ) -> None:
        done = await self._submit(content, msg_id, request_id)
        await asyncio.shield(done)

    async def _consume(self, message: dict) -> None:
        content = _text(message.get("content"))
        for index, (expected, msg_id, request_id) in enumerate(self._admissions):
            if content != expected:
                continue
            self._admissions.pop(index)
            if msg_id:
                await self._emit(
                    {
                        "type": "user_consumed",
                        "event_type": "pi.user.consumed",
                        "msg_id": msg_id,
                        "request_id": request_id,
                    }
                )
            return

    async def _assistant(self, blocks: list[dict]) -> None:
        await self._emit(
            {
                "type": "assistant",
                "message": {"model": self._model, "content": blocks},
                "content": blocks,
            }
        )

    def _item(self, index: int) -> str:
        return f"{self._message_id}:{index}"

    async def _message_end(self, message: dict) -> None:
        if message.get("role") != "assistant":
            return
        blocks = []
        for index, block in enumerate(message.get("content") or []):
            kind = block.get("type")
            if kind == "text":
                blocks.append(
                    {
                        "type": "text",
                        "id": self._item(index),
                        "text": block.get("text", ""),
                        "complete": True,
                    }
                )
            if kind == "toolCall":
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": block["id"],
                        "name": _TOOLS.get(block["name"], block["name"]),
                        "input": block.get("arguments") or {},
                    }
                )
        await self._assistant(blocks)
        self._final_text = _text(message.get("content"))
        usage = message.get("usage") or {}
        for source, target in (
            ("input", "input_tokens"),
            ("output", "output_tokens"),
            ("cacheRead", "cache_read_input_tokens"),
            ("cacheWrite", "cache_creation_input_tokens"),
        ):
            self._usage[target] = self._usage.get(target, 0) + int(usage.get(source) or 0)
        self._usage["cost"] = self._usage.get("cost", 0) + float(
            (usage.get("cost") or {}).get("total") or 0
        )
        self._error = ""
        if message.get("stopReason") in {"error", "aborted"}:
            self._error = message.get("errorMessage") or message["stopReason"]

    async def _event(self, frame: dict) -> None:
        kind = frame.get("type")
        if kind == "agent_start":
            self._begin()
            return
        if kind == "message_start":
            message = frame.get("message") or {}
            if message.get("role") == "user":
                await self._consume(message)
            if message.get("role") == "assistant":
                self._message_id = (
                    f"pi:{self._session_id}:{message.get('timestamp') or uuid.uuid4()}"
                )
            return
        if kind == "message_update":
            event = frame.get("assistantMessageEvent") or {}
            subtype = event.get("type")
            index = event.get("contentIndex", 0)
            context = {"item_id": self._item(index), "index": index}
            if subtype == "text_start":
                await self._emit(
                    {
                        "type": "content_block_start",
                        **context,
                        "content_block": {"type": "text", "id": self._item(index)},
                    }
                )
            if subtype == "text_delta":
                await self._emit(
                    {
                        "type": "content_block_delta",
                        **context,
                        "delta": {"type": "text_delta", "text": event.get("delta", "")},
                    }
                )
            if subtype == "thinking_delta":
                await self._emit(
                    {
                        "type": "content_block_delta",
                        "delta": {"type": "thinking_delta", "thinking": event.get("delta", "")},
                    }
                )
            if subtype == "text_end":
                await self._emit({"type": "content_block_stop", **context})
            return
        if kind == "message_end":
            await self._message_end(frame.get("message") or {})
            return
        if kind == "tool_execution_start":
            tool_id = frame["toolCallId"]
            self._tools[tool_id] = datetime.now(UTC).isoformat()
            await self._assistant(
                [
                    {
                        "type": "tool_use",
                        "id": tool_id,
                        "name": _TOOLS.get(frame["toolName"], frame["toolName"]),
                        "input": frame.get("args") or {},
                        "started_at": self._tools[tool_id],
                    }
                ]
            )
            return
        if kind == "tool_execution_end":
            tool_id = frame["toolCallId"]
            block = {
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": (frame.get("result") or {}).get("content") or [],
                "is_error": bool(frame.get("isError")),
                "ended_at": datetime.now(UTC).isoformat(),
            }
            if tool_id in self._tools:
                block["started_at"] = self._tools.pop(tool_id)
            await self._emit({"type": "user", "message": {"content": [block]}, "content": [block]})
            return
        if kind == "agent_end":
            await self._finish(self._error)
            return
        if kind == "extension_ui_request":
            await self._question(frame)
            return
        # Preserve compaction, retry, extension status and cumulative tool progress
        # as native telemetry. It must not manufacture another assistant text item.
        await self._emit({"type": "system", "subtype": f"pi.{kind}", "pi": frame})

    async def _finish(self, error: str = "", *, force: bool = False) -> None:
        if not self._active and not force:
            return
        self._active = False
        for request_id in list(self._questions):
            self._questions.pop(request_id)
            await self._emit(
                {
                    "type": "ask_user_resolved",
                    "event_type": "ask_user.resolved",
                    "request_id": request_id,
                    "decision": "cancelled",
                    "accepted": False,
                }
            )
        self._admissions.clear()
        cost = self._usage.get("cost", 0)
        result = {
            "type": "result",
            "subtype": "error_during_execution" if error else "success",
            "is_error": bool(error),
            "result": error or self._final_text,
            "session_id": self._session_id,
            "model": self._model,
            "usage": {k: v for k, v in self._usage.items() if k != "cost"},
            "total_cost_usd": cost,
        }
        self._last_result = result
        await self._emit(result)
        if self._turn_done and not self._turn_done.done():
            self._turn_done.set_result(result)
        if self._watchdog and self._watchdog is not asyncio.current_task():
            self._watchdog.cancel()

    async def _question(self, frame: dict) -> None:
        method = frame.get("method")
        if method not in {"select", "confirm", "input", "editor"}:
            await self._emit({"type": "system", "subtype": "pi.extension_ui", "pi": frame})
            return
        request_id = frame["id"]
        self._questions[request_id] = frame
        options = frame.get("options") or (["Yes", "No"] if method == "confirm" else [])
        question = {
            "header": "PI",
            "question": frame.get("title") or frame.get("message") or "PI input",
            "options": [{"label": str(o), "description": ""} for o in options],
            "multiSelect": False,
        }
        await self._emit(
            {
                "type": "ask_user_question",
                "event_type": "ask_user_question",
                "request_id": request_id,
                "tool_use_id": request_id,
                "questions": [question],
                "metadata": {"source": "pi_rpc"},
            }
        )

    async def _answer(self, request_id: str, answers: Any) -> None:
        question = self._questions.get(request_id)
        if question is None:
            raise PiProtocolError("PI question is no longer pending")
        value = next(iter(answers.values()), "") if isinstance(answers, dict) else answers
        # Forge clients send [{question_id, question, answer}], while direct
        # control clients can send a question-to-answer map or a scalar.
        if isinstance(value, list) and value and isinstance(value[0], dict):
            value = value[0].get("answer")
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        payload: dict = {"type": "extension_ui_response", "id": request_id}
        if value is None:
            payload["cancelled"] = True
        elif question["method"] == "confirm":
            payload["confirmed"] = str(value).lower() in {"yes", "true", "approve"}
        else:
            payload["value"] = str(value)
        await self._write(payload)
        self._questions.pop(request_id)
        await self._emit(
            {
                "type": "ask_user_resolved",
                "event_type": "ask_user.resolved",
                "request_id": request_id,
                "decision": "cancelled" if value is None else "answered",
                "accepted": value is not None,
            }
        )

    async def send_control_response(self, request_id: str, response: dict) -> None:
        await self._answer(request_id, response.get("answers", response.get("value")))

    async def send_control(self, subtype: str, **kwargs: Any) -> None:
        if subtype == "set_effort":
            effort = validate_effort(
                str(kwargs.get("effort") or ""), (await self.get_effort())["levels"]
            )
            await self._command("set_thinking_level", level=effort)
            result = await self._command("get_state")
            if result.get("thinkingLevel") != effort:
                raise PiProtocolError(f"PI did not accept effort {effort}: {result}")
            self._effort = effort
            return
        if subtype == "interrupt":
            await self.interrupt()
            return
        if subtype in {"redirect", "steer", "steer_active_turn"}:
            await self._submit(
                str(kwargs.get("content") or ""), kwargs.get("msg_id"), kwargs.get("request_id")
            )
            return
        if subtype == "ask_user_answer":
            await self._answer(str(kwargs.get("request_id") or ""), kwargs.get("answers"))
            return
        if subtype == "set_model":
            model = str(kwargs.get("model") or "")
            provider, separator, model_id = model.partition("/")
            if not separator or not provider or not model_id:
                raise PiProtocolError("PI model must use provider/model syntax")
            await self._command("set_model", provider=provider, modelId=model_id)
            self._model = model
            return
        raise PiProtocolError(f"PI does not support control {subtype}")

    async def get_effort(self) -> dict:
        state = await self._command("get_state")
        model = state.get("model") or {}
        mapping = model.get("thinkingLevelMap") or {}
        levels = ["off"]
        if model.get("reasoning"):
            levels = [
                level
                for level in ("off", "minimal", "low", "medium", "high", "xhigh", "max")
                if mapping.get(level, level if level not in {"xhigh", "max"} else None) is not None
            ]
        return {
            "current": state.get("thinkingLevel", "off"),
            "levels": levels,
            "mutable": True,
            "applies_to": "next_model_call",
        }

    async def discover_slash_commands(self, *, refresh: bool = False) -> list[dict]:
        del refresh
        if not self._ready:
            return []
        return (await self._command("get_commands")).get("commands") or []

    async def interrupt(self) -> None:
        if not self.is_alive:
            return
        try:
            await self._command("clear_queue")
            await self._command("abort")
        except Exception:
            await self._finish("PI interrupt failed; native process stopped")
            await self.stop()
            raise

    async def stop(self) -> None:
        self._stopping = True
        self._ready = False
        await self._finish("PI session stopped")
        if self.is_alive and self._process:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), self._shutdown_timeout)
            except TimeoutError:
                self._process.kill()
                await self._process.wait()
        tasks = [
            task
            for task in (self._reader, self._stderr, self._watchdog)
            if task and task is not asyncio.current_task()
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
