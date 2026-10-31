"""The voice prompt: the resident's persona, spoken.

``persona.get`` answers with the persona's voice context (identity plus how it
sounds). ``GET /api/voice/prompt`` answers with the composed prompt (version 2): the
same context opened by a preamble (who is speaking, when, and the conversation
id), the provider's session fields, and native web search for Grok. The device
appends only what it alone knows (its runtime context and the resume block).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

#: The prompt's version, recorded in its fingerprint. The prompt itself carries no ``schema``
#: field: clients read an unnamed prompt as version 2.
PROMPT_VERSION = 2

#: Prompt providers this channel composes for, and the persona voice key each uses.
VOICE_KEYS = {"grok": "grok-voice", "gpt-realtime": "openai-realtime"}

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

_SURFACES = {
    "ios": "their iPhone",
    "mac": "their Mac",
    "web": "the web",
}

_HANG_UP_RULE = (
    "When the operator says goodbye or asks you to end the call, say a short goodbye "
    "and call end_conversation."
)

_TOOLS_RULE = (
    "Use your tools for anything current or anything you would otherwise guess. While a "
    "tool runs, say one short line such as 'let me check', then answer from its result. "
    "Hand longer work to a session, a workflow or another agent and say where the result "
    "will land."
)


class UnsupportedProviderError(ValueError):
    """The channel composes no prompt for that provider."""


def normalize_provider(raw: str) -> str:
    provider = _PROVIDER_ALIASES.get(raw.strip().lower())
    if provider is None:
        raise UnsupportedProviderError(f"No voice prompt for provider {raw!r}")
    return provider


@dataclass(frozen=True)
class VoicePersona:
    """What the prompt needs from a persona with a ``voice:`` section."""

    name: str
    display_name: str
    identity: str
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

    def context_prompt(self) -> str:
        """Identity and how the persona sounds — no per-call preamble."""
        parts = [self.identity.strip()]
        if self.style.strip():
            parts.append("## How you sound\n\n" + self.style.strip())
        return "\n\n".join(part for part in parts if part)


class VoicePromptComposer:
    """Compose the per-call prompt for one persona."""

    def __init__(
        self,
        persona: VoicePersona,
        *,
        models: dict[str, str],
        time_zone: str,
        tool_names: list[str],
    ) -> None:
        missing = [provider for provider in VOICE_KEYS if not models.get(provider)]
        if missing:
            raise ValueError(f"Voice channel models are missing {missing}")
        self._persona = persona
        self._models = dict(models)
        self._zone = ZoneInfo(time_zone)
        self._tool_names = list(tool_names)

    def model_for(self, provider: str) -> str:
        return self._models[provider]

    def standing_instructions(self) -> str:
        """The persona's call instructions without the per-call preamble (minted credentials)."""
        parts = [self._persona.context_prompt(), "## Tools\n\n" + _TOOLS_RULE, _HANG_UP_RULE]
        return "\n\n".join(parts)

    def instructions(self, *, surface: str, conversation_id: str) -> str:
        """The call's instructions: who speaks, when and in which conversation, then the rest."""
        persona = self._persona
        now = datetime.now(self._zone)
        place = _SURFACES.get(surface, "their device")
        preamble = (
            f"You are {persona.display_name}, speaking with your operator on a live voice "
            f"call from {place}. It is {now:%A %d %B %Y, %H:%M} ({self._zone.key}). "
            f"This conversation's id is {conversation_id}."
        )
        return preamble + "\n\n" + self.standing_instructions()

    def prompt(self, raw_provider: str, *, surface: str, conversation_id: str) -> dict[str, Any]:
        provider = normalize_provider(raw_provider)
        persona = self._persona
        instructions = self.instructions(surface=surface, conversation_id=conversation_id)
        session: dict[str, Any] = {
            "model": self.model_for(provider),
            "voice": persona.voice_for(provider),
        }
        if provider == "grok":
            session.update(persona.grok_session)
        prompt: dict[str, Any] = {
            "persona": persona.name,
            "displayName": persona.display_name,
            "provider": provider,
            "surface": surface,
            "conversationId": conversation_id,
            "generatedAt": datetime.now(self._zone).isoformat(),
            "instructions": instructions,
            "session": session,
            "fingerprint": {
                "version": PROMPT_VERSION,
                "persona": persona.name,
                "provider": provider,
                "surface": surface,
                "stackSha": _sha12(persona.context_prompt() + "|" + provider),
                "toolProfile": "voice",
                "toolCount": len(self._tool_names),
                "toolsSha": _sha12("|".join(sorted(self._tool_names))),
            },
        }
        if provider == "grok":
            # Grok searches the web itself; the app then drops a web_search function.
            prompt["webSearch"] = "native"
            prompt["providerTools"] = [{"type": "web_search"}]
        return prompt


def _sha12(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
