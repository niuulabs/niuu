"""The fact service: where the platform decides trust.

Everything that reaches a :class:`~niuu.ports.mimir_facts.MimirFactPort` from
outside (the MCP server, the agent tools, a peer) goes through
:class:`FactService`, which takes a :class:`~niuu.domain.mimir_facts.FactCaller`
the platform built from the authenticated principal. The caller's own words
never choose a level; see ``niuu.domain.mimir_facts`` for the rules.
"""

from __future__ import annotations

import logging
from datetime import datetime

from niuu.domain.mimir_facts import (
    AuthorKind,
    ContextPack,
    EntityRecord,
    Fact,
    FactAuthor,
    FactBackendError,
    FactCaller,
    FactDraft,
    FactError,
    FactRecall,
    FactValidationError,
    NewFact,
    RememberOutcome,
    SharedFact,
    SharedOrigin,
    TrustError,
    TrustLevel,
    derive_trust,
    received_trust,
    require_may_override,
    share_bundle,
)
from niuu.domain.models import Principal, actor_name
from niuu.ports.mimir_facts import MimirFactPort

logger = logging.getLogger(__name__)


class FactCallerResolver:
    """Turns an authenticated principal into the caller whose level it earns.

    A *delegated* principal (a workload acting for a user: it has an ``actor``, a
    scoped ``token_use`` or scopes) is an agent, or a tool when one of its scopes
    is mapped to the tool level, and never higher: the mapping is validated to
    stop there. A *human* principal gets the highest level its roles map to.
    A human with no mapped role is refused rather than given a default.
    """

    def __init__(
        self,
        role_levels: dict[str, str],
        scope_levels: dict[str, str] | None = None,
    ) -> None:
        self._roles = {role: TrustLevel.parse(level) for role, level in role_levels.items()}
        self._scopes = {
            scope: TrustLevel.parse(level) for scope, level in (scope_levels or {}).items()
        }
        too_high = [s for s, level in self._scopes.items() if level > TrustLevel.TOOL]
        if too_high:
            raise ValueError(
                f"scope_levels {too_high} map a workload scope above 'tool'. A workload "
                "(a resident, a session) never holds operator or person trust; grant those "
                "to people through role_levels"
            )

    def resolve(self, principal: Principal) -> FactCaller:
        if principal.actor or principal.token_use or principal.scopes:
            ident = (
                actor_name(principal.actor)
                if principal.actor
                else principal.bound_session_id or principal.user_id
            )
            tool = any(self._scopes.get(scope) == TrustLevel.TOOL for scope in principal.scopes)
            kind = AuthorKind.TOOL if tool else AuthorKind.AGENT
            return FactCaller(FactAuthor(kind, ident))
        levels = [self._roles[role] for role in principal.roles if role in self._roles]
        if not levels:
            raise TrustError(
                f"{principal.user_id} holds no role with a fact trust level "
                f"(roles: {principal.roles or 'none'}). Map one in facts.trust.role_levels"
            )
        kind = {
            TrustLevel.PERSON: AuthorKind.PERSON,
            TrustLevel.OPERATOR: AuthorKind.OPERATOR,
            TrustLevel.TOOL: AuthorKind.TOOL,
            TrustLevel.AGENT: AuthorKind.AGENT,
        }[max(levels)]
        return FactCaller(FactAuthor(kind, principal.user_id))


