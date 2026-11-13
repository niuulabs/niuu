"""People curating memory: deciding proposals and promoting records (contract C9).

Design sections 4 and 8, decisions of section 13:

- A proposal to a **project** layer is decided by a project member whose role is
  ``approver`` or ``owner`` (a tenant admin counts as owner); to the **org** layer
  by an org admin. Anyone else is refused; a person who is not a member of the
  project learns nothing about it (not found).
- **Approval** applies the change at ``operator`` trust, as the approver, keeping
  the proposal's evidence and adding the proposal itself as evidence
  (``proposal:<layer>/<id>``), whose record names the proposer. **Rejection**
  records the reason. Either way the decided proposal moves from the layer's
  pending namespace to its decided one, so it stays citable.
- **Promotion** proposes records of a session or run layer to the project or the
  org. Only the session's owner promotes from a session layer, and only the run's
  owner from a run layer. What it creates are proposals; nothing moves up on its
  own.

Who a person is comes from Mímir's identity adapter; their role and ownership
from Forge, asked with their own login (:class:`~mimir.ports.people_directory.PeopleDirectory`).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any

from mimir.memory.gateway import (
    LayerUnavailableError,
    MemoryGateway,
    list_proposals,
    proposal_from_page,
    write_decision,
    write_proposal,
)
from mimir.memory.models import (
    Attribution,
    ChangeAction,
    Evidence,
    EvidenceKind,
    Layer,
    LayerKind,
    MemoryBackendError,
    MemoryStoreError,
    MemoryValidationError,
    NewSource,
    NewTimelineRow,
    PageWrite,
    Proposal,
    ProposalStatus,
    RecordNotFoundError,
    SourceKind,
)
from mimir.memory.review import ReviewItem, review_item
from mimir.memory.rules import approver_of
from mimir.ports.memory_backend import MemoryBackend
from mimir.ports.people_directory import NotVisibleError, PeopleDirectory, Person
from niuu.cap_telemetry import record_cap_hit
from niuu.domain.mimir_facts import (
    AuthorKind,
    FactAuthor,
    FactBackendError,
    FactCaller,
    FactDraft,
    FactKind,
    NewFact,
    TrustLevel,
)

#: Project roles that decide proposals to the project layer (C5; a tenant admin is owner).
DECIDING_ROLES = frozenset({"approver", "owner"})


class CurationRefusedError(Exception):
    """The person may not do this. The message says who may."""


class DecisionError(MemoryStoreError):
    """The proposal cannot be decided as asked (already decided, unattested, or its change
    would lower trust). The message says why."""


@dataclass(frozen=True)
class Decided:
    proposal: Proposal
    summary: str


@dataclass(frozen=True)
class Review:
    """What waits for a person: each pending proposal as they review it, oldest first.

    ``names`` gives each layer a person-facing name (the project's name, or "the org").
    ``more`` names the layers that had more pending proposals than were read; the cap hit
    was counted under ``memory.default_result_count``.
    """

    items: tuple[ReviewItem, ...]
    names: dict[str, str]
    more: tuple[str, ...] = ()


#: The knob a review's per-layer read is bounded by.
REVIEW_COUNT_KNOB = "memory.default_result_count"


class Curation:
    """Args:
    gateway: the memory gateway (its systems, and namespace placement).
    people: Forge's answers about a person's roles and ownership.
    org_admin_roles: roles that make a person an org admin.
    """

    def __init__(
        self,
        gateway: MemoryGateway,
        people: PeopleDirectory,
        *,
        org_admin_roles: Sequence[str],
    ) -> None:
        if not org_admin_roles:
            raise ValueError(
                "memory.org_admin_roles is empty: name the roles that approve org proposals"
            )
        self._gateway = gateway
        self._people = people
        self._admin_roles = frozenset(org_admin_roles)

    @property
    def people(self) -> PeopleDirectory:
        return self._people

    async def close(self) -> None:
        await self._people.close()

    # -- who may ---------------------------------------------------------

    @staticmethod
    def proposal_layer(layer_id: str) -> Layer:
        layer = Layer.parse(layer_id)
        if layer.kind not in (LayerKind.PROJECT, LayerKind.ORG):
            raise MemoryValidationError(
                f"proposals are kept for project and org layers, not {layer.kind.value}"
            )
        return layer

    async def _require_decider(self, person: Person, layer: Layer) -> None:
        if layer.kind is LayerKind.ORG:
            if self._admin_roles & set(person.roles):
                return
            raise CurationRefusedError(
                f"only an org admin ({', '.join(sorted(self._admin_roles))}) decides proposals "
                "to the org layer"
            )
        role = await self._people.project_role(layer.key, person)
        if role in DECIDING_ROLES:
            return
        raise CurationRefusedError(
            f"your role on project {layer.key} is {role}; proposals to it are decided by its "
            "approvers and owners"
        )

    def _system(self, tenant_id: str) -> tuple[str, MemoryBackend]:
        return self._gateway.systems.for_tenant(tenant_id)

    @staticmethod
    def _decider(person: Person) -> FactCaller:
        return FactCaller(FactAuthor(AuthorKind.OPERATOR, person.user_id))

    # -- listing ---------------------------------------------------------

    async def pending(self, person: Person, layer_id: str, *, limit: int) -> list[Proposal]:
        layer = self.proposal_layer(layer_id)
        await self._require_decider(person, layer)
        system, backend = self._system(person.tenant_id)
        try:
            return await list_proposals(backend, layer, limit=limit)
        except (MemoryBackendError, FactBackendError) as exc:
            raise LayerUnavailableError(
                f"proposals for {layer.id} could not be read on memory system {system!r}: {exc}"
            ) from exc

    async def deciding_layers(self, person: Person) -> list[tuple[Layer, str]]:
        """The layers whose proposals this person decides, with a name for each.

        The org when they are an org admin, and every active project they are on with
        role ``approver`` or ``owner``.
        """
        layers: list[tuple[Layer, str]] = []
        if self._admin_roles & set(person.roles):
            layers.append((Layer(LayerKind.ORG), "the org"))
        for project in await self._people.projects(person):
            role = await self._people.project_role(project.project_id, person)
            if role in DECIDING_ROLES:
                layers.append((Layer(LayerKind.PROJECT, project.project_id), project.name))
        return layers

    async def review(self, person: Person, layer_id: str | None, *, limit: int) -> Review:
        """The proposals waiting for *person*: one layer's, or every layer they decide.

        Each layer's pending proposals are read up to *limit*; a layer that has more is
        counted and named in :attr:`Review.more`, never cut silently.
        """
        if layer_id:
            layer = self.proposal_layer(layer_id)
            await self._require_decider(person, layer)
            name = (
                "the org"
                if layer.kind is LayerKind.ORG
                else await self._project_name(person, layer.key)
            )
            layers = [(layer, name)]
        else:
            layers = await self.deciding_layers(person)
        system, backend = self._system(person.tenant_id)
        items: list[ReviewItem] = []
        more: list[str] = []
        for layer, _name in layers:
            try:
                pending = await list_proposals(backend, layer, limit=limit)
                items.extend([await review_item(backend, layer, p) for p in pending])
            except (MemoryBackendError, FactBackendError) as exc:
                raise LayerUnavailableError(
                    f"proposals for {layer.id} could not be read on memory system {system!r}: {exc}"
                ) from exc
            if len(pending) >= limit:
                record_cap_hit(
                    REVIEW_COUNT_KNOB,
                    f"review read the first {limit} pending proposals of {layer.id}; more may "
                    f"wait. Raise {REVIEW_COUNT_KNOB} to read more at once",
                )
                more.append(layer.id)
        items.sort(key=lambda item: item.proposal.created_at)
        return Review(
            items=tuple(items),
            names={layer.id: name for layer, name in layers},
            more=tuple(more),
        )

    async def _project_name(self, person: Person, project_id: str) -> str:
        for project in await self._people.projects(person):
            if project.project_id == project_id:
                return project.name
        raise NotVisibleError(f"project {project_id} was not found")

    # -- deciding --------------------------------------------------------

    async def _pending(
        self, backend: MemoryBackend, system: str, layer: Layer, proposal_id: str
    ) -> Proposal:
        if not proposal_id.startswith("p-"):
            raise RecordNotFoundError(f"no proposal {proposal_id} for {layer.id}")
        try:
            await backend.get_page(layer.decided_namespace, proposal_id)
        except RecordNotFoundError:
            pass
        else:
            raise DecisionError(f"proposal {proposal_id} for {layer.id} was already decided")
        try:
            page = await backend.get_page(layer.proposals_namespace, proposal_id)
        except RecordNotFoundError:
            raise RecordNotFoundError(f"no pending proposal {proposal_id} for {layer.id}") from None
        except (MemoryBackendError, FactBackendError) as exc:
            raise LayerUnavailableError(
                f"proposal {proposal_id} could not be read on memory system {system!r}: {exc}"
            ) from exc
        proposal = proposal_from_page(page)
        if not proposal.attested:
            raise DecisionError(
                f"proposal {proposal_id} was not written by the gateway (its seal does not "
                "verify); it cannot be decided. Remove it from the memory system"
            )
        if proposal.target_layer != layer.id or proposal.status is not ProposalStatus.PENDING:
            raise DecisionError(
                f"proposal {proposal_id} names layer {proposal.target_layer} and status "
                f"{proposal.status.value}; it is not a pending proposal for {layer.id}"
            )
        return proposal

    async def approve(self, person: Person, layer_id: str, proposal_id: str) -> Decided:
        layer = self.proposal_layer(layer_id)
        await self._require_decider(person, layer)
        system, backend = self._system(person.tenant_id)
        proposal = await self._pending(backend, system, layer, proposal_id)
        decider = self._decider(person)
        claimed = replace(
            proposal,
            status=ProposalStatus.APPROVED,
            decided_by=person.ref,
            decided_at=datetime.now(UTC),
        )
        # Recording the decision first makes a second approver lose the race here,
        # before anything is applied twice.
        revision = await write_decision(
            backend, system, layer, claimed, decider, self._gateway.place
        )
        try:
            await self._gateway.place(system, backend, layer.kind, layer.namespace)
            summary = await self._apply(backend, layer, proposal, decider)
        except Exception:
            # Not applied: release the claim so the proposal can be decided again.
            await backend.delete_page(layer.decided_namespace, proposal.id)
            raise
        decided = replace(claimed, applied=summary)
        await write_decision(
            backend,
            system,
            layer,
            decided,
            decider,
            self._gateway.place,
            expected_revision=revision,
        )
        await backend.delete_page(layer.proposals_namespace, proposal.id)
        return Decided(decided, f"Approved {proposal.id}: {summary}")

    async def reject(
        self, person: Person, layer_id: str, proposal_id: str, *, reason: str
    ) -> Decided:
        if not reason.strip():
            raise MemoryValidationError("rejecting a proposal needs a reason")
        layer = self.proposal_layer(layer_id)
        await self._require_decider(person, layer)
        system, backend = self._system(person.tenant_id)
        proposal = await self._pending(backend, system, layer, proposal_id)
        decided = replace(
            proposal,
            status=ProposalStatus.REJECTED,
            decided_by=person.ref,
            decided_at=datetime.now(UTC),
            decision=reason.strip(),
        )
        await write_decision(
            backend, system, layer, decided, self._decider(person), self._gateway.place
        )
        await backend.delete_page(layer.proposals_namespace, proposal.id)
        return Decided(decided, f"Rejected {proposal.id}: {reason.strip()}")

    async def _apply(
        self, backend: MemoryBackend, layer: Layer, proposal: Proposal, decider: FactCaller
    ) -> str:
        """Write the proposal's change at operator trust, citing the proposal. A summary."""
        evidence = (*proposal.evidence, Evidence(EvidenceKind.PROPOSAL, proposal.ref))
        attribution = Attribution.by(decider, evidence, datetime.now(UTC))
        change = proposal.change
        ns = layer.namespace
        match proposal.action:
            case ChangeAction.ADD_SOURCE:
                source = NewSource(
                    title=_text(change, "title"),
                    content=_text(change, "content"),
                    kind=_source_kind(change),
                    origin_url=change.get("url") or None,
                )
                record, new = await backend.put_source(ns, source, attribution)
                return f"{'stored' if new else 'already held'} source {record.id} in {layer.id}"
            case ChangeAction.WRITE_PAGE:
                ref = _text(change, "ref")
                page = PageWrite(
                    compiled_truth=_text(change, "compiled_truth"),
                    title=str(change.get("title") or ""),
                    page_type=str(change.get("type") or ""),
                    evidence=evidence,
                )
                written = await backend.put_page(
                    ns, ref, page, attribution, expected_revision=change.get("expected_revision")
                )
                return f"wrote page {ref} in {layer.id} (revision {written.revision})"
            case ChangeAction.ADD_TIMELINE:
                ref = _text(change, "ref")
                row = NewTimelineRow(
                    on=_day(change),
                    summary=_text(change, "entry"),
                    evidence=evidence,
                    detail=str(change.get("detail") or ""),
                )
                await backend.append_timeline(ns, ref, row, attribution)
                return f"added a timeline entry to {ref} in {layer.id}"
            case ChangeAction.REMEMBER:
                return await self._remember(backend, layer, change, evidence, decider)
            case ChangeAction.RETIRE:
                fact_id = _text(change, "fact")
                fact = await backend.get_fact(fact_id)
                if fact.namespace != ns:
                    raise RecordNotFoundError(f"no fact {fact_id} in {layer.id}")
                _require_not_above(fact.fact.trust, fact_id, "retire")
                await backend.retire(
                    ns, fact_id, by=decider.author.ref, why=_text(change, "reason")
                )
                return f"retired fact {fact_id} in {layer.id}"
        raise MemoryValidationError(f"proposal {proposal.id} has no known action")

    async def _remember(
        self,
        backend: MemoryBackend,
        layer: Layer,
        change: dict[str, Any],
        evidence: tuple[Evidence, ...],
        decider: FactCaller,
    ) -> str:
        try:
            kind = FactKind(_text(change, "kind"))
        except ValueError:
            raise MemoryValidationError(f"the proposal's fact kind {change['kind']!r} is unknown")
        valid_until = change.get("valid_until")
        replaces = tuple(str(r) for r in change.get("replaces") or [])
        old = []
        for fact_id in replaces:
            found = await backend.get_fact(fact_id)
            if found.namespace != layer.namespace:
                raise DecisionError(f"fact {fact_id} it replaces is not in {layer.id}")
            _require_not_above(found.fact.trust, fact_id, "replace")
            old.append(found.fact)
        draft = FactDraft(
            subject=_text(change, "about"),
            statement=_text(change, "fact"),
            evidence=tuple(e.ref for e in evidence),
            kind=kind,
            valid_until=datetime.fromisoformat(valid_until) if valid_until else None,
            replaces=replaces,
        )
        new = NewFact(draft=draft, author=decider.author, trust=TrustLevel.OPERATOR)
        outcome = await backend.remember(layer.namespace, new)
        for fact in old:
            if fact.id == outcome.fact.fact.id or fact.retired is not None:
                continue
            await backend.retire(
                layer.namespace,
                fact.id,
                by=decider.author.ref,
                why=f"replaced by {outcome.fact.fact.id}",
                superseded_by=outcome.fact.fact.id,
            )
        return f"remembered fact {outcome.fact.fact.id} in {layer.id} ({outcome.status})"

    # -- promotion -------------------------------------------------------

    async def promote(
        self, person: Person, *, source_id: str, refs: Sequence[str], target_id: str
    ) -> list[Proposal]:
        source = Layer.parse(source_id)
        if source.kind is LayerKind.SESSION:
            owned = await self._people.session_owner(source.key, person)
            owner_of = f"session {source.key}"
        elif source.kind is LayerKind.RUN:
            owned = await self._people.run_owner(source.key, person)
            owner_of = f"run {source.key}"
        else:
            raise MemoryValidationError(
                f"promotion is from a session or run layer, not {source.kind.value}"
            )
        if owned.tenant_id != person.tenant_id:
            raise NotVisibleError(f"{owner_of} was not found")
        if owned.owner_id != person.user_id:
            raise CurationRefusedError(f"only the owner of {owner_of} promotes from its layer")
        target = self.proposal_layer(target_id)
        if target.kind is LayerKind.PROJECT:
            await self._people.project_role(target.key, person)
        if not refs:
            raise MemoryValidationError("name the records to promote (refs)")
        system, backend = self._system(owned.tenant_id)
        drafts = [await self._promotion(backend, source, target, ref) for ref in refs]
        caller = FactCaller(FactAuthor(AuthorKind.PERSON, person.user_id))
        created: list[Proposal] = []
        for action, change, evidence, trust, ref in drafts:
            proposal = Proposal(
                id=f"p-{uuid.uuid4().hex}",
                target_layer=target.id,
                status=ProposalStatus.PENDING,
                action=action,
                change=change,
                evidence=evidence,
                proposer=person.ref,
                proposer_session=source.key if source.kind is LayerKind.SESSION else "",
                session_owner=owned.owner_id,
                trust=trust,
                reason=f"promoted by {person.ref} from {source.id}",
                approver=approver_of(target),
                created_at=datetime.now(UTC),
                promoted_from=f"{source.id}/{ref}",
            )
            try:
                await write_proposal(backend, system, target, proposal, caller, self._gateway.place)
            except LayerUnavailableError as exc:
                made = ", ".join(p.id for p in created) or "none"
                raise LayerUnavailableError(
                    f"{exc}; proposals already created by this promotion: {made}"
                ) from exc
            created.append(proposal)
        return created

    async def _promotion(
        self, backend: MemoryBackend, source: Layer, target: Layer, ref: str
    ) -> tuple[ChangeAction, dict[str, Any], tuple[Evidence, ...], TrustLevel, str]:
        """The proposal a promoted record becomes: action, change, evidence, trust, ref."""
        kind, sep, rest = ref.partition(":")
        ns = source.namespace
        if sep and kind == "fact":
            found = await backend.get_fact(rest.strip())
            if found.namespace != ns:
                raise RecordNotFoundError(f"no fact {rest.strip()} in {source.id}")
            fact = found.fact
            change = {
                "fact": fact.statement,
                "about": fact.subject,
                "kind": fact.kind.value,
                "valid_until": fact.valid_until.isoformat() if fact.valid_until else None,
                "replaces": [],
            }
            evidence = Evidence.parse_all(list(fact.provenance.evidence))
            return ChangeAction.REMEMBER, change, evidence, fact.trust, ref
        if sep and kind == "source":
            record = await backend.get_source(ns, rest.strip())
            change = {
                "title": record.title,
                "content": record.content,
                "kind": record.kind,
                "url": record.origin_url,
            }
            return (
                ChangeAction.ADD_SOURCE,
                change,
                record.attribution.evidence,
                record.attribution.trust,
                ref,
            )
        page = await backend.get_page(ns, ref)
        try:
            current: str | None = (await backend.get_page(target.namespace, ref)).revision
        except RecordNotFoundError:
            current = None
        evidence = tuple(
            dict.fromkeys(
                [
                    *page.attribution.evidence,
                    *(e for row in page.timeline for e in row.attribution.evidence),
                ]
            )
        )
        change = {
            "ref": ref,
            "compiled_truth": page.compiled_truth,
            "expected_revision": current,
            "title": page.title,
            "type": page.page_type,
        }
        return ChangeAction.WRITE_PAGE, change, evidence, page.attribution.trust, ref


def _text(change: dict[str, Any], key: str) -> str:
    value = change.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MemoryValidationError(f"the proposal's change has no {key}")
    return value


def _day(change: dict[str, Any]) -> date:
    try:
        return date.fromisoformat(_text(change, "date"))
    except ValueError:
        raise MemoryValidationError("the proposal's timeline date is not YYYY-MM-DD") from None


def _source_kind(change: dict[str, Any]) -> SourceKind:
    try:
        return SourceKind(_text(change, "kind"))
    except ValueError:
        raise MemoryValidationError(
            f"the proposal's source kind {change.get('kind')!r} is unknown"
        ) from None


def _require_not_above(trust: TrustLevel, fact_id: str, act: str) -> None:
    """Approval writes at operator trust, which never overrides a person's fact (rule 3)."""
    if trust > TrustLevel.OPERATOR:
        raise DecisionError(
            f"fact {fact_id} is {trust.label}-level; an approval writes at operator level and "
            f"cannot {act} it. Ask a person to change it"
        )
