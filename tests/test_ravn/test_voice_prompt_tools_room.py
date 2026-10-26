"""The voice prompt, the voice tool bridge and the room copy."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from ravn.adapters.channels.voice.prompt import (
    PROMPT_VERSION,
    UnsupportedProviderError,
    VoicePersona,
    VoicePromptComposer,
    normalize_provider,
)
from ravn.adapters.channels.voice.room import RoomCopyError, VoiceRoomCopier
from ravn.adapters.channels.voice.store import VoiceCallStore
from ravn.adapters.channels.voice.tools import (
    UnknownToolError,
    VoiceConversationTools,
    VoiceToolBridge,
    VoiceToolTimeoutError,
)
from ravn.adapters.permission.allow_deny import AllowAllPermission, DenyAllPermission
from ravn.domain.models import ToolResult
from ravn.ports.tool import ToolPort

MODELS = {"grok": "grok-voice-3.0", "gpt-realtime": "gpt-realtime-2.1"}


def persona(**overrides: object) -> VoicePersona:
    fields: dict[str, object] = {
        "name": "sidekick",
        "display_name": "Sidekick",
        "identity": "You are Sidekick.",
        "style": "Warm.",
        "voices": {"grok-voice": "ara", "openai-realtime": "marin"},
        "grok_session": {"reasoning": {"effort": "high"}},
    }
    fields.update(overrides)
    return VoicePersona(**fields)  # type: ignore[arg-type]


class FakeTool(ToolPort):
    def __init__(
        self, name: str, *, result: str = "ok", raises: bool = False, delay: float = 0
    ) -> None:
        self._name = name
        self._result = result
        self._raises = raises
        self._delay = delay
        self.calls: list[dict] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"The {self._name} tool."

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {"q": {"type": "string"}}}

    @property
    def required_permission(self) -> str:
        return "web:read"

    async def execute(self, input: dict) -> ToolResult:
        self.calls.append(input)
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._raises:
            raise RuntimeError("boom")
        return ToolResult(tool_call_id="x", content=self._result)


@pytest.fixture
def store(tmp_path: Path) -> VoiceCallStore:
    store = VoiceCallStore(str(tmp_path / "calls.db"))
    yield store
    store.close()


# -- prompt -----------------------------------------------------------------


def test_grok_prompt_carries_persona_session_and_native_search() -> None:
    composer = VoicePromptComposer(
        persona(), models=MODELS, time_zone="Europe/Amsterdam", tool_names=["a", "b"]
    )

    prompt = composer.prompt("xai", surface="ios", conversation_id="CALL-1")

    assert "schema" not in prompt  # clients read an unnamed prompt as version 2
    assert prompt["fingerprint"]["version"] == PROMPT_VERSION
    assert prompt["provider"] == "grok"
    assert prompt["session"] == {
        "model": "grok-voice-3.0",
        "voice": "ara",
        "reasoning": {"effort": "high"},
    }
    assert prompt["webSearch"] == "native"
    assert prompt["providerTools"] == [{"type": "web_search"}]
    assert prompt["fingerprint"]["toolCount"] == 2
    instructions = prompt["instructions"]
    assert instructions.startswith(
        "You are Sidekick, speaking with your operator on a live voice call from their iPhone."
    )
    assert "This conversation's id is CALL-1." in instructions
    assert "You are Sidekick." in instructions and "## How you sound\n\nWarm." in instructions
    assert "end_conversation" in instructions


def test_gpt_realtime_prompt_has_no_grok_fields() -> None:
    composer = VoicePromptComposer(persona(), models=MODELS, time_zone="UTC", tool_names=[])

    prompt = composer.prompt("gpt-realtime", surface="mac", conversation_id="C")

    assert prompt["session"] == {"model": "gpt-realtime-2.1", "voice": "marin"}
    assert "webSearch" not in prompt
    assert "from their Mac" in prompt["instructions"]


def test_standing_instructions_have_no_per_call_preamble() -> None:
    composer = VoicePromptComposer(persona(), models=MODELS, time_zone="UTC", tool_names=[])

    assert composer.standing_instructions().startswith("You are Sidekick.")


@pytest.mark.parametrize("provider", ["gemini", "gpt-live", "live"])
def test_unsupported_providers_raise(provider: str) -> None:
    with pytest.raises(UnsupportedProviderError):
        normalize_provider(provider)


def test_persona_without_a_providers_voice_cannot_be_prompted() -> None:
    composer = VoicePromptComposer(
        persona(voices={"grok-voice": "ara"}), models=MODELS, time_zone="UTC", tool_names=[]
    )

    with pytest.raises(UnsupportedProviderError, match="openai-realtime"):
        composer.prompt("openai", surface="ios", conversation_id="C")


def test_composer_requires_a_model_per_provider() -> None:
    with pytest.raises(ValueError, match="missing"):
        VoicePromptComposer(persona(), models={"grok": "g"}, time_zone="UTC", tool_names=[])


# -- tools ------------------------------------------------------------------


def _bridge(
    store: VoiceCallStore, *tools: ToolPort, permission=None, timeout: float = 5.0
) -> VoiceToolBridge:
    return VoiceToolBridge(
        tools=list(tools),
        permission=permission or AllowAllPermission(),
        conversation=VoiceConversationTools(store, search_limit=10),
        agent_name="sidekick",
        timeout_seconds=timeout,
    )


async def test_bridge_lists_resident_and_conversation_tools(store: VoiceCallStore) -> None:
    bridge = _bridge(store, FakeTool("web_search"))

    definitions = bridge.definitions()

    assert definitions[0] == {
        "type": "function",
        "name": "web_search",
        "description": "The web_search tool.",
        "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
        "category": "resident",
        "timeoutMs": 5000,
    }
    assert {d["name"] for d in definitions[1:]} == {
        "recall_conversation",
        "search_conversations",
        "conversation_info",
        "set_conversation_title",
        "pin_conversation",
    }


async def test_bridge_runs_a_resident_tool(store: VoiceCallStore) -> None:
    tool = FakeTool("web_search", result="sunny")
    bridge = _bridge(store, tool)

    outcome = await bridge.call("web_search", {"q": "weather"}, call_id="t1", conversation_id="C")

    assert outcome.content == "sunny" and not outcome.is_error
    assert tool.calls == [{"q": "weather"}]


async def test_bridge_checks_the_resident_permission(store: VoiceCallStore) -> None:
    tool = FakeTool("web_search")
    bridge = _bridge(store, tool, permission=DenyAllPermission())

    outcome = await bridge.call("web_search", {}, call_id="", conversation_id="")

    assert outcome.is_error and "Permission denied" in outcome.content
    assert tool.calls == []


async def test_bridge_reports_a_raising_tool_and_times_out_a_slow_one(
    store: VoiceCallStore,
) -> None:
    bridge = _bridge(
        store, FakeTool("broken", raises=True), FakeTool("slow", delay=1), timeout=0.05
    )

    broken = await bridge.call("broken", {}, call_id="", conversation_id="")
    assert broken.is_error and "boom" in broken.content
    with pytest.raises(VoiceToolTimeoutError):
        await bridge.call("slow", {}, call_id="", conversation_id="")
    with pytest.raises(UnknownToolError):
        await bridge.call("absent", {}, call_id="", conversation_id="")


def test_bridge_refuses_a_tool_named_like_a_conversation_tool(store: VoiceCallStore) -> None:
    with pytest.raises(ValueError, match="clash"):
        _bridge(store, FakeTool("recall_conversation"))


async def test_conversation_tools_work_over_the_store(store: VoiceCallStore) -> None:
    store.start("OLD", persona="sidekick", provider="xai", metadata={})
    store.log("OLD", "user_speech", {"text": "plan the garden"})
    store.start("NOW", persona="sidekick", provider="xai", metadata={})
    bridge = _bridge(store)

    titled = await bridge.call(
        "set_conversation_title", {"title": "Today"}, call_id="", conversation_id="NOW"
    )
    pinned = await bridge.call(
        "pin_conversation", {"pinned": True}, call_id="", conversation_id="NOW"
    )
    unpinned = await bridge.call(
        "pin_conversation", {"pinned": False}, call_id="", conversation_id="NOW"
    )
    info = await bridge.call("conversation_info", {}, call_id="", conversation_id="NOW")
    found = await bridge.call(
        "search_conversations", {"query": "garden"}, call_id="", conversation_id="NOW"
    )
    recalled = await bridge.call(
        "recall_conversation", {"callId": "OLD"}, call_id="", conversation_id="NOW"
    )

    assert (
        titled.content == "Titled: Today"
        and pinned.content == "Pinned."
        and unpinned.content == "Unpinned."
    )
    assert json.loads(info.content)["title"] == "Today"
    assert [c["callId"] for c in json.loads(found.content)] == ["OLD"]
    assert recalled.content == "Call OLD\nuser: plan the garden"


@pytest.mark.parametrize(
    ("name", "args", "conversation", "message"),
    [
        ("set_conversation_title", {"title": " "}, "NOW", "title is required"),
        ("search_conversations", {}, "NOW", "query is required"),
        ("recall_conversation", {}, "NOW", "callId is required"),
        ("recall_conversation", {"callId": "GONE"}, "NOW", "Unknown voice call"),
        ("conversation_info", {}, "GONE", "Unknown voice call"),
    ],
)
async def test_conversation_tools_answer_bad_input_as_errors(
    store: VoiceCallStore, name: str, args: dict, conversation: str, message: str
) -> None:
    store.start("NOW", persona="sidekick", provider="xai", metadata={})
    outcome = await _bridge(store).call(name, args, call_id="", conversation_id=conversation)

    assert outcome.is_error and message in outcome.content


async def test_conversation_tools_reject_an_unknown_name(store: VoiceCallStore) -> None:
    tools = VoiceConversationTools(store, search_limit=1)

    with pytest.raises(UnknownToolError):
        await tools.call("delete_everything", {}, conversation_id="")


# -- room copy ----------------------------------------------------------------


def _room(status: int, seen: list[httpx.Request]) -> VoiceRoomCopier:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json={"status": "sent"} if status < 400 else {"detail": "no"})

    return VoiceRoomCopier(
        broker_url="http://127.0.0.1:8081/",
        participant_id="flock-sidekick",
        timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    )


async def test_room_copy_posts_commentary_under_the_right_speaker() -> None:
    seen: list[httpx.Request] = []
    room = _room(200, seen)

    await room.copy(call_id="C", speaker="agent", text="Hello.")
    await room.copy(call_id="C", speaker="user", text="Hi.")
    await room.aclose()

    agent, user = (json.loads(r.content) for r in seen)
    assert str(seen[0].url) == "http://127.0.0.1:8081/api/room/message"
    assert agent == {
        "content": "Hello.",
        "source": "voice",
        "metadata": {"channel": "voice", "call_id": "C", "speaker": "agent"},
        "deliver_to_transport": False,
        "participant_id": "flock-sidekick",
    }
    assert "participant_id" not in user and user["metadata"]["speaker"] == "user"


async def test_room_copy_failures_raise() -> None:
    room = _room(404, [])
    with pytest.raises(RoomCopyError, match="HTTP 404"):
        await room.copy(call_id="C", speaker="user", text="x")
    with pytest.raises(ValueError, match="speaker"):
        await room.copy(call_id="C", speaker="bot", text="x")

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    offline = VoiceRoomCopier(
        broker_url="http://b",
        participant_id="p",
        timeout_seconds=1,
        transport=httpx.MockTransport(down),
    )
    with pytest.raises(RoomCopyError, match="unreachable"):
        await offline.copy(call_id="C", speaker="user", text="x")


def test_room_copy_needs_a_broker_and_a_participant() -> None:
    with pytest.raises(ValueError, match="broker URL"):
        VoiceRoomCopier(broker_url="", participant_id="p", timeout_seconds=1)
    with pytest.raises(ValueError, match="room_participant_id"):
        VoiceRoomCopier(broker_url="http://b", participant_id="", timeout_seconds=1)