class FactService:
    """Typed-fact operations with the trust policy applied.

    Args:
        port: the fact backend.
        source: this instance's name, stamped on facts it shares with peers.
        chars_per_token: the estimate ``context_pack`` budgets with.
    """

    def __init__(self, port: MimirFactPort, *, source: str, chars_per_token: int) -> None:
        if chars_per_token < 1:
            raise ValueError("chars_per_token must be positive")
        self._port = port
        self._source = source
        self._chars_per_token = chars_per_token

    async def remember(self, caller: FactCaller, draft: FactDraft) -> RememberOutcome:
        inputs = [await self._port.get(fact_id) for fact_id in draft.derived_from]
        for fact in inputs:
            if fact.retired is not None:
                raise FactValidationError(
                    f"fact {fact.id} was retired ({fact.retired.why}); a fact cannot be "
                    "derived from one that is no longer current"
                )
        replaced = [await self._port.get(fact_id) for fact_id in draft.replaces]
        for fact in replaced:
            if fact.retired is not None:
                raise FactValidationError(
                    f"fact {fact.id} is already retired and cannot be replaced"
                )
            require_may_override(caller, fact, "replace")
        new = NewFact(draft=draft, author=caller.author, trust=derive_trust(caller, inputs))
        outcome = await self._port.remember(new)
        await self._retire_replaced(caller, replaced, outcome.fact)
        return outcome

    async def _retire_replaced(self, caller: FactCaller, replaced: list[Fact], new: Fact) -> None:
        for fact in replaced:
            if fact.id == new.id:
                continue
            try:
                await self._port.forget(
                    fact.id,
                    by=caller.author.ref,
                    why=f"replaced by {new.id}",
                    superseded_by=new.id,
                )
            except FactError as exc:
                raise FactBackendError(
                    f"fact {new.id} was stored, but retiring the fact it replaces ({fact.id}) "
                    f"failed: {exc}. Retire {fact.id} with forget to finish the replacement"
                ) from exc

    async def receive(self, caller: FactCaller, shared: SharedFact) -> RememberOutcome:
        """Store a fact a peer shared. It stays its author's, at no more than the caller's level."""
        evidence = shared.evidence or ()
        draft = FactDraft(
            subject=shared.subject,
            statement=shared.statement,
            evidence=evidence,
            kind=shared.kind,
            valid_from=shared.valid_from,
            valid_until=shared.valid_until,
        )
        origin = shared.origin or SharedOrigin(
            author=shared.author,
            claimed_trust=shared.trust,
            fact_id=shared.fact_id,
            source=shared.source or "unknown peer",
            evidence=evidence,
        )
        new = NewFact(
            draft=draft,
            author=shared.author,
            trust=received_trust(caller, shared.trust),
            origin=origin,
            imported_by=caller.author,
        )
        return await self._port.remember(new)

    async def share(self, fact_id: str) -> SharedFact:
        return share_bundle(await self._port.get(fact_id), source=self._source)

    async def get(self, fact_id: str) -> Fact:
        return await self._port.get(fact_id)

    async def recall(
        self,
        *,
        subject: str | None = None,
        contains: str | None = None,
        min_trust: TrustLevel = TrustLevel.AGENT,
        as_of: datetime | None = None,
    ) -> FactRecall:
        return await self._port.recall(
            subject=subject, contains=contains, min_trust=min_trust, as_of=as_of
        )

    async def forget(self, caller: FactCaller, fact_id: str, why: str) -> Fact:
        if not why.strip():
            raise FactValidationError("forgetting a fact needs a reason (why)")
        fact = await self._port.get(fact_id)
        if fact.retired is not None:
            return fact
        require_may_override(caller, fact, "retire")
        return await self._port.forget(fact_id, by=caller.author.ref, why=why.strip())

    async def entity(self, subject: str) -> EntityRecord:
        return await self._port.entity(subject)

    async def context_pack(
        self,
        subjects: list[str],
        *,
        task: str | None = None,
        budget_tokens: int | None = None,
        min_trust: TrustLevel = TrustLevel.AGENT,
        as_of: datetime | None = None,
    ) -> ContextPack:
        if not subjects:
            raise FactValidationError("context_pack needs at least one subject")
        if budget_tokens is not None and budget_tokens < 1:
            raise FactValidationError("budget_tokens must be at least 1; omit it for no budget")
        return await self._port.context_pack(
            subjects,
            task=task,
            budget_tokens=budget_tokens,
            min_trust=min_trust,
            as_of=as_of,
            chars_per_token=self._chars_per_token,
        )
