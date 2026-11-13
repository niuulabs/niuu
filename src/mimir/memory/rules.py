"""Which layers a session has, and what an agent in it may write where.

Design section 4. The rules the agent cannot change:

==========  =================================================================
Layer       Agent in a session
==========  =================================================================
session     writes
run         writes: Forge names the run only for a session that is one of its
            members
project     writes pages and timeline rows under the project's write prefixes;
            anything else (other pages, facts, sources, retirements) becomes a
            proposal for the project's owners
attached    as the attachment says: ``read`` refuses every write;
            ``read_write`` takes pages and timeline rows under its write
            prefixes and refuses the rest. Nothing becomes a proposal: the
            memory's own owner curates it, outside the gateway
org         proposes; an org admin approves
==========  =================================================================

A layer the session does not have is refused. Every refusal is counted and
names its rule; a proposal is not a refusal.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from mimir.memory.models import (
    Attachment,
    AttachmentAccess,
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
    RUN_MEMBER_WRITES = "run_member_writes"
    ATTACHED_READ_ONLY = "attached_read_only"
    ATTACHED_WRITE_PREFIXES = "attached_write_prefixes"
    ATTACHED_PAGES_ONLY = "attached_pages_only"
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
    if layer.kind is LayerKind.RUN:
        return f"the owner of run {layer.key}"
    if layer.kind is LayerKind.ATTACHED:
        return f"the owner of attached memory {layer.key}"
    return f"the owner of session {layer.key}"


def normalize_prefix(prefix: str) -> str:
    cleaned = prefix.strip().strip("/").lower()
    return f"{cleaned}/" if cleaned else ""


def under_prefixes(slug: str, prefixes: tuple[str, ...]) -> bool:
    """True when *slug* sits under one of *prefixes* (segment-wise: ``decisions/`` matches
    ``decisions/x``, never ``decisions-old/x``)."""
    path = slug.strip().strip("/").lower()
    return any(prefix and path.startswith(prefix) for prefix in map(normalize_prefix, prefixes))


@dataclass(frozen=True, kw_only=True)
class LayerView:
    """Layers someone reads, nearest first, and the memory system holding the org's own.

    A session's view (:class:`SessionView`) also writes; a person's reading view
    (:meth:`for_reader`) only reads: the gateway's write paths take a session's view.
    """

    tenant_id: str
    system: str
    layers: tuple[Layer, ...]
    attachments: tuple[Attachment, ...] = ()
    #: Who holds these layers, in refusals ("this session", "you").
    holder: str = "you"

    @classmethod
    def for_reader(
        cls,
        *,
        tenant_id: str,
        system: str,
        project_ids: tuple[str, ...],
        attachments: tuple[Attachment, ...] = (),
    ) -> LayerView:
        """A person's reading view: the projects named, their attachments, and the org."""
        layers = [Layer(LayerKind.PROJECT, project_id.lower()) for project_id in project_ids]
        layers.extend(_attached_layers(attachments, "you"))
        layers.append(Layer(LayerKind.ORG))
        return cls(
            tenant_id=tenant_id, system=system, layers=tuple(layers), attachments=attachments
        )

    def attachment(self, layer: Layer) -> Attachment:
        """The attachment behind an attached layer of this view."""
        for attachment in self.attachments:
            if attachment.name == layer.key:
                return attachment
        raise MemoryValidationError(f"{layer.id} is not one of {self._whose} attachments")

    @property
    def namespaces(self) -> tuple[str, ...]:
        return tuple(layer.namespace for layer in self.layers)

    @property
    def _whose(self) -> str:
        return "your" if self.holder == "you" else f"{self.holder}'s"

    @property
    def _possessive(self) -> str:
        return "your" if self.holder == "you" else "its"

    def layer(self, name: str | None) -> Layer:
        """The layer *name* names (default: the session layer).

        Accepts ``session``, ``run``, ``project`` and ``org`` and full ids
        (``project:<id>``, ``attached:<name>``); an attached layer is named in full.
        A layer the view does not have is a counted refusal.
        """
        wanted = (name or LayerKind.SESSION.value).strip().lower()
        for layer in self.layers:
            if wanted == layer.id:
                return layer
            if wanted == layer.kind.value and layer.kind is not LayerKind.ATTACHED:
                return layer
        try:
            Layer.parse(wanted)
        except MemoryValidationError as exc:
            raise refuse(Rule.LAYER_NOT_IN_SESSION, str(exc)) from exc
        have = ", ".join(layer.id for layer in self.layers)
        raise refuse(
            Rule.LAYER_NOT_IN_SESSION,
            f"{self.holder} {'have' if self.holder == 'you' else 'has'} no layer {wanted!r}; "
            f"{self._possessive} layers are {have}",
        )

    def layer_of_namespace(self, namespace: str) -> Layer | None:
        return next((layer for layer in self.layers if layer.namespace == namespace), None)


