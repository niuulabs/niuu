"""Shared builders for the session voice channel tests (``skuld.voice``)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from niuu.domain.message_author import MessageAuthor
from skuld.voice.calls import VoiceCallBook
from skuld.voice.channel import SessionVoiceChannel, register_voice_routes
from skuld.voice.minting import MintedCredential, VoiceMintError
from skuld.voice.persona import VoicePromptComposer, resolve_voice_persona
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


def make_composer(
    *,
    persona: dict[str, Any] | None = None,
    tool_names: list[str] | None = None,
    card_recent_turns: int = 6,
    card_turn_max_chars: int = 1500,
    time_zone: str = "UTC",
) -> VoicePromptComposer:
    voice_persona = resolve_voice_persona(None, persona or persona_document())
    return VoicePromptComposer(
        voice_persona,
        models=MODELS,
        time_zone=time_zone,
        tool_names=tool_names or [],
        card_recent_turns=card_recent_turns,
        card_turn_max_chars=card_turn_max_chars,
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
    tools = SessionVoiceTools(
        session, tool_timeout_seconds=60.0, full_read_max_chars=full_read_max_chars
    )
    channel = SessionVoiceChannel(
        session=session,
        composer=make_composer(
            persona=persona, tool_names=tools.names, card_turn_max_chars=card_turn_max_chars
        ),
        tools=tools,
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
