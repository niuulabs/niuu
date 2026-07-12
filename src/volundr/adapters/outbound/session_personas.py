"""Forge-facing adapter for the existing Ravn persona registry."""

from typing import Any

from volundr.domain.ports import SessionPersona, SessionPersonaProvider


class RegistrySessionPersonaProvider(SessionPersonaProvider):
    """Project Ravn registry records into Forge's narrow persona contract."""

    def __init__(self, registry: Any) -> None:
        self._registry = registry

    async def get(self, owner_id: str, name: str) -> SessionPersona | None:
        view = await self._registry.get_persona(owner_id, name)
        if view is None:
            return None
        return SessionPersona(
            name=view.config.name,
            system_prompt=view.config.system_prompt_template,
        )
