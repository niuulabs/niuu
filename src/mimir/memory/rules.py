"""Which layers a session has, and what an agent in it may write where.

Design section 4. The rules the agent cannot change, for the layers of step 1:

==========  =================================================================
Layer       Agent in a session
==========  =================================================================
session     writes
project     writes pages and timeline rows under the project's write prefixes;
            anything else (other pages, facts, sources, retirements) becomes a
            proposal for the project's owners
org         proposes; an org admin approves
==========  =================================================================

A layer the session does not have is refused. Every refusal is counted and
names its rule; a proposal is not a refusal.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from mimir.memory.models import (
    ChangeAction,
    Layer,
    LayerKind,
    MemoryValidationError,
    SessionMemoryContext,
)
from niuu.cap_telemetry import record_cap_hit
from niuu.domain.mimir_facts import AuthorKind, FactAuthor, FactCaller

#: The cap_telemetry knob prefix every rule refusal is counted under.
REFUSAL_KNOB = "memory.write_rules"


class Rule(StrEnum):
    SESSION_WRITES = "session_layer_writes"
    PROJECT_WRITE_PREFIXES = "project_write_prefixes"
    PROJECT_PROPOSES_RECORDS = "project_proposes_records"
    ORG_PROPOSES = "org_proposes"
    LOWER_TRUST_PROPOSES = "lower_trust_proposes"
    LAYER_NOT_IN_SESSION = "layer_not_in_session"
    PROPOSAL_NOT_NEEDED = "proposal_not_needed"
    OPERATIONAL_SOURCE = "operational_source_refused"
    REPLACE_ACROSS_LAYERS = "replace_across_layers"


class RuleRefusalError(Exception):
    """A write the rules refuse. Raised only through :func:`refuse`, which counts it."""

    def __init__(self, rule: Rule, message: str) -> None:
        super().__init__(message)
        self.rule = rule


def refuse(rule: Rule, message: str) -> RuleRefusalError:
    """Count the refusal and return the exception to raise."""
    record_cap_hit(f"{REFUSAL_KNOB}.{rule.value}", message)
    return RuleRefusalError(rule, f"refused by rule {rule.value}: {message}")


class Verdict(StrEnum):
    WRITE = "write"
    PROPOSE = "propose"


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    rule: Rule
    reason: str


def approver_of(layer: Layer) -> str:
    """Who approves a proposal for *layer*."""
    if layer.kind is LayerKind.ORG:
        return "an org admin"
    if layer.kind is LayerKind.PROJECT:
        return f"the owners of project {layer.key}"
    return f"the owner of session {layer.key}"


def normalize_prefix(prefix: str) -> str:
    cleaned = prefix.strip().strip("/").lower()
    return f"{cleaned}/" if cleaned else ""


def under_prefixes(slug: str, prefixes: tuple[str, ...]) -> bool:
    """True when *slug* sits under one of *prefixes* (segment-wise: ``decisions/`` matches
    ``decisions/x``, never ``decisions-old/x``)."""
    path = slug.strip().strip("/").lower()
    return any(prefix and path.startswith(prefix) for prefix in map(normalize_prefix, prefixes))


@dataclass(frozen=True)
class SessionView:
    """A session as the gateway sees it: who it is and its layers, nearest first."""

    context: SessionMemoryContext
    system: str
    layers: tuple[Layer, ...]

    @classmethod
    def of(cls, context: SessionMemoryContext, system: str) -> SessionView:
        layers = [Layer(LayerKind.SESSION, context.session_id)]
        if context.project_id:
            layers.append(Layer(LayerKind.PROJECT, context.project_id))
        layers.append(Layer(LayerKind.ORG))
        return cls(context=context, system=system, layers=tuple(layers))

    @property
    def caller(self) -> FactCaller:
        """Sessions are agents (design section 3): the trust never comes from the request."""
        return FactCaller(FactAuthor(AuthorKind.AGENT, f"session:{self.context.session_id}"))

    @property
    def namespaces(self) -> tuple[str, ...]:
        return tuple(layer.namespace for layer in self.layers)

    def layer(self, name: str | None) -> Layer:
        """The session's layer *name* names (default: the session layer).

        Accepts ``session``, ``project`` and ``org`` and full ids (``project:<id>``).
        A layer the session does not have is a counted refusal.
        """
        wanted = (name or LayerKind.SESSION.value).strip().lower()
        for layer in self.layers:
            if wanted in (layer.id, layer.kind.value):
                return layer
        try:
            Layer.parse(wanted)
        except MemoryValidationError as exc:
            raise refuse(Rule.LAYER_NOT_IN_SESSION, str(exc)) from exc
        have = ", ".join(layer.id for layer in self.layers)
        raise refuse(
            Rule.LAYER_NOT_IN_SESSION,
            f"this session has no layer {wanted!r}; its layers are {have}",
        )

    def layer_of_namespace(self, namespace: str) -> Layer | None:
        return next((layer for layer in self.layers if layer.namespace == namespace), None)

    def decide(self, layer: Layer, action: ChangeAction, slug: str | None = None) -> Decision:
        """Whether an agent's *action* on *layer* is written or becomes a proposal."""
        if layer.kind is LayerKind.SESSION:
            return Decision(Verdict.WRITE, Rule.SESSION_WRITES, "the session layer is the agent's")
        if layer.kind is LayerKind.ORG:
            return Decision(
                Verdict.PROPOSE, Rule.ORG_PROPOSES, "agents propose changes to the org layer"
            )
        prefixes = self.context.project_write_prefixes
        if action in (ChangeAction.WRITE_PAGE, ChangeAction.ADD_TIMELINE):
            if slug is not None and under_prefixes(slug, prefixes):
                return Decision(
                    Verdict.WRITE, Rule.PROJECT_WRITE_PREFIXES, "the page is under a write prefix"
                )
            allowed = ", ".join(normalize_prefix(p) for p in prefixes if normalize_prefix(p))
            return Decision(
                Verdict.PROPOSE,
                Rule.PROJECT_WRITE_PREFIXES,
                f"{layer.id} takes agent page writes only under its write prefixes "
                f"({allowed or 'none set'})",
            )
        return Decision(
            Verdict.PROPOSE,
            Rule.PROJECT_PROPOSES_RECORDS,
            f"agents propose {action.value.replace('_', ' ')} changes to {layer.id}",
        )
