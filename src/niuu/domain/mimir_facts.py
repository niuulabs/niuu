"""Typed facts for Mímir: provenance, trust, validity, and how they combine.

A *fact* is one claim about one subject with the evidence behind it. Mímir keeps
facts apart from wiki pages because a resident that stores its beliefs as prose
later reads its own opinions back as established truth, and what it then
learns feeds on itself. A fact carries who wrote it, from what, and a trust
level, so a reader can ask only for what a person or a tool vouched for.

Trust rules (the whole policy lives here, in pure functions):

* There are four levels, highest first: confirmed by a **person**, curated by an
  **operator**, observed by a **tool**, written by an **agent**.
* A level belongs to the *caller's identity* (:class:`FactCaller`), which the
  platform builds from an authenticated principal. A request carries no trust
  field; :class:`FactDraft` has none.
* A fact derived from other facts takes the lowest level of its inputs and of
  its caller (:func:`derive_trust`).
* A caller may not retire or replace a fact above its own level.
* A fact received from a peer keeps its original author; it can come in no
  higher than the importing caller's own level, because the sender's claim of
  trust cannot be checked here (:func:`received_trust`).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum, StrEnum


class FactError(Exception):
    """Base class for every typed-fact failure; each message names its remedy."""


class FactValidationError(FactError):
    """The request is malformed or lacks something required (provenance, a reason...)."""


class TrustError(FactError):
    """The caller's level does not permit this act."""


class FactNotFoundError(FactError):
    """No fact has this id."""


class FactBackendError(FactError):
    """The backend refused or could not complete a call. Never read as 'no result'."""


class TrustLevel(IntEnum):
    """Trust, ordered: a larger value is more trusted."""

    AGENT = 1
    TOOL = 2
    OPERATOR = 3
    PERSON = 4

    @property
    def label(self) -> str:
        return self.name.lower()

    @classmethod
    def parse(cls, value: str | int | TrustLevel) -> TrustLevel:
        if isinstance(value, TrustLevel):
            return value
        try:
            if isinstance(value, int):
                return cls(value)
            return cls[value.strip().upper()]
        except (KeyError, ValueError):
            names = ", ".join(level.label for level in cls)
            raise FactValidationError(
                f"unknown trust level {value!r}; use one of: {names}"
            ) from None


class AuthorKind(StrEnum):
    """What wrote a fact. EXTERNAL marks a backend row Mímir did not write."""

    PERSON = "person"
    OPERATOR = "operator"
    TOOL = "tool"
    AGENT = "agent"
    EXTERNAL = "external"


#: The trust level each kind of author holds. An external row is the lowest.
AUTHOR_TRUST: dict[AuthorKind, TrustLevel] = {
    AuthorKind.PERSON: TrustLevel.PERSON,
    AuthorKind.OPERATOR: TrustLevel.OPERATOR,
    AuthorKind.TOOL: TrustLevel.TOOL,
    AuthorKind.AGENT: TrustLevel.AGENT,
    AuthorKind.EXTERNAL: TrustLevel.AGENT,
}


class FactKind(StrEnum):
    """gbrain's own fact kinds (its memory protocol freezes these five)."""

    FACT = "fact"
    EVENT = "event"
    PREFERENCE = "preference"
    COMMITMENT = "commitment"
    BELIEF = "belief"
    #: Written by gbrain's own extractor only; readable, never accepted by ``remember``.
    IDEA = "idea"


@dataclass(frozen=True)
class FactAuthor:
    """Who wrote a fact, as ``kind:id`` (``agent:regin``, ``person:jozef``)."""

    kind: AuthorKind
    id: str

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise FactValidationError("a fact author needs a non-empty id")
        if ":" in self.kind.value:
            raise FactValidationError(f"author kind {self.kind.value!r} may not contain ':'")

    @property
    def ref(self) -> str:
        return f"{self.kind.value}:{self.id}"

    @classmethod
    def parse(cls, ref: str) -> FactAuthor:
        kind, sep, ident = ref.partition(":")
        if not sep:
            raise FactValidationError(f"author reference {ref!r} is not 'kind:id'")
        try:
            return cls(AuthorKind(kind), ident)
        except ValueError:
            raise FactValidationError(f"author reference {ref!r} has an unknown kind") from None


@dataclass(frozen=True)
class FactCaller:
    """The platform's view of who is calling: an author and, from it, a trust level.

    Built only by the platform from an authenticated principal (or by a
    composition root for an in-process tool). Nothing a request says can change
    it, and the level follows from the author's kind, so there is no way to
    construct an agent holding a person's trust.
    """

    author: FactAuthor

    @property
    def trust(self) -> TrustLevel:
        return AUTHOR_TRUST[self.author.kind]


