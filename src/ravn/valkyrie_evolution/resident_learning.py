"""Resident Valkyrie learning subscriber, installer, and usage loop."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from ravn.skills.management import SkillManagementRegistry
from ravn.valkyrie_evolution.adapters import LocalOdinReviewAdapter
from ravn.valkyrie_evolution.models import (
    BuildResult,
    CapabilityGap,
    EvolutionRequest,
    OperationalSignal,
    ReviewResult,
)
from ravn.valkyrie_evolution.ports import EvolutionReviewPort
from sleipnir.domain import registry
from sleipnir.domain.catalog import (
    learning_adoption_recorded,
    odin_court_decided,
    valkyrie_judgment_proposed,
)
from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import SleipnirPublisher, SleipnirSubscriber, Subscription

LearningAction = Literal["adopted", "rejected", "ignored"]

_SKILL_ARTIFACT_TYPES = frozenset({"ravn_skill_tool", "tool_skill"})
_SAFE_REDACTION_STATES = frozenset({"", "none", "redacted", "safe"})
_SUBSCRIBED_EVENT_TYPES = [
    registry.LEARNING_PROMOTED,
    "flock.learning.proposed",
]


@dataclass(frozen=True)
class ResidentLearningIdentity:
    """Identity and relevance scope for one long-running resident Valkyrie."""

    environment_id: str
    valkyrie_id: str
    domain: str = ""
    flock_ids: list[str] = field(default_factory=list)
    autonomy_mode: str = "guarded"
    environment_type: str = ""


@dataclass(frozen=True)
class ResidentLearningArtifact:
    """A learning artifact a resident can evaluate and optionally install."""

    learning_id: str
    title: str
    summary: str
    content: str
    artifact_type: str
    scope: str
    confidence: float
    source_environment_id: str = ""
    source_valkyrie_id: str = ""
    promotion_id: str = ""
    flock_id: str = ""
    domain: str = ""
    redaction_status: str = ""
    artifact_path: str = ""
    causation_id: str = ""
    correlation_id: str = ""


@dataclass(frozen=True)
class ResidentLearningDecision:
    """Decision made by a resident for one incoming learning."""

    action: LearningAction
    rationale: str
    installed_skill_name: str = ""
    review: ReviewResult | None = None
    relevant: bool = False


class ResidentLearningPolicy:
    """Decide whether a learning may be considered by this resident."""

    def evaluate(
        self,
        artifact: ResidentLearningArtifact,
        identity: ResidentLearningIdentity,
    ) -> tuple[bool, str]:
        if artifact.source_valkyrie_id == identity.valkyrie_id:
            return False, "source Valkyrie already owns this learning"
        if artifact.artifact_type not in _SKILL_ARTIFACT_TYPES:
            return False, f"unsupported artifact type: {artifact.artifact_type or 'unknown'}"
        if not artifact.content.strip():
            return False, "artifact content unavailable for local install"
        if artifact.redaction_status.lower() not in _SAFE_REDACTION_STATES:
            return False, "artifact has not been redacted for peer adoption"

        scope = _normalise_scope(artifact.scope)
        if scope == "private":
            return False, "private learning does not travel to peer residents"
        if scope == "environment" and artifact.source_environment_id != identity.environment_id:
            return False, "environment-scoped learning belongs to another environment"
        if scope == "domain" and not _domain_matches(artifact.domain, identity.domain):
            return False, "domain does not match resident domain"
        if scope == "flock" and not _flock_matches(artifact.flock_id, identity.flock_ids):
            return False, "resident is not a member of the target flock"
        if scope == "shared" and artifact.domain and not _domain_matches(
            artifact.domain, identity.domain
        ):
            return False, "shared learning is for a different domain"
        return True, f"learning is relevant to {identity.environment_id}"


class ResidentLearningRuntime:
    """Subscribe to shared learnings and install relevant skills into a resident."""

    def __init__(
        self,
        *,
        identity: ResidentLearningIdentity,
        skills: SkillManagementRegistry,
        publisher: SleipnirPublisher,
        subscriber: SleipnirSubscriber,
        reviewer: EvolutionReviewPort | None = None,
        policy: ResidentLearningPolicy | None = None,
        source: str = "",
    ) -> None:
        self.identity = identity
        self._skills = skills
        self._publisher = publisher
        self._subscriber = subscriber
        self._reviewer = reviewer or LocalOdinReviewAdapter(reviewer="odin:resident-learning")
        self._policy = policy or ResidentLearningPolicy()
        self._source = source or identity.valkyrie_id
        self._subscription: Subscription | None = None
        self._decisions: list[ResidentLearningDecision] = []

    @property
    def is_running(self) -> bool:
        return self._subscription is not None

    def decisions(self) -> list[ResidentLearningDecision]:
        return list(self._decisions)

    async def start(self) -> None:
        if self._subscription is not None:
            return
        self._subscription = await self._subscriber.subscribe(
            _SUBSCRIBED_EVENT_TYPES,
            self._handle_learning_event,
        )

    async def stop(self) -> None:
        if self._subscription is None:
            return
        await self._subscription.unsubscribe()
        self._subscription = None

    async def process_signal(self, signal: OperationalSignal | SleipnirEvent) -> dict[str, Any]:
        """Use an installed adopted skill when a later signal matches its capability."""

        operational_signal = _to_operational_signal(signal, self.identity)
        capability = _derive_capability_name(operational_signal)
        skill = await self._find_installed_skill_by_capability(capability)
        if skill is None:
            return {
                "signalId": operational_signal.signal_id,
                "capabilityName": capability,
                "decision": "defer_and_request_capability",
                "usedAdoptedLearning": False,
                "skillName": "",
            }

        await self._skills.record_usage(
            skill.name,
            success=True,
            environment_id=self.identity.environment_id,
            domain=operational_signal.domain,
            action_safety_class=_safety_class_from_content(skill.content),
        )
        judgment = valkyrie_judgment_proposed(
            environment_id=self.identity.environment_id,
            valkyrie_id=self.identity.valkyrie_id,
            attention_tier="ambient",
            recommended_action="inspect_with_adopted_learning",
            authority_boundary=_authority_boundary(self.identity.autonomy_mode),
            confidence=0.86,
            operational_state="using_adopted_learning",
            rationale=(
                f"Installed learning skill {skill.name} matches capability {capability}."
            ),
            signal_refs=[operational_signal.signal_id],
            evidence=[
                {
                    "skill_name": skill.name,
                    "capability_name": capability,
                    "learning_source": "resident_skill_registry",
                }
            ],
            correlation_ids={"root": operational_signal.signal_id},
            source=self._source,
            correlation_id=operational_signal.signal_id,
        )
        await self._publisher.publish(judgment)
        return {
            "signalId": operational_signal.signal_id,
            "capabilityName": capability,
            "decision": "inspect_with_adopted_learning",
            "usedAdoptedLearning": True,
            "skillName": skill.name,
            "judgmentEventId": judgment.event_id,
        }

    async def _handle_learning_event(self, event: SleipnirEvent) -> None:
        artifact = _artifact_from_event(event)
        decision = await self.evaluate_and_apply(artifact)
        self._decisions.append(decision)

    async def evaluate_and_apply(
        self,
        artifact: ResidentLearningArtifact,
    ) -> ResidentLearningDecision:
        relevant, reason = self._policy.evaluate(artifact, self.identity)
        if not relevant:
            decision = ResidentLearningDecision("rejected", reason, relevant=False)
            await self._publish_adoption(artifact, decision)
            return decision

        request, build = _review_inputs(artifact, self.identity)
        review = await self._reviewer.review(
            request=request,
            build=build,
            autonomy_mode=self.identity.autonomy_mode,
        )
        await self._publish_odin_decision(artifact, review)

        if not _review_allows_install(review, self.identity.autonomy_mode):
            decision = ResidentLearningDecision(
                "rejected",
                f"Odin review blocked install: {review.rationale}",
                review=review,
                relevant=True,
            )
            await self._publish_adoption(artifact, decision)
            return decision

        skill_name = await self._install_skill(artifact, build)
        decision = ResidentLearningDecision(
            "adopted",
            f"Installed {skill_name}: {review.rationale}",
            installed_skill_name=skill_name,
            review=review,
            relevant=True,
        )
        await self._publish_activation(artifact, skill_name, review)
        await self._publish_adoption(artifact, decision)
        return decision

    async def _install_skill(
        self,
        artifact: ResidentLearningArtifact,
        build: BuildResult,
    ) -> str:
        scope = _normalise_scope(artifact.scope)
        try:
            await self._skills.create(
                name=build.skill_name,
                content=build.skill_content,
                description=build.description,
                scope=scope,
                environment_id=self.identity.environment_id,
                domain=self.identity.domain or artifact.domain,
                source=f"flock-learning:{artifact.learning_id}",
                action_safety_class=_safety_class_from_content(build.skill_content),
            )
        except ValueError:
            await self._skills.update(
                name=build.skill_name,
                content=build.skill_content,
                description=build.description,
            )
            await self._skills.promote(
                build.skill_name,
                scope=scope,
                environment_id=self.identity.environment_id,
                domain=self.identity.domain or artifact.domain,
            )
        return build.skill_name

    async def _find_installed_skill_by_capability(self, capability: str) -> Any | None:
        rows = await self._skills.list_skills()
        marker = f"capability: {capability}"
        for row in rows:
            skill = row["skill"]
            if marker in str(skill.get("content", "")):
                return type("RunnableSkill", (), skill)()
        return None

    async def _publish_odin_decision(
        self,
        artifact: ResidentLearningArtifact,
        review: ReviewResult,
    ) -> None:
        decision = "learning_adoption_allowed" if _review_allows_install(
            review, self.identity.autonomy_mode
        ) else "learning_adoption_blocked"
        event = odin_court_decided(
            environment_id=self.identity.environment_id,
            court_id=f"odin-learning:{artifact.learning_id}:{self.identity.environment_id}",
            decision=decision,
            authority_boundary=_authority_boundary(self.identity.autonomy_mode),
            dissent=[
                {
                    "reviewer": review.reviewer,
                    "outcome": review.outcome,
                    "approved": review.approved,
                    "rationale": review.rationale,
                    "findings": list(review.findings),
                }
            ],
            source="odin:resident-learning",
            correlation_id=artifact.correlation_id or artifact.learning_id,
            causation_id=artifact.causation_id,
        )
        event.payload["learning_id"] = artifact.learning_id
        event.payload["artifact_name"] = artifact.title
        await self._publisher.publish(event)

    async def _publish_activation(
        self,
        artifact: ResidentLearningArtifact,
        skill_name: str,
        review: ReviewResult,
    ) -> None:
        await self._publisher.publish(
            SleipnirEvent(
                event_type="valkyrie.evolution.activated",
                source=self._source,
                payload={
                    "environment_id": self.identity.environment_id,
                    "valkyrie_id": self.identity.valkyrie_id,
                    "learning_id": artifact.learning_id,
                    "promotion_id": artifact.promotion_id,
                    "skill_name": skill_name,
                    "artifact_type": artifact.artifact_type,
                    "scope": _normalise_scope(artifact.scope),
                    "source_environment_id": artifact.source_environment_id,
                    "source_valkyrie_id": artifact.source_valkyrie_id,
                    "review_outcome": review.outcome,
                    "autonomy_mode": self.identity.autonomy_mode,
                },
                summary=f"{self.identity.valkyrie_id} installed learning skill {skill_name}",
                urgency=0.25,
                domain="infrastructure",
                timestamp=datetime.now(UTC),
                correlation_id=artifact.correlation_id or artifact.learning_id,
                causation_id=artifact.causation_id,
            )
        )

    async def _publish_adoption(
        self,
        artifact: ResidentLearningArtifact,
        decision: ResidentLearningDecision,
    ) -> None:
        event = learning_adoption_recorded(
            environment_id=self.identity.environment_id,
            learning_id=artifact.learning_id,
            promotion_id=artifact.promotion_id or artifact.learning_id,
            action=decision.action if decision.action != "ignored" else "rejected",
            rationale=decision.rationale,
            canary_passed=decision.action == "adopted",
            local_override_path="",
            source=self._source,
            correlation_id=artifact.correlation_id or artifact.learning_id,
            causation_id=artifact.causation_id,
        )
        event.payload.update(
            {
                "resident_valkyrie_id": self.identity.valkyrie_id,
                "installed_skill_name": decision.installed_skill_name,
                "relevant": decision.relevant,
                "artifact_type": artifact.artifact_type,
                "scope": _normalise_scope(artifact.scope),
            }
        )
        await self._publisher.publish(event)


def _artifact_from_event(event: SleipnirEvent) -> ResidentLearningArtifact:
    payload = event.payload
    event_type = event.event_type
    if event_type == "flock.learning.proposed":
        title = str(payload.get("title") or payload.get("artifact_name") or payload["learning_id"])
        scope = "flock"
    else:
        title = str(
            payload.get("artifact_name")
            or payload.get("promoted_tool")
            or payload.get("title")
            or payload["learning_id"]
        )
        scope = str(payload.get("to_scope") or payload.get("target_scope") or payload.get("scope"))
    return ResidentLearningArtifact(
        learning_id=str(payload.get("learning_id") or ""),
        title=title,
        summary=str(payload.get("summary") or event.summary or title),
        content=str(payload.get("artifact_content") or payload.get("content") or ""),
        artifact_type=str(payload.get("artifact_type") or ""),
        scope=scope,
        confidence=float(payload.get("confidence") or 0.0),
        source_environment_id=str(
            payload.get("source_environment_id") or payload.get("environment_id") or ""
        ),
        source_valkyrie_id=str(payload.get("source_valkyrie_id") or ""),
        promotion_id=str(payload.get("promotion_id") or payload.get("learning_id") or ""),
        flock_id=_normalise_flock_id(str(payload.get("flock_id") or "")),
        domain=str(payload.get("domain") or payload.get("domain_scope") or event.domain or ""),
        redaction_status=str(payload.get("redaction_status") or ""),
        artifact_path=str(payload.get("artifact_path") or payload.get("promoted_path") or ""),
        causation_id=event.event_id,
        correlation_id=event.correlation_id or event.event_id,
    )


def _review_inputs(
    artifact: ResidentLearningArtifact,
    identity: ResidentLearningIdentity,
) -> tuple[EvolutionRequest, BuildResult]:
    capability = _capability_from_content(artifact.content) or _slug(artifact.title)
    request = EvolutionRequest(
        request_id=f"resident-adopt:{artifact.learning_id}:{identity.environment_id}",
        gap=CapabilityGap(
            gap_id=f"peer-learning:{artifact.learning_id}",
            capability_name=capability,
            environment_id=identity.environment_id,
            domain=artifact.domain or identity.domain,
            reason=f"Peer learning proposed by {artifact.source_valkyrie_id or 'unknown'}",
            signal_ids=[],
            evidence={
                "summary": artifact.summary,
                "source_environment_id": artifact.source_environment_id,
                "source_valkyrie_id": artifact.source_valkyrie_id,
            },
            safety_class=_safety_class_from_content(artifact.content),
        ),
        autonomy_mode=identity.autonomy_mode,
        target_scope=_normalise_scope(artifact.scope),
    )
    artifact_type = (
        "ravn_skill_tool" if artifact.artifact_type == "tool_skill" else artifact.artifact_type
    )
    build = BuildResult(
        request_id=request.request_id,
        skill_name=artifact.title,
        skill_content=artifact.content,
        description=artifact.summary,
        artifact_type=artifact_type,
        artifact_path=artifact.artifact_path,
        evidence={
            "learning_id": artifact.learning_id,
            "promotion_id": artifact.promotion_id,
            "source_environment_id": artifact.source_environment_id,
            "source_valkyrie_id": artifact.source_valkyrie_id,
        },
    )
    return request, build


def _review_allows_install(review: ReviewResult, autonomy_mode: str) -> bool:
    if review.approved:
        return True
    if autonomy_mode.lower() != "yolo":
        return False
    blocked_findings = [
        finding
        for finding in review.findings
        if finding.startswith("non-read-only")
        or finding == "missing capability marker"
        or finding.startswith("blocked operation")
    ]
    return not blocked_findings


def _to_operational_signal(
    signal: OperationalSignal | SleipnirEvent,
    identity: ResidentLearningIdentity,
) -> OperationalSignal:
    if isinstance(signal, OperationalSignal):
        return signal
    payload = signal.payload
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    data = data if isinstance(data, dict) else {}
    return OperationalSignal(
        signal_id=str(data.get("signal_id") or data.get("signalId") or signal.event_id),
        event_type=signal.event_type,
        environment_id=str(payload.get("environment_id") or identity.environment_id),
        domain=str(payload.get("domain_scope") or signal.domain or identity.domain),
        severity=str(payload.get("severity") or "medium"),
        summary=signal.summary,
        payload=data,
    )


def _derive_capability_name(signal: OperationalSignal) -> str:
    namespace = signal.event_type.removeprefix("signal.").removesuffix(".event")
    reason = str(signal.payload.get("reason") or signal.payload.get("kind") or "unknown")
    kind = str(signal.payload.get("kind") or signal.payload.get("signal_kind") or namespace)
    return f"inspect.{_slug(namespace)}.{_slug(kind)}.{_slug(reason)}"


def _capability_from_content(content: str) -> str:
    match = re.search(r"^metadata:\n(?:.*\n)*?\s*capability:\s*([^\n]+)", content, re.M)
    return match.group(1).strip() if match else ""


def _safety_class_from_content(content: str) -> str:
    match = re.search(r"^metadata:\n(?:.*\n)*?\s*safety_class:\s*([^\n]+)", content, re.M)
    return match.group(1).strip() if match else "read_only"


def _authority_boundary(autonomy_mode: str) -> str:
    return "yolo" if autonomy_mode.lower() == "yolo" else "human_review_required"


def _normalise_scope(scope: str) -> str:
    value = (scope or "private").lower()
    return value if value in {"private", "environment", "domain", "flock", "shared"} else "private"


def _normalise_flock_id(flock_id: str) -> str:
    value = flock_id.strip()
    if not value:
        return ""
    return value if value.startswith("flock:") else f"flock:{value}"


def _flock_matches(candidate_flock_id: str, resident_flock_ids: list[str]) -> bool:
    candidate = _normalise_flock_id(candidate_flock_id)
    if not candidate:
        return False
    resident = {_normalise_flock_id(item) for item in resident_flock_ids}
    return candidate in resident


def _domain_matches(candidate_domain: str, resident_domain: str) -> bool:
    return not candidate_domain or not resident_domain or candidate_domain == resident_domain


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "unknown"
