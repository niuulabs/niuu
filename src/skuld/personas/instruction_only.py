"""Project a persona onto harnesses that take instructions but enforce no tool boundary.

OpenCode, Grok, Pi and dsh each run every tool they have under their own always-approve
policy, and Muse and the Claude/Codex remote-control transports take no instructions at all.
For them a persona can only be its instructions, so:

* the instructions are delivered where the transport takes them (appended to the harness's
  own prompt: OpenCode's per-message ``system`` part, Pi's ``--append-system-prompt``, dsh's
  ``DSH_SYSTEM_PROMPT``, Grok's ACP ``systemPrompt``);
* any tool restriction (``allowed_tools``, ``forbidden_tools``) or restricting permission
  mode (``read_only``, ``deny_all``, ``prompt``) is refused, because the harness would run
  with every tool and write freely;
* a harness that cannot take instructions refuses a persona that has any.

``workspace_write`` is a note: these harnesses run inside the session runtime, which is the
workspace boundary.
"""

from __future__ import annotations

from typing import Literal

from personas.domain.permission_mode import PermissionMode
from personas.domain.persona_document import PortablePersonaDefinition
from skuld.personas.permissions import PersonaPolicy
from skuld.personas.projection import (
    NOTE,
    REFUSE,
    HarnessProjection,
    ProjectionContext,
    ProjectionGap,
    build_projection,
    compose_instructions,
)

InstructionDelivery = Literal["append", "unverified", "unsupported"]


class InstructionOnlyProjector:
    """Instructions where the harness takes them, a refusal for anything it cannot enforce."""

    def __init__(
        self,
        engine: str,
        *,
        delivery: InstructionDelivery,
        why: str = "",
    ) -> None:
        self.engine = engine
        self._delivery = delivery
        self._why = why

    def project(
        self, persona: PortablePersonaDefinition, context: ProjectionContext
    ) -> HarnessProjection:
        policy = PersonaPolicy.from_definition(persona.id, persona.definition)
        gaps: list[ProjectionGap] = []
        for field_name, tools in (
            ("allowed_tools", policy.allowed),
            ("forbidden_tools", policy.forbidden),
        ):
            if tools:
                listed = ", ".join(ref.text for ref in tools)
                gaps.append(
                    ProjectionGap(
                        REFUSE,
                        f"{field_name}:{listed}",
                        f"{self.engine} cannot restrict its tools; it would run with all of them",
                    )
                )
        if policy.restricts_mode and policy.mode is not None:
            gaps.append(
                ProjectionGap(
                    REFUSE,
                    f"permission_mode:{policy.mode.value}",
                    f"{self.engine} cannot enforce this mode; it runs every tool without "
                    "asking and can write freely",
                )
            )
        if policy.mode is PermissionMode.WORKSPACE_WRITE:
            gaps.append(
                ProjectionGap(
                    NOTE,
                    "permission_mode:workspace_write",
                    f"{self.engine} cannot confine writes to the workspace; the session "
                    "runtime is the boundary",
                )
            )
        kwargs: dict[str, object] = {}
        instructions = compose_instructions(persona.definition, context.request_prompt)
        if self._delivery == "unsupported":
            if instructions:
                gaps.append(
                    ProjectionGap(
                        REFUSE,
                        "system_prompt_template",
                        self._why or f"{self.engine} cannot take system instructions",
                    )
                )
        else:
            kwargs["system_prompt"] = instructions
        if self._delivery == "unverified" and instructions:
            gaps.append(
                ProjectionGap(
                    NOTE,
                    "system_prompt_template",
                    self._why or f"{self.engine} may ignore the instructions it is sent",
                )
            )
        return build_projection(self.engine, persona, context, kwargs, gaps)
