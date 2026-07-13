"""CLI-backed executor adapter for Ravn personas."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import sys
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from niuu.adapters.cli import CliTurnRunner
from niuu.ports.cli import CLITransport
from niuu.utils import import_class
from ravn.domain.checkpoint import InterruptReason
from ravn.domain.events import RavnEvent
from ravn.domain.models import Message, Session, TokenUsage, ToolCall, ToolResult, TurnResult
from ravn.ports.channel import ChannelPort
from ravn.ports.checkpoint import CheckpointPort
from ravn.ports.executor import ExecutionAgentPort, ExecutorPort

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _TransportBinding:
    """Resolved transport class plus constructor capabilities."""

    cls: type[CLITransport]
    supports_system_prompt: bool


def _delegates_permission_config_to_cli(cls: type[CLITransport]) -> bool:
    """Return true for transports whose native CLI profile owns permissions."""
    return (
        cls.__module__ == "skuld.transports.codex_ws" and cls.__name__ == "CodexWebSocketTransport"
    )


def _sum_model_usage(raw: dict | None) -> TokenUsage:
    """Convert transport ``modelUsage`` payloads into ``TokenUsage``."""
    if not isinstance(raw, dict):
        return TokenUsage(input_tokens=0, output_tokens=0)

    model_usage = raw.get("modelUsage", {})
    if not isinstance(model_usage, dict):
        return TokenUsage(input_tokens=0, output_tokens=0)

    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0
    cache_write_tokens = 0
    thinking_tokens = 0

    for usage in model_usage.values():
        if not isinstance(usage, dict):
            continue
        input_tokens += int(usage.get("inputTokens", 0) or 0)
        output_tokens += int(usage.get("outputTokens", 0) or 0)
        cache_read_tokens += int(usage.get("cacheReadInputTokens", 0) or 0)
        cache_write_tokens += int(usage.get("cacheCreationInputTokens", 0) or 0)
        thinking_tokens += int(usage.get("thinkingTokens", 0) or 0)

    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        thinking_tokens=thinking_tokens,
    )


def _is_ting_workflow_tool(tool_name: str) -> bool:
    name = tool_name.replace("-", "_")
    return name.startswith("ting_") or any(
        marker in name for marker in ("__ting_", "/ting_", ".ting_")
    )


def _decode_tool_result_json(content: str) -> dict[str, Any] | None:
    text = content.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    wrapped_content = data.get("content")
    if isinstance(wrapped_content, list):
        for item in wrapped_content:
            if not isinstance(item, dict):
                continue
            item_text = item.get("text")
            if not isinstance(item_text, str) or not item_text.strip():
                continue
            nested = _decode_tool_result_json(item_text)
            if nested is not None:
                return nested

    return data


class CliTransportAgent(ExecutionAgentPort):
    """Agent-shaped wrapper that executes turns through a CLI transport."""

    def __init__(
        self,
        *,
        transport_binding: _TransportBinding,
        transport_kwargs: dict[str, Any],
        channel: ChannelPort,
        system_prompt: str,
        session: Session,
        model: str,
        max_iterations: int,
        checkpoint_port: CheckpointPort | None,
        task_id: str,
        persona: str,
        preloaded_tools: Iterable[object] = (),
        session_join_manager: Any | None = None,
    ) -> None:
        self._transport_binding = transport_binding
        self._transport_kwargs = dict(transport_kwargs)
        self._channel = channel
        self._system_prompt = system_prompt
        self._session = session
        self._model = model
        self._max_iterations = max_iterations
        self._checkpoint_port = checkpoint_port
        self._task_id = task_id
        self._persona = persona
        self._source_id = f"ravn-cli-{uuid.uuid4().hex[:8]}"
        self._transport: CLITransport | None = None
        self._turn_runner: CliTurnRunner | None = None
        self._started = False
        self._tools = {
            getattr(tool, "name", f"tool_{idx}"): tool for idx, tool in enumerate(preloaded_tools)
        }
        self._session_join_manager = session_join_manager
        self._interrupt_reason: InterruptReason | None = None
        self._current_tool_names: dict[str, str] = {}
        self._turn_tool_calls: list[ToolCall] = []
        self._turn_tool_results: list[ToolResult] = []

    @property
    def session(self) -> Session:
        return self._session

    @property
    def tools(self) -> list[object]:
        return list(self._tools.values())

    @property
    def max_iterations(self) -> int:
        return self._max_iterations

    @property
    def llm_adapter_name(self) -> str:
        return self._transport_binding.cls.__name__

    @property
    def checkpoint_port(self) -> CheckpointPort | None:
        return self._checkpoint_port

    @property
    def task_id(self) -> str:
        return self._task_id

    @property
    def supports_steering(self) -> bool:
        return self._transport is not None and self._transport.capabilities.steer

    @property
    def steering_mode(self) -> str:
        if self._transport is None:
            return "none"
        return self._transport.capabilities.steering_mode

    def interrupt(self, reason: InterruptReason) -> None:
        if self._interrupt_reason is None:
            self._interrupt_reason = reason

        if self._transport is None or not self._transport.capabilities.interrupt:
            return

        asyncio.create_task(self._transport.send_control("interrupt"))

    async def steer(self, content: str) -> bool:
        transport = self._transport
        if (
            transport is None
            or not transport.capabilities.steer
            or not transport.is_turn_active
            or not content.strip()
        ):
            return False
        await transport.send_control("steer", content=content)
        return True

    async def run_turn(self, user_input: str) -> TurnResult:
        if self._interrupt_reason is not None:
            raise RuntimeError(f"turn interrupted: {self._interrupt_reason}")

        await self._ensure_transport()
        assert self._transport is not None
        assert self._turn_runner is not None

        correlation_id = str(self._session.id)
        self._current_tool_names.clear()
        self._turn_tool_calls = []
        self._turn_tool_results = []
        self._session.add_message(Message(role="user", content=user_input))

        prompt = self._build_prompt(user_input)
        response_text = await self._turn_runner.run_prompt(prompt, request_id=correlation_id)

        result_payload = self._transport.last_result or {}
        self._raise_if_transport_failed(result_payload)

        usage = _sum_model_usage(result_payload)
        self._session.add_message(Message(role="assistant", content=response_text))
        self._session.record_turn(usage)

        await self._channel.emit(
            RavnEvent.response(
                source=self._source_id,
                text=response_text,
                correlation_id=correlation_id,
                session_id=correlation_id,
                task_id=self._task_id,
            )
        )

        return TurnResult(
            response=response_text,
            tool_calls=list(self._turn_tool_calls),
            tool_results=list(self._turn_tool_results),
            usage=usage,
        )

    async def _ensure_transport(self) -> None:
        if self._transport is not None:
            return

        self._transport = self._create_transport()
        self._transport.on_event(self._handle_transport_event)
        self._turn_runner = CliTurnRunner(self._transport)

        if not self._started:
            await self._transport.start()
            self._started = True

    def _create_transport(self) -> CLITransport:
        kwargs = dict(self._transport_kwargs)
        sig = inspect.signature(self._transport_binding.cls)
        filtered = {key: value for key, value in kwargs.items() if key in sig.parameters}
        return self._transport_binding.cls(**filtered)

    def _build_prompt(self, user_input: str) -> str:
        assert self._transport is not None

        if self._transport.capabilities.session_resume and (
            self._transport.session_id or self._transport_binding.supports_system_prompt
        ):
            return user_input

        transcript = self._render_transcript()
        if transcript:
            return (
                f"System instructions:\n{self._system_prompt}\n\n"
                f"Conversation so far:\n{transcript}\n\n"
                f"User:\n{user_input}"
            )
        return f"System instructions:\n{self._system_prompt}\n\nUser:\n{user_input}"

    def _render_transcript(self) -> str:
        lines: list[str] = []
        for message in self._session.messages[:-1]:
            label = "User" if message.role == "user" else "Assistant"
            lines.append(f"{label}:\n{self._stringify_message_content(message.content)}")
        return "\n\n".join(lines)

    def _stringify_message_content(self, content: str | list[dict]) -> str:
        if isinstance(content, str):
            return content
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if "text" in block and isinstance(block["text"], str):
                parts.append(block["text"])
            elif "content" in block and isinstance(block["content"], str):
                parts.append(block["content"])
        return "\n".join(parts)

    def _raise_if_transport_failed(self, payload: dict[str, Any]) -> None:
        stop_reason = str(payload.get("stop_reason", "") or payload.get("subtype", "")).lower()
        is_error = bool(payload.get("is_error", False))
        if stop_reason == "error" or is_error:
            message = payload.get("result") or payload.get("content") or "CLI transport failed"
            raise RuntimeError(str(message))

    async def _handle_transport_event(self, data: dict) -> None:
        correlation_id = str(self._session.id)
        event_type = str(data.get("type", ""))

        if event_type == "content_block_delta":
            await self._emit_delta(data, correlation_id)
            return

        if event_type == "content_block_start":
            block = data.get("content_block")
            if isinstance(block, dict) and block.get("type") == "tool_result":
                await self._record_tool_result_block(block, correlation_id)
            return

        if event_type in {"assistant", "message"}:
            await self._emit_message_event(data, correlation_id)
            return

        if event_type == "user":
            await self._emit_user_event(data, correlation_id)
            return

        if event_type == "error":
            message = str(data.get("content") or data.get("message") or data)
            await self._channel.emit(
                RavnEvent.error(
                    source=self._source_id,
                    message=message,
                    correlation_id=correlation_id,
                    session_id=correlation_id,
                    task_id=self._task_id,
                )
            )

    async def _emit_delta(self, data: dict, correlation_id: str) -> None:
        delta = data.get("delta", {})
        if not isinstance(delta, dict):
            return

        text = delta.get("text")
        if isinstance(text, str) and text:
            await self._channel.emit(
                RavnEvent.thought(
                    source=self._source_id,
                    text=text,
                    correlation_id=correlation_id,
                    session_id=correlation_id,
                    task_id=self._task_id,
                )
            )
            return

        thinking = delta.get("thinking")
        if isinstance(thinking, str) and thinking:
            await self._channel.emit(
                RavnEvent.thinking(
                    source=self._source_id,
                    text=thinking,
                    correlation_id=correlation_id,
                    session_id=correlation_id,
                    task_id=self._task_id,
                )
            )

    async def _emit_message_event(self, data: dict, correlation_id: str) -> None:
        message = data.get("message", data)
        if not isinstance(message, dict):
            return

        content = message.get("content", data.get("content", ""))
        if isinstance(content, str) and content:
            await self._channel.emit(
                RavnEvent.thought(
                    source=self._source_id,
                    text=content,
                    correlation_id=correlation_id,
                    session_id=correlation_id,
                    task_id=self._task_id,
                )
            )
            return

        if isinstance(content, list):
            for block in content:
                await self._emit_content_block(block, correlation_id)

    async def _emit_user_event(self, data: dict, correlation_id: str) -> None:
        content = data.get("content", "")
        if not isinstance(content, list):
            return

        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            await self._record_tool_result_block(block, correlation_id)

    async def _record_tool_result_block(self, block: dict, correlation_id: str) -> None:
        tool_use_id = str(block.get("tool_use_id", ""))
        tool_name = self._current_tool_names.get(tool_use_id, "")
        result = str(block.get("content", ""))
        is_error = bool(block.get("is_error", False))
        self._turn_tool_results.append(
            ToolResult(tool_call_id=tool_use_id, content=result, is_error=is_error)
        )
        if not is_error:
            await self._join_launched_workflow_session(tool_name, result)
        await self._channel.emit(
            RavnEvent.tool_result(
                source=self._source_id,
                tool_name=tool_name,
                result=result,
                correlation_id=correlation_id,
                session_id=correlation_id,
                task_id=self._task_id,
                is_error=is_error,
            )
        )

    async def _join_launched_workflow_session(self, tool_name: str, result: str) -> None:
        manager = self._session_join_manager
        if manager is None or not _is_ting_workflow_tool(tool_name):
            return
        payload = _decode_tool_result_json(result)
        if not payload:
            return
        if isinstance(payload.get("data"), dict):
            payload = payload["data"]
        session_id = str(payload.get("sessionId") or payload.get("session_id") or "").strip()
        chat_endpoint = str(
            payload.get("chatEndpoint") or payload.get("chat_endpoint") or ""
        ).strip()
        if not session_id or not chat_endpoint:
            return
        task = asyncio.create_task(
            manager.join(session_id, chat_endpoint),
            name=f"join-workflow-session-{session_id}",
        )

        def _log_join_result(done: asyncio.Task) -> None:
            try:
                done.result()
            except Exception:
                logger.warning(
                    "CLI executor failed to join launched workflow session %s",
                    session_id,
                    exc_info=True,
                )

        task.add_done_callback(_log_join_result)

    async def _emit_content_block(self, block: dict, correlation_id: str) -> None:
        block_type = str(block.get("type", ""))
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str) and text:
                await self._channel.emit(
                    RavnEvent.thought(
                        source=self._source_id,
                        text=text,
                        correlation_id=correlation_id,
                        session_id=correlation_id,
                        task_id=self._task_id,
                    )
                )
            return

        if block_type != "tool_use":
            return

        tool_name = str(block.get("name", ""))
        tool_input = block.get("input", {})
        tool_use_id = str(block.get("id", f"tool_{len(self._turn_tool_calls) + 1}"))
        normalized_input = tool_input if isinstance(tool_input, dict) else {}
        self._current_tool_names[tool_use_id] = tool_name
        self._turn_tool_calls.append(
            ToolCall(id=tool_use_id, name=tool_name, input=normalized_input)
        )
        await self._channel.emit(
            RavnEvent.tool_start(
                source=self._source_id,
                tool_name=tool_name,
                tool_input=normalized_input,
                correlation_id=correlation_id,
                session_id=correlation_id,
                task_id=self._task_id,
            )
        )


class CliTransportExecutor(ExecutorPort):
    """Build a persona execution agent backed by a CLI transport."""

    def __init__(
        self,
        *,
        transport_adapter: str = "skuld.transports.subprocess.SubprocessTransport",
        transport_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self._transport_adapter = transport_adapter
        self._transport_kwargs = dict(transport_kwargs or {})
        cls = import_class(transport_adapter)
        sig = inspect.signature(cls)
        self._binding = _TransportBinding(
            cls=cls,
            supports_system_prompt="system_prompt" in sig.parameters,
        )

    def build(self, **kwargs: Any) -> ExecutionAgentPort:
        workspace_dir = str(kwargs.get("workspace_dir", ""))
        session: Session = kwargs["session"]
        task_id = str(kwargs.get("task_id") or session.id)
        permission_mode = str(kwargs.get("permission_mode", "workspace_write"))
        tools = list(kwargs.get("tools", []))
        transport_kwargs = {
            "workspace_dir": workspace_dir,
            "model": str(kwargs.get("model", "")),
            "session_id": str(session.id),
            "system_prompt": str(kwargs.get("system_prompt", "")),
            "initial_prompt": "",
        }
        if not _delegates_permission_config_to_cli(self._binding.cls):
            transport_kwargs["skip_permissions"] = permission_mode != "prompt"
        if "mcp_servers" in kwargs:
            transport_kwargs["mcp_servers"] = _with_ravn_tool_mcp_server(
                list(kwargs["mcp_servers"]),
                persona=str(kwargs.get("persona", "")),
                tools=tools,
            )
        transport_kwargs.update(self._transport_kwargs)

        return CliTransportAgent(
            transport_binding=self._binding,
            transport_kwargs=transport_kwargs,
            channel=kwargs["channel"],
            system_prompt=str(kwargs.get("system_prompt", "")),
            session=session,
            model=str(kwargs.get("model", "")),
            max_iterations=int(kwargs.get("max_iterations", 1)),
            checkpoint_port=kwargs.get("checkpoint_port"),
            task_id=task_id,
            persona=str(kwargs.get("persona", "")),
            preloaded_tools=tools,
            session_join_manager=kwargs.get("session_join_manager"),
        )


def _with_ravn_tool_mcp_server(
    mcp_servers: list[dict[str, Any]],
    *,
    persona: str,
    tools: list[object],
) -> list[dict[str, Any]]:
    if not tools or any(str(server.get("name") or "") == "ravn-tools" for server in mcp_servers):
        return mcp_servers

    env: dict[str, str] = {}
    if config := os.environ.get("RAVN_CONFIG"):
        env["RAVN_CONFIG"] = config
    if pythonpath := os.environ.get("PYTHONPATH"):
        env["PYTHONPATH"] = pythonpath

    args = ["-m", "ravn", "tool-mcp"]
    if persona:
        args.extend(["--persona", persona])

    return [
        *mcp_servers,
        {
            "name": "ravn-tools",
            "type": "stdio",
            "command": sys.executable,
            "args": args,
            "env": env,
            "required": True,
        },
    ]
