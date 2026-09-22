"""Read-only exact persona source for workflow-scoped portable definitions."""

from __future__ import annotations

import dataclasses
from typing import Any

import yaml

from ravn.adapters.personas.loader import (
    FilesystemPersonaAdapter,
    PersonaConfig,
    _apply_outcome_instruction,
)
from ravn.domain.persona_document import (
    PortablePersonaDefinition,
    parse_portable_persona,
)
from ravn.ports.persona import PersonaPort


class InlinePersonaAdapter(PersonaPort):
    """Serve immutable portable definitions embedded in a runtime config.

    The mapping key is the workflow-local alias. The portable document keeps
    the source persona's stable identity; returned configs use the alias so
    workflow graph routing remains local and deterministic.
    """

    def __init__(self, *, definitions: dict[str, dict[str, Any]]) -> None:
        self._definitions = {
            str(alias): parse_portable_persona(document) for alias, document in definitions.items()
        }

    def load(self, name: str) -> PersonaConfig | None:
        document = self._definitions.get(name)
        if document is None:
            return None
        config = FilesystemPersonaAdapter.parse(
            yaml.safe_dump(document.definition, allow_unicode=True, sort_keys=False)
        )
        if config is None:
            raise ValueError(f"Portable persona definition for {name!r} could not be parsed")
        return _apply_outcome_instruction(dataclasses.replace(config, name=name))

    def load_portable(
        self,
        persona_id: str,
        revision: str,
    ) -> PortablePersonaDefinition | None:
        for document in self._definitions.values():
            if document.id == persona_id and document.revision == revision:
                return document
        return None

    def list_names(self) -> list[str]:
        return sorted(self._definitions)

    def load_current_portable(
        self,
        persona_id: str,
    ) -> PortablePersonaDefinition | None:
        candidates = [
            document for document in self._definitions.values() if document.id == persona_id
        ]
        if len(candidates) > 1:
            raise ValueError(f"Multiple inline revisions exist for persona {persona_id!r}")
        return candidates[0] if candidates else None
