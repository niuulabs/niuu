"""What a persona allows, forbids and permits, resolved once for every projector.

``allowed_tools`` is a closed world when it is not empty: only the tools it names (and what
they imply) are available. When it is empty the persona is open and only ``forbidden_tools``
narrow it. ``forbidden_tools`` wins over ``allowed_tools``. ``permission_mode`` narrows the
workspace the persona may change; it never widens what the tool lists grant.

Implication, because a shell can run anything a narrower tool can:

``shell`` ⊃ ``git`` ⊃ ``git_read``, and ``shell`` ⊃ every command-backed capability
(``present_file``, ``tracker``, ``mimir``, ``mimir:<tool>``).

Forbidding a tool that a granted parent already contains (``git`` while ``shell`` is granted)
is a restriction no harness can enforce, so projectors refuse it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from personas.domain.permission_mode import PermissionMode, parse_optional_permission_mode
from personas.domain.tool_vocabulary import (
    FILE_WRITE,
    GIT,
    GIT_READ,
    MIMIR,
    SHELL,
    ToolRef,
    validate_tool_names,
)
from skuld.personas.tool_vocabulary import shell_commands

#: Modes that limit what the persona may change.
MODES_THAT_RESTRICT: frozenset[PermissionMode] = frozenset(
    {PermissionMode.READ_ONLY, PermissionMode.DENY_ALL, PermissionMode.PROMPT}
)

_READ_ONLY_REMOVES: frozenset[str] = frozenset({FILE_WRITE, GIT, SHELL})


def _is_builtin(ref: ToolRef, name: str) -> bool:
    return ref.kind == "builtin" and ref.name == name


@dataclass(frozen=True)
class PersonaPolicy:
    """The persona's tool lists and permission mode, parsed."""

    persona_id: str
    mode: PermissionMode | None
    allowed: tuple[ToolRef, ...]
    forbidden: tuple[ToolRef, ...]

    @classmethod
    def from_definition(cls, persona_id: str, definition: Mapping[str, Any]) -> PersonaPolicy:
        return cls(
            persona_id=persona_id,
            mode=parse_optional_permission_mode(definition.get("permission_mode")),
            allowed=tuple(
                validate_tool_names("allowed_tools", definition.get("allowed_tools", []))
            ),
            forbidden=tuple(
                validate_tool_names("forbidden_tools", definition.get("forbidden_tools", []))
            ),
        )

    @property
    def closed(self) -> bool:
        """True when ``allowed_tools`` lists the only tools the persona has."""
        return bool(self.allowed)

    @property
    def restricts_tools(self) -> bool:
        return self.closed or bool(self.forbidden)

    @property
    def restricts_mode(self) -> bool:
        return self.mode in MODES_THAT_RESTRICT

    @property
    def restricted(self) -> bool:
        """True when the persona limits the harness beyond its instructions."""
        return self.restricts_tools or self.restricts_mode

    def forbids(self, ref: ToolRef) -> bool:
        """True when ``forbidden_tools`` names the ref or the whole it belongs to."""
        for item in self.forbidden:
            if item == ref:
                return True
            if item.kind == ref.kind and item.name == ref.name and not item.tool:
                return True
        return False

    def lists(self, ref: ToolRef) -> bool:
        """True when ``allowed_tools`` names the ref or the whole it belongs to.

        An open persona lists everything.
        """
        if not self.closed:
            return True
        for item in self.allowed:
            if item == ref:
                return True
            if item.kind == ref.kind and item.name == ref.name and not item.tool:
                return True
        return False

    def shell_granted(self) -> bool:
        shell = ToolRef("builtin", SHELL)
        return self.lists(shell) and not self.forbids(shell)

    def contained_by_granted_shell(self, ref: ToolRef) -> bool:
        """True when ``ref`` is something a granted shell already contains."""
        if not self.shell_granted():
            return False
        if ref.kind == "builtin":
            return ref.name in (GIT, GIT_READ)
        return bool(shell_commands(ref)) or (ref.kind == "capability" and ref.name == MIMIR)

    def grants(self, ref: ToolRef) -> bool:
        """True when the persona has the tool as a tool, before any mode narrowing."""
        return self.lists(ref) and not self.forbids(ref)

    def grants_command(self, ref: ToolRef) -> bool:
        """True when the persona can run the command-backed ref: granted itself or by a shell."""
        if self.grants(ref):
            return True
        if ref.kind == "builtin" and ref.name == GIT_READ:
            return self.grants(ToolRef("builtin", GIT)) or self.shell_granted()
        if ref.kind == "builtin" and ref.name == GIT:
            return self.shell_granted()
        if self.forbids(ref):
            return False
        return bool(shell_commands(ref)) and self.shell_granted()

    def mode_removes(self, ref: ToolRef) -> bool:
        """True when ``read_only`` takes the tool away whatever the tool lists say."""
        return self.mode is PermissionMode.READ_ONLY and (
            ref.kind == "builtin" and ref.name in _READ_ONLY_REMOVES
        )

    def carve_outs(self) -> list[ToolRef]:
        """Forbidden tools that a granted shell contains, which no harness can take out."""
        return [ref for ref in self.forbidden if self.contained_by_granted_shell(ref)]