@dataclass(frozen=True)
class SharedOrigin:
    """Where a received fact came from. It stays with the fact for good."""

    author: FactAuthor
    claimed_trust: TrustLevel
    fact_id: str
    source: str
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class FactProvenance:
    """Who wrote a fact and from what.

    ``author`` is the original author, also for a received fact; ``imported_by``
    names who brought a received fact in. ``attested`` is False for a backend
    row Mímir did not write (or whose signature does not verify): such a row is
    the lowest level and its ``author`` is EXTERNAL.
    """

    author: FactAuthor
    evidence: tuple[str, ...]
    derived_from: tuple[str, ...] = ()
    replaces: tuple[str, ...] = ()
    origin: SharedOrigin | None = None
    imported_by: FactAuthor | None = None
    attested: bool = True


@dataclass(frozen=True)
class Retirement:
    """A fact that is no longer current. The fact itself is kept."""

    at: datetime
    by: str
    why: str
    superseded_by: str | None = None


@dataclass(frozen=True)
class FactDraft:
    """What a caller supplies to ``remember``. There is deliberately no trust field."""

    subject: str
    statement: str
    evidence: tuple[str, ...]
    kind: FactKind = FactKind.FACT
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    replaces: tuple[str, ...] = ()
    derived_from: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.subject.strip():
            raise FactValidationError("a fact needs a subject (the entity it is about)")
        if not self.statement.strip():
            raise FactValidationError("a fact needs a statement")
        if not self.evidence or any(not item.strip() for item in self.evidence):
            raise FactValidationError(
                "a fact needs provenance: pass evidence naming what it was learned from "
                "(a page path, a source id, a message, a tool run)"
            )
        if self.kind is FactKind.IDEA:
            raise FactValidationError(
                "kind 'idea' is read-only; use fact, event, preference, commitment or belief"
            )
        for label, moment in (("valid_from", self.valid_from), ("valid_until", self.valid_until)):
            if moment is not None and moment.tzinfo is None:
                raise FactValidationError(f"{label} must carry a timezone")
        if (
            self.valid_from is not None
            and self.valid_until is not None
            and self.valid_until <= self.valid_from
        ):
            raise FactValidationError("valid_until must be later than valid_from")


@dataclass(frozen=True)
class NewFact:
    """A draft after the platform stamped it: the author and the trust it earned."""

    draft: FactDraft
    author: FactAuthor
    trust: TrustLevel
    origin: SharedOrigin | None = None
    imported_by: FactAuthor | None = None


@dataclass(frozen=True)
class Fact:
    id: str
    subject: str
    statement: str
    kind: FactKind
    trust: TrustLevel
    provenance: FactProvenance
    recorded_at: datetime
    valid_from: datetime
    valid_until: datetime | None = None
    retired: Retirement | None = None

    @property
    def ends_at(self) -> datetime | None:
        """When the fact stopped being current: its expiry or its retirement, the earlier."""
        moments = [m for m in (self.valid_until, self.retired.at if self.retired else None) if m]
        return min(moments) if moments else None

    def valid_at(self, moment: datetime) -> bool:
        if moment < self.valid_from:
            return False
        end = self.ends_at
        return end is None or moment < end


class OmissionReason(StrEnum):
    BELOW_MIN_TRUST = "below_min_trust"
    NOT_VALID_AT = "not_valid_at"
    BUDGET = "budget"
    BACKEND_LIMIT = "backend_limit"


@dataclass(frozen=True)
class Omission:
    """Facts left out, and why. ``fact_ids`` is empty when the backend cut them unseen."""

    reason: OmissionReason
    count: int
    detail: str
    fact_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class FactRecall:
    facts: tuple[Fact, ...]
    omitted: tuple[Omission, ...] = ()


@dataclass(frozen=True)
class RememberOutcome:
    """What ``remember`` did. ``duplicate`` means the backend already held this claim
    and wrote nothing: the returned fact is the existing one, with its own provenance."""

    fact: Fact
    status: str


@dataclass(frozen=True)
class EntityRecord:
    subject: str
    found: bool
    facts: tuple[Fact, ...] = ()
    history: tuple[Fact, ...] = ()
    card: dict | None = None
    omitted: tuple[Omission, ...] = ()


@dataclass(frozen=True)
class ContextPack:
    facts: tuple[Fact, ...]
    omitted: tuple[Omission, ...]
    text: str
    budget_tokens: int | None
    used_tokens: int


@dataclass(frozen=True)
class SharedFact:
    """A fact in transit to a peer, with its provenance intact."""

    fact_id: str
    subject: str
    statement: str
    kind: FactKind
    trust: TrustLevel
    author: FactAuthor
    evidence: tuple[str, ...]
    valid_from: datetime
    valid_until: datetime | None = None
    source: str = ""
    origin: SharedOrigin | None = field(default=None)


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


def derive_trust(caller: FactCaller, inputs: Iterable[Fact]) -> TrustLevel:
    """The level of a fact the caller derives from *inputs*: the lowest of all of them."""
    return min([caller.trust, *(fact.trust for fact in inputs)])


def received_trust(caller: FactCaller, claimed: TrustLevel) -> TrustLevel:
    """The level a fact shared by a peer can have here.

    The peer's claim cannot be verified, so it never exceeds the importing
    caller's own level.
    """
    return min(caller.trust, claimed)


