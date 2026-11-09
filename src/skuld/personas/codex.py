"""Project a persona onto Codex (app-server and exec).

What Codex can enforce:

* instructions ride as ``developerInstructions`` (app-server) or ``developer_instructions``
  (exec), additive to Codex's own prompt, and are sent again on every start and resume;
* ``read_only`` / ``workspace_write`` become Codex's OS sandbox (``read-only`` /
  ``workspace-write``) with approvals ``never``. The sandbox stops a read-only persona from
  changing files through the shell or ``apply_patch``.

What Codex cannot do, so the projection refuses it:

* ``prompt`` and ``deny_all`` permission modes.

Restricting a persona's tools per harness is not available yet, so nothing else in a persona
narrows what Codex is offered. The sandbox is only ever narrowed against the node's own
configuration, never widened.
"""

from __future__ import annotations

from typing import Any

from personas.domain.permission_mode import PermissionMode
from personas.domain.persona_document import PortablePersonaDefinition
from skuld.personas.permissions import PersonaPolicy
from skuld.personas.projection import (
    REFUSE,
    HarnessProjection,
    ProjectionContext,
    ProjectionGap,
    build_projection,
    compose_instructions,
)

#: Codex sandbox modes, narrowest first.
SANDBOX_RANK: dict[str, int] = {"read-only": 0, "workspace-write": 1, "danger-full-access": 2}
UNATTENDED_APPROVAL = "never"
_SANDBOX_FOR_MODE: dict[PermissionMode, str] = {
    PermissionMode.READ_ONLY: "read-only",
    PermissionMode.WORKSPACE_WRITE: "workspace-write",
}


def narrower_sandbox(node: str, persona: str) -> str:
    """The narrower of the node's sandbox and the persona's. ``node`` empty means unset."""
    if not node:
        return persona
    return min(node, persona, key=SANDBOX_RANK.__getitem__)


class CodexProjector:
    """Instructions and the sandbox for Codex transports."""

    engine = "codex"

    def project(
        self, persona: PortablePersonaDefinition, context: ProjectionContext
    ) -> HarnessProjection:
        policy = PersonaPolicy.from_definition(persona.id, persona.definition)
        gaps: list[ProjectionGap] = []
        kwargs: dict[str, Any] = {
            "system_prompt": compose_instructions(persona.definition, context.request_prompt)
        }
        self._permissions(policy, context, kwargs, gaps)
        return build_projection(self.engine, persona, context, kwargs, gaps)

    @staticmethod
    def _permissions(
        policy: PersonaPolicy,
        context: ProjectionContext,
        kwargs: dict[str, Any],
        gaps: list[ProjectionGap],
    ) -> None:
        if policy.mode in (PermissionMode.PROMPT, PermissionMode.DENY_ALL):
            gaps.append(
                ProjectionGap(
                    REFUSE,
                    f"permission_mode:{policy.mode.value}",
                    "Codex cannot ask an operator per action in an unattended session and has "
                    "no mode that denies every tool",
                )
            )
            return
        wanted = _SANDBOX_FOR_MODE.get(policy.mode) if policy.mode else None
        if wanted is None:
            return
        node = "danger-full-access" if context.node_skip_permissions else context.node_sandbox
        if node and node not in SANDBOX_RANK:
            gaps.append(
                ProjectionGap(
                    REFUSE,
                    "node:sandbox",
                    f"the node's sandbox {node!r} is not a Codex sandbox mode "
                    f"({', '.join(SANDBOX_RANK)}); fix the node's sandbox setting",
                )
            )
            return
        kwargs["skip_permissions"] = False
        kwargs["sandbox"] = narrower_sandbox(node, wanted)
        kwargs["approval_policy"] = context.node_approval_policy or UNATTENDED_APPROVAL
