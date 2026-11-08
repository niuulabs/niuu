"""Apply a selected persona to a Forge session.

The session plays the pinned portable persona document, not a prompt string: Skuld projects
the document onto the session's harness (its instructions appended to the harness's own
prompt, its tool and permission limits enforced or refused). The contributor therefore emits
``values.session.persona`` and leaves ``values.session.systemPrompt`` to the launch request
and the launch spec. How they combine: the persona is the base and a request's ad-hoc prompt
is appended after the persona's instructions, never in place of them.

A launch may instead carry the exact pinned document (``persona_definition``, validated and
digest-checked when the request was accepted). It is played verbatim: the catalog is not
consulted, so a workflow run plays the revision it pinned even if the catalog has moved on.
"""

from volundr.domain.models import Session
from volundr.domain.ports import (
    SessionContext,
    SessionContribution,
    SessionContributor,
    SessionPersonaProvider,
)
from volundr.domain.workflow_session import PERSONA_DEFINITION_KEY, PERSONA_NAME_KEY


class PersonaContributor(SessionContributor):
    """Resolve the launch persona and apply its cognitive prompt."""

    def __init__(
        self,
        *,
        persona_provider: SessionPersonaProvider | None = None,
        **_extra: object,
    ) -> None:
        self._provider = persona_provider

    @property
    def name(self) -> str:
        return "persona"

    async def contribute(
        self,
        session: Session,
        context: SessionContext,
    ) -> SessionContribution:
        pinned = context.workload_config.get(PERSONA_DEFINITION_KEY)
        raw_name = context.workload_config.get(PERSONA_NAME_KEY)
        named = isinstance(raw_name, str) and bool(raw_name.strip())
        if pinned is not None and named:
            raise ValueError(
                "A launch names a persona and also carries a pinned persona document; "
                "send one or the other"
            )
        if pinned is not None:
            if not isinstance(pinned, dict) or not pinned:
                raise ValueError("The pinned persona document of a launch must be a mapping")
            return SessionContribution(values={"session": {"persona": pinned}})
        if not named:
            return SessionContribution()

        name = raw_name.strip()
        if self._provider is None:
            raise ValueError(
                f"Persona {name!r} was requested by name, but this Forge has no persona "
                "catalog; send the pinned document as persona_definition instead"
            )
        owner_id = context.principal.user_id if context.principal else ""
        persona = await self._provider.get(owner_id, name)
        if persona is None:
            raise ValueError(f"Persona not found: {name}")

        if not persona.document:
            raise ValueError(
                f"Persona {name!r} carries no pinned document, so it cannot be played by a "
                "session; the persona provider must return its portable document"
            )
        return SessionContribution(values={"session": {"persona": persona.document}})
