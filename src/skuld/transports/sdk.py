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

logger = logging.getLogger("skuld.transport")

_DEFAULT_PERMISSION_MODE = "bypassPermissions"


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
    ) -> None:
        super().__init__()
        self.workspace_dir = workspace_dir
        self._model = model
        self._skip_permissions = skip_permissions
        self._agent_teams = agent_teams
        self._system_prompt = system_prompt
        self._initial_prompt = initial_prompt
        self._mcp_servers = build_sdk_mcp_servers(mcp_servers or [])
        self._initial_prompt_sent = False
        self._send_lock = asyncio.Lock()
        self._client: ClaudeSDKClient | None = None
        self._connected = False
        self._session_id: str | None = None
        self._last_result: dict[str, Any] | None = None

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(
            session_resume=False,
            interrupt=True,
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

            await client.query(content)
            async for message in client.receive_response():
                await self._handle_sdk_message(message)
                if isinstance(message, ResultMessage):
                    return

    async def interrupt(self) -> None:
        """Interrupt the in-flight turn if the SDK client is connected.

        Per SDK semantics, the partial assistant turn is dropped while the
        conversation session remains usable for follow-up turns.
        """
        client = self._client
        if client is None:
            return
        await client.interrupt()

    async def _connect_client(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        if self._agent_teams:
            env["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] = "1"

        options = ClaudeAgentOptions(
            model=self._model or None,
            system_prompt=self._system_prompt or None,
            permission_mode=(_DEFAULT_PERMISSION_MODE if self._skip_permissions else "default"),
            cwd=self.workspace_dir,
            mcp_servers=self._mcp_servers,
            env=env,
        )
        client = ClaudeSDKClient(options)
        self._client = await client.__aenter__()
        self._connected = True

    async def _handle_sdk_message(self, message: object) -> None:
        self._capture_session_id(message)

        event = _to_stream_json(message)
        if event is None:
            return

        filtered = _filter_event(event)
        if filtered is not None:
            try:
                await self._emit(filtered)
            except Exception:
                logger.warning(
                    "on_event handler raised for type=%s",
                    filtered.get("type"),
                    exc_info=True,
                )

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
