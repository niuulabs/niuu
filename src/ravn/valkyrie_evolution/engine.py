"""Local proof runner for Valkyrie self-improvement."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ravn.adapters.reflection.flock_learning import (
    FlockLearningCandidate,
    FlockLearningExchange,
    FlockLearningStore,
)
from ravn.adapters.skill.file_registry import FileSkillRegistry
from ravn.skills.management import SkillManagementRegistry
from ravn.valkyrie_evolution.adapters import JsonlEventLedger, PolicyCourtReviewer
from ravn.valkyrie_evolution.models import (
    BuildResult,
    CapabilityGap,
    EvolutionRequest,
    OperationalSignal,
    ProofArtifacts,
    ProofReport,
    ReviewResult,
    ValkyrieDecision,
)
from ravn.valkyrie_evolution.ports import (
    EventLedgerPort,
    EvolutionBuilderPort,
    EvolutionReviewPort,
)
from ravn.valkyrie_evolution.resident_learning import (
    ResidentLearningIdentity,
    ResidentLearningRuntime,
)
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain import registry
from sleipnir.domain.events import SleipnirEvent


class ValkyrieEvolutionProofRunner:
    """Run a deterministic local proof without binding the loop to NATS."""

    def __init__(
        self,
        *,
        out_dir: str | Path,
        builder: EvolutionBuilderPort,
        reviewer: EvolutionReviewPort | None = None,
        ledger: EventLedgerPort | None = None,
        environment_id: str = "local-proof",
        autonomy_mode: str = "yolo",
        reset: bool = True,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.skills_dir = self.out_dir / "skills"
        self.environment_id = environment_id
        self.autonomy_mode = autonomy_mode
        if reset:
            _reset_managed_artifacts(self.out_dir)
        self.ledger = ledger or JsonlEventLedger(self.out_dir / "events.jsonl")
        self.builder = builder
        self.reviewer = reviewer or PolicyCourtReviewer()
        skill_port = FileSkillRegistry(
            skill_dirs=[str(self.skills_dir)],
            write_dir=self.skills_dir,
            include_builtin=False,
        )
        self.skills = SkillManagementRegistry(
            skill_port,
            metadata_path=self.out_dir / "skill_management.json",
        )

    async def run(self, signals: list[OperationalSignal] | None = None) -> ProofReport:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        signals = signals or default_proof_signals(environment_id=self.environment_id)

        first_pass: list[ValkyrieDecision] = []
        gaps: list[CapabilityGap] = []
        for signal in signals:
            await self._record_signal(signal, phase="first_pass")
            decision = await self._decide(signal, phase="first_pass")
            first_pass.append(decision)
            await self._record_decision(decision)
            if decision.capability_gap is not None:
                gaps.append(decision.capability_gap)
                await self._record_gap(decision.capability_gap, decision.signal_id)

        dream_id = "dream-cycle-1"
        await self._record(
            "valkyrie.dream.started",
            "Dream cycle started for unresolved capability gaps",
            {"dream_id": dream_id, "gap_count": len(gaps)},
            urgency=0.2,
        )
        build_results: list[BuildResult] = []
        review_results: list[ReviewResult] = []
        for gap in gaps:
            request = EvolutionRequest(
                request_id=f"evolve-{gap.gap_id}",
                gap=gap,
                autonomy_mode=self.autonomy_mode,
                target_scope="environment",
            )
            await self._record(
                "valkyrie.evolution.requested",
                f"Requested evolution for {gap.capability_name}",
                asdict(request),
                urgency=0.5,
            )
            build = await self.builder.build(request)
            build_results.append(build)
            await self._record(
                "valkyrie.evolution.built",
                f"Built skill {build.skill_name}",
                asdict(build),
                urgency=0.4,
            )
            review = await self.reviewer.review(
                request=request,
                build=build,
                autonomy_mode=self.autonomy_mode,
            )
            review_results.append(review)
            await self._record(
                registry.ODIN_COURT_DECIDED,
                f"Odin reviewed {build.skill_name}: {review.outcome}",
                asdict(review),
                urgency=0.5 if review.required_for_activation else 0.25,
            )
            if review.approved and not review.blocking_findings:
                await self.skills.create(
                    name=build.skill_name,
                    content=build.skill_content,
                    description=build.description,
                    scope="environment",
                    environment_id=self.environment_id,
                    domain=gap.domain,
                    source="valkyrie-dream-cycle",
                    action_safety_class=gap.safety_class,
                )
                await self._record(
                    "valkyrie.evolution.activated",
                    f"Activated skill {build.skill_name}",
                    {
                        "skill_name": build.skill_name,
                        "review_outcome": review.outcome,
                        "autonomy_mode": self.autonomy_mode,
                    },
                    urgency=0.35,
                )
            else:
                await self._record(
                    "valkyrie.evolution.held",
                    f"Held skill {build.skill_name}",
                    {
                        "skill_name": build.skill_name,
                        "review_outcome": review.outcome,
                        "findings": review.findings,
                    },
                    urgency=0.6,
                )
        await self._record(
            "valkyrie.dream.completed",
            "Dream cycle completed with evolved skills",
            {
                "dream_id": dream_id,
                "skills_built": [build.skill_name for build in build_results],
            },
            urgency=0.2,
        )

        replay: list[ValkyrieDecision] = []
        for signal in signals:
            await self._record_signal(signal, phase="replay")
            decision = await self._decide(signal, phase="replay")
            replay.append(decision)
            await self._record_decision(decision)
            if decision.skill_name:
                await self.skills.record_usage(
                    decision.skill_name,
                    success=True,
                    environment_id=self.environment_id,
                    domain=signal.domain,
                    action_safety_class="read_only",
                )
                await self._record(
                    "valkyrie.evolution.proven",
                    f"Replay used generated skill {decision.skill_name}",
                    {
                        "signal_id": signal.signal_id,
                        "skill_name": decision.skill_name,
                        "capability_name": decision.capability_name,
                    },
                    urgency=0.3,
                )

        await self._run_flock_resident_proof(signals, build_results, review_results)
        return await self._write_report(
            signals,
            first_pass,
            gaps,
            build_results,
            review_results,
            replay,
        )

    async def _run_flock_resident_proof(
        self,
        signals: list[OperationalSignal],
        build_results: list[BuildResult],
        review_results: list[ReviewResult],
    ) -> None:
        shareable = [
            build
            for build, review in zip(build_results, review_results, strict=False)
            if build.artifact_type == "ravn_skill_tool"
            and (review.approved and not review.blocking_findings)
            and "capability:" in build.skill_content
        ]
        if not shareable:
            return

        build = next(
            (candidate for candidate in shareable if "kubernetes" in candidate.skill_content),
            shareable[0],
        )
        signal = next(
            (
                candidate
                for candidate in signals
                if candidate.event_type == registry.SIGNAL_KUBERNETES_EVENT
            ),
            signals[0],
        )
        bus = InProcessBus()
        await bus.subscribe(
            [
                "flock.learning.*",
                "learning.*",
                "valkyrie.evolution.*",
                "odin.*",
                "valkyrie.judgment.*",
            ],
            self.ledger.record,
        )
        exchange = FlockLearningExchange(
            store=FlockLearningStore(self.out_dir / "flock_learning.json"),
            publisher=bus,
            source="valkyrie:source",
        )
        relevant = ResidentLearningRuntime(
            identity=ResidentLearningIdentity(
                environment_id="peer-k8s",
                valkyrie_id="valkyrie:peer-k8s",
                domain="k8s",
                flock_ids=["k8s-valkyries"],
                autonomy_mode="yolo",
            ),
            skills=_resident_skill_manager(self.out_dir, "peer-k8s"),
            publisher=bus,
            subscriber=bus,
        )
        irrelevant = ResidentLearningRuntime(
            identity=ResidentLearningIdentity(
                environment_id="peer-printer",
                valkyrie_id="valkyrie:peer-printer",
                domain="home",
                flock_ids=["printer-operators"],
                autonomy_mode="yolo",
            ),
            skills=_resident_skill_manager(self.out_dir, "peer-printer"),
            publisher=bus,
            subscriber=bus,
        )
        guarded = ResidentLearningRuntime(
            identity=ResidentLearningIdentity(
                environment_id="peer-k8s-guarded",
                valkyrie_id="valkyrie:peer-k8s-guarded",
                domain="k8s",
                flock_ids=["k8s-valkyries"],
                autonomy_mode="guarded",
            ),
            skills=_resident_skill_manager(self.out_dir, "peer-k8s-guarded"),
            publisher=bus,
            subscriber=bus,
        )
        await relevant.start()
        await irrelevant.start()
        await guarded.start()
        try:
            await exchange.propose(
                FlockLearningCandidate(
                    learning_id=f"flock-{build.skill_name}",
                    title=build.skill_name,
                    artifact_type="tool_skill",
                    summary=build.description,
                    content=build.skill_content,
                    flock_id="k8s-valkyries",
                    source_environment_id=self.environment_id,
                    source_valkyrie_id="valkyrie:source",
                    confidence=0.88,
                    redaction_status="redacted",
                    promotion_id=f"promotion-{build.skill_name}",
                    promoted_path=f"learnings/flock/k8s-valkyries/{build.skill_name}.md",
                    tags=["k8s", "flock-proof"],
                    metadata={"domain": "k8s"},
                )
            )
            await bus.flush()
            await bus.flush()
            await relevant.process_signal(
                OperationalSignal(
                    signal_id=f"peer-replay-{signal.signal_id}",
                    event_type=signal.event_type,
                    environment_id="peer-k8s",
                    domain=signal.domain,
                    severity=signal.severity,
                    summary=signal.summary,
                    payload=dict(signal.payload),
                )
            )
            await bus.flush()
        finally:
            await relevant.stop()
            await irrelevant.stop()
            await guarded.stop()

    async def _decide(self, signal: OperationalSignal, *, phase: str) -> ValkyrieDecision:
        capability = _derive_capability_name(signal)
        skill = await self._find_skill_by_capability(capability)
        if skill is None:
            gap = CapabilityGap(
                gap_id=f"gap-{_slug(signal.signal_id)}",
                capability_name=capability,
                environment_id=signal.environment_id,
                domain=signal.domain,
                reason=_gap_reason(signal),
                signal_ids=[signal.signal_id],
                evidence={"summary": signal.summary, "payload": signal.payload},
            )
            return ValkyrieDecision(
                signal_id=signal.signal_id,
                phase=phase,
                decision="defer_and_request_capability",
                confidence=0.42,
                rationale="No matching resident skill exists for this signal shape yet.",
                capability_name=capability,
                capability_gap=gap,
            )
        return ValkyrieDecision(
            signal_id=signal.signal_id,
            phase=phase,
            decision="inspect_with_generated_skill",
            confidence=0.83,
            rationale="A dream-built resident skill now matches this signal capability.",
            capability_name=capability,
            skill_name=skill.name,
        )

    async def _find_skill_by_capability(self, capability_name: str) -> Any | None:
        rows = await self.skills.list_skills(include_archived=False)
        marker = f"capability: {capability_name}"
        for row in rows:
            skill = row["skill"]
            if marker in str(skill.get("content", "")):
                return type("RunnableSkill", (), skill)()
        return None

    async def _record_signal(self, signal: OperationalSignal, *, phase: str) -> None:
        event_type = signal.event_type
        await self._record(
            event_type,
            signal.summary,
            {
                "phase": phase,
                "signal_id": signal.signal_id,
                "environment_id": signal.environment_id,
                **signal.payload,
            },
            urgency=_urgency(signal.severity),
            domain=signal.domain,
        )

    async def _record_decision(self, decision: ValkyrieDecision) -> None:
        payload = asdict(decision)
        await self._record(
            registry.VALKYRIE_JUDGMENT_PROPOSED,
            f"{decision.decision} for {decision.signal_id}",
            payload,
            urgency=0.6 if decision.capability_gap else 0.4,
        )

    async def _record_gap(self, gap: CapabilityGap, signal_id: str) -> None:
        await self._record(
            "valkyrie.capability_gap.detected",
            f"Capability gap {gap.capability_name}",
            {"signal_id": signal_id, **asdict(gap)},
            urgency=0.6,
            domain=gap.domain,
        )

    async def _record(
        self,
        event_type: str,
        summary: str,
        payload: dict[str, Any],
        *,
        urgency: float,
        domain: str = "infrastructure",
    ) -> None:
        await self.ledger.record(
            SleipnirEvent(
                event_type=event_type,
                source="ravn:valkyrie-evolution-proof",
                payload=payload,
                summary=summary,
                urgency=urgency,
                domain=domain,
                timestamp=datetime.now(UTC),
                correlation_id="valkyrie-evolution-proof",
            )
        )

    async def _write_report(
        self,
        signals: list[OperationalSignal],
        first_pass: list[ValkyrieDecision],
        gaps: list[CapabilityGap],
        build_results: list[BuildResult],
        review_results: list[ReviewResult],
        replay: list[ValkyrieDecision],
    ) -> ProofReport:
        events = await self.ledger.list_events()
        artifacts = ProofArtifacts(
            out_dir=self.out_dir,
            events_path=self.out_dir / "events.jsonl",
            report_json_path=self.out_dir / "proof-report.json",
            report_markdown_path=self.out_dir / "proof-report.md",
            skills_dir=self.skills_dir,
        )
        summary = {
            "signals_received": sum(1 for e in events if e.event_type.startswith("signal.")),
            "sample_signal_shapes_exercised": len(signals),
            "first_pass_decisions": len(first_pass),
            "capability_gaps_detected": len(gaps),
            "dream_cycles_completed": sum(
                1 for e in events if e.event_type == "valkyrie.dream.completed"
            ),
            "skills_built": len(build_results),
            "odin_reviews": len(review_results),
            "odin_reviews_required": sum(
                1 for review in review_results if review.required_for_activation
            ),
            "skills_activated": sum(
                1 for e in events if e.event_type == "valkyrie.evolution.activated"
            ),
            "local_skills_activated": sum(
                1
                for e in events
                if e.event_type == "valkyrie.evolution.activated" and "learning_id" not in e.payload
            ),
            "resident_skills_installed": sum(
                1
                for e in events
                if e.event_type == "valkyrie.evolution.activated" and "learning_id" in e.payload
            ),
            "skills_held": sum(1 for e in events if e.event_type == "valkyrie.evolution.held"),
            "replay_decisions": len(replay),
            "skills_used_on_replay": sum(1 for d in replay if d.skill_name),
            "flock_learnings_proposed": sum(
                1 for e in events if e.event_type == "flock.learning.proposed"
            ),
            "resident_learnings_adopted": sum(
                1
                for e in events
                if e.event_type == registry.LEARNING_ADOPTION_RECORDED
                and e.payload.get("action") == "adopted"
            ),
            "resident_learnings_rejected": sum(
                1
                for e in events
                if e.event_type == registry.LEARNING_ADOPTION_RECORDED
                and e.payload.get("action") == "rejected"
            ),
            "resident_adopted_skills_used": sum(
                1
                for e in events
                if e.event_type == registry.VALKYRIE_JUDGMENT_PROPOSED
                and e.payload.get("recommended_action") == "inspect_with_adopted_learning"
            ),
            "resident_odin_decisions": sum(
                1
                for e in events
                if e.event_type == registry.ODIN_COURT_DECIDED
                and str(e.payload.get("court_id", "")).startswith("odin-learning:")
            ),
            "autonomy_mode": self.autonomy_mode,
            "builder_adapter": self.builder.__class__.__name__,
            "review_adapter": self.reviewer.__class__.__name__,
            "container_safe_artifacts": True,
            "hardcoded_tool_choices": False,
        }
        report = ProofReport(
            summary=summary,
            signals=[asdict(signal) for signal in signals],
            first_pass_decisions=[asdict(decision) for decision in first_pass],
            dream_cycles=[
                event.to_dict()
                for event in events
                if event.event_type in {"valkyrie.dream.started", "valkyrie.dream.completed"}
            ],
            build_results=[asdict(build) for build in build_results],
            review_results=[asdict(review) for review in review_results],
            replay_decisions=[asdict(decision) for decision in replay],
            artifacts={
                "out_dir": str(artifacts.out_dir),
                "events": str(artifacts.events_path),
                "report_json": str(artifacts.report_json_path),
                "report_markdown": str(artifacts.report_markdown_path),
                "skills_dir": str(artifacts.skills_dir),
            },
        )
        artifacts.report_json_path.write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        artifacts.report_markdown_path.write_text(_render_report_markdown(report), encoding="utf-8")
        return report


def default_proof_signals(*, environment_id: str) -> list[OperationalSignal]:
    return [
        OperationalSignal(
            signal_id="sig-k8s-oom-restart-loop",
            event_type=registry.SIGNAL_KUBERNETES_EVENT,
            environment_id=environment_id,
            domain="infrastructure",
            severity="high",
            summary="Pod restart loop with OOMKilled reason",
            payload={
                "kind": "pod",
                "reason": "oomkilled",
                "object": "payments-api-7c9",
                "namespace": "prod",
                "metric": "restart_count",
                "observed": "5",
            },
        ),
        OperationalSignal(
            signal_id="sig-host-disk-pressure",
            event_type=registry.SIGNAL_HOST_EVENT,
            environment_id=environment_id,
            domain="infrastructure",
            severity="medium",
            summary="Host reports rising disk pressure",
            payload={
                "kind": "host",
                "host": "workstation",
                "reason": "disk_pressure",
                "metric": "disk_free_percent",
                "threshold": "10",
                "observed": "7",
            },
        ),
        OperationalSignal(
            signal_id="sig-printer-resin-low",
            event_type=registry.SIGNAL_PRINTER_EVENT,
            environment_id=environment_id,
            domain="home",
            severity="medium",
            summary="Printer reports resin below learned threshold",
            payload={
                "kind": "printer",
                "printer": "saturn-4",
                "reason": "resin_low",
                "material": "resin",
                "threshold": "18",
                "observed": "9",
            },
        ),
    ]


def _derive_capability_name(signal: OperationalSignal) -> str:
    namespace = signal.event_type.removeprefix("signal.").removesuffix(".event")
    reason = str(signal.payload.get("reason") or signal.payload.get("kind") or "unknown")
    kind = str(signal.payload.get("kind") or namespace)
    return f"inspect.{_slug(namespace)}.{_slug(kind)}.{_slug(reason)}"


def _gap_reason(signal: OperationalSignal) -> str:
    reason = str(signal.payload.get("reason") or "unknown condition")
    kind = str(signal.payload.get("kind") or "signal")
    return f"{kind} reported {reason}"


def _urgency(severity: str) -> float:
    return {"low": 0.2, "medium": 0.5, "high": 0.8, "critical": 0.95}.get(severity, 0.4)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "unknown"


def _resident_skill_manager(out_dir: Path, resident_id: str) -> SkillManagementRegistry:
    skill_dir = out_dir / "resident-skills" / resident_id
    skill_port = FileSkillRegistry(
        skill_dirs=[str(skill_dir)],
        write_dir=skill_dir,
        include_builtin=False,
    )
    return SkillManagementRegistry(
        skill_port,
        metadata_path=out_dir / "resident-skill-management" / f"{resident_id}.json",
    )


def _render_report_markdown(report: ProofReport) -> str:
    lines = [
        "# Valkyrie Evolution Proof",
        "",
        "## Summary",
        "",
    ]
    lines.extend(f"- {key}: {value}" for key, value in report.summary.items())
    lines.extend(["", "## Replay Skill Use", ""])
    for decision in report.replay_decisions:
        lines.append(
            "- {signal_id}: {decision} using {skill_name} at confidence {confidence}".format(
                **decision
            )
        )
    lines.extend(["", "## Artifacts", ""])
    lines.extend(f"- {key}: `{value}`" for key, value in report.artifacts.items())
    return "\n".join(lines) + "\n"


def _reset_managed_artifacts(out_dir: Path) -> None:
    for path in [
        out_dir / "events.jsonl",
        out_dir / "proof-report.json",
        out_dir / "proof-report.md",
        out_dir / "skill_management.json",
    ]:
        if path.exists():
            path.unlink()
    skills_dir = out_dir / "skills"
    if skills_dir.is_dir():
        for path in skills_dir.glob("*.md"):
            path.unlink()
