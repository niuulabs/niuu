"""The memory gateway: one view of a session's layers, and the write rules on every call.

Design sections 4–6. The gateway resolves a session's layers from Forge's answer
(org always, project when the session has one, session always), reads them as
one view with every record labelled by its layer, and routes each write by the
rules in :mod:`mimir.memory.rules`: written to the layer, or recorded as a
proposal for the layer's owner. Trust comes from who calls, never from what the
call says; a session is an ``agent``.

Reads that disagree are shown, not hidden: the same page in several layers is
returned for each, nearest layer first; facts come highest trust first, then
the one that holds most recently, and facts from different layers that look
like the same claim with different words are reported as a possible
disagreement. One layer that cannot be read fails the whole call, naming it.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from mimir.memory.models import (
    Attribution,
    ChangeAction,
    Evidence,
    HitKind,
    Layer,
    LayerKind,
    MemoryBackendError,
    MemoryStoreError,
    MemoryValidationError,
    NewSource,
    NewTimelineRow,
    PageRecord,
    PageWrite,
    Proposal,
    ProposalStatus,
    RecordNotFoundError,
    SearchHit,
    SessionMemoryContext,
    SourceKind,
    SourceRecord,
)
from mimir.memory.rules import (
    Decision,
    Rule,
    SessionView,
    Verdict,
    approver_of,
    refuse,
)
from mimir.memory.systems import MemorySystems
from mimir.ports.memory_backend import MemoryBackend, require_capabilities
from niuu.domain.mimir import OPERATIONAL_SOURCE_TYPES
from niuu.domain.mimir_facts import (
    Fact,
    FactBackendError,
    FactCaller,
    FactDraft,
    FactKind,
    NewFact,
    Omission,
    TrustLevel,
    derive_trust,
)

logger = logging.getLogger(__name__)

_PROPOSAL_TYPE = "niuu-proposal"


class LayerUnavailableError(MemoryStoreError):
    """A layer could not be read or written. The message names it."""


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LabelledHit:
    layer: Layer
    hit: SearchHit


@dataclass(frozen=True)
class LabelledPage:
    layer: Layer
    page: PageRecord


@dataclass(frozen=True)
class LabelledSource:
    layer: Layer
    source: SourceRecord


@dataclass(frozen=True)
class LabelledFact:
    layer: Layer
    fact: Fact


@dataclass(frozen=True)
class Disagreement:
    """Records that disagree. ``records`` are ``<layer>:<ref>``, the preferred one first."""

    about: str
    records: tuple[str, ...]
    note: str


@dataclass(frozen=True)
class ReadResult:
    pages: tuple[LabelledPage, ...] = ()
    sources: tuple[LabelledSource, ...] = ()
    facts: tuple[LabelledFact, ...] = ()
    disagreements: tuple[Disagreement, ...] = ()


@dataclass(frozen=True)
class EntityCard:
    subject: str
    pages: tuple[LabelledPage, ...]
    aliases: tuple[tuple[Layer, str], ...]
    links: tuple[tuple[Layer, str], ...]
    facts: tuple[LabelledFact, ...]
    disagreements: tuple[Disagreement, ...]
    omitted: tuple[Omission, ...]


@dataclass(frozen=True)
class RecallResult:
    facts: tuple[LabelledFact, ...]
    disagreements: tuple[Disagreement, ...]
    omitted: tuple[Omission, ...]


@dataclass(frozen=True)
class WriteOutcome:
    """Where a write went: a layer, or a proposal waiting for that layer's owner."""

    layer: Layer
    summary: str
    proposal: Proposal | None = None
    record: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Ordering and disagreement
# ---------------------------------------------------------------------------


def order_facts(facts: Iterable[LabelledFact]) -> list[LabelledFact]:
    """Highest trust first; at equal trust, the fact that holds most recently."""
    return sorted(
        facts,
        key=lambda f: (-int(f.fact.trust), -f.fact.valid_from.timestamp(), f.layer.distance),
    )


