"""Apply a selected persona to a Forge session.

The session plays the pinned portable persona document, not a prompt string: Skuld projects
the document onto the session's harness (its instructions appended to the harness's own
prompt, its tool and permission limits enforced or refused). The contributor therefore emits
``values.session.persona`` and leaves ``values.session.systemPrompt`` to the launch request
and the launch spec. How they combine: the persona is the base and a request's ad-hoc prompt
is appended after the persona's instructions, never in place of them.
"""

from volundr.domain.models import Session
from volundr.domain.ports import (
    SessionContext,
    SessionContribution,
    SessionContributor,
    SessionPersonaProvider,
)


class PersonaContributor(SessionContributor):
    """Resolve the launch persona and apply its cognitive prompt."""

    def __init__(
        self,
        *,
        persona_provider: SessionPersonaProvider,
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
        raw_name = context.workload_config.get("persona")
        if not isinstance(raw_name, str) or not raw_name.strip():
            return SessionContribution()

        name = raw_name.strip()
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
