"""SDKTransport — Claude SDK adapter with stream-json compatibility.

Uses ``claude-agent-sdk`` to manage the underlying Claude CLI lifecycle while
converting typed SDK messages back into the dict events Skuld already emits.

Known gaps versus ``PersistentSubprocessTransport``:

- No session resume after process death; the Python SDK does not expose
  ``--resume`` recovery.
- No slash-command transport surface such as ``/clear`` or ``/compact``.
- Mid-turn injection still requires ``interrupt()`` followed by a new query,
  which drops the in-flight partial assistant turn per SDK semantics.
- The SDK is alpha software; this adapter is pinned to the 0.1.x line.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import asdict
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import (
    AssistantMessage,
    RateLimitEvent,
    ResultMessage,
    ServerToolResultBlock,
    ServerToolUseBlock,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from niuu.adapters.cli.runtime import filter_cli_event as _filter_event
from niuu.ports.cli import CLITransport, TransportCapabilities
from skuld.transports.mcp_config import build_sdk_mcp_servers
from skuld.transports.tool_shims import ensure_codex_tool_shims

logger = logging.getLogger("skuld.transport")

_DEFAULT_PERMISSION_MODE = "bypassPermissions"
_TRANSIENT_ERROR_RE = re.compile(
    r"(internal server error|server-side issue|try again in a moment|"
    r"status\.claude\.com|overloaded)",
    re.IGNORECASE,
)
_MAX_TRANSIENT_RETRIES = 1
_DEFAULT_TURN_TIMEOUT_S = 0.0
_SDK_TIMEOUT_RECOVERY_PROMPT = (
    "Time budget reached. Stop exploring and conclude immediately using the current "
    "workspace state. Do not do more investigation. Return only the required final "
    "outcome block for this task."
)


def _content_block_to_dict(block: object) -> dict[str, Any]:
    """Translate SDK content blocks into Anthropic-style stream-json blocks."""
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ThinkingBlock):
        return {
            "type": "thinking",
            "thinking": block.thinking,
            "signature": block.signature,
        }
    if isinstance(block, ToolUseBlock):
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input,
        }
    if isinstance(block, ToolResultBlock):
        payload: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
        }
        if block.content is not None:
            payload["content"] = block.content
        if block.is_error is not None:
            payload["is_error"] = block.is_error
        return payload
    if isinstance(block, ServerToolUseBlock):
        return {
            "type": "server_tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input,
        }
    if isinstance(block, ServerToolResultBlock):
        return {
            "type": "advisor_tool_result",
            "tool_use_id": block.tool_use_id,
            "content": block.content,
        }
    raise TypeError(f"Unsupported SDK content block: {type(block).__name__}")


def _to_stream_json(message: object) -> dict[str, Any] | None:
    """Convert an SDK message object into the broker's existing dict event shape."""
    if isinstance(message, UserMessage):
        content = message.content
        if isinstance(content, list):
            content = [_content_block_to_dict(block) for block in content]

        payload: dict[str, Any] = {
            "type": "user",
            "message": {"role": "user", "content": content},
        }
        if message.parent_tool_use_id:
            payload["parent_tool_use_id"] = message.parent_tool_use_id
        if message.tool_use_result is not None:
            payload["tool_use_result"] = message.tool_use_result
        if message.uuid:
            payload["uuid"] = message.uuid
        return payload

    if isinstance(message, AssistantMessage):
        payload = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [_content_block_to_dict(block) for block in message.content],
                "model": message.model,
            },
        }
        if message.parent_tool_use_id:
            payload["parent_tool_use_id"] = message.parent_tool_use_id
        if message.error is not None:
            payload["error"] = message.error
        if message.usage is not None:
            payload["message"]["usage"] = message.usage
        if message.message_id:
            payload["message"]["id"] = message.message_id
        if message.stop_reason:
            payload["message"]["stop_reason"] = message.stop_reason
        if message.session_id:
            payload["session_id"] = message.session_id
        if message.uuid:
            payload["uuid"] = message.uuid
        return payload

    if isinstance(message, SystemMessage):
        return dict(message.data)

    if isinstance(message, ResultMessage):
        payload = {
            "type": "result",
            "subtype": message.subtype,
            "duration_ms": message.duration_ms,
            "duration_api_ms": message.duration_api_ms,
            "is_error": message.is_error,
            "num_turns": message.num_turns,
            "session_id": message.session_id,
        }
        if message.stop_reason is not None:
            payload["stop_reason"] = message.stop_reason
        if message.total_cost_usd is not None:
            payload["total_cost_usd"] = message.total_cost_usd
        if message.usage is not None:
            payload["usage"] = message.usage
        if message.result is not None:
            payload["result"] = message.result
        if message.structured_output is not None:
            payload["structured_output"] = message.structured_output
        if message.model_usage is not None:
            payload["modelUsage"] = message.model_usage
        if message.permission_denials is not None:
            payload["permission_denials"] = message.permission_denials
        if message.deferred_tool_use is not None:
            payload["deferred_tool_use"] = asdict(message.deferred_tool_use)
        if message.errors is not None:
            payload["errors"] = message.errors
        if message.uuid:
            payload["uuid"] = message.uuid
        return payload

    if isinstance(message, StreamEvent):
        event = dict(message.event)
        event_type = event.get("type")

        # Claude SDK partial streaming uses Anthropic wire events like
        # ``message_start`` and ``content_block_delta``. Normalize the start
        # event into the Skuld-style ``assistant`` frame so existing UIs know
        # when to create a running assistant bubble before the deltas arrive.
        if event_type == "message_start":
            raw_message = event.get("message", {})
            if isinstance(raw_message, dict):
                payload: dict[str, Any] = {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": raw_message.get("content", []),
                        "model": raw_message.get("model"),
                    },
                }
                usage = raw_message.get("usage")
                if usage is not None:
                    payload["message"]["usage"] = usage
                message_id = raw_message.get("id")
                if isinstance(message_id, str) and message_id:
                    payload["message"]["id"] = message_id
                stop_reason = raw_message.get("stop_reason")
                if stop_reason is not None:
                    payload["message"]["stop_reason"] = stop_reason
                event = payload

        event.setdefault("session_id", message.session_id)
        event.setdefault("uuid", message.uuid)
        if message.parent_tool_use_id is not None:
            event.setdefault("parent_tool_use_id", message.parent_tool_use_id)
        return event

    if isinstance(message, RateLimitEvent):
        return {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": message.rate_limit_info.status,
                "resetsAt": message.rate_limit_info.resets_at,
                "rateLimitType": message.rate_limit_info.rate_limit_type,
                "utilization": message.rate_limit_info.utilization,
                "overageStatus": message.rate_limit_info.overage_status,
                "overageResetsAt": message.rate_limit_info.overage_resets_at,
                "overageDisabledReason": message.rate_limit_info.overage_disabled_reason,
                **message.rate_limit_info.raw,
            },
            "session_id": message.session_id,
            "uuid": message.uuid,
        }

    logger.debug("Skipping unsupported SDK message: %s", type(message).__name__)
    return None