def fact_disagreements(facts: Sequence[LabelledFact]) -> list[Disagreement]:
    """Current facts of one kind about one subject that different layers word differently.

    Whether two statements make the same claim is a judgement for the reader, not for a
    word count, so every such pair is reported and the preferred one is named.
    """
    found: list[Disagreement] = []
    ordered = order_facts(facts)
    for index, first in enumerate(ordered):
        for second in ordered[index + 1 :]:
            if (
                first.layer == second.layer
                or first.fact.subject != second.fact.subject
                or first.fact.kind != second.fact.kind
                or " ".join(first.fact.statement.split())
                == " ".join(second.fact.statement.split())
            ):
                continue
            found.append(
                Disagreement(
                    about=first.fact.subject,
                    records=(
                        f"{first.layer.id}:{first.fact.id}",
                        f"{second.layer.id}:{second.fact.id}",
                    ),
                    note=(
                        f"{first.layer.id} says {first.fact.statement!r} "
                        f"({first.fact.trust.label}); {second.layer.id} says "
                        f"{second.fact.statement!r} ({second.fact.trust.label}). The first is "
                        "preferred: higher trust wins, then the one that holds more recently"
                    ),
                )
            )
    return found


def page_disagreements(pages: Sequence[LabelledPage]) -> list[Disagreement]:
    """The same page in several layers with different current state."""
    by_slug: dict[str, list[LabelledPage]] = {}
    for labelled in pages:
        by_slug.setdefault(labelled.page.slug, []).append(labelled)
    found = []
    for slug, versions in by_slug.items():
        truths = {" ".join(v.page.compiled_truth.split()) for v in versions}
        if len(truths) < 2:
            continue
        versions = sorted(versions, key=lambda v: v.layer.distance)
        found.append(
            Disagreement(
                about=slug,
                records=tuple(f"{v.layer.id}:{slug}" for v in versions),
                note=(
                    f"{slug} differs between {', '.join(v.layer.id for v in versions)}; each "
                    "is shown, nearest layer first"
                ),
            )
        )
    return found


# ---------------------------------------------------------------------------
# The gateway
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Session:
    """One call's resolved session: its view and the system holding its layers."""

    view: SessionView
    backend: MemoryBackend

    @property
    def layers(self) -> tuple[Layer, ...]:
        return self.view.layers


