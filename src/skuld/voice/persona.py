"""Who speaks on a voice call, and how its instructions are put together.

The voice is a persona's ``voice:`` section (its voices per provider and how it sounds),
taken from the session's own pinned persona when that has one, else from the persona
Forge configured as the default (``voice.default_persona``). Only the ``voice:`` section,
the persona's name and its description are used: the rest of a session's persona is the
coding agent's instructions, which the voice model must not follow as its own.

The instructions are the persona, then what the call is bound to
(:class:`~skuld.voice.binding.VoiceCallBinding`): how to behave on this call and the data
it opens with.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from personas.domain.persona_document import PortablePersonaDefinition
from skuld.voice.binding import VoiceCallBinding

#: The prompt's version, recorded in its fingerprint. The prompt carries no ``schema``
#: field: clients read an unnamed prompt as version 2.
PROMPT_VERSION = 2

#: Prompt providers the channel composes for, and the persona voice key each uses.
VOICE_KEYS = {"grok": "grok-voice", "gpt-realtime": "openai-realtime"}

#: Mint provider (what ``voice.ephemeral`` and Forge's ``voice.providers`` name) → prompt
#: provider.
MINT_PROVIDERS = {"xai": "grok", "openai": "gpt-realtime"}

#: Spellings the app and older clients send, normalised to a prompt provider.
_PROVIDER_ALIASES = {
    "grok": "grok",
    "xai": "grok",
    "grok-voice": "grok",
    "gpt-realtime": "gpt-realtime",
    "gpt": "gpt-realtime",
    "openai": "gpt-realtime",
    "realtime": "gpt-realtime",
    "openai-realtime": "gpt-realtime",
}

_SURFACES = {"ios": "their iPhone", "mac": "their Mac", "web": "the web"}


class UnsupportedProviderError(ValueError):
    """The channel composes no prompt for that provider."""


class VoicePersonaError(ValueError):
    """No persona this session can speak with."""


def normalize_provider(raw: str) -> str:
    provider = _PROVIDER_ALIASES.get(raw.strip().lower())
    if provider is None:
        raise UnsupportedProviderError(f"No voice prompt for provider {raw!r}")
    return provider


@dataclass(frozen=True)
class VoicePersona:
    """What the call needs from a persona with a ``voice:`` section."""

    name: str
    display_name: str
    description: str
    style: str
    voices: dict[str, str]
    grok_session: dict[str, Any]

    def voice_for(self, provider: str) -> str:
        key = VOICE_KEYS[provider]
        voice = self.voices.get(key)
        if not voice:
            raise UnsupportedProviderError(
                f"Persona {self.name!r} has no voice for {key}: add it under voice.voices"
            )
        return voice

    def default_voice(self) -> str:
        return self.voices.get(VOICE_KEYS["gpt-realtime"]) or next(iter(self.voices.values()))

    def context_prompt(self) -> str:
        """Who the voice is and how it sounds — no per-call preamble."""
        parts = [f"You are {self.display_name}."]
        if self.description.strip():
            parts[0] += " " + self.description.strip()
        if self.style.strip():
            parts.append("## How you sound\n\n" + self.style.strip())
        return "\n\n".join(parts)


def _voice_section(document: Mapping[str, Any]) -> Mapping[str, Any] | None:
    persona = PortablePersonaDefinition.from_dict(document)
    voice = persona.definition.get("voice")
    if not isinstance(voice, Mapping) or not voice.get("voices"):
        return None
    return voice


def resolve_voice_persona(
    session_persona: Mapping[str, Any] | None,
    default_persona: Mapping[str, Any] | None,
    *,
    display_name: str = "",
) -> VoicePersona:
    """The session's own voice persona, else the configured default; raises when neither.

    A configured persona that is malformed, or a default without a ``voice:`` section, is
    an error: it was asked for and cannot be used.
    """
    chosen = None
    if session_persona is not None and _voice_section(session_persona) is not None:
        chosen = session_persona
    if chosen is None and default_persona is not None:
        if _voice_section(default_persona) is None:
            raise VoicePersonaError(
                f"voice_channel.default_persona {default_persona.get('id')!r} has no voice: "
                "section with voices; give it one, or name another persona in Forge's "
                "voice.default_persona"
            )
        chosen = default_persona
    if chosen is None:
        raise VoicePersonaError(
            "voice_channel.enabled is true but neither the session's persona nor its default "
            "has a voice: section; Forge sends the default persona at launch, so set Forge's "
            "voice.default_persona, or set SKULD__VOICE_CHANNEL__ENABLED=false"
        )
    persona = PortablePersonaDefinition.from_dict(chosen)
    voice = persona.definition["voice"]
    name = str(persona.definition.get("name") or persona.id)
    return VoicePersona(
        name=name,
        display_name=display_name or name.capitalize(),
        description=str(persona.definition.get("description") or ""),
        style=str(voice.get("style") or ""),
        voices={str(k): str(v) for k, v in voice["voices"].items()},
        grok_session=dict(voice.get("grok") or {}),
    )


class VoicePromptComposer:
    """Compose a call's instructions and prompt: the persona, then what the call is bound to."""

    def __init__(
        self,
        persona: VoicePersona,
        *,
        models: Mapping[str, str],
        time_zone: str,
    ) -> None:
        missing = [provider for provider in VOICE_KEYS if not models.get(provider)]
        if missing:
            raise ValueError(f"voice.models is missing {missing}")
        self._persona = persona
        self._models = dict(models)
        self._zone = ZoneInfo(time_zone)

    @property
    def persona(self) -> VoicePersona:
        return self._persona

    def model_for(self, provider: str) -> str:
        return self._models[provider]

    def standing_instructions(self, binding: VoiceCallBinding) -> str:
        """The call's instructions without the per-call preamble (baked into a credential)."""
        return self._persona.context_prompt() + "\n\n" + binding.grounding()

    def instructions(self, binding: VoiceCallBinding, *, surface: str, conversation_id: str) -> str:
        """Who speaks, when and in which conversation, then the standing instructions."""
        now = datetime.now(self._zone)
        place = _SURFACES.get(surface, "their device")
        preamble = (
            f"You are {self._persona.display_name}, speaking with the owner of "
            f"{binding.subject} on a live voice call from {place}. It is "
            f"{now:%A %d %B %Y, %H:%M} ({self._zone.key}). This conversation's id is "
            f"{conversation_id}."
        )
        return preamble + "\n\n" + self.standing_instructions(binding)

    def prompt(
        self,
        raw_provider: str,
        binding: VoiceCallBinding,
        *,
        surface: str,
        conversation_id: str,
    ) -> dict[str, Any]:
        provider = normalize_provider(raw_provider)
        persona = self._persona
        session: dict[str, Any] = {
            "model": self.model_for(provider),
            "voice": persona.voice_for(provider),
        }
        if provider == "grok":
            session.update(persona.grok_session)
        tool_names = binding.tool_names
        prompt: dict[str, Any] = {
            "persona": persona.name,
            "displayName": persona.display_name,
            "provider": provider,
            "surface": surface,
            "conversationId": conversation_id,
            "generatedAt": datetime.now(self._zone).isoformat(),
            "instructions": self.instructions(
                binding, surface=surface, conversation_id=conversation_id
            ),
            "session": session,
            "fingerprint": {
                "version": PROMPT_VERSION,
                "persona": persona.name,
                "provider": provider,
                "surface": surface,
                "stackSha": _sha12(persona.context_prompt() + "|" + provider),
                "toolProfile": "voice",
                "toolCount": len(tool_names),
                "toolsSha": _sha12("|".join(sorted(tool_names))),
            },
        }
        if provider == "grok":
            # Grok searches the web itself; the app then drops a web_search function.
            prompt["webSearch"] = "native"
            prompt["providerTools"] = [{"type": "web_search"}]
        return prompt


def _sha12(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