class SDKTransport(CLITransport):
    """Claude SDK-backed transport that preserves existing broker event shapes."""

    def __init__(
        self,
        workspace_dir: str,
        model: str = "",
        skip_permissions: bool = True,
        agent_teams: bool = False,
        system_prompt: str = "",
        initial_prompt: str = "",
        mcp_servers: list[dict] | None = None,
        turn_timeout_s: float = _DEFAULT_TURN_TIMEOUT_S,
    ) -> None:
        super().__init__()
        self.workspace_dir = workspace_dir
        self._model = model
        self._skip_permissions = skip_permissions
        self._agent_teams = agent_teams
        self._system_prompt = system_prompt
        self._initial_prompt = initial_prompt
        self._raw_mcp_servers = list(mcp_servers or [])
        self._mcp_servers = build_sdk_mcp_servers(mcp_servers or [])
        self._turn_timeout_s = max(float(turn_timeout_s or 0.0), 0.0)
        self._initial_prompt_sent = False
        self._send_lock = asyncio.Lock()
        self._client: ClaudeSDKClient | None = None
        self._connected = False
        self._session_id: str | None = None
        self._last_result: dict[str, Any] | None = None
        self._turn_active = False
        self._pending_steers: list[str] = []
        self._steer_interrupt_requested = False

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(
            session_resume=False,
            interrupt=True,
            steer=True,
            steering_mode="interrupt_resume",
            set_model=True,
            set_permission_mode=True,
        )

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def last_result(self) -> dict[str, Any] | None:
        return self._last_result

    @property
    def is_alive(self) -> bool:
        return self._connected and self._client is not None

    @property
    def is_turn_active(self) -> bool:
        return self._turn_active

    async def start(self) -> None:
        """Connect the SDK client and optionally send the initial prompt."""
        if not self.is_alive:
            await self._connect_client()
        if not self._initial_prompt or self._initial_prompt_sent:
            return
        self._initial_prompt_sent = True
        try:
            await self.send_message(self._initial_prompt)
        except Exception:
            self._initial_prompt_sent = False
            raise

    async def stop(self) -> None:
        """Disconnect the SDK client and unblock any pending turn."""
        client = self._client
        self._connected = False
        if client is None:
            return
        try:
            await client.__aexit__(None, None, None)
        except Exception:
            logger.debug("Error stopping Claude SDK client", exc_info=True)
        self._client = None

    async def send_message(self, content: str) -> None:
        """Send a user turn and emit SDK responses as stream-json dicts."""
        async with self._send_lock:
            if not self.is_alive:
                await self._connect_client()
            client = self._client
            if client is None:
                raise RuntimeError("Claude SDK client not connected")
            prompt = content
            while True:
                self._turn_active = True
                self._steer_interrupt_requested = False
                attempt = 0
                timeout_recovery_used = False
                try:
                    while True:
                        try:
                            (
                                last_result_message,
                                last_result_event,
                                visible_output_emitted,
                            ) = await self._run_query(prompt)
                        except TimeoutError:
                            logger.warning(
                                "Claude SDK transport: turn timed out after %.1fs",
                                self._turn_timeout_s,
                            )
                            await client.interrupt()
                            if timeout_recovery_used:
                                raise RuntimeError(
                                    "Claude SDK transport timed out while concluding the turn"
                                )
                            timeout_recovery_used = True
                            prompt = self._timeout_recovery_prompt()
                            continue

                        if (
                            last_result_message is not None
                            and last_result_event is not None
                            and self._should_retry_transient_result(
                                last_result_message, last_result_event
                            )
                            and attempt < _MAX_TRANSIENT_RETRIES
                            and not visible_output_emitted
                        ):
                            attempt += 1
                            logger.warning(
                                "Claude SDK transport: retrying transient provider error "
                                "on attempt %s",
                                attempt,
                            )
                            await asyncio.sleep(1.0)
                            continue

                        if last_result_event is not None:
                            await self._emit_event(last_result_event)
                            self._last_result = last_result_event
                        break
                finally:
                    self._turn_active = False

                next_prompt = self._consume_pending_steers()
                if next_prompt is None:
                    return
                logger.info("Claude SDK transport: continuing interrupted turn with steering")
                prompt = next_prompt

    async def _run_query(
        self,
        prompt: str,
    ) -> tuple[ResultMessage | None, dict[str, Any] | None, bool]:
        client = self._client
        if client is None:
            raise RuntimeError("Claude SDK client not connected")

        async def _query_and_consume() -> tuple[ResultMessage | None, dict[str, Any] | None, bool]:
            await client.query(prompt)
            return await self._consume_response(client)

        if self._turn_timeout_s > 0:
            return await asyncio.wait_for(
                _query_and_consume(),
                timeout=self._turn_timeout_s,
            )
        return await _query_and_consume()

    async def _consume_response(
        self,
        client: ClaudeSDKClient,
    ) -> tuple[ResultMessage | None, dict[str, Any] | None, bool]:
        last_result_message: ResultMessage | None = None
        last_result_event: dict[str, Any] | None = None
        visible_output_emitted = False

        async for message in client.receive_response():
            event = await self._translate_sdk_message(message)
            if isinstance(message, ResultMessage):
                last_result_message = message
                if event is not None:
                    last_result_event = event
                break
            if event is not None:
                await self._emit_event(event)
                if self._is_visible_output_event(event):
                    visible_output_emitted = True

        return last_result_message, last_result_event, visible_output_emitted

    async def interrupt(self) -> None:
        """Interrupt the in-flight turn if the SDK client is connected.

        Per SDK semantics, the partial assistant turn is dropped while the
        conversation session remains usable for follow-up turns.
        """
        client = self._client
        if client is None:
            return
        await client.interrupt()

    async def send_control(self, subtype: str, **kwargs: object) -> None:
        """Handle runtime control messages against the live SDK session."""
        client = self._client
        if client is None:
            return

        if subtype == "interrupt":
            await self.interrupt()
            return

        if subtype == "set_model":
            model = kwargs.get("model")
            if model is None or isinstance(model, str):
                self._model = model or ""
                await client.set_model(model or None)
            return

        if subtype == "set_permission_mode":
            mode = kwargs.get("permissionMode")
            if isinstance(mode, str) and mode:
                await client.set_permission_mode(mode)
            return

        if subtype == "rewind_files":
            user_message_id = kwargs.get("user_message_id")
            if isinstance(user_message_id, str) and user_message_id:
                await client.rewind_files(user_message_id)
            return

        if subtype == "steer":
            content = str(kwargs.get("content") or "").strip()
            if not content:
                return
            if not self._turn_active:
                logger.info("Claude SDK transport: steer requested without an active turn")
                return
            self._pending_steers.append(content)
            if self._steer_interrupt_requested:
                return
            self._steer_interrupt_requested = True
            await client.interrupt()
            return

        if subtype == "redirect":
            content = str(kwargs.get("content") or "").strip()
            if not content:
                return
            if not self._turn_active:
                await self.send_message(content)
                return
            self._pending_steers.append(content)
            if self._steer_interrupt_requested:
                return
            self._steer_interrupt_requested = True
            await client.interrupt()
            return

    async def _connect_client(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        if self._agent_teams:
            env["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] = "1"
        _, shim_env = ensure_codex_tool_shims(
            self.workspace_dir,
            mcp_servers=self._raw_mcp_servers,
        )
        if shim_env:
            env.update(shim_env)

        options = ClaudeAgentOptions(
            model=self._model or None,
            system_prompt=self._system_prompt or None,
            permission_mode=(_DEFAULT_PERMISSION_MODE if self._skip_permissions else "default"),
            cwd=self.workspace_dir,
            mcp_servers=self._mcp_servers,
            include_partial_messages=True,
            thinking={"type": "adaptive", "display": "summarized"},
            env=env,
        )
        client = ClaudeSDKClient(options)
        self._client = await client.__aenter__()
        self._connected = True

    async def _translate_sdk_message(self, message: object) -> dict[str, Any] | None:
        self._capture_session_id(message)

        event = _to_stream_json(message)
        if event is None:
            return None

        filtered = _filter_event(event)
        return filtered

    async def _emit_event(self, event: dict[str, Any]) -> None:
        try:
            await self._emit(event)
        except Exception:
            logger.warning(
                "on_event handler raised for type=%s",
                event.get("type"),
                exc_info=True,
            )

    async def _handle_sdk_message(self, message: object) -> None:
        """Translate and emit one SDK message event.

        Kept as a small wrapper so older tests and callers do not need to know
        about the newer translate/emit split.
        """
        event = await self._translate_sdk_message(message)
        if event is None:
            return
        await self._emit_event(event)
        if isinstance(message, ResultMessage):
            self._last_result = event

    def _capture_session_id(self, message: object) -> None:
        session_id = getattr(message, "session_id", None)
        if isinstance(session_id, str) and session_id:
            self._session_id = session_id
            return

        if not isinstance(message, SystemMessage):
            return
        raw_session_id = message.data.get("session_id")
        if isinstance(raw_session_id, str) and raw_session_id:
            self._session_id = raw_session_id

    def _consume_pending_steers(self) -> str | None:
        """Drain steering updates into the next continuation prompt."""
        self._steer_interrupt_requested = False
        if not self._pending_steers:
            return None

        if len(self._pending_steers) == 1:
            return self._pending_steers.pop(0)

        pending = list(self._pending_steers)
        self._pending_steers.clear()
        lines = [
            "The user sent multiple steering updates while you were working.",
            "Continue the same task and incorporate all of them in order:",
        ]
        lines.extend(f"- {item}" for item in pending)
        return "\n".join(lines)

    @staticmethod
    def _timeout_recovery_prompt() -> str:
        return _SDK_TIMEOUT_RECOVERY_PROMPT

    @staticmethod
    def _is_visible_output_event(event: dict[str, Any]) -> bool:
        event_type = event.get("type")
        if event_type == "assistant":
            message = event.get("message", {})
            if not isinstance(message, dict):
                return False
            content = message.get("content", [])
            return bool(content)
        if event_type == "content_block_start":
            return True
        if event_type == "content_block_delta":
            delta = event.get("delta", {})
            if not isinstance(delta, dict):
                return False
            return bool(delta.get("text") or delta.get("thinking") or delta.get("partial_json"))
        return False

    @staticmethod
    def _should_retry_transient_result(
        message: ResultMessage,
        event: dict[str, Any],
    ) -> bool:
        if not message.is_error:
            return False
        result_text = str(event.get("result") or message.result or "").strip()
        if not result_text:
            return False
        return bool(_TRANSIENT_ERROR_RE.search(result_text))
