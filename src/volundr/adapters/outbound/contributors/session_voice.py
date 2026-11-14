"""Session voice contributor: turns on Skuld's voice channel for every session.

When Forge runs with ``voice.enabled`` every session it launches gets
``SKULD__VOICE__ENABLED=true``, and the persona document the channel falls back to
(``SKULD__VOICE__DEFAULT_PERSONA``): the persona the operator named in
``voice.default_persona``, resolved from the owner's own catalog at launch. Skuld serves the
channel (``/voice``) for the session's owner, and plays the voice of the session's own
persona when that persona has a ``voice:`` section, the default otherwise. Skuld decides
which, so the default is always sent.

The variables travel as pod-spec env, which every pod manager hands to Skuld, so the setting
reaches a session whichever runtime starts it. The contributor also reports the launch fact
``voice_channel`` (``session_launch_facts``), which is what lets Forge advertise the
session's voice endpoint only for a launch that really runs the channel.

A persona that is missing, or has no voice, is a launch error naming the persona and the
remedy: the operator asked for voice, so a session never starts without it. Disabled
(``voice.enabled: false``) is the operator's decision to go without voice and wires nothing.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from volundr.domain.models import PodSpecAdditions, Session
from volundr.domain.ports import (
    SessionContext,
    SessionContribution,
    SessionContributor,
    SessionPersonaProvider,
)
from volundr.domain.session_launch_facts import VOICE_CHANNEL_FACT

#: Skuld's ``voice.enabled`` / ``voice.default_persona`` settings, in their environment form.
VOICE_ENABLED_ENV = "SKULD__VOICE__ENABLED"
VOICE_DEFAULT_PERSONA_ENV = "SKULD__VOICE__DEFAULT_PERSONA"


class SessionVoiceContributor(SessionContributor):
    """Emit the voice channel settings for every session."""

    def __init__(
        self,
        *,
        default_persona: str,
        persona_provider: SessionPersonaProvider | None = None,
        **_extra: object,
    ) -> None:
        self._default_persona = default_persona.strip()
        if not self._default_persona:
            raise ValueError(
                "The session voice contributor needs a persona with a voice: section: set "
                "voice.default_persona, or set voice.enabled: false to run sessions without "
                "a voice channel"
            )
        if persona_provider is None:
            raise ValueError(
                "The session voice contributor reads voice.default_persona "
                f"({self._default_persona!r}) from the persona catalog, but this Forge has no "
                "persona catalog: configure one, or set voice.enabled: false"
            )
        self._provider = persona_provider

    @property
    def name(self) -> str:
        return "session_voice"

    async def contribute(
        self,
        session: Session,
        context: SessionContext,
    ) -> SessionContribution:
        owner_id = context.principal.user_id if context.principal else session.owner_id
        persona = await self._provider.get(owner_id, self._default_persona)
        if persona is None:
            raise ValueError(
                f"voice.default_persona {self._default_persona!r} is not in the persona "
                f"catalog of {owner_id!r}: create that persona, or point voice.default_persona "
                "at one that exists"
            )
        if not persona.document:
            raise ValueError(
                f"voice.default_persona {self._default_persona!r} carries no pinned document, "
                "so a session cannot play it; the persona provider must return its portable "
                "document"
            )
        if not _has_voices(persona.document):
            raise ValueError(
                f"voice.default_persona {self._default_persona!r} has no voice: section "
                "with at least one voice; add one to the persona, or point "
                "voice.default_persona at a persona that has one"
            )
        return SessionContribution(
            pod_spec=PodSpecAdditions(
                env=(
                    {"name": VOICE_ENABLED_ENV, "value": "true"},
                    {"name": VOICE_DEFAULT_PERSONA_ENV, "value": json.dumps(persona.document)},
                )
            ),
            launch_facts={VOICE_CHANNEL_FACT: True},
        )


def _has_voices(document: Mapping[str, object]) -> bool:
    definition = document.get("definition")
    if not isinstance(definition, Mapping):
        return False
    voice = definition.get("voice")
    return isinstance(voice, Mapping) and bool(voice.get("voices"))