class MemoryGateway:
    """Args:
    systems: the memory systems and which org lives on which.
    default_result_count: results a list read returns when the caller names no limit.
    """

    def __init__(
        self,
        systems: MemorySystems,
        *,
        default_result_count: int,
    ) -> None:
        if default_result_count < 1:
            raise ValueError("default_result_count must be positive")
        self._systems = systems
        self._default_count = default_result_count
        self._ready: set[tuple[str, str]] = set()

    @property
    def default_result_count(self) -> int:
        return self._default_count

    # -- sessions --------------------------------------------------------

    async def open(self, context: SessionMemoryContext) -> Session:
        """Resolve a session's layers and make sure each exists on its system.

        Raises TenantNotConfiguredError when the session's org has no system.
        """
        system, backend = self._systems.for_tenant(context.tenant_id)
        view = SessionView.of(context, system)
        for layer in view.layers:
            await self._place(system, backend, layer.kind, layer.namespace)
        return Session(view=view, backend=backend)

    async def _place(self, system: str, backend: MemoryBackend, kind: LayerKind, ns: str) -> None:
        if (system, ns) in self._ready:
            return
        require_capabilities(system, backend, kind)
        await self._guard([ns], system, backend.create_namespace(ns), "prepared")
        self._ready.add((system, ns))

    async def _guard[T](
        self, namespaces: Sequence[str], system: str, call: Awaitable[T], verb: str = "read"
    ) -> T:
        try:
            return await call
        except (MemoryBackendError, FactBackendError) as exc:
            raise LayerUnavailableError(
                f"layer namespace {', '.join(namespaces)} on memory system {system!r} could "
                f"not be {verb}: {exc}"
            ) from exc

    def _limit(self, limit: int | None) -> int:
        if limit is None:
            return self._default_count
        if limit < 1:
            raise MemoryValidationError("limit must be at least 1")
        return limit

    def _layers(self, session: Session, names: Sequence[str] | None) -> tuple[Layer, ...]:
        if not names:
            return session.layers
        chosen = {session.view.layer(name) for name in names}
        return tuple(layer for layer in session.layers if layer in chosen)

    # -- reads -----------------------------------------------------------

    async def search(
        self,
        session: Session,
        query: str,
        *,
        layers: Sequence[str] | None = None,
        kinds: Iterable[HitKind] | None = None,
        limit: int | None = None,
    ) -> list[LabelledHit]:
        chosen = self._layers(session, layers)
        namespaces = [layer.namespace for layer in chosen]
        hits = await self._guard(
            namespaces,
            session.view.system,
            session.backend.search(
                namespaces,
                query,
                kinds=frozenset(kinds or HitKind),
                limit=self._limit(limit),
            ),
        )
        by_ref: dict[tuple[HitKind, str], list[LabelledHit]] = {}
        order: list[tuple[HitKind, str]] = []
        for hit in hits:
            layer = session.view.layer_of_namespace(hit.namespace)
            if layer is None:
                raise MemoryBackendError(
                    f"memory system {session.view.system!r} returned a hit outside the "
                    f"requested layers ({hit.namespace})"
                )
            key = (hit.kind, hit.ref)
            if key not in by_ref:
                order.append(key)
            by_ref.setdefault(key, []).append(LabelledHit(layer, hit))
        merged: list[LabelledHit] = []
        for key in order:
            merged.extend(sorted(by_ref[key], key=lambda h: h.layer.distance))
        return merged

    async def read(self, session: Session, ref: str, *, layer: str | None = None) -> ReadResult:
        """A page, ``source:<id>`` or ``fact:<id>``, from every layer that has it."""
        chosen = self._layers(session, [layer] if layer else None)
        kind, sep, rest = ref.partition(":")
        if sep and kind == "fact":
            return await self._read_fact(session, rest.strip())
        if sep and kind == "source":
            sources = await self._each_layer(
                session, chosen, lambda lyr: session.backend.get_source(lyr.namespace, rest.strip())
            )
            if not sources:
                raise RecordNotFoundError(f"no source {rest.strip()} in this session's layers")
            return ReadResult(sources=tuple(LabelledSource(lyr, s) for lyr, s in sources))
        pages = await self._each_layer(
            session, chosen, lambda lyr: session.backend.get_page(lyr.namespace, ref)
        )
        if not pages:
            raise RecordNotFoundError(f"no page {ref} in this session's layers")
        labelled = tuple(LabelledPage(lyr, p) for lyr, p in pages)
        return ReadResult(pages=labelled, disagreements=tuple(page_disagreements(labelled)))

    async def _read_fact(self, session: Session, fact_id: str) -> ReadResult:
        found = await self._guard(
            session.view.namespaces, session.view.system, session.backend.get_fact(fact_id)
        )
        layer = session.view.layer_of_namespace(found.namespace)
        if layer is None:
            raise RecordNotFoundError(f"no fact {fact_id} in this session's layers")
        return ReadResult(facts=(LabelledFact(layer, found.fact),))

    async def _each_layer[T](
        self,
        session: Session,
        layers: Sequence[Layer],
        read: Callable[[Layer], Awaitable[T]],
    ) -> list[tuple[Layer, T]]:
        """Read every layer, nearest first; a missing record is skipped, a failure is not."""
        found: list[tuple[Layer, T]] = []
        for layer in sorted(layers, key=lambda lyr: lyr.distance):
            try:
                found.append(
                    (layer, await self._guard([layer.namespace], session.view.system, read(layer)))
                )
            except RecordNotFoundError:
                continue
        return found

    async def entity(self, session: Session, name: str) -> EntityCard:
        subject = _subject(name)
        views = [
            (
                layer,
                await self._guard(
                    [layer.namespace],
                    session.view.system,
                    session.backend.entity(layer.namespace, subject),
                ),
            )
            for layer in session.layers
        ]
        pages = tuple(LabelledPage(lyr, v.page) for lyr, v in views if v.page is not None)
        facts = order_facts(LabelledFact(lyr, f.fact) for lyr, v in views for f in v.facts)
        return EntityCard(
            subject=subject,
            pages=pages,
            aliases=tuple((lyr, alias) for lyr, v in views for alias in v.aliases),
            links=tuple((lyr, link) for lyr, v in views for link in v.links),
            facts=tuple(facts),
            disagreements=(
                *page_disagreements(pages),
                *fact_disagreements(facts),
            ),
            omitted=tuple(o for _, v in views for o in v.omitted),
        )

    async def recall(
        self, session: Session, about: str, *, kinds: Iterable[FactKind] | None = None
    ) -> RecallResult:
        subject = _subject(about)
        recalled = await self._guard(
            session.view.namespaces,
            session.view.system,
            session.backend.recall(session.view.namespaces, subject=subject),
        )
        wanted = set(kinds or FactKind)
        facts = []
        for found in recalled.facts:
            layer = session.view.layer_of_namespace(found.namespace)
            if layer is None:
                raise MemoryBackendError(
                    f"memory system {session.view.system!r} returned a fact outside the "
                    f"requested layers ({found.namespace})"
                )
            if found.fact.kind in wanted:
                facts.append(LabelledFact(layer, found.fact))
        ordered = order_facts(facts)
        return RecallResult(
            facts=tuple(ordered),
            disagreements=tuple(fact_disagreements(ordered)),
            omitted=recalled.omitted,
        )

    # -- writes ----------------------------------------------------------

    async def add_source(
        self,
        session: Session,
        *,
        title: str,
        content: str,
        kind: str,
        evidence: Sequence[str],
        url: str | None = None,
        layer: str | None = None,
    ) -> WriteOutcome:
        if kind in OPERATIONAL_SOURCE_TYPES:
            raise refuse(
                Rule.OPERATIONAL_SOURCE,
                f"{kind} is not a source: cite tool output by its tool call id "
                "(evidence tool_call:<id>) instead of storing it",
            )
        try:
            source_kind = SourceKind(kind)
        except ValueError:
            kinds = ", ".join(k.value for k in SourceKind)
            raise MemoryValidationError(
                f"unknown source kind {kind!r}; use one of {kinds}"
            ) from None
        refs = list(evidence) or ([f"url:{url}"] if url else [])
        cited = Evidence.parse_all(refs)
        if not cited:
            raise MemoryValidationError(
                "a source needs evidence of where it came from (url, session_event, ...)"
            )
        source = NewSource(title=title, content=content, kind=source_kind, origin_url=url)
        target = session.view.layer(layer)
        decision = session.view.decide(target, ChangeAction.ADD_SOURCE)
        if decision.verdict is Verdict.PROPOSE:
            change = {"title": title, "content": content, "kind": kind, "url": url}
            return await self._propose(
                session, target, ChangeAction.ADD_SOURCE, change, cited, decision
            )
        record, new = await self._guard(
            [target.namespace],
            session.view.system,
            session.backend.put_source(
                target.namespace, source, self._attribution(session.view.caller, cited)
            ),
            "written",
        )
        verb = "Stored" if new else "Already held"
        return WriteOutcome(
            layer=target,
            summary=f"{verb} source {record.id} in layer {target.id}.",
            record={"source_id": record.id, "new": new},
        )

    async def write_page(
        self,
        session: Session,
        ref: str,
        *,
        compiled_truth: str,
        expected_revision: str | None,
        title: str = "",
        page_type: str = "",
        evidence: Sequence[str] = (),
        layer: str | None = None,
    ) -> WriteOutcome:
        cited = Evidence.parse_all(list(evidence))
        page = PageWrite(
            compiled_truth=compiled_truth, title=title, page_type=page_type, evidence=cited
        )
        target = session.view.layer(layer)
        decision = session.view.decide(target, ChangeAction.WRITE_PAGE, ref)
        if decision.verdict is Verdict.PROPOSE:
            change = {
                "ref": ref,
                "compiled_truth": compiled_truth,
                "expected_revision": expected_revision,
                "title": title,
                "type": page_type,
            }
            return await self._propose(
                session, target, ChangeAction.WRITE_PAGE, change, cited, decision
            )
        record = await self._guard(
            [target.namespace],
            session.view.system,
            session.backend.put_page(
                target.namespace,
                ref,
                page,
                self._attribution(session.view.caller, cited),
                expected_revision=expected_revision or None,
            ),
            "written",
        )
        return WriteOutcome(
            layer=target,
            summary=f"Wrote page {ref} in layer {target.id} (revision {record.revision}).",
            record={"ref": ref, "revision": record.revision},
        )

    async def add_timeline(
        self,
        session: Session,
        ref: str,
        *,
        entry: str,
        evidence: Sequence[str],
        on: date | None = None,
        detail: str = "",
        layer: str | None = None,
    ) -> WriteOutcome:
        cited = Evidence.parse_all(list(evidence))
        row = NewTimelineRow(
            on=on or datetime.now(UTC).date(), summary=entry, evidence=cited, detail=detail
        )
        target = session.view.layer(layer)
        decision = session.view.decide(target, ChangeAction.ADD_TIMELINE, ref)
        if decision.verdict is Verdict.PROPOSE:
            change = {"ref": ref, "entry": entry, "date": row.on.isoformat(), "detail": detail}
            return await self._propose(
                session, target, ChangeAction.ADD_TIMELINE, change, cited, decision
            )
        await self._guard(
            [target.namespace],
            session.view.system,
            session.backend.append_timeline(
                target.namespace, ref, row, self._attribution(session.view.caller, cited)
            ),
            "written",
        )
        return WriteOutcome(
            layer=target,
            summary=f"Added a timeline entry to {ref} in layer {target.id}.",
            record={"ref": ref, "date": row.on.isoformat()},
        )

    async def remember(
        self,
        session: Session,
        *,
        fact: str,
        about: str,
        kind: str,
        evidence: Sequence[str],
        valid_until: datetime | None = None,
        replaces: Sequence[str] = (),
        layer: str | None = None,
    ) -> WriteOutcome:
        cited = Evidence.parse_all(list(evidence))
        try:
            fact_kind = FactKind(kind)
        except ValueError:
            kinds = ", ".join(k.value for k in FactKind if k is not FactKind.IDEA)
            raise MemoryValidationError(f"unknown fact kind {kind!r}; use one of {kinds}") from None
        draft = FactDraft(
            subject=_subject(about),
            statement=fact,
            evidence=tuple(e.ref for e in cited),
            kind=fact_kind,
            valid_until=valid_until,
            replaces=tuple(replaces),
        )
        target = session.view.layer(layer)
        decision = session.view.decide(target, ChangeAction.REMEMBER)
        replaced = [await self._fact_in(session, fact_id) for fact_id in draft.replaces]
        for layer_of, old in replaced:
            if layer_of != target:
                raise refuse(
                    Rule.REPLACE_ACROSS_LAYERS,
                    f"fact {old.id} is in {layer_of.id}, not {target.id}; a fact replaces only "
                    "facts of its own layer. Propose the change to that layer instead",
                )
            if old.trust > session.view.caller.trust and decision.verdict is Verdict.WRITE:
                decision = Decision(
                    Verdict.PROPOSE,
                    Rule.LOWER_TRUST_PROPOSES,
                    f"fact {old.id} is {old.trust.label}-level; a lower level never replaces it",
                )
        if decision.verdict is Verdict.PROPOSE:
            change = {
                "fact": fact,
                "about": draft.subject,
                "kind": fact_kind.value,
                "valid_until": valid_until.isoformat() if valid_until else None,
                "replaces": list(draft.replaces),
            }
            return await self._propose(
                session, target, ChangeAction.REMEMBER, change, cited, decision
            )
        caller = session.view.caller
        new = NewFact(draft=draft, author=caller.author, trust=derive_trust(caller, []))
        outcome = await self._guard(
            [target.namespace],
            session.view.system,
            session.backend.remember(target.namespace, new),
            "written",
        )
        for _, old in replaced:
            if old.id == outcome.fact.fact.id or old.retired is not None:
                continue
            await self._guard(
                [target.namespace],
                session.view.system,
                session.backend.retire(
                    target.namespace,
                    old.id,
                    by=caller.author.ref,
                    why=f"replaced by {outcome.fact.fact.id}",
                    superseded_by=outcome.fact.fact.id,
                ),
                "written",
            )
        landed = session.view.layer_of_namespace(outcome.fact.namespace) or target
        what = {
            "inserted": "Remembered",
            "superseded": "Remembered (it supersedes an older fact)",
            "duplicate": "Already known as",
        }[outcome.status]
        return WriteOutcome(
            layer=landed,
            summary=f"{what} fact {outcome.fact.fact.id} in layer {landed.id}.",
            record={"fact_id": outcome.fact.fact.id, "status": outcome.status},
        )

    async def _fact_in(self, session: Session, fact_id: str) -> tuple[Layer, Fact]:
        found = await self._guard(
            session.view.namespaces, session.view.system, session.backend.get_fact(fact_id)
        )
        layer = session.view.layer_of_namespace(found.namespace)
        if layer is None:
            raise RecordNotFoundError(f"no fact {fact_id} in this session's layers")
        return layer, found.fact

    async def retire(self, session: Session, fact_id: str, *, reason: str) -> WriteOutcome:
        if not reason.strip():
            raise MemoryValidationError("retiring a fact needs a reason")
        layer, fact = await self._fact_in(session, fact_id)
        if fact.retired is not None:
            return WriteOutcome(
                layer=layer,
                summary=f"Fact {fact_id} in {layer.id} was already retired ({fact.retired.why}).",
                record={"fact_id": fact_id, "retired": True},
            )
        decision = session.view.decide(layer, ChangeAction.RETIRE)
        caller = session.view.caller
        if decision.verdict is Verdict.WRITE and fact.trust > caller.trust:
            decision = Decision(
                Verdict.PROPOSE,
                Rule.LOWER_TRUST_PROPOSES,
                f"fact {fact_id} is {fact.trust.label}-level; a lower level never retires it",
            )
        if decision.verdict is Verdict.PROPOSE:
            return await self._propose(
                session,
                layer,
                ChangeAction.RETIRE,
                {"fact": fact_id, "reason": reason},
                (Evidence.parse(f"fact:{fact_id}"),),
                decision,
            )
        await self._guard(
            [layer.namespace],
            session.view.system,
            session.backend.retire(layer.namespace, fact_id, by=caller.author.ref, why=reason),
            "written",
        )
        return WriteOutcome(
            layer=layer,
            summary=f"Retired fact {fact_id} in layer {layer.id}.",
            record={"fact_id": fact_id, "retired": True},
        )

    async def propose(
        self,
        session: Session,
        *,
        layer: str,
        action: str,
        change: dict[str, Any],
        evidence: Sequence[str],
    ) -> WriteOutcome:
        target = session.view.layer(layer)
        try:
            change_action = ChangeAction(action)
        except ValueError:
            actions = ", ".join(a.value for a in ChangeAction)
            raise MemoryValidationError(
                f"unknown change {action!r}; use one of {actions}"
            ) from None
        slug = change.get("ref") if isinstance(change.get("ref"), str) else None
        decision = session.view.decide(target, change_action, slug)
        if decision.verdict is Verdict.WRITE:
            raise refuse(
                Rule.PROPOSAL_NOT_NEEDED,
                f"this session may write that to {target.id} directly ({decision.reason}); "
                "use the write tool instead of proposing",
            )
        cited = Evidence.parse_all(list(evidence))
        if not cited:
            raise MemoryValidationError("a proposal needs evidence for its owner to check")
        return await self._propose(session, target, change_action, change, cited, decision)

    # -- proposals -------------------------------------------------------

    async def _propose(
        self,
        session: Session,
        target: Layer,
        action: ChangeAction,
        change: dict[str, Any],
        evidence: tuple[Evidence, ...],
        decision: Decision,
    ) -> WriteOutcome:
        caller = session.view.caller
        proposal = Proposal(
            id=f"p-{uuid.uuid4().hex}",
            target_layer=target.id,
            status=ProposalStatus.PENDING,
            action=action,
            change=change,
            evidence=evidence,
            proposer=caller.author.ref,
            proposer_session=session.view.context.session_id,
            session_owner=session.view.context.owner_id,
            trust=caller.trust,
            reason=f"{decision.rule.value}: {decision.reason}",
            approver=approver_of(target),
            created_at=datetime.now(UTC),
        )
        await write_proposal(
            session.backend, session.view.system, target, proposal, caller, self._place
        )
        return WriteOutcome(
            layer=target,
            summary=(
                f"Not written to {target.id}: {decision.reason}. Recorded proposal "
                f"{proposal.id}; {proposal.approver} must approve it."
            ),
            proposal=proposal,
            record={"proposal_id": proposal.id, "approver": proposal.approver},
        )

    @staticmethod
    def _attribution(caller: FactCaller, evidence: tuple[Evidence, ...]) -> Attribution:
        return Attribution.by(caller, evidence, datetime.now(UTC))


