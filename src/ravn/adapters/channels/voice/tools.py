"""The voice call's tools: the resident's own, plus a few over its call store.

The phone's realtime model calls tools directly, as a Codex executor does over
``ravn tool-mcp``: the resident's ``ToolPort``s named by the persona's
``voice.tools``, each checked against the resident's permission policy exactly
as the agent loop checks it, and run through ``execute_observed_tool`` with the
call id as the conversation id so every call is traced and attributed.

The conversation tools read and edit this resident's voice calls: recall an
earlier call, search them, and title or pin the current one.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from ravn.adapters.channels.voice.store import UnknownCallError, VoiceCallStore
from ravn.ports.permission import PermissionPort
from ravn.ports.tool import ToolPort
from ravn.tool_observability import execute_observed_tool

logger = logging.getLogger(__name__)

#: Category the app files conversation tools under.
_CONVERSATION_CATEGORY = "conversation"


class UnknownToolError(LookupError):
    """No voice tool by that name."""


class VoiceToolTimeoutError(TimeoutError):
    """A voice tool ran past the channel's tool timeout."""


@dataclass(frozen=True)
class ToolOutcome:
    """What the voice model hears back."""

    content: str
    is_error: bool = False


class VoiceConversationTools:
    """Tools over this resident's voice calls."""

    def __init__(self, store: VoiceCallStore, *, search_limit: int) -> None:
        self._store = store
        self._search_limit = search_limit

    @property
    def names(self) -> list[str]:
        return [definition["name"] for definition in self.definitions()]

    def definitions(self) -> list[dict[str, Any]]:
        return [
            _definition(
                "recall_conversation",
                "Read the transcript of an earlier voice call by its id.",
                {"callId": {"type": "string", "description": "The call's id."}},
                ["callId"],
            ),
            _definition(
                "search_conversations",
                "Find earlier voice calls whose words or title contain a phrase.",
                {"query": {"type": "string", "description": "Words to look for."}},
                ["query"],
            ),
            _definition(
                "conversation_info",
                "The current call's id, title and how long it has run.",
                {},
                [],
            ),
            _definition(
                "set_conversation_title",
                "Give the current call a short title.",
                {"title": {"type": "string", "description": "A few words."}},
                ["title"],
            ),
            _definition(
                "pin_conversation",
                "Pin or unpin the current call in the call list.",
                {"pinned": {"type": "boolean", "description": "true to pin, false to unpin."}},
                ["pinned"],
            ),
        ]

    async def call(self, name: str, args: dict[str, Any], *, conversation_id: str) -> ToolOutcome:
        try:
            match name:
                case "recall_conversation":
                    return self._recall(str(args.get("callId") or ""))
                case "search_conversations":
                    return self._search(str(args.get("query") or ""))
                case "conversation_info":
                    return _json(self._store.history_json(self._store.get(conversation_id)))
                case "set_conversation_title":
                    title = str(args.get("title") or "").strip()
                    if not title:
                        return ToolOutcome("title is required", is_error=True)
                    self._store.update(conversation_id, title=title)
                    return ToolOutcome(f"Titled: {title}")
                case "pin_conversation":
                    pinned = bool(args.get("pinned"))
                    self._store.update(conversation_id, pinned=pinned)
                    return ToolOutcome("Pinned." if pinned else "Unpinned.")
        except UnknownCallError as exc:
            return ToolOutcome(str(exc), is_error=True)
        raise UnknownToolError(name)

    def _recall(self, call_id: str) -> ToolOutcome:
        if not call_id:
            return ToolOutcome("callId is required", is_error=True)
        row = self._store.history_json(self._store.get(call_id))
        lines = [f"{m['role']}: {m['text']}" for m in row["messages"]]
        header = f"Call {call_id}" + (f" — {row['title']}" if row.get("title") else "")
        return ToolOutcome("\n".join([header, *lines]))

    def _search(self, query: str) -> ToolOutcome:
        if not query.strip():
            return ToolOutcome("query is required", is_error=True)
        calls = self._store.search(query.strip(), self._search_limit)
        return _json(
            [
                {
                    "callId": call.call_id,
                    "title": call.title,
                    "startedAt": call.created_at_ms,
                    "state": call.state,
                }
                for call in calls
            ]
        )


class VoiceToolBridge:
    """List and run the voice call's tools."""

    def __init__(
        self,
        *,
        tools: list[ToolPort],
        permission: PermissionPort,
        conversation: VoiceConversationTools,
        agent_name: str,
        timeout_seconds: float,
    ) -> None:
        clashes = {tool.name for tool in tools} & set(conversation.names)
        if clashes:
            raise ValueError(
                f"Voice tools {sorted(clashes)} clash with the voice channel's conversation tools"
            )
        self._tools = {tool.name: tool for tool in tools}
        self._permission = permission
        self._conversation = conversation
        self._agent_name = agent_name
        self._timeout_seconds = timeout_seconds

    @property
    def names(self) -> list[str]:
        return [*self._tools, *self._conversation.names]

    def definitions(self) -> list[dict[str, Any]]:
        """The catalog in the app's shape (``AgentServiceToolDefinitionPayload``)."""
        timeout_ms = int(self._timeout_seconds * 1000)
        resident = [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
                "category": "resident",
                "timeoutMs": timeout_ms,
            }
            for tool in self._tools.values()
        ]
        conversation = [
            {**definition, "timeoutMs": timeout_ms}
            for definition in self._conversation.definitions()
        ]
        return [*resident, *conversation]

    async def call(
        self, name: str, args: dict[str, Any], *, call_id: str, conversation_id: str
    ) -> ToolOutcome:
        if name in self._conversation.names:
            return await self._conversation.call(name, args, conversation_id=conversation_id)
        tool = self._tools.get(name)
        if tool is None:
            raise UnknownToolError(name)
        if not await self._permission.check(tool.required_permission):
            return ToolOutcome(
                f"Permission denied: {name} needs {tool.required_permission}", is_error=True
            )

        async def _execute() -> Any:
            return await tool.execute(args)

        try:
            result = await asyncio.wait_for(
                execute_observed_tool(
                    name=name,
                    arguments=args,
                    execute=_execute,
                    call_id=call_id,
                    agent_name=self._agent_name,
                    conversation_id=conversation_id,
                    runtime_component="voice",
                ),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as exc:
            raise VoiceToolTimeoutError(
                f"Tool {name!r} ran past {self._timeout_seconds:g} s"
            ) from exc
        except Exception as exc:  # the agent loop reports a raising tool to the model the same way
            logger.warning("Voice tool %s raised: %s", name.replace("\n", " "), exc)
            return ToolOutcome(f"Tool error: {exc}", is_error=True)
        return ToolOutcome(result.content, is_error=result.is_error)


def _definition(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
        "category": _CONVERSATION_CATEGORY,
    }


def _json(value: Any) -> ToolOutcome:
    return ToolOutcome(json.dumps(value, ensure_ascii=False))
