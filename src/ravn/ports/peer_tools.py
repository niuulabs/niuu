"""Port for the tools a resident's flock peers already have.

A resident builds a tool without knowing a peer runs the same capability, so the
flock pays for it twice. Peers advertise what they run; this port is how the
``build_tool`` gate reads that and asks for a peer's tool to be installed here.
Deciding that a request and a peer's tool are the same capability stays with
:class:`~ravn.ports.capability_matching.CapabilityMatcherPort`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from ravn.ports.capability_matching import CapabilityDescriptor

PeerAdoptionOutcome = Literal["adopted", "held", "refused"]


@dataclass(frozen=True)
class PeerTool:
    """One learned tool as a flock peer last reported it."""

    name: str
    artifact_id: str
    #: The ``valkyrie_id`` reporting the tool, which holds a runnable copy.
    valkyrie_id: str
    environment_id: str
    manifest: dict[str, Any]
    tool_code: str
    test_code: str = ""
    requirements: tuple[str, ...] = ()
    capability_id: str = ""
    #: Input the peer's own canary ran on; empty when it never recorded one.
    canary_sample: dict[str, Any] = field(default_factory=dict)
    runs: int = 0
    successes: int = 0
    reported_at: str = ""

    @property
    def description(self) -> str:
        return str(self.manifest.get("description") or "")

    @property
    def inputs(self) -> tuple[str, ...]:
        schema = self.manifest.get("input_schema")
        properties = schema.get("properties") if isinstance(schema, dict) else None
        return tuple(sorted(properties)) if isinstance(properties, dict) else ()

    def descriptor(self) -> CapabilityDescriptor:
        return CapabilityDescriptor(
            key=f"peer:{self.artifact_id}",
            name=self.name,
            description=self.description,
            inputs=self.inputs,
        )


@dataclass(frozen=True)
class PeerAdoption:
    """What became of asking to install a peer's tool here."""

    outcome: PeerAdoptionOutcome
    detail: str
    #: The name it was installed under, when ``outcome`` is ``adopted``.
    tool_name: str = ""
    #: False when it was refused on sight, with no review, job or canary run.
    attempted: bool = True


class PeerToolPort(Protocol):
    """Read what peers run, and try to install one of their tools here."""

    def peer_tools(self) -> Sequence[PeerTool]:
        """Every verified tool heard from a peer in a shared flock.

        Empty until a peer has been heard from, which is an answer and not a
        failure.
        """
        ...

    async def adopt_peer_tool(self, tool: PeerTool) -> PeerAdoption:
        """Install *tool* here through the resident install pipeline.

        ``refused`` is a decision about this tool on this resident. Failing to
        reach one raises.
        """
        ...
