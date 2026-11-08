"""The contract between a persona and the harness that plays it.

A persona is a pinned portable document (``personas.domain.persona_document``). The harness
is whatever transport Skuld constructed for the session. A :class:`PersonaProjector` turns the
first into the constructor arguments, files and gaps of the second:

* ``kwargs`` are merged over the broker's transport kwargs before the transport is built;
* ``files`` are written to the per-start runtime dir, never to the shared home or the
  workspace;
* ``gaps`` say what the harness cannot do. A ``refuse`` gap stops the session from starting:
  a persona restriction the harness cannot enforce is never dropped, and a read-only persona
  is never run on a harness that could write. A ``note`` gap is something the persona asked
  for that the harness delegates to the session's runtime or cannot provide; it is logged and
  reported in the broker's startup status.

The projection is a pure function of the persona and the :class:`ProjectionContext`, so a
resume projects exactly what the first start did.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from niuu.domain.outcome import OutcomeSchema, generate_outcome_instruction
from personas.domain.persona import parse_produces
from personas.domain.persona_document import PortablePersonaDefinition

GapSeverity = Literal["refuse", "note"]
REFUSE: GapSeverity = "refuse"
NOTE: GapSeverity = "note"


class PersonaProjectionError(RuntimeError):
    """The harness cannot play the persona; the message carries every refusal and a remedy."""


@dataclass(frozen=True)
class ProjectionGap:
    """One thing the harness cannot do for the persona."""

    severity: GapSeverity
    subject: str
    message: str

    def describe(self) -> str:
        return f"{self.subject}: {self.message}"


@dataclass(frozen=True)
class ProjectedFile:
    """A file the projection needs; ``name`` is a plain file name in the runtime dir."""

    name: str
    content: str

    def __post_init__(self) -> None:
        if not self.name or Path(self.name).name != self.name or self.name.startswith("."):
            raise ValueError(
                f"projected file {self.name!r} must be a plain file name; persona files live "
                "only in the per-start runtime dir"
            )


@dataclass(frozen=True)
class ProjectionContext:
    """What a projector may know about the session beyond the persona.

    ``node_*`` fields are the operator's own configuration of this Skuld node. A projection
    may narrow them for a persona and never widens them.
    """

    transport: str
    runtime_dir: Path
    request_prompt: str = ""
    mcp_servers: tuple[Mapping[str, Any], ...] = ()
    platform_mcp_servers: tuple[str, ...] = ()
    node_skip_permissions: bool = False
    node_claude_permission_mode: str = ""
    node_approval_policy: str = ""
    node_sandbox: str = ""
    supports_read_only_mcp_boundary: bool = False

    def mcp_server_names(self) -> list[str]:
        return [str(server.get("name") or "") for server in self.mcp_servers if server.get("name")]


@dataclass(frozen=True)
class HarnessProjection:
    """The persona as one harness will play it."""

    engine: str
    kwargs: dict[str, Any] = field(default_factory=dict)
    gaps: tuple[ProjectionGap, ...] = ()
    files: tuple[ProjectedFile, ...] = ()

    @property
    def refusals(self) -> tuple[ProjectionGap, ...]:
        return tuple(gap for gap in self.gaps if gap.severity == REFUSE)

    @property
    def notes(self) -> tuple[ProjectionGap, ...]:
        return tuple(gap for gap in self.gaps if gap.severity == NOTE)

    def require_playable(self, persona_id: str, transport: str) -> None:
        """Raise :class:`PersonaProjectionError` unless the harness can play the persona."""
        if not self.refusals:
            return
        reasons = "; ".join(gap.describe() for gap in self.refusals)
        raise PersonaProjectionError(
            f"Persona {persona_id!r} cannot run on {self.engine} ({transport}): {reasons}. "
            "Launch it on a harness that can enforce this, or change the persona's "
            "allowed_tools, forbidden_tools or permission_mode."
        )


class PersonaProjector(Protocol):
    """Turns a persona into one engine family's transport configuration."""

    engine: str

    def project(
        self, persona: PortablePersonaDefinition, context: ProjectionContext
    ) -> HarnessProjection: ...


def compose_instructions(definition: Mapping[str, Any], request_prompt: str) -> str:
    """The persona's instructions with the request's ad-hoc prompt appended after them.

    The persona is the base; a request prompt adds to it and never replaces it. The outcome
    block instruction a persona's ``produces.schema`` implies is part of the persona.
    """
    prompt = str(definition.get("system_prompt_template") or "")
    produces = parse_produces(definition.get("produces"))
    if produces.schema:
        instruction = generate_outcome_instruction(OutcomeSchema(fields=produces.schema))
        if instruction not in prompt:
            prompt = f"{prompt}\n\n{instruction}".strip()
    return "\n\n".join(part for part in (prompt.strip(), request_prompt.strip()) if part)


PERSONA_FILE = "persona.json"


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    return value


def build_projection(
    engine: str,
    persona: PortablePersonaDefinition,
    context: ProjectionContext,
    kwargs: dict[str, Any],
    gaps: Sequence[ProjectionGap],
) -> HarnessProjection:
    """Assemble a projection and the ``persona.json`` that records what was made of the persona.

    The file goes to the per-start runtime dir (the broker writes it there) so an operator can
    read the exact boundary a session runs under; the instructions are not repeated in it.
    """
    summary: dict[str, Any] = {"engine": engine, "transport": context.transport}
    summary |= {key: _jsonable(value) for key, value in kwargs.items() if key != "system_prompt"}
    summary["gaps"] = [f"{gap.severity}: {gap.describe()}" for gap in gaps]
    document = json.dumps({"persona": persona.to_dict(), "projection": summary}, indent=2)
    return HarnessProjection(engine, kwargs, tuple(gaps), (ProjectedFile(PERSONA_FILE, document),))
