"""Persistent ODIN court audit — decisions recorded as episodic memory.

NIU-1021 requires court decisions to be replayable from persisted records.
This sink converts each :class:`~ravn.odin.court.CourtDecisionRecord` into an
:class:`~ravn.domain.models.Episode` through the shared ``MemoryPort``, the
same persistence path the feedback recorder uses, so court history survives
restarts and is queryable by environment and correlation.
"""

from __future__ import annotations

from dataclasses import asdict

from ravn.domain.models import Episode, Outcome
from ravn.odin.court import CourtDecisionRecord
from ravn.ports.memory import MemoryPort

#: Court decisions that resolve without escalating to a human.
_AUTONOMOUS_DECISIONS = frozenset({"autonomous_action", "record_only", "no_op"})


class EpisodicCourtAuditSink:
    """Persist court decisions as episodes via the shared memory port."""

    def __init__(self, memory: MemoryPort) -> None:
        self._memory = memory

    async def record_decision(self, record: CourtDecisionRecord) -> None:
        await self._memory.record_episode(court_decision_to_episode(record))


def court_decision_to_episode(record: CourtDecisionRecord) -> Episode:
    """Convert one resolved court case into an episodic memory record."""
    tags = [
        "odin-court",
        f"environment:{record.environment_id}",
        f"decision:{record.decision}",
        f"tier:{record.tier}",
    ]
    if record.huddle_id:
        tags.append(f"huddle:{record.huddle_id}")

    structured = asdict(record)
    structured["kind"] = "odin_court_decision"
    structured["created_at"] = record.created_at.isoformat()

    summary = (
        f"ODIN court {record.decision} ({record.tier}) for "
        f"{record.environment_id} case {record.root_correlation_id}"
    )
    outcome = Outcome.SUCCESS if record.decision in _AUTONOMOUS_DECISIONS else Outcome.PARTIAL
    return Episode(
        episode_id=f"court:{record.audit_ref}",
        session_id=f"odin-court:{record.environment_id}",
        timestamp=record.created_at,
        summary=summary,
        task_description=(
            f"Resolve resident Valkyrie judgments for {record.environment_id} "
            f"case {record.root_correlation_id}"
        ),
        tools_used=[],
        outcome=outcome,
        tags=tags,
        reflection=record.rationale or None,
        structured_outcome=structured,
        outcome_valid=True,
    )