def require_may_override(caller: FactCaller, fact: Fact, act: str) -> None:
    """Raise unless *caller* may retire or replace *fact*."""
    if caller.trust >= fact.trust:
        return
    raise TrustError(
        f"{caller.author.ref} (trust {caller.trust.label}) may not {act} fact {fact.id}, "
        f"which is {fact.trust.label}-level. A caller can only {act} facts at or below its "
        "own level; ask someone with that level to do it."
    )


def share_bundle(fact: Fact, *, source: str) -> SharedFact:
    """Package *fact* for a peer. Original authorship is carried, never replaced."""
    origin = fact.provenance.origin
    return SharedFact(
        fact_id=origin.fact_id if origin else fact.id,
        subject=fact.subject,
        statement=fact.statement,
        kind=fact.kind,
        trust=origin.claimed_trust if origin else fact.trust,
        author=origin.author if origin else fact.provenance.author,
        evidence=origin.evidence if origin else fact.provenance.evidence,
        valid_from=fact.valid_from,
        valid_until=fact.valid_until,
        source=origin.source if origin else source,
        origin=origin,
    )


# ---------------------------------------------------------------------------
# Selection, ranking, packing
# ---------------------------------------------------------------------------


def select_facts(
    facts: Sequence[Fact], *, min_trust: TrustLevel, as_of: datetime
) -> tuple[list[Fact], list[Omission]]:
    """Keep the facts valid at *as_of* and at or above *min_trust*; say what was dropped."""
    kept: list[Fact] = []
    invalid: list[Fact] = []
    weak: list[Fact] = []
    for fact in facts:
        if not fact.valid_at(as_of):
            invalid.append(fact)
        elif fact.trust < min_trust:
            weak.append(fact)
        else:
            kept.append(fact)
    omitted: list[Omission] = []
    if weak:
        omitted.append(
            Omission(
                OmissionReason.BELOW_MIN_TRUST,
                len(weak),
                f"below the requested minimum trust ({min_trust.label})",
                tuple(f.id for f in weak),
            )
        )
    if invalid:
        omitted.append(
            Omission(
                OmissionReason.NOT_VALID_AT,
                len(invalid),
                f"not valid at {as_of.isoformat()} (expired, retired or not yet in effect)",
                tuple(f.id for f in invalid),
            )
        )
    return kept, omitted


def merge_omissions(omissions: Iterable[Omission]) -> list[Omission]:
    """One omission per reason, with counts and ids added up."""
    merged: dict[OmissionReason, Omission] = {}
    for omission in omissions:
        seen = merged.get(omission.reason)
        if seen is None:
            merged[omission.reason] = omission
            continue
        merged[omission.reason] = Omission(
            omission.reason,
            seen.count + omission.count,
            seen.detail,
            (*seen.fact_ids, *omission.fact_ids),
        )
    return list(merged.values())


def render_fact_line(fact: Fact) -> str:
    evidence = "; ".join(fact.provenance.evidence)
    return (
        f"[{fact.trust.label}] {fact.subject}: {fact.statement} "
        f"(by {fact.provenance.author.ref}; evidence: {evidence})"
    )


def _tokens(text: str) -> set[str]:
    return {word for word in text.lower().split() if len(word) > 2}


def rank_facts(facts: Sequence[Fact], task: str | None) -> list[Fact]:
    """Order facts for a task: relevance to it, then trust, then recency."""
    task_words = _tokens(task) if task else set()

    def key(fact: Fact) -> tuple[int, int, float]:
        overlap = len(task_words & _tokens(f"{fact.subject} {fact.statement}"))
        return (-overlap, -int(fact.trust), -fact.recorded_at.timestamp())

    return sorted(facts, key=key)


def estimate_tokens(text: str, chars_per_token: int) -> int:
    return math.ceil(len(text) / chars_per_token)


def pack_facts(
    facts: Sequence[Fact],
    *,
    task: str | None,
    budget_tokens: int | None,
    chars_per_token: int,
) -> tuple[list[Fact], list[Omission], int]:
    """Rank *facts* and keep as many whole facts as *budget_tokens* allows.

    Packing stops at the first fact that does not fit (no skipping to smaller
    ones, which would reorder the ranking), and says how many were left out.
    """
    ranked = rank_facts(facts, task)
    if budget_tokens is None:
        used = sum(estimate_tokens(render_fact_line(f), chars_per_token) for f in ranked)
        return ranked, [], used
    kept: list[Fact] = []
    used = 0
    for fact in ranked:
        cost = estimate_tokens(render_fact_line(fact), chars_per_token)
        if used + cost > budget_tokens:
            break
        kept.append(fact)
        used += cost
    dropped = ranked[len(kept) :]
    omitted = []
    if dropped:
        omitted.append(
            Omission(
                OmissionReason.BUDGET,
                len(dropped),
                f"did not fit the {budget_tokens}-token budget "
                f"({used} used); raise budget_tokens to include them",
                tuple(f.id for f in dropped),
            )
        )
    return kept, omitted, used
