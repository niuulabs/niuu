"""A proposal as a person reviews it: what it is about, what memory says now, what it
would say, why, and the sources behind it.

The Review page shows one proposal at a time in plain words ("It says now", "It would
say", "Why"), so the gateway reads what the proposal would replace from the target layer
and resolves each cited source to its title and origin, instead of handing a person
record ids. Nothing here decides anything; deciding is :mod:`mimir.memory.curation`.
"""

from __future__ import annotations

from dataclasses import dataclass

from mimir.memory.models import (
    ChangeAction,
    EvidenceKind,
    Layer,
    LayerKind,
    Proposal,
    RecordNotFoundError,
)
from mimir.ports.memory_backend import MemoryBackend


@dataclass(frozen=True)
class ReviewSource:
    """One piece of a proposal's evidence, as a person opens it.

    ``title`` and ``url`` are set when the evidence leads somewhere: a source the gateway
    still holds (its title and origin), or a URL. ``kept`` is false for a source that was
    in a session's layer which has since been deleted.
    """

    ref: str
    kind: str
    title: str | None = None
    url: str | None = None
    kept: bool = True


@dataclass(frozen=True)
class ReviewItem:
    proposal: Proposal
    title: str
    now: str | None
    would: str
    sources: tuple[ReviewSource, ...]

    @property
    def by(self) -> str:
        """``person`` for a person's promotion, ``agent`` for an agent's proposal."""
        return "person" if self.proposal.proposer.startswith("person:") else "agent"


def subject_title(ref: str) -> str:
    """A page ref as a heading: ``projects/booking-cutover`` reads "Booking cutover"."""
    last = ref.strip().strip("/").rsplit("/", 1)[-1]
    words = last.replace("-", " ").replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else ref


def proposal_title(proposal: Proposal) -> str:
    """What a proposal is about, in a few words."""
    change = proposal.change
    match proposal.action:
        case ChangeAction.WRITE_PAGE:
            title = str(change.get("title") or "").strip()
            return title or subject_title(str(change.get("ref") or ""))
        case ChangeAction.ADD_TIMELINE:
            return subject_title(str(change.get("ref") or ""))
        case ChangeAction.REMEMBER:
            return subject_title(str(change.get("about") or ""))
        case ChangeAction.ADD_SOURCE:
            return str(change.get("title") or "").strip() or "A new source"
        case ChangeAction.RETIRE:
            return "A fact no longer held"
    return proposal.id


def would_say(proposal: Proposal) -> str:
    """What memory would say once the proposal is kept."""
    change = proposal.change
    match proposal.action:
        case ChangeAction.WRITE_PAGE:
            return str(change.get("compiled_truth") or "")
        case ChangeAction.ADD_TIMELINE:
            entry = str(change.get("entry") or "")
            on = str(change.get("date") or "")
            return f"{on}: {entry}" if on else entry
        case ChangeAction.REMEMBER:
            return str(change.get("fact") or "")
        case ChangeAction.ADD_SOURCE:
            return str(change.get("content") or "")
        case ChangeAction.RETIRE:
            return f"No longer held: {change.get('reason') or ''}".strip()
    return ""


async def says_now(backend: MemoryBackend, layer: Layer, proposal: Proposal) -> str | None:
    """What the target layer holds now that the proposal would change; None when nothing.

    A page that does not exist yet, a new fact, a timeline entry and a source add to
    memory, so they replace nothing.
    """
    change = proposal.change
    match proposal.action:
        case ChangeAction.WRITE_PAGE:
            try:
                page = await backend.get_page(layer.namespace, str(change.get("ref") or ""))
            except RecordNotFoundError:
                return None
            return page.compiled_truth
        case ChangeAction.REMEMBER:
            statements = [
                statement
                for fact_id in change.get("replaces") or []
                if (statement := await _statement(backend, str(fact_id))) is not None
            ]
            return "\n".join(statements) or None
        case ChangeAction.RETIRE:
            return await _statement(backend, str(change.get("fact") or ""))
    return None


async def _statement(backend: MemoryBackend, fact_id: str) -> str | None:
    try:
        return (await backend.get_fact(fact_id)).fact.statement
    except RecordNotFoundError:
        return None


async def review_sources(
    backend: MemoryBackend, layer: Layer, proposal: Proposal
) -> tuple[ReviewSource, ...]:
    """Each piece of the proposal's evidence, sources resolved to their title and origin.

    A source is looked for in the target layer, then in the proposing session's layer;
    one in neither was in a session layer that has since been deleted.
    """
    places = [layer.namespace]
    if proposal.proposer_session:
        places.append(Layer(LayerKind.SESSION, proposal.proposer_session.lower()).namespace)
    found: list[ReviewSource] = []
    for evidence in proposal.evidence:
        if evidence.kind is EvidenceKind.URL:
            found.append(ReviewSource(evidence.ref, evidence.kind.value, url=evidence.value))
            continue
        if evidence.kind is not EvidenceKind.SOURCE:
            found.append(ReviewSource(evidence.ref, evidence.kind.value))
            continue
        resolved: ReviewSource | None = None
        for namespace in places:
            try:
                record = await backend.get_source(namespace, evidence.value)
            except RecordNotFoundError:
                continue
            resolved = ReviewSource(
                evidence.ref, evidence.kind.value, title=record.title, url=record.origin_url
            )
            break
        found.append(resolved or ReviewSource(evidence.ref, evidence.kind.value, kept=False))
    return tuple(found)


async def review_item(backend: MemoryBackend, layer: Layer, proposal: Proposal) -> ReviewItem:
    return ReviewItem(
        proposal=proposal,
        title=proposal_title(proposal),
        now=await says_now(backend, layer, proposal),
        would=would_say(proposal),
        sources=await review_sources(backend, layer, proposal),
    )
