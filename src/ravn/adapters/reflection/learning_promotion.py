"""Promotion, adoption, and rollback records for resident Valkyrie learnings."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from niuu.ports.mimir import MimirPort
from ravn.context.autonomy import AutonomyMode
from sleipnir.domain.catalog import learning_adoption_recorded, learning_promoted

if TYPE_CHECKING:
    from sleipnir.ports.events import SleipnirPublisher


_TARGET_THRESHOLDS: dict[str, tuple[float, int]] = {
    "environment": (0.55, 2),
    "domain": (0.70, 3),
    "flock": (0.75, 3),
    "shared": (0.85, 4),
}
_TARGET_MOUNTS: dict[str, str] = {
    "private": "local",
    "environment": "local",
    "domain": "domain",
    "flock": "shared",
    "shared": "shared",
}
_ADOPTION_ACTIONS = frozenset({"canary", "adopted", "rejected", "overridden", "regressed"})


@dataclass(frozen=True)
class LearningPromotionCandidate:
    """A learning eligible for promotion out of private scope."""

    learning_id: str
    source_path: str
    title: str
    summary: str
    content: str
    current_scope: str = "private"
    target_scope: str = "environment"
    environment_id: str = ""
    source_valkyrie_id: str = ""
    domain: str = ""
    flock_id: str = ""
    source_episode_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    repetition_count: int = 1
    successful_reuse_count: int = 0
    feedback_score: float = 0.0
    redaction_status: str = "unredacted"
    autonomy_mode: str = AutonomyMode.GUARDED.value
    reviewer: str = ""
    promotion_mode: str = "policy"
    rollout: str = "candidate"


@dataclass
class LearningAdoptionRecord:
    """Peer Environment adoption/canary/negative-transfer record."""

    peer_environment_id: str
    action: str
    rationale: str = ""
    canary_passed: bool = False
    local_override_path: str = ""
    recorded_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class LearningPromotionRecord:
    """Durable audit record for a promoted or deferred learning."""

    promotion_id: str
    learning_id: str
    source_path: str
    promoted_path: str
    from_scope: str
    to_scope: str
    target_mount: str
    status: str
    policy_decision: str
    policy_reason: str
    environment_id: str = ""
    source_valkyrie_id: str = ""
    domain: str = ""
    flock_id: str = ""
    source_episode_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    repetition_count: int = 0
    successful_reuse_count: int = 0
    feedback_score: float = 0.0
    redaction_status: str = "unredacted"
    promotion_mode: str = "policy"
    reviewer: str = ""
    rollout: str = "candidate"
    rollback_metadata: dict = field(default_factory=dict)
    adoptions: list[LearningAdoptionRecord] = field(default_factory=list)
    negative_transfer: list[dict] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    promoted_at: str = ""
    demoted_at: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> LearningPromotionRecord:
        adoptions = [
            LearningAdoptionRecord(**item)
            for item in data.get("adoptions", [])
            if isinstance(item, dict)
        ]
        return cls(
            promotion_id=str(data.get("promotion_id") or uuid4()),
            learning_id=str(data.get("learning_id") or ""),
            source_path=str(data.get("source_path") or ""),
            promoted_path=str(data.get("promoted_path") or ""),
            from_scope=str(data.get("from_scope") or "private"),
            to_scope=str(data.get("to_scope") or "environment"),
            target_mount=str(data.get("target_mount") or "local"),
            status=str(data.get("status") or "candidate"),
            policy_decision=str(data.get("policy_decision") or ""),
            policy_reason=str(data.get("policy_reason") or ""),
            environment_id=str(data.get("environment_id") or ""),
            source_valkyrie_id=str(data.get("source_valkyrie_id") or ""),
            domain=str(data.get("domain") or ""),
            flock_id=str(data.get("flock_id") or ""),
            source_episode_ids=list(data.get("source_episode_ids") or []),
            confidence=float(data.get("confidence") or 0.0),
            repetition_count=int(data.get("repetition_count") or 0),
            successful_reuse_count=int(data.get("successful_reuse_count") or 0),
            feedback_score=float(data.get("feedback_score") or 0.0),
            redaction_status=str(data.get("redaction_status") or "unredacted"),
            promotion_mode=str(data.get("promotion_mode") or "policy"),
            reviewer=str(data.get("reviewer") or ""),
            rollout=str(data.get("rollout") or "candidate"),
            rollback_metadata=dict(data.get("rollback_metadata") or {}),
            adoptions=adoptions,
            negative_transfer=list(data.get("negative_transfer") or []),
            created_at=str(data.get("created_at") or datetime.now(UTC).isoformat()),
            updated_at=str(data.get("updated_at") or datetime.now(UTC).isoformat()),
            promoted_at=str(data.get("promoted_at") or ""),
            demoted_at=str(data.get("demoted_at") or ""),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class LearningPromotionDecision:
    decision: str
    reason: str


class LearningPromotionPolicy:
    """Decide whether a private learning may promote to a wider scope."""

    def decide(self, candidate: LearningPromotionCandidate) -> LearningPromotionDecision:
        target = _normalise_scope(candidate.target_scope)
        mode = _normalise_mode(candidate.autonomy_mode)

        if target == "private":
            return LearningPromotionDecision("deny", "private learnings are already local")
        if candidate.feedback_score < 0:
            return LearningPromotionDecision("deny", "negative feedback blocks promotion")
        if candidate.redaction_status not in {"redacted", "safe", "none"}:
            return LearningPromotionDecision(
                "needs_review",
                "learning must be redacted before sharing",
            )

        threshold, repetitions = _TARGET_THRESHOLDS[target]
        if candidate.confidence < threshold:
            return LearningPromotionDecision(
                "needs_review",
                f"confidence {candidate.confidence:.2f} is below {threshold:.2f}",
            )
        if candidate.repetition_count < repetitions:
            return LearningPromotionDecision(
                "needs_review",
                f"needs {repetitions} observations for {target} promotion",
            )
        if candidate.successful_reuse_count < 1:
            return LearningPromotionDecision("needs_review", "requires successful reuse evidence")

        if mode == AutonomyMode.GUARDED.value:
            return LearningPromotionDecision(
                "needs_review",
                "guarded mode records review candidates",
            )
        if mode == AutonomyMode.AUTONOMOUS.value and target != "environment":
            return LearningPromotionDecision(
                "needs_review",
                f"autonomous mode only auto-promotes Environment learnings, not {target}",
            )
        if mode == AutonomyMode.YOLO.value and target == "shared":
            return LearningPromotionDecision(
                "needs_review",
                "shared ODIN/Mimir promotion requires curation even in YOLO mode",
            )

        return LearningPromotionDecision("allow", f"{mode} policy allows {target} promotion")


class LearningPromotionStore:
    """JSON store for promotion, adoption, demotion, and negative-transfer records."""

    def __init__(self, path: str | Path = "~/.ravn/learning_promotions.json") -> None:
        self._path = Path(path).expanduser()
        self._records: dict[str, LearningPromotionRecord] = {}
        self._load()

    def save(self, record: LearningPromotionRecord) -> LearningPromotionRecord:
        record.updated_at = datetime.now(UTC).isoformat()
        self._records[record.promotion_id] = record
        self._save()
        return record

    def get(self, promotion_id: str) -> LearningPromotionRecord:
        try:
            return self._records[promotion_id]
        except KeyError as exc:
            raise ValueError(f"unknown learning promotion: {promotion_id}") from exc

    def list(
        self,
        *,
        status: str | None = None,
        environment_id: str | None = None,
        target_scope: str | None = None,
    ) -> list[LearningPromotionRecord]:
        records = list(self._records.values())
        if status:
            records = [r for r in records if r.status == status]
        if environment_id:
            records = [r for r in records if r.environment_id == environment_id]
        if target_scope:
            records = [r for r in records if r.to_scope == target_scope]
        return sorted(records, key=lambda r: r.created_at)

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._records = {}
            return
        rows = raw.get("promotions", raw if isinstance(raw, list) else [])
        self._records = {
            record.promotion_id: record
            for record in (LearningPromotionRecord.from_dict(row) for row in rows)
        }

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"promotions": [record.to_dict() for record in self.list()]}
        self._path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


class LearningPromotionService:
    """Promote learnings through existing Mimir mounts and Sleipnir learning events."""

    def __init__(
        self,
        *,
        mimir: MimirPort,
        store: LearningPromotionStore,
        policy: LearningPromotionPolicy | None = None,
        publisher: SleipnirPublisher | None = None,
        source: str = "ravn:learning-promotion",
        target_mounts: dict[str, str] | None = None,
    ) -> None:
        self._mimir = mimir
        self._store = store
        self._policy = policy or LearningPromotionPolicy()
        self._publisher = publisher
        self._source = source
        self._target_mounts = {**_TARGET_MOUNTS, **(target_mounts or {})}

    async def promote(self, candidate: LearningPromotionCandidate) -> LearningPromotionRecord:
        decision = self._policy.decide(candidate)
        record = self._record_for(candidate, decision)
        if decision.decision != "allow":
            record.status = "needs_review" if decision.decision == "needs_review" else "blocked"
            return self._store.save(record)

        promoted_content = _render_promoted_learning(candidate, record)
        await self._mimir.upsert_page(
            record.promoted_path,
            promoted_content,
            mimir=record.target_mount,
        )
        now = datetime.now(UTC).isoformat()
        record.status = "promoted"
        record.promoted_at = now
        record.rollback_metadata = {
            "source_path": candidate.source_path,
            "promoted_path": record.promoted_path,
            "target_mount": record.target_mount,
        }
        saved = self._store.save(record)
        if self._publisher is not None:
            await self._publisher.publish(
                learning_promoted(
                    environment_id=candidate.environment_id,
                    learning_id=candidate.learning_id,
                    from_scope=candidate.current_scope,
                    to_scope=candidate.target_scope,
                    summary=candidate.summary,
                    confidence=candidate.confidence,
                    source=self._source,
                    correlation_id=candidate.learning_id,
                )
            )
        return saved

    async def record_adoption(
        self,
        promotion_id: str,
        *,
        peer_environment_id: str,
        action: str,
        rationale: str = "",
        canary_passed: bool = False,
        local_override_path: str = "",
    ) -> LearningPromotionRecord:
        if action not in _ADOPTION_ACTIONS:
            raise ValueError(f"unknown learning adoption action: {action}")
        record = self._store.get(promotion_id)
        adoption = LearningAdoptionRecord(
            peer_environment_id=peer_environment_id,
            action=action,
            rationale=rationale,
            canary_passed=canary_passed,
            local_override_path=local_override_path,
        )
        record.adoptions.append(adoption)
        if action in {"rejected", "overridden", "regressed"}:
            record.negative_transfer.append(asdict(adoption))
        saved = self._store.save(record)
        if self._publisher is not None:
            await self._publisher.publish(
                learning_adoption_recorded(
                    environment_id=peer_environment_id,
                    learning_id=record.learning_id,
                    promotion_id=record.promotion_id,
                    action=action,
                    rationale=rationale,
                    canary_passed=canary_passed,
                    local_override_path=local_override_path,
                    source=self._source,
                    correlation_id=record.promotion_id,
                )
            )
        return saved

    def demote(self, promotion_id: str, *, reason: str) -> LearningPromotionRecord:
        record = self._store.get(promotion_id)
        record.status = "demoted"
        record.demoted_at = datetime.now(UTC).isoformat()
        record.negative_transfer.append(
            {
                "reason": reason,
                "recorded_at": record.demoted_at,
            }
        )
        return self._store.save(record)

    def _record_for(
        self,
        candidate: LearningPromotionCandidate,
        decision: LearningPromotionDecision,
    ) -> LearningPromotionRecord:
        target_scope = _normalise_scope(candidate.target_scope)
        flock_id = _normalise_flock_id(candidate.flock_id, candidate.domain)
        promoted_path = _promoted_path(candidate, target_scope, flock_id)
        target_mount = self._target_mounts[target_scope]
        if target_scope == "domain" and target_mount == "domain" and not candidate.domain:
            target_mount = "shared"
        return LearningPromotionRecord(
            promotion_id=str(uuid4()),
            learning_id=candidate.learning_id,
            source_path=candidate.source_path,
            promoted_path=promoted_path,
            from_scope=candidate.current_scope,
            to_scope=target_scope,
            target_mount=target_mount,
            status="candidate",
            policy_decision=decision.decision,
            policy_reason=decision.reason,
            environment_id=candidate.environment_id,
            source_valkyrie_id=candidate.source_valkyrie_id,
            domain=candidate.domain,
            flock_id=flock_id,
            source_episode_ids=list(candidate.source_episode_ids),
            confidence=candidate.confidence,
            repetition_count=candidate.repetition_count,
            successful_reuse_count=candidate.successful_reuse_count,
            feedback_score=candidate.feedback_score,
            redaction_status=candidate.redaction_status,
            promotion_mode=candidate.promotion_mode,
            reviewer=candidate.reviewer,
            rollout=candidate.rollout,
        )


def _render_promoted_learning(
    candidate: LearningPromotionCandidate,
    record: LearningPromotionRecord,
) -> str:
    episodes = ", ".join(candidate.source_episode_ids)
    frontmatter = [
        "---",
        f'title: "Learning: {candidate.title}"',
        "category: learnings",
        f"scope: {record.to_scope}",
        f"source_scope: {record.from_scope}",
        f"environment_id: {candidate.environment_id}",
        f"source_valkyrie_id: {candidate.source_valkyrie_id}",
        f"domain: {candidate.domain}",
        f"flock_id: {record.flock_id}",
        f"confidence: {candidate.confidence:.2f}",
        f"redaction_status: {candidate.redaction_status}",
        f"promotion_id: {record.promotion_id}",
        f"source_episodes: [{episodes}]",
        "---",
        "",
    ]
    body = [
        f"# Learning: {candidate.title}",
        "",
        "## Summary",
        candidate.summary,
        "",
        "## Promoted Knowledge",
        candidate.content.strip(),
        "",
        "## Provenance",
        f"- Source path: `{candidate.source_path}`",
        f"- Source Environment: `{candidate.environment_id}`",
        f"- Source Valkyrie: `{candidate.source_valkyrie_id}`",
        f"- Repetitions: {candidate.repetition_count}",
        f"- Successful reuse count: {candidate.successful_reuse_count}",
        f"- Feedback score: {candidate.feedback_score:.2f}",
    ]
    return "\n".join(frontmatter + body).rstrip() + "\n"


def _promoted_path(
    candidate: LearningPromotionCandidate,
    target_scope: str,
    flock_id: str,
) -> str:
    slug = _slugify(candidate.title or candidate.learning_id)
    if target_scope == "environment":
        return f"learnings/environment/{_slugify(candidate.environment_id or 'default')}/{slug}.md"
    if target_scope == "domain":
        return f"learnings/domain/{_slugify(candidate.domain or 'general')}/{slug}.md"
    if target_scope == "flock":
        return f"learnings/flock/{_slugify(flock_id)}/{slug}.md"
    if target_scope == "shared":
        return f"learnings/shared/{slug}.md"
    return f"learnings/private/{slug}.md"


def _normalise_scope(scope: str) -> str:
    value = (scope or "private").lower()
    if value.startswith("flock:"):
        return "flock"
    if value in _TARGET_MOUNTS:
        return value
    return "private"


def _normalise_flock_id(flock_id: str, domain: str) -> str:
    if flock_id:
        return flock_id if flock_id.startswith("flock:") else f"flock:{flock_id}"
    if domain:
        return f"flock:{domain}"
    return "flock:general"


def _normalise_mode(mode: str) -> str:
    try:
        return AutonomyMode(mode).value
    except ValueError:
        return AutonomyMode.GUARDED.value


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:80] or "learning"
