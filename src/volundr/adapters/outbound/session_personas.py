"""Forge-facing adapter for the existing Ravn persona registry."""

from typing import Any

from niuu.domain.outcome import OutcomeSchema, generate_outcome_instruction
from volundr.domain.ports import SessionPersona, SessionPersonaProvider


class RegistrySessionPersonaProvider(SessionPersonaProvider):
    """Project Ravn registry records into Forge's narrow persona contract."""

    def __init__(self, registry: Any) -> None:
        self._registry = registry

    async def get(self, owner_id: str, name: str) -> SessionPersona | None:
        view = await self._registry.get_persona(owner_id, name)
        if view is None:
            return None
        config = view.config
        system_prompt = config.system_prompt_template
        if config.produces.schema:
            instruction = generate_outcome_instruction(OutcomeSchema(config.produces.schema))
            if instruction not in system_prompt:
                system_prompt = f"{system_prompt}\n\n{instruction}".strip()
        return SessionPersona(
            name=config.name,
            system_prompt=system_prompt,
            consumes_event_types=tuple(config.consumes.event_types),
            produces_event_type=config.produces.event_type,
            produces_schema=dict(config.produces.schema),
        )
