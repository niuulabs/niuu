"""Apply a selected Ravn persona to a Forge session."""

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

        if not persona.system_prompt:
            return SessionContribution()
        return SessionContribution(values={"session": {"systemPrompt": persona.system_prompt}})
