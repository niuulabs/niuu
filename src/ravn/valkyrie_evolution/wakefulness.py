"""Wakefulness state machine and scheduled consolidation dreams for residents.

A resident Valkyrie has a runtime sense of wakefulness:

* ``wakeful``  — signals arrived recently; actively handling load.
* ``watching`` — idle but alert; the default operating state.
* ``dreaming`` — running a scheduled consolidation pass.
* ``sleeping`` — the runtime is stopped.

Micro-dreams (reactive capability builds in
:class:`~ravn.valkyrie_evolution.resident_learning.ResidentLearningRuntime`)
stay signal-driven; the scheduled dream implemented here is the reflective
layer: it reviews skill usage telemetry, marks stale skills, promotes
repeatedly successful private skills when the autonomy policy allows it, and
reopens deferred capability gaps so held builds get another chance.

Every transition is published as ``valkyrie.state.changed`` and every dream as
``valkyrie.dream.started``/``valkyrie.dream.completed`` with a summary, so the
dashboard's wakefulness and last-dream columns reflect live truth.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from ravn.odin.review import ReviewItem, ReviewKind, ReviewRequester
from ravn.skills.management import SkillManagementRegistry
from ravn.valkyrie_evolution.resident_learning import ResidentLearningIdentity
from sleipnir.domain.catalog import learning_promoted, valkyrie_state_changed
from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import SleipnirPublisher

logger = logging.getLogger(__name__)

DEFAULT_TICK_INTERVAL_SECONDS = 5.0
DEFAULT_WAKEFUL_WINDOW_SECONDS = 30.0
DEFAULT_DREAM_INTERVAL_SECONDS = 3600.0
DEFAULT_DREAM_MIN_IDLE_SECONDS = 60.0
DEFAULT_STALE_SKILL_AGE_SECONDS = 7 * 24 * 3600.0
DEFAULT_PROMOTE_MIN_SUCCESSES = 3

#: How many recent feedback episodes one consolidation dream considers.
DEFAULT_FEEDBACK_QUERY_LIMIT = 50

_WAKEFUL = "wakeful"
_WATCHING = "watching"
_DREAMING = "dreaming"
_SLEEPING = "sleeping"


class ResidentWakefulness:
    """Drive wakefulness transitions and scheduled consolidation dreams."""

    def __init__(
        self,
        *,
        identity: ResidentLearningIdentity,
        skills: SkillManagementRegistry,
        publisher: SleipnirPublisher,
        resident_learning: Any | None = None,
        memory: Any | None = None,
        review_requester: ReviewRequester | None = None,
        tick_interval_seconds: float = DEFAULT_TICK_INTERVAL_SECONDS,
        wakeful_window_seconds: float = DEFAULT_WAKEFUL_WINDOW_SECONDS,
        dream_interval_seconds: float = DEFAULT_DREAM_INTERVAL_SECONDS,
        dream_min_idle_seconds: float = DEFAULT_DREAM_MIN_IDLE_SECONDS,
        stale_skill_age_seconds: float = DEFAULT_STALE_SKILL_AGE_SECONDS,
        promote_min_successes: int = DEFAULT_PROMOTE_MIN_SUCCESSES,
        clock: Callable[[], float] = time.monotonic,
        source: str = "",
    ) -> None:
        self._identity = identity
        self._skills = skills
        self._publisher = publisher
        self._resident_learning = resident_learning
        self._memory = memory
        self._review_requester = review_requester
        self._tick_interval_seconds = tick_interval_seconds
        self._wakeful_window_seconds = wakeful_window_seconds
        self._dream_interval_seconds = dream_interval_seconds
        self._dream_min_idle_seconds = dream_min_idle_seconds
        self._stale_skill_age_seconds = stale_skill_age_seconds
        self._promote_min_successes = promote_min_successes
        self._clock = clock
        self._source = source or identity.valkyrie_id
        self._state = _SLEEPING
        self._last_activity_at = clock()
        self._last_dream_at = clock()
        self._task: asyncio.Task | None = None

    @property
    def identity(self) -> ResidentLearningIdentity:
        """Live identity — follows operator autonomy changes applied by the
        learning runtime instead of a snapshot captured at construction."""
        if self._resident_learning is not None:
            return self._resident_learning.identity
        return self._identity

    @property
    def state(self) -> str:
        return self._state

    def notify_activity(self) -> None:
        """Record signal activity; drives watching -> wakeful transitions."""
        self._last_activity_at = self._clock()

    async def start(self) -> None:
        if self._task is not None:
            return
        await self._transition(_WATCHING, reason="resident runtime started")
        self._task = asyncio.create_task(self._run(), name="resident_wakefulness")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        await self._transition(_SLEEPING, reason="resident runtime stopped")

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._tick_interval_seconds)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("wakefulness: tick failed for %s", self.identity.valkyrie_id)

    async def tick(self) -> None:
        """Evaluate transitions and run a due consolidation dream."""
        if self._state in (_DREAMING, _SLEEPING):
            return
        now = self._clock()
        idle_seconds = now - self._last_activity_at
        dream_due = (
            now - self._last_dream_at >= self._dream_interval_seconds
            and idle_seconds >= self._dream_min_idle_seconds
        )
        if dream_due:
            await self.dream()
            return
        target = _WAKEFUL if idle_seconds <= self._wakeful_window_seconds else _WATCHING
        if target != self._state:
            reason = "signals arriving" if target == _WAKEFUL else f"idle for {int(idle_seconds)}s"
            await self._transition(target, reason=reason)

    async def dream(self) -> dict[str, Any]:
        """Run one scheduled consolidation dream and return its summary."""
        dream_id = f"dream:consolidation:{self.identity.environment_id}:{uuid4().hex[:8]}"
        await self._transition(_DREAMING, reason="scheduled consolidation dream")
        await self._publish_dream_event(
            "valkyrie.dream.started",
            dream_id,
            {"dream_kind": "consolidation"},
            summary=f"{self.identity.valkyrie_id} started consolidation dream",
        )
        summary = await self._consolidate()
        self._last_dream_at = self._clock()
        await self._publish_dream_event(
            "valkyrie.dream.completed",
            dream_id,
            {"dream_kind": "consolidation", **summary},
            summary=(
                f"{self.identity.valkyrie_id} consolidated "
                f"{summary['skills_reviewed']} skill(s): "
                f"{len(summary['promoted'])} promoted, "
                f"{len(summary['marked_stale'])} stale"
            ),
        )
        await self._transition(_WATCHING, reason="consolidation dream completed")
        return summary

    async def _consolidate(self) -> dict[str, Any]:
        from ravn.context.autonomy import (  # noqa: PLC0415
            AutonomyPolicy,
            SelfImprovementProposal,
        )

        rows = await self._skills.list_skills()
        now = datetime.now(UTC)
        marked_stale: list[str] = []
        promoted: list[str] = []
        promotion_candidates: list[str] = []
        policy = AutonomyPolicy()
        feedback = await self._recent_feedback()

        for row in rows:
            metadata = row["metadata"]
            name = str(metadata.get("name") or row["skill"].get("name") or "")
            if not name or metadata.get("pinned"):
                continue

            if self._is_stale(metadata, now) and metadata.get("status") == "active":
                await self._skills.mark_stale(name)
                marked_stale.append(name)
                continue

            if not self._is_promotion_candidate(metadata):
                continue
            implicated = name in feedback["implicated_skills"]
            proposal = SelfImprovementProposal(
                proposal_id=f"dream-promote:{self.identity.environment_id}:{name}",
                title=name,
                artifact_type="skill",
                action="promote",
                content=str(row["skill"].get("content") or ""),
                scope="environment",
                environment_id=self.identity.environment_id,
                domain=self.identity.domain,
                mode=self.identity.autonomy_mode,
                risk_class="low",
            )
            decision = policy.decide(proposal)
            if implicated or decision.decision != "allow":
                # Held promotions go to the operator through the one review
                # path instead of evaporating with the dream summary.
                promotion_candidates.append(name)
                await self._file_promotion_review(
                    name,
                    metadata,
                    policy_decision=decision.decision,
                    policy_reason=(
                        "negative feedback names this skill" if implicated else decision.reason
                    ),
                    implicated_by_feedback=implicated,
                )
                continue
            await self._skills.promote(
                name,
                scope="environment",
                environment_id=self.identity.environment_id,
                domain=self.identity.domain,
            )
            promoted.append(name)
            await self._publisher.publish(
                learning_promoted(
                    environment_id=self.identity.environment_id,
                    learning_id=f"skill:{name}",
                    from_scope="private",
                    to_scope="environment",
                    summary=(
                        f"Consolidation dream promoted {name} after "
                        f"{metadata.get('success_count')} successful runs"
                    ),
                    source=self._source,
                    confidence=self._confidence(metadata),
                )
            )

        return {
            "skills_reviewed": len(rows),
            "marked_stale": marked_stale,
            "promoted": promoted,
            "promotion_candidates": promotion_candidates,
            "feedback": {
                "positive": feedback["positive"],
                "negative": feedback["negative"],
                "delivery": feedback["delivery"],
                "implicated_skills": sorted(feedback["implicated_skills"]),
            },
        }

    async def _file_promotion_review(
        self,
        name: str,
        metadata: dict[str, Any],
        *,
        policy_decision: str,
        policy_reason: str,
        implicated_by_feedback: bool,
    ) -> None:
        """File a held promotion as a ReviewItem on the unified ODIN path."""
        if self._review_requester is None:
            return
        success_count = int(metadata.get("success_count") or 0)
        item = ReviewItem.new(
            kind=ReviewKind.SKILL_PROMOTION.value,
            requested_action="promote",
            environment_id=self.identity.environment_id,
            valkyrie_id=self.identity.valkyrie_id,
            title=name,
            summary=(
                f"Promote {name} from private to environment scope after "
                f"{success_count} successful run(s)"
            ),
            domain=self.identity.domain,
            urgency=0.4,
            dedupe_key=(
                f"{ReviewKind.SKILL_PROMOTION.value}:{self.identity.environment_id}:{name}"
            ),
            evidence={
                "skill_name": name,
                "from_scope": str(metadata.get("scope") or "private"),
                "to_scope": "environment",
                "success_count": success_count,
                "failure_count": int(metadata.get("failure_count") or 0),
                "run_count": int(metadata.get("run_count") or 0),
                "confidence": self._confidence(metadata),
                "policy_decision": policy_decision,
                "policy_reason": policy_reason,
                "implicated_by_feedback": implicated_by_feedback,
            },
            requested_by=self.identity.valkyrie_id,
        )
        await self._review_requester.request(item)

    async def _recent_feedback(self) -> dict[str, Any]:
        """Summarize recorded resident feedback for this environment.

        Feedback episodes are written by the feedback recorder (NIU-1022);
        the dream uses them as signal-quality input: counts go into the dream
        summary, and skills named by failure feedback (in the correction
        payload or notes) are held back from automatic promotion.
        """
        summary: dict[str, Any] = {
            "positive": 0,
            "negative": 0,
            "delivery": 0,
            "implicated_skills": set(),
        }
        if self._memory is None:
            return summary
        from ravn.feedback.recorder import _FAILURE_FEEDBACK  # noqa: PLC0415

        matches = await self._memory.query_episodes(
            f"valkyrie-feedback environment:{self.identity.environment_id}",
            limit=DEFAULT_FEEDBACK_QUERY_LIMIT,
            min_relevance=0.0,
        )
        for match in matches:
            structured = match.episode.structured_outcome or {}
            if structured.get("kind") != "environment_feedback":
                continue
            if structured.get("environment_id") != self.identity.environment_id:
                continue
            feedback_type = str(structured.get("feedback_type") or "")
            if feedback_type in _FAILURE_FEEDBACK:
                summary["negative"] += 1
                correction = structured.get("correction") or {}
                skill_name = str(correction.get("skill_name") or "").strip()
                if skill_name:
                    summary["implicated_skills"].add(skill_name)
            elif feedback_type in ("snooze", "escalate"):
                summary["delivery"] += 1
            else:
                summary["positive"] += 1
        return summary

    def _is_stale(self, metadata: dict[str, Any], now: datetime) -> bool:
        last_used = str(metadata.get("last_used_at") or metadata.get("updated_at") or "")
        if not last_used:
            return False
        try:
            last_used_at = datetime.fromisoformat(last_used)
        except ValueError:
            return False
        return (now - last_used_at).total_seconds() >= self._stale_skill_age_seconds

    def _is_promotion_candidate(self, metadata: dict[str, Any]) -> bool:
        return (
            metadata.get("status") == "active"
            and metadata.get("scope") == "private"
            and int(metadata.get("success_count") or 0) >= self._promote_min_successes
            and int(metadata.get("failure_count") or 0) == 0
        )

    @staticmethod
    def _confidence(metadata: dict[str, Any]) -> float:
        successes = int(metadata.get("success_count") or 0)
        runs = int(metadata.get("run_count") or 0)
        if runs <= 0:
            return 0.0
        return round(successes / runs, 2)

    async def _transition(self, new_state: str, *, reason: str) -> None:
        if new_state == self._state:
            return
        previous = self._state
        self._state = new_state
        await self._publisher.publish(
            valkyrie_state_changed(
                environment_id=self.identity.environment_id,
                valkyrie_id=self.identity.valkyrie_id,
                previous_state=previous,
                new_state=new_state,
                reason=reason,
                source=self._source,
            )
        )

    async def _publish_dream_event(
        self,
        event_type: str,
        dream_id: str,
        payload: dict[str, Any],
        *,
        summary: str,
    ) -> None:
        await self._publisher.publish(
            SleipnirEvent(
                event_type=event_type,
                source=self._source,
                payload={
                    "environment_id": self.identity.environment_id,
                    "environment_type": self.identity.environment_type,
                    "valkyrie_id": self.identity.valkyrie_id,
                    "dream_id": dream_id,
                    **payload,
                },
                summary=summary,
                urgency=0.2,
                domain="infrastructure",
                timestamp=datetime.now(UTC),
                correlation_id=dream_id,
            )
        )


# Re-export for config-driven discovery alongside the other evolution pieces.
__all__ = [
    "DEFAULT_DREAM_INTERVAL_SECONDS",
    "DEFAULT_DREAM_MIN_IDLE_SECONDS",
    "DEFAULT_PROMOTE_MIN_SUCCESSES",
    "DEFAULT_STALE_SKILL_AGE_SECONDS",
    "DEFAULT_TICK_INTERVAL_SECONDS",
    "DEFAULT_WAKEFUL_WINDOW_SECONDS",
    "ResidentWakefulness",
]
