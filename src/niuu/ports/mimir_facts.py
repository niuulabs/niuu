"""MimirFactPort — typed facts with provenance, trust and validity.

Beside :class:`~niuu.ports.mimir.MimirPort` (the wiki), not inside it: the
wiki is prose a model maintains, facts are claims with an author and a level
of trust. It lives in ``niuu`` for the reason ``MimirPort`` does: the Mímir
service (``src/mimir/``) implements it and the clients that call it (Ravn,
Skuld, harness sessions, Ting) depend on it, and per
``.claude/rules/module-boundaries.md`` shared interfaces go in ``niuu`` so
neither side imports the other.

The port is the *backend* contract. It does not decide trust: callers reach it
through ``mimir.facts.FactService``, which builds :class:`NewFact` with the
trust the platform assigned. An adapter stores what it is given and must never
raise a level.

Errors are loud. A backend that cannot answer raises
:class:`~niuu.domain.mimir_facts.FactBackendError`; an empty result means the
backend answered and held nothing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime

from niuu.domain.mimir_facts import (
    ContextPack,
    EntityRecord,
    Fact,
    FactRecall,
    NewFact,
    Omission,
    RememberOutcome,
    TrustLevel,
    merge_omissions,
    pack_facts,
    render_fact_line,
)


class MimirFactPort(ABC):
    @abstractmethod
    async def remember(self, new: NewFact) -> RememberOutcome:
        """Store one fact, keeping its provenance, trust, validity and what it replaces."""
        raise NotImplementedError

    @abstractmethod
    async def get(self, fact_id: str) -> Fact:
        """Return one fact, current or retired. Raises ``FactNotFoundError``."""
        raise NotImplementedError

    @abstractmethod
    async def recall(
        self,
        *,
        subject: str | None = None,
        contains: str | None = None,
        min_trust: TrustLevel = TrustLevel.AGENT,
        as_of: datetime | None = None,
    ) -> FactRecall:
        """Facts valid at *as_of* (default now) and at or above *min_trust*.

        *contains* keeps facts whose statement contains that text (case-insensitive).

        Retired and expired facts that were valid at *as_of* are included: history
        is kept, so asking about the past gets the past. Facts left out say why.
        """
        raise NotImplementedError

    @abstractmethod
    async def forget(
        self, fact_id: str, *, by: str, why: str, superseded_by: str | None = None
    ) -> Fact:
        """Retire a fact, recording *by* and *why*. The fact stays in history.

        Retiring an already-retired fact changes nothing and returns it.
        """
        raise NotImplementedError

    @abstractmethod
    async def entity(self, subject: str) -> EntityRecord:
        """Everything known about one entity: current facts, history, and its card."""
        raise NotImplementedError

    async def context_pack(
        self,
        subjects: list[str],
        *,
        task: str | None = None,
        budget_tokens: int | None = None,
        min_trust: TrustLevel = TrustLevel.AGENT,
        as_of: datetime | None = None,
        chars_per_token: int,
    ) -> ContextPack:
        """A ranked, budget-packed set of facts for a task, saying what was left out.

        Built on :meth:`recall`, so trust and as-of hold and every omission is
        accounted for (budget, trust, validity, a backend cap). Adapters whose
        backend can do this natively may override it, but only if the override
        keeps those guarantees.
        """
        moment = as_of or datetime.now(UTC)
        gathered: list[Fact] = []
        omitted: list[Omission] = []
        for subject in subjects:
            recalled = await self.recall(subject=subject, min_trust=min_trust, as_of=moment)
            gathered.extend(recalled.facts)
            omitted.extend(recalled.omitted)
        unique = list({fact.id: fact for fact in gathered}.values())
        packed, budget_omitted, used = pack_facts(
            unique, task=task, budget_tokens=budget_tokens, chars_per_token=chars_per_token
        )
        return ContextPack(
            facts=tuple(packed),
            omitted=tuple(merge_omissions([*omitted, *budget_omitted])),
            text="\n".join(render_fact_line(fact) for fact in packed),
            budget_tokens=budget_tokens,
            used_tokens=used,
        )
