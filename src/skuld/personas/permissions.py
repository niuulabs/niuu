"""What a persona's permission mode permits, resolved once for every projector.

``permission_mode`` narrows the workspace the persona may change. Restricting tools per
harness (a persona naming the tools it may or may not use) is not available yet, so the mode
is the only boundary a persona puts around a harness besides its instructions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from personas.domain.permission_mode import PermissionMode, parse_optional_permission_mode

#: Modes that limit what the persona may change.
MODES_THAT_RESTRICT: frozenset[PermissionMode] = frozenset(
    {PermissionMode.READ_ONLY, PermissionMode.DENY_ALL, PermissionMode.PROMPT}
)


@dataclass(frozen=True)
class PersonaPolicy:
    """The persona's permission mode, parsed."""

    persona_id: str
    mode: PermissionMode | None

    @classmethod
    def from_definition(cls, persona_id: str, definition: Mapping[str, Any]) -> PersonaPolicy:
        return cls(
            persona_id=persona_id,
            mode=parse_optional_permission_mode(definition.get("permission_mode")),
        )

    @property
    def restricts_mode(self) -> bool:
        return self.mode in MODES_THAT_RESTRICT
