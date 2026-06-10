"""Resident Valkyrie wakefulness and dream-cycle scheduler."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

from ravn.context.autonomy import (
    AutonomyMode,
    JsonProposalStore,
    ProposalScope,
    ProposalStatus,
    SelfImprovementProposal,
    evaluate_and_store_proposals,
    proposals_from_evolution,
)
from ravn.context.evolution import PromptEvolution
from ravn.domain.environment import Environment, apply_environment_metadata
from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import SleipnirPublisher

WakefulnessState = Literal["awake", "watchful", "dreaming", "suspended", "unhealthy"]
EvolutionProvider = Callable[[], Awaitable[PromptEvolution]]
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class ValkyrieCycleConfig:
    """Runtime cadence for a resident Valkyrie."""

    valkyrie_id: str
    dream_interval_seconds: int = 3600
    wake_interval_seconds: int = 30
    autonomy_mode: str = AutonomyMode.GUARDED.value
    proposal_store_path: str | Path = "~/.ravn/autonomy_proposals.json"
    domain: str = ""
    auto_apply_allowed: bool = True


@dataclass
class ValkyrieCycleStatus:
    """UI/status projection for the resident rhythm."""

    environment_id: str
    valkyrie_id: str
    wakefulness: WakefulnessState = "watchful"
    last_wake_at: datetime | None = None
    last_dream_started_at: datetime | None = None
    last_dream_completed_at: datetime | None = None
    next_dream_after: datetime | None = None
    last_error: str = ""
    proposals_created: int = 0
    proposals_applied: int = 0
    proposals_deferred: int = 0

    def to_ui_contract(self) -> dict:
        """Return the Warden-style status shape consumed by UI projections."""
        return {
            "environment_id": self.environment_id,
            "valkyrie_id": self.valkyrie_id,
            "wakefulness": self.wakefulness,
            "last_wake_at": self.last_wake_at.isoformat() if self.last_wake_at else "",
            "last_dream_started_at": (
                self.last_dream_started_at.isoformat() if self.last_dream_started_at else ""
            ),
            "last_dream_completed_at": (
                self.last_dream_completed_at.isoformat() if self.last_dream_completed_at else ""
            ),
            "next_dream_after": self.next_dream_after.isoformat() if self.next_dream_after else "",
            "last_error": self.last_error,
            "proposals_created": self.proposals_created,
            "proposals_applied": self.proposals_applied,
            "proposals_deferred": self.proposals_deferred,
        }


@dataclass
class DreamCycleResult:
    """Auditable dream-cycle output."""

    dream_id: str
    started_at: datetime
    completed_at: datetime | None = None
    status: Literal["completed", "failed"] = "completed"
    proposals: list[SelfImprovementProposal] = field(default_factory=list)
    applied: list[SelfImprovementProposal] = field(default_factory=list)
    deferred: list[SelfImprovementProposal] = field(default_factory=list)
    error: str = ""


class ValkyrieCycleScheduler:
    """Coordinate resident wake and dream cycles using existing Ravn primitives."""

    def __init__(
        self,
        *,
        environment: Environment,
        config: ValkyrieCycleConfig,
        publisher: SleipnirPublisher,
        evolution_provider: EvolutionProvider,
        proposal_store: JsonProposalStore | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.environment = environment
        self.config = config
        self._publisher = publisher
        self._evolution_provider = evolution_provider
        self._store = proposal_store or JsonProposalStore(config.proposal_store_path)
        self._clock = clock or (lambda: datetime.now(UTC))
        now = self._clock()
        self.status = ValkyrieCycleStatus(
            environment_id=environment.id,
            valkyrie_id=config.valkyrie_id,
            next_dream_after=now + timedelta(seconds=config.dream_interval_seconds),
        )

    async def tick(self) -> DreamCycleResult | None:
        """Run one scheduler tick and start a dream cycle if it is due."""
        now = self._clock()
        if self.status.next_dream_after and now >= self.status.next_dream_after:
            return await self.run_dream_cycle()
        await self.run_wake_cycle(reason="scheduler tick")
        return None

    async def run_wake_cycle(self, *, reason: str = "signals available") -> None:
        """Record a wake/watch cycle status update."""
        now = self._clock()
        self.status.last_wake_at = now
        await self._transition("awake", reason=reason, at=now)
        await self._transition("watchful", reason="wake cycle completed", at=now)

    async def suspend(self, *, reason: str) -> None:
        """Suspend the resident visibly without deleting state."""
        await self._transition("suspended", reason=reason, at=self._clock())

    async def run_dream_cycle(self) -> DreamCycleResult:
        """Run reflection/evolution and emit auditable dream-cycle events."""
        dream_id = f"dream:{self.environment.id}:{uuid4().hex[:12]}"
        started_at = self._clock()
        result = DreamCycleResult(dream_id=dream_id, started_at=started_at)
        self.status.last_dream_started_at = started_at
        await self._publish_dream_event(
            "learning.dream.started",
            dream_id=dream_id,
            timestamp=started_at,
            payload={"autonomy_mode": self.config.autonomy_mode},
        )
        await self._transition("dreaming", reason="dream cycle started", at=started_at)

        try:
            evolution = await self._evolution_provider()
            proposals = proposals_from_evolution(
                evolution,
                mode=self.config.autonomy_mode,
                scope=ProposalScope.ENVIRONMENT.value,
                environment_id=self.environment.id,
                domain=self.config.domain or self.environment.type,
            )
            saved = await evaluate_and_store_proposals(proposals, store=self._store)
            applied = await self._apply_allowed(saved)
            deferred = [
                proposal for proposal in saved if proposal.status != ProposalStatus.APPLIED.value
            ]
            completed_at = self._clock()
            result.completed_at = completed_at
            result.proposals = saved
            result.applied = applied
            result.deferred = deferred
            self.status.last_dream_completed_at = completed_at
            self.status.next_dream_after = completed_at + timedelta(
                seconds=self.config.dream_interval_seconds
            )
            self.status.last_error = ""
            self.status.proposals_created += len(saved)
            self.status.proposals_applied += len(applied)
            self.status.proposals_deferred += len(deferred)
            await self._publish_dream_event(
                "learning.dream.completed",
                dream_id=dream_id,
                timestamp=completed_at,
                payload={
                    "episodes_analyzed": evolution.episodes_analyzed,
                    "outcomes_analyzed": evolution.outcomes_analyzed,
                    "proposals_created": len(saved),
                    "proposals_applied": len(applied),
                    "proposals_deferred": len(deferred),
                    "proposal_ids": [proposal.proposal_id for proposal in saved],
                    "applied_proposal_ids": [proposal.proposal_id for proposal in applied],
                    "deferred_proposal_ids": [proposal.proposal_id for proposal in deferred],
                },
            )
            await self._transition("watchful", reason="dream cycle completed", at=completed_at)
            return result
        except Exception as exc:
            failed_at = self._clock()
            result.completed_at = failed_at
            result.status = "failed"
            result.error = str(exc)
            self.status.last_error = str(exc)
            self.status.next_dream_after = failed_at + timedelta(
                seconds=self.config.dream_interval_seconds
            )
            await self._publish_dream_event(
                "learning.dream.failed",
                dream_id=dream_id,
                timestamp=failed_at,
                payload={"error": str(exc)},
            )
            await self._transition("unhealthy", reason=str(exc), at=failed_at)
            return result

    async def _apply_allowed(
        self,
        proposals: list[SelfImprovementProposal],
    ) -> list[SelfImprovementProposal]:
        if not self.config.auto_apply_allowed:
            return []
        applied: list[SelfImprovementProposal] = []
        for proposal in proposals:
            if proposal.policy_decision != "allow":
                continue
            applied.append(
                await self._store.apply(
                    proposal.proposal_id,
                    self._apply_proposal,
                )
            )
        return applied

    async def _apply_proposal(
        self,
        proposal: SelfImprovementProposal,
    ) -> tuple[dict[str, str], dict]:
        artifact_ref = f"{proposal.artifact_type}:{proposal.proposal_id}"
        return (
            {
                "audit_ref": artifact_ref,
                "environment_id": self.environment.id,
                "mode": proposal.mode,
            },
            {
                "proposal_id": proposal.proposal_id,
                "previous_status": proposal.status,
            },
        )

    async def _transition(
        self,
        new_state: WakefulnessState,
        *,
        reason: str,
        at: datetime,
    ) -> None:
        previous = self.status.wakefulness
        self.status.wakefulness = new_state
        event = SleipnirEvent(
            event_type="valkyrie.wakefulness.changed",
            source=f"valkyrie:{self.config.valkyrie_id}",
            payload={
                "environment_id": self.environment.id,
                "valkyrie_id": self.config.valkyrie_id,
                "previous_state": previous,
                "new_state": new_state,
                "reason": reason,
                "status": self.status.to_ui_contract(),
            },
            summary=f"Valkyrie {self.config.valkyrie_id} wakefulness {previous} -> {new_state}",
            urgency=0.6 if new_state == "unhealthy" else 0.2,
            domain="infrastructure",
            timestamp=at,
            correlation_id=f"{self.environment.id}:wakefulness",
            tenant_id=self.environment.tenant_id,
        )
        apply_environment_metadata(event, self.environment)
        await self._publisher.publish(event)

    async def _publish_dream_event(
        self,
        event_type: str,
        *,
        dream_id: str,
        timestamp: datetime,
        payload: dict,
    ) -> None:
        event = SleipnirEvent(
            event_type=event_type,
            source=f"valkyrie:{self.config.valkyrie_id}",
            payload={
                "dream_id": dream_id,
                "environment_id": self.environment.id,
                "valkyrie_id": self.config.valkyrie_id,
                **payload,
                "status": self.status.to_ui_contract(),
            },
            summary=f"{event_type} for {self.config.valkyrie_id}",
            urgency=0.2 if event_type != "learning.dream.failed" else 0.7,
            domain="infrastructure",
            timestamp=timestamp,
            correlation_id=dream_id,
            tenant_id=self.environment.tenant_id,
        )
        apply_environment_metadata(event, self.environment, root_correlation_id=dream_id)
        await self._publisher.publish(event)