@dataclass(frozen=True, kw_only=True)
class SessionView(LayerView):
    """A session as the gateway sees it: who it is and its layers, nearest first."""

    context: SessionMemoryContext
    holder: str = "this session"

    @classmethod
    def of(cls, context: SessionMemoryContext, system: str) -> SessionView:
        layers = [Layer(LayerKind.SESSION, context.session_id)]
        if context.workflow_run_id:
            layers.append(Layer(LayerKind.RUN, context.workflow_run_id.lower()))
        if context.project_id:
            layers.append(Layer(LayerKind.PROJECT, context.project_id))
        layers.extend(_attached_layers(context.attached, f"session {context.session_id}"))
        layers.append(Layer(LayerKind.ORG))
        return cls(
            context=context,
            tenant_id=context.tenant_id,
            system=system,
            layers=tuple(layers),
            attachments=context.attached,
        )

    @property
    def caller(self) -> FactCaller:
        """Sessions are agents (design section 3): the trust never comes from the request."""
        return FactCaller(FactAuthor(AuthorKind.AGENT, f"session:{self.context.session_id}"))

    def decide(self, layer: Layer, action: ChangeAction, slug: str | None = None) -> Decision:
        """Whether an agent's *action* on *layer* is written or becomes a proposal.

        Raises RuleRefusalError for a write an attached layer does not take.
        """
        if layer.kind is LayerKind.SESSION:
            return Decision(Verdict.WRITE, Rule.SESSION_WRITES, "the session layer is the agent's")
        if layer.kind is LayerKind.RUN:
            return Decision(
                Verdict.WRITE, Rule.RUN_MEMBER_WRITES, "the session is a member of this run"
            )
        if layer.kind is LayerKind.ATTACHED:
            return self._decide_attached(layer, action, slug)
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

    def _decide_attached(self, layer: Layer, action: ChangeAction, slug: str | None) -> Decision:
        attachment = self.attachment(layer)
        if attachment.access is AttachmentAccess.READ:
            raise refuse(
                Rule.ATTACHED_READ_ONLY,
                f"{layer.id} is attached read-only; its own owner curates it",
            )
        if action not in (ChangeAction.WRITE_PAGE, ChangeAction.ADD_TIMELINE):
            raise refuse(
                Rule.ATTACHED_PAGES_ONLY,
                f"{layer.id} is a registered store that holds pages only; "
                f"{action.value.replace('_', ' ')} goes to a layer of the org's memory",
            )
        if slug is not None and under_prefixes(slug, attachment.write_prefixes):
            return Decision(
                Verdict.WRITE, Rule.ATTACHED_WRITE_PREFIXES, "the page is under a write prefix"
            )
        allowed = ", ".join(
            normalize_prefix(p) for p in attachment.write_prefixes if normalize_prefix(p)
        )
        raise refuse(
            Rule.ATTACHED_WRITE_PREFIXES,
            f"{layer.id} takes page writes only under its write prefixes ({allowed or 'none set'})",
        )


def _attached_layers(attachments: tuple[Attachment, ...], whose: str) -> list[Layer]:
    names: set[str] = set()
    layers = []
    for attachment in attachments:
        if attachment.name in names:
            raise MemoryValidationError(
                f"Forge named attached memory {attachment.name!r} twice for {whose}; each "
                "attachment needs its own name"
            )
        names.add(attachment.name)
        layers.append(Layer(LayerKind.ATTACHED, attachment.name))
    return layers
