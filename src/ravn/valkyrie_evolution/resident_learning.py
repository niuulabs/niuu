"""Resident Valkyrie learning subscriber, installer, and usage loop."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from ravn.skills.management import SkillManagementRegistry
from ravn.valkyrie_evolution.adapters import LocalOdinReviewAdapter, LocalSkillBuilderAdapter
from ravn.valkyrie_evolution.models import (
    BuildResult,
    CapabilityGap,
    EvolutionRequest,
    OperationalSignal,
    ReviewResult,
)
from ravn.valkyrie_evolution.ports import EvolutionBuilderPort, EvolutionReviewPort
from sleipnir.domain import registry
from sleipnir.domain.catalog import (
    learning_adoption_recorded,
    odin_court_decided,
    valkyrie_judgment_proposed,
)
from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import SleipnirPublisher, SleipnirSubscriber, Subscription

LearningAction = Literal["adopted", "rejected", "rolled_back", "ignored"]

_SKILL_ARTIFACT_TYPES = frozenset({"ravn_skill_tool", "tool_skill"})
_SAFE_REDACTION_STATES = frozenset({"", "none", "redacted", "safe"})
_SUBSCRIBED_EVENT_TYPES = [
    registry.LEARNING_PROMOTED,
    registry.LEARNING_ADOPTION_RECORDED,
    "flock.learning.proposed",
    "flock.learning.adopted",
    "flock.learning.rejected",
    "flock.learning.rolled_back",
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
    operator_command: bool = False
    command_action: str = ""


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
        if artifact.source_valkyrie_id == identity.valkyrie_id and not artifact.operator_command:
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
        builder: EvolutionBuilderPort | None = None,
        reviewer: EvolutionReviewPort | None = None,
        policy: ResidentLearningPolicy | None = None,
        source: str = "",
    ) -> None:
        self.identity = identity
        self._skills = skills
        self._publisher = publisher
        self._subscriber = subscriber
        self._builder = builder or LocalSkillBuilderAdapter()
        self._reviewer = reviewer or LocalOdinReviewAdapter(reviewer="odin:resident-learning")
        self._policy = policy or ResidentLearningPolicy()
        self._source = source or identity.valkyrie_id
        self._subscription: Subscription | None = None
        self._decisions: list[ResidentLearningDecision] = []
        self._local_evolved_capabilities: set[str] = set()

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
            evolved = await self._evolve_missing_capability(operational_signal, capability)
            if evolved is not None:
                return evolved
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

    async def _evolve_missing_capability(
        self,
        signal: OperationalSignal,
        capability: str,
    ) -> dict[str, Any] | None:
        """Build a resident-local skill for a repeated capability gap."""

        if capability in self._local_evolved_capabilities:
            return None
        if self.identity.autonomy_mode.lower() not in {"autonomous", "yolo"}:
            await self._publish_capability_gap(signal, capability)
            self._local_evolved_capabilities.add(capability)
            return None

        self._local_evolved_capabilities.add(capability)
        dream_id = f"dream:{self.identity.environment_id}:{_slug(capability)}:{signal.signal_id}"
        gap = CapabilityGap(
            gap_id=f"gap:{self.identity.environment_id}:{_slug(capability)}",
            capability_name=capability,
            environment_id=self.identity.environment_id,
            domain=signal.domain or self.identity.domain,
            reason=f"Resident saw signal without an installed skill: {signal.summary}",
            signal_ids=[signal.signal_id],
            evidence={
                "signal_id": signal.signal_id,
                "event_type": signal.event_type,
                "severity": signal.severity,
                "summary": signal.summary,
                "payload": signal.payload,
            },
            safety_class="read_only",
        )
        request = EvolutionRequest(
            request_id=f"resident-evolve:{gap.gap_id}",
            gap=gap,
            autonomy_mode=self.identity.autonomy_mode,
            target_scope="environment",
        )
        await self._publish_dream_event(
            "valkyrie.dream.started",
            dream_id,
            {
                "capability_name": capability,
                "signal_id": signal.signal_id,
                "autonomy_mode": self.identity.autonomy_mode,
            },
            summary=f"{self.identity.valkyrie_id} started dream for {capability}",
            urgency=0.25,
            correlation_id=signal.signal_id,
        )
        await self._publish_evolution_event(
            "valkyrie.evolution.requested",
            f"Requested resident evolution for {capability}",
            asdict(request),
            signal.signal_id,
        )
        build = await self._builder.build(request)
        await self._publish_evolution_event(
            "valkyrie.evolution.built",
            f"Built resident skill {build.skill_name}",
            asdict(build),
            signal.signal_id,
        )
        review = await self._reviewer.review(
            request=request,
            build=build,
            autonomy_mode=self.identity.autonomy_mode,
        )
        artifact = ResidentLearningArtifact(
            learning_id=f"resident:{self.identity.environment_id}:{_slug(capability)}",
            title=build.skill_name,
            summary=build.description,
            content=build.skill_content,
            artifact_type=build.artifact_type,
            scope="environment",
            confidence=0.74,
            source_environment_id=self.identity.environment_id,
            source_valkyrie_id=self.identity.valkyrie_id,
            promotion_id=f"resident:{self.identity.environment_id}:{_slug(capability)}",
            flock_id=_normalise_flock_id(self.identity.flock_ids[0])
            if self.identity.flock_ids
            else "",
            domain=gap.domain,
            redaction_status="redacted",
            artifact_path=build.artifact_path,
            causation_id="",
            correlation_id=signal.signal_id,
            operator_command=False,
            command_action="resident_evolve",
        )
        await self._publish_odin_decision(artifact, review)

        installed_skill_name = ""
        if _review_allows_install(review, self.identity.autonomy_mode):
            installed_skill_name = await self._install_skill(artifact, build)
            await self._publish_activation(artifact, installed_skill_name, review)
            await self._publish_flock_learning_proposal(artifact, build, review)
        else:
            await self._publish_evolution_event(
                "valkyrie.evolution.held",
                f"Held resident skill {build.skill_name}",
                {
                    "skill_name": build.skill_name,
                    "review_outcome": review.outcome,
                    "findings": list(review.findings),
                },
                signal.signal_id,
                urgency=0.6,
            )

        await self._publish_dream_event(
            "valkyrie.dream.completed",
            dream_id,
            {
                "capability_name": capability,
                "signal_id": signal.signal_id,
                "skills_built": [build.skill_name],
                "skills_activated": [installed_skill_name] if installed_skill_name else [],
                "odin_review_outcome": review.outcome,
                "flock_proposed": bool(installed_skill_name and artifact.flock_id),
            },
            summary=f"{self.identity.valkyrie_id} completed dream for {capability}",
            urgency=0.25,
            correlation_id=signal.signal_id,
        )
        return {
            "signalId": signal.signal_id,
            "capabilityName": capability,
            "decision": "built_capability_for_next_signal"
            if installed_skill_name
            else "capability_build_held",
            "usedAdoptedLearning": False,
            "skillName": installed_skill_name,
            "dreamId": dream_id,
        }

    async def _publish_capability_gap(
        self,
        signal: OperationalSignal,
        capability: str,
    ) -> None:
        await self._publish_evolution_event(
            "valkyrie.evolution.requested",
            f"Capability gap observed for {capability}",
            {
                "environment_id": self.identity.environment_id,
                "valkyrie_id": self.identity.valkyrie_id,
                "capability_name": capability,
                "signal_id": signal.signal_id,
                "autonomy_mode": self.identity.autonomy_mode,
                "status": "deferred",
            },
            signal.signal_id,
            urgency=0.45,
        )

    async def _handle_learning_event(self, event: SleipnirEvent) -> None:
        if _is_resident_ack(event):
            return
        artifact = _artifact_from_event(event)
        if _is_retraction_event(event):
            decision = await self.retract(artifact)
        else:
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

    async def retract(
        self,
        artifact: ResidentLearningArtifact,
    ) -> ResidentLearningDecision:
        """Remove or reject a learning in response to an operator/flock control event."""

        relevant, reason = self._policy.evaluate(artifact, self.identity)
        skill_name = await self._archive_learning_skill(artifact)
        if skill_name:
            decision = ResidentLearningDecision(
                "rolled_back",
                f"Archived {skill_name}: {artifact.command_action or 'retraction'}",
                installed_skill_name=skill_name,
                relevant=True,
            )
            await self._publish_retraction(artifact, decision)
            await self._publish_adoption(artifact, decision)
            return decision

        decision = ResidentLearningDecision(
            "rejected",
            (
                f"No installed learning to retract: {reason}"
                if relevant
                else f"Retraction ignored because learning is not relevant: {reason}"
            ),
            relevant=relevant,
        )
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

    async def _archive_learning_skill(self, artifact: ResidentLearningArtifact) -> str:
        rows = await self._skills.list_skills(include_archived=True)
        artifact_skill_name = _normalise_skill_name(artifact.title)
        capability = _capability_from_content(artifact.content)
        for row in rows:
            skill = row["skill"]
            metadata = row["metadata"]
            name = str(skill.get("name") or "")
            source = str(metadata.get("source") or "")
            if (
                source == f"flock-learning:{artifact.learning_id}"
                or name == artifact_skill_name
                or (capability and f"capability: {capability}" in str(skill.get("content", "")))
            ):
                await self._skills.archive(name)
                return name
        return ""

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

    async def _publish_retraction(
        self,
        artifact: ResidentLearningArtifact,
        decision: ResidentLearningDecision,
    ) -> None:
        await self._publisher.publish(
            SleipnirEvent(
                event_type="valkyrie.evolution.rolled_back",
                source=self._source,
                payload={
                    "environment_id": self.identity.environment_id,
                    "valkyrie_id": self.identity.valkyrie_id,
                    "learning_id": artifact.learning_id,
                    "promotion_id": artifact.promotion_id,
                    "skill_name": decision.installed_skill_name,
                    "artifact_type": artifact.artifact_type,
                    "scope": _normalise_scope(artifact.scope),
                    "source_environment_id": artifact.source_environment_id,
                    "source_valkyrie_id": artifact.source_valkyrie_id,
                    "command_action": artifact.command_action,
                    "rationale": decision.rationale,
                },
                summary=(
                    f"{self.identity.valkyrie_id} archived learning skill "
                    f"{decision.installed_skill_name}"
                ),
                urgency=0.45,
                domain="infrastructure",
                timestamp=datetime.now(UTC),
                correlation_id=artifact.correlation_id or artifact.learning_id,
                causation_id=artifact.causation_id,
            )
        )

    async def _publish_dream_event(
        self,
        event_type: str,
        dream_id: str,
        payload: dict[str, Any],
        *,
        summary: str,
        urgency: float,
        correlation_id: str,
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
                urgency=urgency,
                domain="infrastructure",
                timestamp=datetime.now(UTC),
                correlation_id=correlation_id,
            )
        )

    async def _publish_evolution_event(
        self,
        event_type: str,
        summary: str,
        payload: dict[str, Any],
        correlation_id: str,
        *,
        urgency: float = 0.4,
    ) -> None:
        await self._publisher.publish(
            SleipnirEvent(
                event_type=event_type,
                source=self._source,
                payload={
                    "environment_id": self.identity.environment_id,
                    "environment_type": self.identity.environment_type,
                    "valkyrie_id": self.identity.valkyrie_id,
                    **payload,
                },
                summary=summary,
                urgency=urgency,
                domain="infrastructure",
                timestamp=datetime.now(UTC),
                correlation_id=correlation_id,
            )
        )

    async def _publish_flock_learning_proposal(
        self,
        artifact: ResidentLearningArtifact,
        build: BuildResult,
        review: ReviewResult,
    ) -> None:
        if not artifact.flock_id:
            return
        await self._publisher.publish(
            SleipnirEvent(
                event_type="flock.learning.proposed",
                source=self._source,
                payload={
                    "learning_id": artifact.learning_id,
                    "title": artifact.title,
                    "summary": artifact.summary,
                    "flock_id": artifact.flock_id,
                    "artifact_type": artifact.artifact_type,
                    "content": artifact.content,
                    "artifact_content": artifact.content,
                    "status": "candidate",
                    "domain": artifact.domain,
                    "source_environment_id": artifact.source_environment_id,
                    "source_valkyrie_id": artifact.source_valkyrie_id,
                    "confidence": artifact.confidence,
                    "redaction_status": artifact.redaction_status,
                    "promotion_id": artifact.promotion_id,
                    "artifact_path": artifact.artifact_path,
                    "review_outcome": review.outcome,
                    "builder_evidence": dict(build.evidence),
                    "nats_subject": "ravn.environment.flock.learning.proposed",
                    "additional_nats_subjects": [
                        _flock_nats_subject(self.identity, "flock.learning.proposed")
                    ],
                },
                summary=f"flock.learning.proposed: {artifact.title}",
                urgency=0.2,
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
            action=_adoption_event_action(decision.action),
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
                "ack_kind": "resident_learning",
                "source_environment_id": artifact.source_environment_id,
                "source_valkyrie_id": artifact.source_valkyrie_id,
                "command_action": artifact.command_action,
                "additional_nats_subjects": [
                    _flock_nats_subject(self.identity, "learning.adoption.recorded")
                ],
            }
        )
        await self._publisher.publish(event)


def _artifact_from_event(event: SleipnirEvent) -> ResidentLearningArtifact:
    payload = event.payload
    event_type = event.event_type
    if event_type == "flock.learning.proposed":
        title = str(payload.get("title") or payload.get("artifact_name") or payload["learning_id"])
        scope = "flock"
    elif event_type == registry.LEARNING_ADOPTION_RECORDED:
        title = str(
            payload.get("artifact_name")
            or payload.get("promoted_tool")
            or payload.get("title")
            or payload.get("learning_id")
            or ""
        )
        scope = str(payload.get("target_scope") or payload.get("scope") or "flock")
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
        operator_command=bool(payload.get("operator_id") or payload.get("action_kind")),
        command_action=str(payload.get("action_kind") or payload.get("action") or ""),
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
        or finding.startswith("unavailable runtime dependency")
    ]
    return not blocked_findings


def _is_resident_ack(event: SleipnirEvent) -> bool:
    payload = event.payload
    if event.event_type != registry.LEARNING_ADOPTION_RECORDED:
        return False
    return bool(payload.get("resident_valkyrie_id") or payload.get("ack_kind"))


def _is_retraction_event(event: SleipnirEvent) -> bool:
    event_type = event.event_type
    payload = event.payload
    if event_type in {"flock.learning.rejected", "flock.learning.rolled_back"}:
        return True
    if event_type == registry.LEARNING_ADOPTION_RECORDED:
        action = str(payload.get("action_kind") or payload.get("action") or "").lower()
        return action in {"reject", "rejected", "rollback", "rolled_back", "regressed"}
    if event_type == registry.LEARNING_PROMOTED:
        action = str(payload.get("action_kind") or "").lower()
        if action != "demote":
            return False
        return _normalise_scope(str(payload.get("to_scope") or payload.get("scope") or "")) in {
            "private",
            "environment",
        }
    return False


def _adoption_event_action(action: LearningAction) -> str:
    if action == "ignored":
        return "rejected"
    if action == "rolled_back":
        return "regressed"
    return action


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


def _flock_nats_subject(identity: ResidentLearningIdentity, event_type: str) -> str:
    domain = _nats_token(identity.domain or identity.environment_type or "environment")
    environment_id = _nats_token(identity.environment_id)
    return f"flock.{domain}.{environment_id}.{event_type}"


def _nats_token(value: str) -> str:
    token = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value).strip()).strip("-").lower()
    return token or "unknown"


def _normalise_scope(scope: str) -> str:
    value = (scope or "private").lower()
    return value if value in {"private", "environment", "domain", "flock", "shared"} else "private"


def _normalise_flock_id(flock_id: str) -> str:
    value = flock_id.strip()
    if not value:
        return ""
    return value if value.startswith("flock:") else f"flock:{value}"


def _normalise_skill_name(name: str) -> str:
    return name.strip()


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