def _subject(name: str) -> str:
    subject = name.strip().strip("/").lower()
    if not subject:
        raise MemoryValidationError("name the subject (an entity slug such as people/ines-moreau)")
    if any(ch.isspace() for ch in subject):
        raise MemoryValidationError(
            f"subject {name!r} is not a slug: use lowercase words joined by '-' and '/', "
            "e.g. services/booking-api"
        )
    return subject


# ---------------------------------------------------------------------------
# Proposal pages
# ---------------------------------------------------------------------------


def _proposal_json(proposal: Proposal) -> dict[str, Any]:
    return {
        "id": proposal.id,
        "target_layer": proposal.target_layer,
        "status": proposal.status.value,
        "action": proposal.action.value,
        "change": proposal.change,
        "evidence": [e.ref for e in proposal.evidence],
        "proposer": proposal.proposer,
        "proposer_session": proposal.proposer_session,
        "session_owner": proposal.session_owner,
        "trust": proposal.trust.label,
        "reason": proposal.reason,
        "approver": proposal.approver,
        "created_at": proposal.created_at.isoformat(),
    }


async def write_proposal(
    backend: MemoryBackend,
    system: str,
    target: Layer,
    proposal: Proposal,
    caller: FactCaller,
    place: Any,
) -> None:
    """Record *proposal* as a page in the target layer's proposals namespace."""
    namespace = target.proposals_namespace
    await place(system, backend, target.kind, namespace)
    body = (
        f"Proposal to {proposal.action.value.replace('_', ' ')} in {target.id}, "
        f"from session {proposal.proposer_session} (owner {proposal.session_owner}). "
        f"{proposal.approver} must approve it. Why it was not written: {proposal.reason}.\n\n"
        f"```json\n{json.dumps(_proposal_json(proposal), ensure_ascii=False)}\n```"
    )
    try:
        await backend.put_page(
            namespace,
            proposal.id,
            PageWrite(
                compiled_truth=body,
                title=f"Proposal {proposal.id}",
                page_type=_PROPOSAL_TYPE,
                evidence=proposal.evidence,
            ),
            Attribution.by(caller, proposal.evidence, proposal.created_at),
            expected_revision=None,
        )
    except (MemoryBackendError, FactBackendError) as exc:
        raise LayerUnavailableError(
            f"the proposal for {target.id} could not be recorded on memory system {system!r}: {exc}"
        ) from exc


