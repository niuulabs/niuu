"""Flock learning exchange built on existing Flock, Mimir, and Sleipnir paths."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from ravn.adapters.reflection.learning_promotion import LearningPromotionRecord
from ravn.domain.environment import Environment
from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import SleipnirPublisher

FlockLearningArtifactType = Literal[
    "signal_heuristic",
    "incident_signature",
    "tool_skill",
    "runbook_patch",
    "caution",
]
FlockLearningStatus = Literal[
    "candidate",
    "blocked",
    "canary",
    "adopted",
    "rejected",
    "active",
    "archived",
    "rolled_back",
]
FlockPeerAction = Literal["canary_started", "adopted", "rejected", "overridden", "rolled_back"]

FLOCK_LEARNING_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "flock.learning.proposed",
        "flock.learning.canary_started",
        "flock.learning.adopted",
        "flock.learning.rejected",
        "flock.learning.activated",
        "flock.learning.rolled_back",
    }
)

#: Statuses a re-produced duplicate never folds into: the earlier learning was
#: declined here, so the new production deserves its own record and review.
FOLD_EXCLUDED_STATUSES: frozenset[str] = frozenset(
    {"rejected", "blocked", "rolled_back", "archived"}
)


def learning_fingerprint(title: str, scope: str) -> str:
    """Duplicate-detection key: normalized lowercase title plus scope."""
    return f"{' '.join(title.lower().split())}::{scope.strip().lower()}"


@dataclass(frozen=True)
class FlockLearningCandidate:
    """Learning artifact proposed from one Environment into an existing Flock."""

    learning_id: str
    title: str
    artifact_type: FlockLearningArtifactType
    summary: str
    content: str
    flock_id: str
    source_environment_id: str
    source_valkyrie_id: str
    confidence: float
    redaction_status: str = "unredacted"
    promotion_id: str = ""
    source_path: str = ""
    promoted_path: str = ""
    source_episode_ids: list[str] = field(default_factory=list)
    evaluation_results: dict = field(default_factory=dict)
    rollback_status: str = ""
    tags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        """Duplicate-detection key for repetition folding."""
        return learning_fingerprint(self.title, str(self.metadata.get("scope") or "flock"))

    @classmethod
    def from_promotion(
        cls,
        record: LearningPromotionRecord,
        *,
        artifact_type: FlockLearningArtifactType,
        title: str,
        summary: str,
        content: str,
        tags: list[str] | None = None,
    ) -> FlockLearningCandidate:
        """Create a Flock candidate from an existing learning-promotion record."""
        return cls(
            learning_id=record.learning_id,
            title=title,
            artifact_type=artifact_type,
            summary=summary,
            content=content,
            flock_id=record.flock_id,
            source_environment_id=record.environment_id,
            source_valkyrie_id=record.source_valkyrie_id,
            confidence=record.confidence,
            redaction_status=record.redaction_status,
            promotion_id=record.promotion_id,
            source_path=record.source_path,
            promoted_path=record.promoted_path,
            source_episode_ids=list(record.source_episode_ids),
            rollback_status=record.status,
            tags=tags or [],
            metadata={
                "target_mount": record.target_mount,
                "promotion_status": record.status,
                "policy_decision": record.policy_decision,
                "domain": record.domain,
            },
        )


@dataclass
class FlockPeerDecision:
    """Local peer canary/adoption/override state for one Environment."""

    environment_id: str
    action: FlockPeerAction
    rationale: str = ""
    canary_passed: bool = False
    local_override_path: str = ""
    evaluation_results: dict = field(default_factory=dict)
    recorded_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class FlockLearningRecord:
    """Durable Flock learning lifecycle record."""

    exchange_id: str
    candidate: FlockLearningCandidate
    status: FlockLearningStatus = "candidate"
    peer_decisions: list[FlockPeerDecision] = field(default_factory=list)
    active_environment_ids: list[str] = field(default_factory=list)
    archived_at: str = ""
    rolled_back_at: str = ""
    #: How many times this learning has been (re-)produced; duplicates by
    #: fingerprint fold into the existing record instead of multiplying.
    repetition: int = 1
    #: Count of operator revisions applied to the candidate in place.
    revision: int = 0
    #: Last operator feedback verdict, kept so future dreams can see it.
    operator_feedback: dict = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @classmethod
    def from_dict(cls, data: dict) -> FlockLearningRecord:
        return cls(
            exchange_id=str(data.get("exchange_id") or uuid4()),
            candidate=FlockLearningCandidate(**data["candidate"]),
            status=str(data.get("status") or "candidate"),  # type: ignore[arg-type]
            peer_decisions=[
                FlockPeerDecision(**decision)
                for decision in data.get("peer_decisions", [])
                if isinstance(decision, dict)
            ],
            active_environment_ids=list(data.get("active_environment_ids") or []),
            archived_at=str(data.get("archived_at") or ""),
            rolled_back_at=str(data.get("rolled_back_at") or ""),
            repetition=max(int(data.get("repetition") or 1), 1),
            revision=int(data.get("revision") or 0),
            operator_feedback=dict(data.get("operator_feedback") or {}),
            created_at=str(data.get("created_at") or datetime.now(UTC).isoformat()),
            updated_at=str(data.get("updated_at") or datetime.now(UTC).isoformat()),
        )

    def to_dict(self) -> dict:
        return asdict(self)

    def decision_for(self, environment_id: str) -> FlockPeerDecision | None:
        for decision in reversed(self.peer_decisions):
            if decision.environment_id == environment_id:
                return decision
        return None


class FlockLearningStore:
    """JSON store for Flock learning exchange records."""

    def __init__(self, path: str | Path = "~/.ravn/flock_learning.json") -> None:
        self._path = Path(path).expanduser()
        self._records: dict[str, FlockLearningRecord] = {}
        self._load()

    def save(self, record: FlockLearningRecord) -> FlockLearningRecord:
        record.updated_at = datetime.now(UTC).isoformat()
        self._records[record.exchange_id] = record
        self._save()
        return record

    def get(self, exchange_id: str) -> FlockLearningRecord:
        try:
            return self._records[exchange_id]
        except KeyError as exc:
            raise ValueError(f"unknown Flock learning exchange: {exchange_id}") from exc

    def list(
        self,
        *,
        flock_id: str | None = None,
        status: str | None = None,
    ) -> list[FlockLearningRecord]:
        records = list(self._records.values())
        if flock_id:
            records = [record for record in records if record.candidate.flock_id == flock_id]
        if status:
            records = [record for record in records if record.status == status]
        return sorted(records, key=lambda record: record.created_at)

    def find_active_by_fingerprint(self, fingerprint: str) -> FlockLearningRecord | None:
        """The non-declined record matching a duplicate-detection key, if any."""
        for record in self.list():
            if record.status in FOLD_EXCLUDED_STATUSES:
                continue
            if record.candidate.fingerprint == fingerprint:
                return record
        return None

    def fold_duplicate(self, candidate: FlockLearningCandidate) -> FlockLearningRecord | None:
        """Fold a re-produced learning into its active fingerprint match.

        A dream that re-produces an existing non-rejected learning must not
        multiply records: the match's ``repetition`` is incremented, evidence
        is merged, and the timestamp refreshes. Returns None when there is
        nothing to fold into. Superseding revisions never fold — they are a
        deliberate new version of the learning they replace.
        """
        if str(candidate.metadata.get("supersedes") or ""):
            return None
        existing = self.find_active_by_fingerprint(candidate.fingerprint)
        if existing is None:
            return None
        existing.repetition += 1
        updates: dict = {
            "confidence": max(existing.candidate.confidence, candidate.confidence),
            "source_episode_ids": list(
                dict.fromkeys(
                    [
                        *existing.candidate.source_episode_ids,
                        *candidate.source_episode_ids,
                    ]
                )
            ),
            "evaluation_results": {
                **existing.candidate.evaluation_results,
                **candidate.evaluation_results,
            },
        }
        if not existing.candidate.content and candidate.content:
            updates["content"] = candidate.content
        if not existing.candidate.summary and candidate.summary:
            updates["summary"] = candidate.summary
        existing.candidate = replace(existing.candidate, **updates)
        return self.save(existing)

    def ready_for_peer(self, *, flock_id: str, environment_id: str) -> list[FlockLearningRecord]:
        """Return records a peer can still canary/adopt."""
        ready: list[FlockLearningRecord] = []
        for record in self.list(flock_id=flock_id):
            if record.status in {"blocked", "archived", "rolled_back"}:
                continue
            decision = record.decision_for(environment_id)
            if decision and decision.action in {"rejected", "overridden", "rolled_back"}:
                continue
            if decision and decision.action == "adopted":
                continue
            ready.append(record)
        return ready

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._records = {}
            return
        rows = raw.get("flock_learnings", raw if isinstance(raw, list) else [])
        self._records = {
            record.exchange_id: record
            for record in (FlockLearningRecord.from_dict(row) for row in rows)
        }

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"flock_learnings": [record.to_dict() for record in self.list()]}
        self._path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


class FlockLearningExchange:
    """Flock learning lifecycle using existing Sleipnir and Environment membership."""

    def __init__(
        self,
        *,
        store: FlockLearningStore,
        publisher: SleipnirPublisher | None = None,
        source: str = "ravn:flock-learning",
    ) -> None:
        self._store = store
        self._publisher = publisher
        self._source = source

    async def propose(self, candidate: FlockLearningCandidate) -> FlockLearningRecord:
        if candidate.redaction_status not in {"redacted", "safe", "none"}:
            blocked = self._store.save(
                FlockLearningRecord(
                    exchange_id=str(uuid4()),
                    candidate=candidate,
                    status="blocked",
                )
            )
            await self._emit(
                "flock.learning.rejected",
                blocked,
                reason="learning must be redacted before Flock exchange",
            )
            return blocked

        folded = self._store.fold_duplicate(candidate)
        if folded is not None:
            # A re-produced duplicate reinforces the existing record instead
            # of multiplying it; the update re-emits so mirrors converge.
            await self._emit(
                "flock.learning.proposed",
                folded,
                reason=f"duplicate folded; repetition {folded.repetition}",
            )
            return folded

        record = self._store.save(
            FlockLearningRecord(
                exchange_id=str(uuid4()),
                candidate=candidate,
                status="candidate",
            )
        )
        await self._emit("flock.learning.proposed", record)
        return record

    async def start_canary(
        self,
        exchange_id: str,
        *,
        environment_id: str,
        evaluation_results: dict | None = None,
        rationale: str = "",
    ) -> FlockLearningRecord:
        record = self._store.get(exchange_id)
        record.status = "canary"
        record.peer_decisions.append(
            FlockPeerDecision(
                environment_id=environment_id,
                action="canary_started",
                rationale=rationale,
                evaluation_results=evaluation_results or {},
            )
        )
        saved = self._store.save(record)
        await self._emit(
            "flock.learning.canary_started",
            saved,
            peer_environment_id=environment_id,
            evaluation_results=evaluation_results or {},
            rationale=rationale,
        )
        return saved

    async def adopt(
        self,
        exchange_id: str,
        *,
        environment_id: str,
        evaluation_results: dict | None = None,
        rationale: str = "",
    ) -> FlockLearningRecord:
        record = self._store.get(exchange_id)
        record.status = "adopted"
        if environment_id not in record.active_environment_ids:
            record.active_environment_ids.append(environment_id)
        record.peer_decisions.append(
            FlockPeerDecision(
                environment_id=environment_id,
                action="adopted",
                rationale=rationale,
                canary_passed=True,
                evaluation_results=evaluation_results or {},
            )
        )
        saved = self._store.save(record)
        await self._emit(
            "flock.learning.adopted",
            saved,
            peer_environment_id=environment_id,
            evaluation_results=evaluation_results or {},
            rationale=rationale,
        )
        return saved

    async def reject(
        self,
        exchange_id: str,
        *,
        environment_id: str,
        rationale: str,
        local_override_path: str = "",
        evaluation_results: dict | None = None,
    ) -> FlockLearningRecord:
        record = self._store.get(exchange_id)
        record.status = "rejected"
        record.peer_decisions.append(
            FlockPeerDecision(
                environment_id=environment_id,
                action="rejected",
                rationale=rationale,
                local_override_path=local_override_path,
                evaluation_results=evaluation_results or {},
            )
        )
        saved = self._store.save(record)
        await self._emit(
            "flock.learning.rejected",
            saved,
            peer_environment_id=environment_id,
            evaluation_results=evaluation_results or {},
            rationale=rationale,
            local_override_path=local_override_path,
        )
        return saved

    async def activate(self, exchange_id: str, *, environment_id: str = "") -> FlockLearningRecord:
        record = self._store.get(exchange_id)
        record.status = "active"
        if environment_id and environment_id not in record.active_environment_ids:
            record.active_environment_ids.append(environment_id)
        saved = self._store.save(record)
        await self._emit("flock.learning.activated", saved, peer_environment_id=environment_id)
        return saved

    async def rollback(
        self,
        exchange_id: str,
        *,
        environment_id: str,
        rationale: str,
    ) -> FlockLearningRecord:
        record = self._store.get(exchange_id)
        record.status = "rolled_back"
        record.rolled_back_at = datetime.now(UTC).isoformat()
        record.peer_decisions.append(
            FlockPeerDecision(
                environment_id=environment_id,
                action="rolled_back",
                rationale=rationale,
            )
        )
        saved = self._store.save(record)
        await self._emit(
            "flock.learning.rolled_back",
            saved,
            peer_environment_id=environment_id,
            rationale=rationale,
        )
        return saved

    def ready_for_environment(self, environment: Environment) -> list[FlockLearningRecord]:
        """Use existing Environment Flock memberships to find candidate learnings."""
        records: list[FlockLearningRecord] = []
        for flock_id in environment.flock_ids:
            records.extend(
                self._store.ready_for_peer(
                    flock_id=flock_id,
                    environment_id=environment.id,
                )
            )
        return records

    def adopted_context(self, *, environment_id: str, flock_id: str) -> str:
        """Render adopted/active Flock learning context for Ravn/Mimir injection."""
        records = [
            record
            for record in self._store.list(flock_id=flock_id)
            if record.status in {"adopted", "active"}
            and environment_id in record.active_environment_ids
        ]
        if not records:
            return ""
        lines = ["## Adopted Flock Learnings"]
        for record in records:
            candidate = record.candidate
            lines.append(
                f"- {candidate.title}: {candidate.summary} "
                f"(flock={candidate.flock_id}, source={candidate.source_environment_id}, "
                f"confidence={candidate.confidence:.2f})"
            )
        return "\n".join(lines)

    def skill_discovery_metadata(self, *, environment_id: str, flock_id: str) -> list[dict]:
        """Return adopted tool/skill learnings as Ravn skill-discovery metadata."""
        metadata: list[dict] = []
        for record in self._store.list(flock_id=flock_id):
            candidate = record.candidate
            if candidate.artifact_type != "tool_skill":
                continue
            if record.status not in {"adopted", "active"}:
                continue
            if environment_id not in record.active_environment_ids:
                continue
            metadata.append(
                {
                    "name": candidate.title,
                    "scope": "flock",
                    "flock_id": candidate.flock_id,
                    "environment_id": environment_id,
                    "source_environment_id": candidate.source_environment_id,
                    "learning_id": candidate.learning_id,
                    "tags": list(candidate.tags),
                    "content": candidate.content,
                }
            )
        return metadata

    def ui_fixture(self, *, flock_id: str) -> dict:
        """Return mock data for Valkyrie UI Flock learning panels."""
        records = self._store.list(flock_id=flock_id)
        return {
            "flock_id": flock_id,
            "learning_count": len(records),
            "learnings": [
                {
                    "exchange_id": record.exchange_id,
                    "learning_id": record.candidate.learning_id,
                    "title": record.candidate.title,
                    "artifact_type": record.candidate.artifact_type,
                    "status": record.status,
                    "source_environment_id": record.candidate.source_environment_id,
                    "active_environment_ids": list(record.active_environment_ids),
                    "peer_decisions": [asdict(decision) for decision in record.peer_decisions],
                }
                for record in records
            ],
        }

    async def _emit(
        self,
        event_type: str,
        record: FlockLearningRecord,
        *,
        peer_environment_id: str = "",
        reason: str = "",
        rationale: str = "",
        local_override_path: str = "",
        evaluation_results: dict | None = None,
    ) -> None:
        if self._publisher is None:
            return
        event = SleipnirEvent(
            event_type=event_type,
            source=self._source,
            payload={
                "exchange_id": record.exchange_id,
                "learning_id": record.candidate.learning_id,
                "title": record.candidate.title,
                "summary": record.candidate.summary,
                "flock_id": record.candidate.flock_id,
                "artifact_type": record.candidate.artifact_type,
                "content": record.candidate.content,
                "status": record.status,
                "domain": record.candidate.metadata.get("domain", ""),
                "source_environment_id": record.candidate.source_environment_id,
                "source_valkyrie_id": record.candidate.source_valkyrie_id,
                "peer_environment_id": peer_environment_id,
                "confidence": record.candidate.confidence,
                "redaction_status": record.candidate.redaction_status,
                "promotion_id": record.candidate.promotion_id,
                "artifact_path": record.candidate.promoted_path or record.candidate.source_path,
                "tags": list(record.candidate.tags),
                "repetition": record.repetition,
                "reason": reason,
                "rationale": rationale,
                "local_override_path": local_override_path,
                "evaluation_results": evaluation_results or {},
                "nats_subject": f"ravn.environment.{event_type}",
            },
            summary=f"{event_type}: {record.candidate.title}",
            urgency=0.4 if event_type.endswith(("rejected", "rolled_back")) else 0.2,
            domain="infrastructure",
            timestamp=datetime.now(UTC),
            correlation_id=record.exchange_id,
        )
        await self._publisher.publish(event)
