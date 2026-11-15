"""Shared builders for the session voice channel tests (``skuld.voice``)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI

from niuu.domain.message_author import MessageAuthor
from skuld.voice.binding import CallOutlet
from skuld.voice.calls import VoiceCallBook
from skuld.voice.channel import SessionVoiceChannel, register_voice_routes
from skuld.voice.minting import MintedCredential, VoiceMintError
from skuld.voice.persona import VoicePromptComposer, resolve_voice_persona
from skuld.voice.room_binding import RoomVoiceBinding
from skuld.voice.room_port import (
    PostKind,
    RoomKind,
    RoomMember,
    RoomPost,
    RoomVoiceStatus,
    VoiceRoomPort,
)
from skuld.voice.session_binding import SessionVoiceBinding
from skuld.voice.session_port import (
    SendOutcome,
    SendStatus,
    SessionTurn,
    SessionVoiceStatus,
    VoiceSessionPort,
)
from skuld.voice.tools import SessionVoiceTools

OWNER = "owner-1"
USER_HEADER = "x-auth-user-id"
NAME_HEADER = "x-auth-display-name"
MODELS = {"grok": "grok-voice-3.0", "gpt-realtime": "gpt-realtime-2.1"}
FULL_READ_MAX_CHARS = 4096


def persona_document(
    name: str = "sidekick",
    *,
    voices: dict[str, str] | None = None,
    style: str = "Warm and brisk.",
    description: str = "a helpful companion",
    grok: dict[str, Any] | None = None,
    with_voice: bool = True,
) -> dict[str, Any]:
    """A portable persona document (schema_version 1) with or without a ``voice:`` section."""
    definition: dict[str, Any] = {"name": name, "description": description}
    if with_voice:
        voice: dict[str, Any] = {
            "voices": voices
            if voices is not None
            else {"grok-voice": "ara", "openai-realtime": "marin"},
            "style": style,
        }
        if grok is not None:
            voice["grok"] = grok
        definition["voice"] = voice
    return {"schema_version": 1, "id": name, "revision": "r1", "definition": definition}


def make_status(**overrides: Any) -> SessionVoiceStatus:
    fields: dict[str, Any] = {
        "session_id": "sess-1",
        "name": "Refactor billing",
        "model": "claude-sonnet",
        "harness": "claude",
        "activity_state": "idle",
        "busy": False,
    }
    fields.update(overrides)
    return SessionVoiceStatus(**fields)


class FakeSession(VoiceSessionPort):
    """A voice session the tests set up, and whose sends and records they read back."""

    def __init__(self, *, status: SessionVoiceStatus | None = None) -> None:
        self.current_status = status or make_status()
        self.current_turns: list[SessionTurn] = []
        self.outcome = SendOutcome(SendStatus.DELIVERED)
        self.sent: list[tuple[str, MessageAuthor | None, str]] = []
        self.records: list[dict[str, Any]] = []
        #: When set, ``record`` raises it: the durable event log refusing a frame.
        self.record_error: Exception | None = None

    def status(self) -> SessionVoiceStatus:
        return self.current_status

    def turns(self) -> list[SessionTurn]:
        return list(self.current_turns)

    async def send(self, text: str, *, author: MessageAuthor | None, call_id: str) -> SendOutcome:
        self.sent.append((text, author, call_id))
        return self.outcome

    def record(self, frame: dict[str, Any]) -> None:
        if self.record_error is not None:
            raise self.record_error
        self.records.append(frame)


class FakeMinter:
    """Stands for ``ForgeVoiceMinter``: returns a credential, or raises the given error."""

    def __init__(self, *, error: Exception | None = None, session_bound: bool = False) -> None:
        self.error = error
        self.session_bound = session_bound
        self.requests: list[dict[str, str]] = []

    async def mint(
        self, *, provider: str, model: str, voice: str, instructions: str, reasoning: str
    ) -> MintedCredential:
        self.requests.append(
            {
                "provider": provider,
                "model": model,
                "voice": voice,
                "instructions": instructions,
                "reasoning": reasoning,
            }
        )
        if self.error is not None:
            raise self.error
        return MintedCredential(
            provider=provider,
            token="secret",
            expires_at_ms=1_800_000_000_000,
            model=model,
            session_bound=self.session_bound,
        )


def failing_minter(reason: str = "Forge refused (HTTP 401): bad key") -> FakeMinter:
    return FakeMinter(error=VoiceMintError(reason))


class _NamedToolsBinding(SessionVoiceBinding):
    """A session binding whose tool names a test chooses (for the prompt's fingerprint)."""

    def __init__(self, *args: Any, tool_names: list[str], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._names = list(tool_names)

    @property
    def tool_names(self) -> list[str]:
        return list(self._names)


def make_binding(
    session: FakeSession | None = None,
    *,
    tool_names: list[str] | None = None,
    card_recent_turns: int = 6,
    card_turn_max_chars: int = 1500,
    full_read_max_chars: int = FULL_READ_MAX_CHARS,
) -> SessionVoiceBinding:
    """A call bound to *session* (a fresh fake by default)."""
    session = session or FakeSession()
    tools = SessionVoiceTools(
        session, tool_timeout_seconds=60.0, full_read_max_chars=full_read_max_chars
    )
    kwargs = {"card_recent_turns": card_recent_turns, "card_turn_max_chars": card_turn_max_chars}
    if tool_names is None:
        return SessionVoiceBinding(session, tools, **kwargs)
    return _NamedToolsBinding(session, tools, tool_names=tool_names, **kwargs)


class ComposerHarness:
    """The real composer over a session binding, driven by a status and turns per call."""

    def __init__(self, composer: VoicePromptComposer, binding_args: dict[str, Any]) -> None:
        self.composer = composer
        self._binding_args = binding_args

    @property
    def persona(self) -> Any:
        return self.composer.persona

    def model_for(self, provider: str) -> str:
        return self.composer.model_for(provider)

    def _binding(self, status: SessionVoiceStatus, turns: list[SessionTurn]) -> SessionVoiceBinding:
        session = FakeSession(status=status)
        session.current_turns = list(turns)
        return make_binding(session, **self._binding_args)

    def card(self, status: SessionVoiceStatus, turns: list[SessionTurn]) -> str:
        return self._binding(status, turns).card(status, turns)

    def standing_instructions(self, status: SessionVoiceStatus, turns: list[SessionTurn]) -> str:
        return self.composer.standing_instructions(self._binding(status, turns))

    def instructions(self, status: SessionVoiceStatus, turns: list[SessionTurn], **kw: Any) -> str:
        return self.composer.instructions(self._binding(status, turns), **kw)

    def prompt(
        self, provider: str, status: SessionVoiceStatus, turns: list[SessionTurn], **kw: Any
    ) -> dict[str, Any]:
        return self.composer.prompt(provider, self._binding(status, turns), **kw)


def make_composer(
    *,
    persona: dict[str, Any] | None = None,
    tool_names: list[str] | None = None,
    card_recent_turns: int = 6,
    card_turn_max_chars: int = 1500,
    time_zone: str = "UTC",
) -> ComposerHarness:
    voice_persona = resolve_voice_persona(None, persona or persona_document())
    composer = VoicePromptComposer(voice_persona, models=MODELS, time_zone=time_zone)
    return ComposerHarness(
        composer,
        {
            "tool_names": tool_names,
            "card_recent_turns": card_recent_turns,
            "card_turn_max_chars": card_turn_max_chars,
        },
    )


def make_channel(
    *,
    session: FakeSession | None = None,
    minter: FakeMinter | None = None,
    owner_id: str = OWNER,
    persona: dict[str, Any] | None = None,
    full_read_max_chars: int = FULL_READ_MAX_CHARS,
    card_turn_max_chars: int = 1500,
) -> tuple[SessionVoiceChannel, FakeSession, FakeMinter]:
    session = session or FakeSession()
    minter = minter or FakeMinter()
    persona_document_ = persona or persona_document()
    channel = SessionVoiceChannel(
        binding=make_binding(
            session,
            full_read_max_chars=full_read_max_chars,
            card_turn_max_chars=card_turn_max_chars,
        ),
        composer=VoicePromptComposer(
            resolve_voice_persona(None, persona_document_), models=MODELS, time_zone="UTC"
        ),
        calls=VoiceCallBook(session.record),
        minter=minter,  # type: ignore[arg-type]
        owner_id=owner_id,
        user_id_header=USER_HEADER,
        display_name_header=NAME_HEADER,
    )
    return channel, session, minter


def make_app(channel: SessionVoiceChannel | None) -> FastAPI:
    app = FastAPI()
    register_voice_routes(app, lambda: channel)
    return app


class FakeRoom(VoiceRoomPort):
    """A voice room the tests set up, and whose sends and records they read back."""

    def __init__(
        self,
        *,
        kind: RoomKind = RoomKind.TEAM,
        members: tuple[RoomMember, ...] = (),
        people: tuple[str, ...] = (),
        name: str = "Release crew",
    ) -> None:
        self.kind = kind
        self.members = members
        self.people = people
        self.name = name
        self.current_posts: list[RoomPost] = []
        self.outcome = SendOutcome(SendStatus.DELIVERED)
        self.everyone_outcome = SendOutcome(SendStatus.DELIVERED)
        self.said: list[tuple[str, str, MessageAuthor | None, str]] = []
        self.said_everyone: list[tuple[str, MessageAuthor | None, str]] = []
        self.records: list[dict[str, Any]] = []
        self.listener: Callable[[RoomPost], None] | None = None

    def status(self) -> RoomVoiceStatus:
        return RoomVoiceStatus(
            room_id="room-1",
            name=self.name,
            kind=self.kind,
            members=self.members,
            people_online=self.people,
        )

    def posts(self) -> list[RoomPost]:
        return list(self.current_posts)

    async def say_to(
        self, member: str, text: str, *, author: MessageAuthor | None, model_note: str
    ) -> SendOutcome:
        self.said.append((member, text, author, model_note))
        return self.outcome

    async def say_to_everyone(
        self, text: str, *, author: MessageAuthor | None, model_note: str
    ) -> SendOutcome:
        self.said_everyone.append((text, author, model_note))
        return self.everyone_outcome

    def listen(self, listener: Callable[[RoomPost], None] | None) -> None:
        self.listener = listener

    def record(self, frame: dict[str, Any]) -> None:
        self.records.append(frame)


class FakeOutlet(CallOutlet):
    """The phones on a call: records every frame pushed, in order, once it has been sent."""

    def __init__(self, *, reached: int = 1, yields: int = 1) -> None:
        self.reached = reached
        #: How many times a push gives up the loop before it is done (a socket write awaits).
        self.yields = yields
        self.frames: list[tuple[str, dict[str, Any]]] = []

    async def push(self, message_type: str, payload: dict[str, Any]) -> int:
        for _ in range(self.yields):
            await asyncio.sleep(0)
        self.frames.append((message_type, payload))
        return self.reached


def make_member(name: str, **overrides: Any) -> RoomMember:
    return RoomMember(name=name, **overrides)


def make_post(post_id: str = "p1", speaker: str = "Reviewer", **overrides: Any) -> RoomPost:
    fields: dict[str, Any] = {
        "post_id": post_id,
        "speaker": speaker,
        "kind": PostKind.ANSWER,
        "text": "Looks fine.",
    }
    fields.update(overrides)
    return RoomPost(**fields)


def make_room_binding(
    room: FakeRoom | None = None,
    *,
    card_recent_turns: int = 6,
    card_turn_max_chars: int = 1500,
    full_read_max_chars: int = FULL_READ_MAX_CHARS,
) -> RoomVoiceBinding:
    return RoomVoiceBinding(
        room or FakeRoom(),
        tool_timeout_seconds=60.0,
        full_read_max_chars=full_read_max_chars,
        card_recent_turns=card_recent_turns,
        card_turn_max_chars=card_turn_max_chars,
    )