def proposal_from_page(page: PageRecord) -> Proposal:
    """Read a proposal page back. An unattested page is returned marked so, never hidden."""
    start = page.compiled_truth.find("```json\n")
    end = page.compiled_truth.rfind("\n```")
    if start < 0 or end <= start:
        raise MemoryBackendError(f"proposal page {page.slug} holds no proposal record")
    data = json.loads(page.compiled_truth[start + len("```json\n") : end])
    return Proposal(
        id=data["id"],
        target_layer=data["target_layer"],
        status=ProposalStatus(data["status"]),
        action=ChangeAction(data["action"]),
        change=dict(data["change"]),
        evidence=Evidence.parse_all(list(data["evidence"])),
        proposer=data["proposer"],
        proposer_session=data["proposer_session"],
        session_owner=data["session_owner"],
        trust=TrustLevel.parse(data["trust"]),
        reason=data["reason"],
        approver=data["approver"],
        created_at=datetime.fromisoformat(data["created_at"]),
        attested=page.attribution.attested,
    )


async def list_proposals(backend: MemoryBackend, layer: Layer, *, limit: int) -> list[Proposal]:
    """Pending proposals for *layer*, for its owner to review (approval is step 2)."""
    namespace = layer.proposals_namespace
    refs = await backend.list_pages(namespace, limit=limit)
    proposals = []
    for ref in refs:
        proposals.append(proposal_from_page(await backend.get_page(namespace, ref.slug)))
    return sorted(proposals, key=lambda p: p.created_at)
