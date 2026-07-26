"""Resident Valkyrie learning subscriber, installer, and usage loop."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from ravn.adapters.reflection.flock_learning import (
    FlockLearningCandidate,
    FlockLearningRecord,
    FlockLearningStore,
    FlockPeerDecision,
)
from ravn.odin.review import (
    ReviewItem,
    ReviewKind,
    ReviewRequester,
    ReviewStatus,
    item_targets,
    review_resolved_event,
)
from ravn.skills.management import SkillManagementRegistry
from ravn.valkyrie_evolution.adapters import PolicyCourtReviewer
from ravn.valkyrie_evolution.learned_tools import (
    LearnedToolRunner,
    LocalLearnedToolRunner,
    learned_tool_artifact_path,
    learned_tool_path,
    learned_tool_storage,
    learned_tool_venvs_dir,
    manifest_review_boundaries,
    manifest_safety_class,
    read_learned_tool_artifact,
    superseded_artifact_path,
    write_learned_tool,
    write_learned_tool_artifact,
)
from ravn.valkyrie_evolution.models import (
    BuildResult,
    CapabilityGap,
    EvolutionRequest,
    LearnedToolArtifact,
    LearnedToolManifest,
    OperationalSignal,
    ReviewResult,
)
from ravn.valkyrie_evolution.ports import EvolutionReviewPort
from ravn.valkyrie_evolution.tool_runtime import (
    DEFAULT_TOOL_TIMEOUT_SECONDS,
    ToolRunResult,
    run_tool,
    tool_path_for_skill,
    write_tool,
)
from ravn.valkyrie_evolution.tool_verification import verify_learned_tool_in_ephemeral_venv
from sleipnir.domain import registry
from sleipnir.domain.catalog import (
    learning_adoption_recorded,
    learning_promoted,
    odin_court_decided,
    valkyrie_action_requested,
    valkyrie_judgment_proposed,
)
from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import SleipnirPublisher, SleipnirSubscriber, Subscription

logger = logging.getLogger(__name__)

LearningAction = Literal["adopted", "rejected", "rolled_back", "ignored", "held"]

#: Event types this module publishes on install/rollback. Consumers (the realm
#: capability sync, dashboards) import these from the publisher so the
#: producer/consumer contract cannot drift.
EVOLUTION_ACTIVATED_EVENT = "valkyrie.evolution.activated"
EVOLUTION_ROLLED_BACK_EVENT = "valkyrie.evolution.rolled_back"

#: Snapshot event: one per currently-installed learned skill, republished on
#: startup and a heartbeat. Activation events fire once at ADOPTION time (rare,
#: historical), so a dashboard that starts with REPLAY_SECONDS=0 never sees the
#: skills a resident already carries. This event carries the same enriched
#: record shape the mirror already consumes plus a ``status`` field, so the
#: dashboard's skill mirror reflects the resident's live INVENTORY, not just
#: lifecycle transitions.
EVOLUTION_SKILL_INVENTORY_EVENT = "valkyrie.evolution.skill_inventory"

#: ``status`` value carried by a skill-inventory event for an installed skill.
SKILL_INVENTORY_STATUS_PRESENT = "present"

#: How often the resident republishes its full skill inventory (seconds).
DEFAULT_SKILL_INVENTORY_INTERVAL_SECONDS = 300.0

#: Consecutive implementation failures before a skill is auto-rolled-back.
#: A regressed tool must fail repeatedly, never once — transient failures
#: (timeouts, odd payloads) must not destroy adopted learning.
DEFAULT_ROLLBACK_CONSECUTIVE_FAILURES = 3

#: Tail of the failing tool's stderr carried in rollback judgment evidence.
DEFAULT_ROLLBACK_STDERR_EVIDENCE_CHARS = 500

#: Tail of the peer re-verification logs carried in a rejection's rationale
#: (and therefore the durable ledger) when adoption verification fails.
DEFAULT_VERIFY_LOG_EVIDENCE_CHARS = 2000

#: How much one useful/good_action operator feedback verdict raises the
#: stored learning's confidence (clamped at 1.0).
DEFAULT_FEEDBACK_CONFIDENCE_BUMP = 0.05

#: Confidence ceiling for operator reinforcement.
MAX_LEARNING_CONFIDENCE = 1.0

_SKILL_ARTIFACT_TYPES = frozenset({"ravn_skill_tool", "tool_skill", "agent_tool"})
_SAFE_REDACTION_STATES = frozenset({"", "none", "redacted", "safe"})
_SUBSCRIBED_EVENT_TYPES = [
    registry.LEARNING_PROMOTED,
    registry.FLOCK_LEARNING_PROPOSED,
    registry.FLOCK_LEARNING_ADOPTED,
    registry.FLOCK_LEARNING_REJECTED,
    registry.FLOCK_LEARNING_ROLLED_BACK,
    registry.ODIN_REVIEW_DECIDED,
]

#: The only autonomy modes a resident accepts (shared with AutonomyPolicy).
CANONICAL_AUTONOMY_MODES = frozenset({"guarded", "autonomous", "yolo"})


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
    tool_code: str = ""
    tool_entry_point: str = "run"
    learned_tool_manifest: dict[str, Any] = field(default_factory=dict)
    #: Self-contained test module travelling with the proposal (contract v2);
    #: peers re-verify it independently before installing (P6.2).
    test_code: str = ""
    #: pip requirement strings the tool needs ([] for stdlib-only tools).
    requirements: list[str] = field(default_factory=list)
    #: artifact_id of the version this artifact replaces (P6.3 version chain).
    supersedes: str = ""
    canary_sample: dict[str, Any] = field(default_factory=dict)
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
    canary_passed: bool = False
    canary_error: str = ""


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
        if artifact.artifact_type != "agent_tool" and not artifact.content.strip():
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
        if (
            scope == "shared"
            and artifact.domain
            and not _domain_matches(artifact.domain, identity.domain)
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
        tools_dir: str | Path | None = None,
        tool_timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
        rollback_consecutive_failures: int = DEFAULT_ROLLBACK_CONSECUTIVE_FAILURES,
        feedback_confidence_bump: float = DEFAULT_FEEDBACK_CONFIDENCE_BUMP,
        learning_store: FlockLearningStore | None = None,
        review_requester: ReviewRequester | None = None,
        skill_inventory_interval_seconds: float = DEFAULT_SKILL_INVENTORY_INTERVAL_SECONDS,
        learned_tool_runner: LearnedToolRunner | None = None,
    ) -> None:
        self.identity = identity
        self._skills = skills
        self._publisher = publisher
        self._subscriber = subscriber
        self._reviewer = reviewer or PolicyCourtReviewer(reviewer="odin:resident-learning")
        self._policy = policy or ResidentLearningPolicy()
        self._source = source or identity.valkyrie_id
        self._tools_dir = Path(tools_dir) if tools_dir else None
        self._learned_tool_runner = learned_tool_runner
        if self._learned_tool_runner is None and self._tools_dir is not None:
            # Direct service construction keeps its historical local adapter;
            # the production composition root always injects the configured
            # runner (whose default is the contained backend).
            self._learned_tool_runner = LocalLearnedToolRunner(
                venvs_dir=learned_tool_venvs_dir(self._tools_dir.parent),
            )
        self._tool_timeout_seconds = tool_timeout_seconds
        self._rollback_consecutive_failures = rollback_consecutive_failures
        self._feedback_confidence_bump = feedback_confidence_bump
        self._learning_store = learning_store
        self._review_requester = review_requester
        self._skill_inventory_interval_seconds = skill_inventory_interval_seconds
        self._subscription: Subscription | None = None
        self._inventory_task: asyncio.Task[None] | None = None
        self._decisions: list[ResidentLearningDecision] = []

    @property
    def is_running(self) -> bool:
        return self._subscription is not None

    @property
    def skills(self) -> SkillManagementRegistry:
        """The resident's skill registry, shared with co-located runtimes."""
        return self._skills

    @property
    def review_requester(self) -> ReviewRequester | None:
        """The resident's review requester, shared with co-located runtimes."""
        return self._review_requester

    def decisions(self) -> list[ResidentLearningDecision]:
        return list(self._decisions)

    async def start(self) -> None:
        if self._subscription is not None:
            return
        self._subscription = await self._subscriber.subscribe(
            _SUBSCRIBED_EVENT_TYPES,
            self._handle_learning_event,
        )
        if self._review_requester is not None:
            reannounced = await self._review_requester.reannounce()
            if reannounced:
                logger.info(
                    "resident_learning: re-announced %d pending review item(s) for %s",
                    reannounced,
                    self.identity.valkyrie_id,
                )
        # Snapshot the resident's live inventory once subscriptions are up so a
        # freshly-restarted dashboard (REPLAY_SECONDS=0) sees every skill the
        # resident already carries, then keep it fresh on a heartbeat.
        await self.publish_skill_inventory()
        if self._inventory_task is None and self._skill_inventory_interval_seconds > 0:
            self._inventory_task = asyncio.create_task(
                self._run_inventory_loop(),
                name="resident_skill_inventory",
            )

    async def stop(self) -> None:
        if self._inventory_task is not None:
            self._inventory_task.cancel()
            try:
                await self._inventory_task
            except asyncio.CancelledError:
                # Expected after requesting cancellation of the heartbeat loop.
                pass
            self._inventory_task = None
        if self._subscription is None:
            return
        await self._subscription.unsubscribe()
        self._subscription = None

    async def _run_inventory_loop(self) -> None:
        """Republish the full skill inventory on the configured heartbeat.

        Guarded like the wakefulness loop: a single bad snapshot must never
        crash the daemon — it is logged and the next tick tries again.
        """
        while True:
            await asyncio.sleep(self._skill_inventory_interval_seconds)
            try:
                await self.publish_skill_inventory()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "resident_learning: skill inventory heartbeat failed for %s",
                    self.identity.valkyrie_id,
                )

    async def publish_skill_inventory(self) -> int:
        """Publish one inventory event per currently-installed learned skill.

        Enumerates learned-tool artifact envelopes on disk and managed skills
        in the resident registry, builds a full enriched record for each, and
        publishes an :data:`EVOLUTION_SKILL_INVENTORY_EVENT` so the dashboard
        mirror reflects the resident's live inventory. A single bad skill is
        logged and skipped — it must never abort the whole snapshot. Returns
        the number of inventory events published.
        """
        published = 0
        seen: set[str] = set()
        for record in self._iter_learned_tool_inventory():
            name = record["skill_name"]
            if name in seen:
                continue
            seen.add(name)
            if await self._publish_skill_inventory_record(record):
                published += 1
        for record in await self._iter_managed_skill_inventory():
            name = record["skill_name"]
            if name in seen:
                continue
            seen.add(name)
            if await self._publish_skill_inventory_record(record):
                published += 1
        return published

    async def _refresh_skill_inventory(self) -> None:
        """Opportunistically republish the inventory; never raise into a caller.

        Adoption/rollback flows call this so the dashboard reflects the change
        immediately — but a snapshot failure must never break the install or
        rollback that already succeeded.
        """
        try:
            await self.publish_skill_inventory()
        except Exception:  # noqa: BLE001 — inventory refresh is best-effort
            logger.exception(
                "resident_learning: opportunistic skill inventory refresh failed for %s",
                self.identity.valkyrie_id,
            )

    def _iter_learned_tool_inventory(self) -> list[dict[str, Any]]:
        """Full records for every installed learned-tool artifact envelope."""
        if self._tools_dir is None:
            return []
        _code_dir, artifacts_dir = learned_tool_storage(self._tools_dir.parent)
        if not artifacts_dir.is_dir():
            return []
        records: list[dict[str, Any]] = []
        for artifact_file in sorted(artifacts_dir.glob("*.json")):
            try:
                artifact = read_learned_tool_artifact(artifact_file)
            except Exception:  # noqa: BLE001 — one bad envelope must not abort the snapshot
                logger.exception(
                    "resident_learning: skipping unreadable learned-tool artifact %s",
                    artifact_file,
                )
                continue
            manifest = artifact.manifest
            if self._skills.status(manifest.name) == "archived":
                continue
            records.append(
                {
                    "skill_name": manifest.name,
                    "skill_content": _agent_tool_content(
                        ResidentLearningArtifact(
                            learning_id=artifact.artifact_id,
                            title=manifest.name,
                            summary=manifest.description,
                            content="",
                            artifact_type="agent_tool",
                            scope="environment",
                            confidence=0.0,
                            source_valkyrie_id=self.identity.valkyrie_id,
                        ),
                        manifest,
                        manifest_safety_class(manifest),
                    ),
                    "learned_tool_manifest": manifest.to_dict(),
                    "tool_code": artifact.tool_code,
                    "test_code": artifact.test_code,
                    "requirements": list(artifact.requirements),
                    "summary_text": manifest.description,
                    "learning_id": artifact.artifact_id,
                    "adopted_at": artifact.created_at,
                    "learning_scope": str(artifact.provenance.get("scope") or "environment"),
                    "learning_source": f"flock-learning:{artifact.artifact_id}",
                    "source_environment_id": str(
                        artifact.provenance.get("source_environment_id") or ""
                    ),
                    "source_valkyrie_id": str(artifact.provenance.get("source_valkyrie_id") or ""),
                }
            )
        return records

    async def _iter_managed_skill_inventory(self) -> list[dict[str, Any]]:
        """Full records for installed managed skills (markdown + optional tool)."""
        try:
            rows = await self._skills.list_skills()
        except Exception:  # noqa: BLE001 — the registry must not abort the snapshot
            logger.exception(
                "resident_learning: could not enumerate managed skills for %s",
                self.identity.valkyrie_id,
            )
            return []
        records: list[dict[str, Any]] = []
        for row in rows:
            skill = row.get("skill") if isinstance(row, dict) else None
            skill = skill if isinstance(skill, dict) else {}
            name = str(skill.get("name") or "").strip()
            if not name:
                continue
            metadata = row.get("metadata") if isinstance(row, dict) else None
            metadata = metadata if isinstance(metadata, dict) else {}
            records.append(
                {
                    "skill_name": name,
                    "skill_content": str(skill.get("content") or ""),
                    "learned_tool_manifest": {},
                    "tool_code": self._skill_tool_code(name),
                    "test_code": "",
                    "requirements": [],
                    "summary_text": str(skill.get("description") or ""),
                    "learning_id": str(metadata.get("skill_id") or ""),
                    "adopted_at": str(metadata.get("created_at") or skill.get("created_at") or ""),
                    "learning_scope": str(metadata.get("scope") or "private"),
                    "learning_source": str(metadata.get("source") or "manual"),
                    "source_environment_id": str(metadata.get("source_environment_id") or ""),
                    "source_valkyrie_id": str(metadata.get("source_valkyrie_id") or ""),
                }
            )
        return records

    def _skill_tool_code(self, skill_name: str) -> str:
        """Read the co-installed tool implementation for a managed skill, if any."""
        if self._tools_dir is None:
            return ""
        tool_path = tool_path_for_skill(self._tools_dir, skill_name)
        if not tool_path.is_file():
            return ""
        try:
            return tool_path.read_text(encoding="utf-8")
        except OSError:
            logger.warning(
                "resident_learning: could not read tool implementation for skill %s",
                skill_name,
            )
            return ""

    async def _publish_skill_inventory_record(self, record: dict[str, Any]) -> bool:
        """Publish one inventory event; log and swallow a per-skill failure."""
        source_valkyrie_id = str(record.get("source_valkyrie_id") or "")
        learning_source = str(record.get("learning_source") or "")
        learning_scope = str(record.get("learning_scope") or "")
        try:
            await self._publisher.publish(
                SleipnirEvent(
                    event_type=EVOLUTION_SKILL_INVENTORY_EVENT,
                    source=self._source,
                    payload={
                        "environment_id": self.identity.environment_id,
                        "valkyrie_id": self.identity.valkyrie_id,
                        "skill_name": record["skill_name"],
                        "status": SKILL_INVENTORY_STATUS_PRESENT,
                        "learning_id": record.get("learning_id") or "",
                        "adopted_at": record.get("adopted_at") or "",
                        "skill_content": record.get("skill_content") or "",
                        "learned_tool_manifest": dict(record.get("learned_tool_manifest") or {}),
                        "tool_code": record.get("tool_code") or "",
                        "test_code": record.get("test_code") or "",
                        "requirements": list(record.get("requirements") or []),
                        "summary_text": record.get("summary_text") or "",
                        "learning_scope": learning_scope,
                        "learning_source": learning_source,
                        "learning_origin": _learning_origin(
                            source_valkyrie_id=source_valkyrie_id,
                            resident_valkyrie_id=self.identity.valkyrie_id,
                            learning_source=learning_source,
                            learning_scope=learning_scope,
                        ),
                        "source_environment_id": record.get("source_environment_id") or "",
                        "source_valkyrie_id": source_valkyrie_id,
                    },
                    summary=(
                        f"{self.identity.valkyrie_id} has installed skill {record['skill_name']}"
                    ),
                    urgency=0.1,
                    domain="infrastructure",
                    timestamp=datetime.now(UTC),
                )
            )
        except Exception:  # noqa: BLE001 — one skill must not abort the whole snapshot
            logger.exception(
                "resident_learning: failed to publish inventory for skill %s",
                record.get("skill_name"),
            )
            return False
        return True

    def _evolution_dedupe_key(self, capability: str) -> str:
        return f"{ReviewKind.EVOLUTION_BUILD.value}:{self.identity.environment_id}:{capability}"

    async def process_signal(self, signal: OperationalSignal | SleipnirEvent) -> dict[str, Any]:
        """Return a cheap capability hint without making or executing a judgment.

        Exact capability-name matching is useful retrieval evidence, but it is
        not enough context to decide that a skill is appropriate, execute its
        implementation, or publish a Valkyrie judgment. The resident LLM sees
        this hint alongside the live signal and the complete capability catalog.
        """

        operational_signal = _to_operational_signal(signal, self.identity)
        capability = _derive_capability_name(operational_signal)
        skill = await self._find_installed_skill_by_capability(capability)
        if skill is None:
            return {
                "signalId": operational_signal.signal_id,
                "capabilityName": capability,
                "decision": "capability_lookup_miss",
                "usedAdoptedLearning": False,
                "skillName": "",
                "capabilityCandidates": [],
            }
        return {
            "signalId": operational_signal.signal_id,
            "capabilityName": capability,
            "decision": "capability_hint_available",
            "usedAdoptedLearning": False,
            "skillName": skill.name,
            "capabilityCandidates": [
                {
                    "kind": "skill",
                    "name": skill.name,
                    "description": skill.description,
                    "source": "resident_skill_registry",
                    "match": "exact_capability_name",
                }
            ],
        }

    async def register_installed_artifact(self, artifact: ResidentLearningArtifact) -> None:
        """Put an already-installed local tool under the resident lifecycle.

        ``build_tool`` has already verified, reviewed, persisted, and registered
        the implementation before calling this hook. This method records the
        same managed-skill projection used by peer adoption, without installing
        the code a second time.
        """
        _, build = review_inputs(artifact, self.identity)
        await self._record_installed_skill(artifact, build)
        await self._refresh_skill_inventory()

    async def execute_selected_capability(
        self,
        signal: OperationalSignal | SleipnirEvent,
        *,
        skill_name: str,
    ) -> dict[str, Any]:
        """Execute a capability explicitly selected by the agent/tool loop.

        This is deliberately separate from :meth:`process_signal`: retrieval
        does not imply execution. The caller must retain the selected skill
        name in the transcript, after which this method supplies auditable
        execution evidence and lifecycle/rollback bookkeeping.
        """
        operational_signal = _to_operational_signal(signal, self.identity)
        capability = _derive_capability_name(operational_signal)
        skill = await self._skills.get_runnable_skill(str(skill_name or "").strip())
        if skill is None:
            return {
                "signalId": operational_signal.signal_id,
                "capabilityName": capability,
                "decision": "selected_capability_unavailable",
                "usedAdoptedLearning": False,
                "skillName": str(skill_name or "").strip(),
            }

        tool_run = await self._run_skill_tool(skill, operational_signal)
        tool_succeeded = tool_run is None or tool_run.ok
        lifecycle = await self._skills.record_usage(
            skill.name,
            success=tool_succeeded,
            environment_id=self.identity.environment_id,
            domain=operational_signal.domain,
            action_safety_class=_safety_class_from_content(skill.content),
        )
        if (
            not tool_succeeded
            and lifecycle.consecutive_failures >= self._rollback_consecutive_failures
        ):
            return await self._rollback_regressed_skill(
                skill,
                capability,
                lifecycle,
                tool_run,
                operational_signal,
            )

        result: dict[str, Any] = {
            "signalId": operational_signal.signal_id,
            "capabilityName": capability,
            "decision": (
                "inspect_with_adopted_learning" if tool_succeeded else "adopted_learning_failed"
            ),
            "usedAdoptedLearning": True,
            "skillName": skill.name,
            "executionSelected": True,
            "consecutiveFailures": lifecycle.consecutive_failures,
        }
        if tool_run is not None:
            result["toolResult"] = tool_run.result if tool_run.ok else {"error": tool_run.error}
        return result

    async def _rollback_regressed_skill(
        self,
        skill: Any,
        capability: str,
        lifecycle: Any,
        tool_run: ToolRunResult,
        signal: OperationalSignal,
    ) -> dict[str, Any]:
        """Archive a skill whose implementation keeps failing and tell the flock.

        The YOLO invariant: rollback is automatic on regression.  The skill is
        archived with full provenance, the regression travels to the flock so
        the teacher and peers record negative transfer, and the capability gap
        reopens so the resident may rebuild a better implementation.
        """
        skill_name = str(skill.name)
        shown = await self._skills.show(skill_name, include_archived=True)
        metadata = shown.get("metadata", {})
        source = str(metadata.get("source") or "")
        learning_id = (
            source.removeprefix("flock-learning:")
            if source.startswith("flock-learning:")
            else skill_name
        )

        await self._skills.archive(skill_name)
        self._prune_tool_venv(skill_name)

        try:
            restored_artifact_id, restore_detail = await self._attempt_restore_of_superseded(
                skill_name,
                signal,
            )
        except Exception as exc:  # noqa: BLE001 — the rollback judgment must still publish
            logger.exception(
                "resident_learning: restore of superseded version failed for %s",
                skill_name,
            )
            restored_artifact_id = ""
            restore_detail = f"restore failed: {type(exc).__name__}: {exc}"

        artifact = ResidentLearningArtifact(
            learning_id=learning_id,
            title=skill_name,
            summary=f"Auto-rolled-back after {lifecycle.consecutive_failures} "
            "consecutive implementation failures",
            content=str(skill.content or ""),
            artifact_type="ravn_skill_tool",
            scope=str(metadata.get("scope") or "environment"),
            confidence=0.0,
            source_environment_id=self.identity.environment_id,
            source_valkyrie_id=self.identity.valkyrie_id,
            promotion_id=learning_id,
            flock_id=_normalise_flock_id(self.identity.flock_ids[0])
            if self.identity.flock_ids
            else "",
            domain=str(metadata.get("domain") or self.identity.domain),
            redaction_status="redacted",
            correlation_id=signal.signal_id,
            command_action="auto_rollback_regression",
        )
        decision = ResidentLearningDecision(
            "rolled_back",
            (
                f"Auto-rolled-back {skill_name} after "
                f"{lifecycle.consecutive_failures} consecutive failures: {tool_run.error}"
            ),
            installed_skill_name=skill_name,
            relevant=True,
            canary_passed=False,
            canary_error=tool_run.error,
        )
        self._decisions.append(decision)
        await self._publish_retraction(artifact, decision)
        await self._publish_adoption(artifact, decision)
        # The archived skill is gone from disk — refresh so the dashboard drops it.
        await self._refresh_skill_inventory()
        judgment = valkyrie_judgment_proposed(
            environment_id=self.identity.environment_id,
            valkyrie_id=self.identity.valkyrie_id,
            attention_tier="present",
            recommended_action="rebuild_rolled_back_capability",
            authority_boundary=_authority_boundary(self.identity.autonomy_mode),
            confidence=0.3,
            operational_state="adopted_learning_regressed",
            rationale=decision.rationale,
            signal_refs=[signal.signal_id],
            evidence=[
                {
                    "skill_name": skill_name,
                    "capability_name": capability,
                    "consecutive_failures": lifecycle.consecutive_failures,
                    "failure_count": lifecycle.failure_count,
                    "run_count": lifecycle.run_count,
                    "tool_error": tool_run.error,
                    "tool_stderr": tool_run.stderr[-DEFAULT_ROLLBACK_STDERR_EVIDENCE_CHARS:],
                    "learning_source": source or "resident_skill_registry",
                    "restored_artifact_id": restored_artifact_id,
                    "restore_detail": restore_detail,
                }
            ],
            correlation_ids={"root": signal.signal_id},
            source=self._source,
            correlation_id=signal.signal_id,
        )
        await self._publisher.publish(judgment)
        return {
            "signalId": signal.signal_id,
            "capabilityName": capability,
            "decision": "adopted_learning_rolled_back",
            "usedAdoptedLearning": True,
            "skillName": skill_name,
            "judgmentEventId": judgment.event_id,
            "toolResult": {"error": tool_run.error},
            "consecutiveFailures": lifecycle.consecutive_failures,
            "restoredArtifactId": restored_artifact_id,
        }

    def _prune_tool_venv(self, skill_name: str) -> None:
        """Best-effort: a rolled-back tool's dependency venv leaves with it."""
        if self._tools_dir is None:
            return
        from ravn.valkyrie_evolution.tool_runtime import remove_tool_venv  # noqa: PLC0415

        try:
            remove_tool_venv(
                venvs_dir=learned_tool_venvs_dir(self._tools_dir.parent),
                tool_name=skill_name,
            )
        except Exception as exc:  # noqa: BLE001 — venv GC must never break a rollback
            logger.warning("venv prune failed for %s: %s", skill_name, exc)

    async def _attempt_restore_of_superseded(
        self,
        skill_name: str,
        signal: OperationalSignal,
    ) -> tuple[str, str]:
        """Try to reinstall the version a rolled-back learned tool superseded.

        Returns ``(restored_artifact_id, detail)``. The id is empty when the
        rolled-back skill has no persisted learned-tool envelope (a plain
        skill), no ``supersedes`` link (a first build — behavior is exactly
        the historical archive-and-rebuild), the predecessor file is gone, or
        the predecessor did not clear the install gate. Restore goes through
        the one review/canary pipeline like any other install — no bypass.
        """
        if self._tools_dir is None:
            return "", ""
        _code_dir, artifacts_dir = learned_tool_storage(self._tools_dir.parent)
        current_path = learned_tool_artifact_path(artifacts_dir, skill_name)
        if not current_path.is_file():
            return "", ""
        current = read_learned_tool_artifact(current_path)
        if not current.supersedes:
            return "", ""
        predecessor_path = superseded_artifact_path(
            artifacts_dir,
            skill_name,
            current.supersedes,
        )
        if not predecessor_path.is_file():
            return "", f"superseded artifact {current.supersedes} is no longer on disk"

        predecessor = read_learned_tool_artifact(predecessor_path)
        restore_artifact = ResidentLearningArtifact(
            learning_id=predecessor.artifact_id,
            title=predecessor.manifest.name,
            summary=(f"Restore {predecessor.artifact_id} after rollback of {current.artifact_id}"),
            content="",
            artifact_type="agent_tool",
            scope="environment",
            confidence=0.0,
            source_environment_id=self.identity.environment_id,
            promotion_id=predecessor.artifact_id,
            domain=self.identity.domain,
            redaction_status="redacted",
            tool_code=predecessor.tool_code,
            tool_entry_point=predecessor.manifest.entry_point,
            learned_tool_manifest=predecessor.manifest.to_dict(),
            test_code=predecessor.test_code,
            requirements=list(predecessor.requirements),
            supersedes=predecessor.supersedes,
            correlation_id=signal.signal_id,
            command_action="restore_superseded_version",
        )
        decision = await self._review_canary_install(restore_artifact, signal=signal)
        if decision.action != "adopted":
            return (
                "",
                f"predecessor {predecessor.artifact_id} was not restored: {decision.rationale}",
            )
        return predecessor.artifact_id, decision.rationale

    async def _run_skill_tool(
        self,
        skill: Any,
        signal: OperationalSignal,
    ) -> ToolRunResult | None:
        """Execute the skill's installed tool implementation, if one exists."""
        if self._tools_dir is None:
            return None
        tool_path = tool_path_for_skill(self._tools_dir, str(skill.name))
        payload = {
            "signal_id": signal.signal_id,
            "event_type": signal.event_type,
            "severity": signal.severity,
            "summary": signal.summary,
            "payload": signal.payload,
        }
        if tool_path.is_file():
            return await run_tool(
                tool_path,
                payload,
                entry_point=_tool_entry_point_from_content(str(skill.content)),
                timeout_seconds=self._tool_timeout_seconds,
            )

        code_dir, artifacts_dir = learned_tool_storage(self._tools_dir.parent)
        artifact_path = learned_tool_artifact_path(artifacts_dir, str(skill.name))
        if not artifact_path.is_file() or self._learned_tool_runner is None:
            return None
        artifact = read_learned_tool_artifact(artifact_path)
        # Agent tools declare their own input schema. Surface the operational
        # payload at the top level while retaining the signal envelope for tools
        # that need event metadata.
        learned_payload = {**payload, **signal.payload}
        return await self._learned_tool_runner.run(
            learned_tool_path(code_dir, artifact.manifest.name),
            learned_payload,
            entry_point=artifact.manifest.entry_point,
            timeout_seconds=self._tool_timeout_seconds,
            requirements=artifact.requirements,
            declared_reach=artifact.manifest.declared_reach,
        )

    async def _handle_learning_event(self, event: SleipnirEvent) -> None:
        if event.event_type == registry.ODIN_REVIEW_DECIDED:
            await self._handle_review_decision(event)
            return
        artifact = _artifact_from_event(event)
        if _is_retraction_event(event):
            decision = await self.retract(artifact)
        else:
            decision = await self.evaluate_and_apply(artifact)
        self._decisions.append(decision)

    async def _handle_review_decision(self, event: SleipnirEvent) -> None:
        """Apply one decided ReviewItem — the only operator command channel.

        Every kind of human decision arrives here: autonomy changes, held
        build approvals, skill promotions, flock-learning verdicts, and
        court escalations. The resident applies it, syncs its outbox, and
        confirms with ``odin.review.resolved``.
        """
        try:
            item = ReviewItem.from_payload(event.payload)
        except (ValueError, TypeError) as exc:
            logger.warning("resident_learning: ignoring malformed review decision: %s", exc)
            return
        if not item_targets(
            item,
            valkyrie_id=self.identity.valkyrie_id,
            environment_id=self.identity.environment_id,
            flock_ids=self.identity.flock_ids,
        ):
            return
        if item.status not in {ReviewStatus.APPROVED.value, ReviewStatus.REJECTED.value}:
            logger.warning(
                "resident_learning: review decision for %s has undecided status %r",
                item.item_id,
                item.status,
            )
            return
        store = self._review_requester.store if self._review_requester is not None else None
        if store is not None:
            try:
                local = store.get(item.item_id)
            except ValueError:
                local = None
            if local is not None and local.apply_outcome:
                return
        item.causation_id = event.event_id
        if not item.correlation_id:
            item.correlation_id = event.correlation_id or event.event_id

        try:
            outcome, detail, decision = await self._apply_review_decision(item)
        except Exception as exc:  # noqa: BLE001 — the operator must see the failure
            logger.exception("resident_learning: review apply failed for %s", item.item_id)
            outcome, detail, decision = "apply_failed", f"{type(exc).__name__}: {exc}", None
        if outcome == "ignored":
            return
        if decision is not None:
            self._decisions.append(decision)
        item.resolve(outcome=outcome, detail=detail)
        if self._review_requester is not None:
            self._review_requester.record_decision(item)
        await self._publisher.publish(review_resolved_event(item, source=self._source))

    async def _apply_review_decision(
        self,
        item: ReviewItem,
    ) -> tuple[str, str, ResidentLearningDecision | None]:
        kind = item.kind
        if kind == ReviewKind.AUTONOMY_CHANGE.value:
            return await self._apply_autonomy_review(item)
        if kind == ReviewKind.SKILL_PROMOTION.value:
            return await self._apply_promotion_review(item)
        if kind == ReviewKind.COURT_ESCALATION.value:
            return await self._apply_court_review(item)
        if kind in {ReviewKind.EVOLUTION_BUILD.value, ReviewKind.FLOCK_LEARNING.value}:
            return await self._apply_learning_review(item)
        if kind == ReviewKind.MORNING_BRIEF.value:
            # Briefs are informational; the operator verdict is the whole action.
            return "applied", "brief acknowledged", None
        return "apply_failed", f"unknown review kind: {kind!r}", None

    async def _apply_autonomy_review(
        self,
        item: ReviewItem,
    ) -> tuple[str, str, ResidentLearningDecision | None]:
        mode = str(item.evidence.get("mode") or "").lower()
        if item.status == ReviewStatus.REJECTED.value:
            return "applied", f"autonomy change to {mode!r} was rejected", None
        if mode not in CANONICAL_AUTONOMY_MODES:
            return (
                "apply_failed",
                f"unknown autonomy mode {mode!r} (known: {sorted(CANONICAL_AUTONOMY_MODES)})",
                None,
            )
        previous = self.identity.autonomy_mode
        self.identity = replace(self.identity, autonomy_mode=mode)
        await self._publisher.publish(
            SleipnirEvent(
                event_type=registry.VALKYRIE_STATE_UPDATED,
                source=self._source,
                payload={
                    "environment_id": self.identity.environment_id,
                    "valkyrie_id": self.identity.valkyrie_id,
                    "autonomy_mode": mode,
                    "previous_autonomy_mode": previous,
                    "operator_id": item.decided_by,
                    "reason": item.decision_reason,
                    "review_item_id": item.item_id,
                },
                summary=(f"{self.identity.valkyrie_id} autonomy changed {previous} -> {mode}"),
                urgency=0.4,
                domain="infrastructure",
                timestamp=datetime.now(UTC),
                correlation_id=item.correlation_id or item.item_id,
                causation_id=item.causation_id or None,
            )
        )
        return "applied", f"autonomy {previous} -> {mode}", None

    async def _apply_promotion_review(
        self,
        item: ReviewItem,
    ) -> tuple[str, str, ResidentLearningDecision | None]:
        name = str(item.evidence.get("skill_name") or item.title)
        if item.status == ReviewStatus.REJECTED.value:
            return "applied", f"promotion of {name} declined by {item.decided_by}", None
        to_scope = _normalise_scope(str(item.evidence.get("to_scope") or "environment"))
        await self._skills.promote(
            name,
            scope=to_scope,
            environment_id=self.identity.environment_id,
            domain=self.identity.domain,
        )
        event = learning_promoted(
            environment_id=self.identity.environment_id,
            learning_id=str(item.evidence.get("learning_id") or f"skill:{name}"),
            from_scope=str(item.evidence.get("from_scope") or "private"),
            to_scope=to_scope,
            summary=f"Operator {item.decided_by} approved {item.requested_action} of {name}",
            source=self._source,
            confidence=float(item.evidence.get("confidence") or 0.0),
            correlation_id=item.correlation_id or item.item_id,
        )
        # Peers treat a demotion out of their scope as a retraction; the
        # action_kind marker is what _is_retraction_event keys on.
        event.payload["action_kind"] = item.requested_action
        await self._publisher.publish(event)
        return "applied", f"{item.requested_action}d {name} to {to_scope}", None

    async def _apply_court_review(
        self,
        item: ReviewItem,
    ) -> tuple[str, str, ResidentLearningDecision | None]:
        action = item.evidence.get("action")
        action = dict(action) if isinstance(action, dict) else {}
        if item.status == ReviewStatus.REJECTED.value:
            await self._publisher.publish(
                SleipnirEvent(
                    event_type=registry.ATTENTION_SUPPRESSED,
                    source=self._source,
                    payload={
                        "environment_id": self.identity.environment_id,
                        "valkyrie_id": self.identity.valkyrie_id,
                        "audit_ref": str(item.evidence.get("audit_ref") or ""),
                        "review_item_id": item.item_id,
                        "operator_id": item.decided_by,
                        "reason": item.decision_reason or "operator rejected court escalation",
                    },
                    summary=f"operator suppressed court escalation {item.title}",
                    urgency=0.3,
                    domain="infrastructure",
                    timestamp=datetime.now(UTC),
                    correlation_id=item.correlation_id or item.item_id,
                    causation_id=item.causation_id or None,
                )
            )
            return "applied", "court escalation suppressed", None
        target = action.get("target")
        await self._publisher.publish(
            valkyrie_action_requested(
                environment_id=item.environment_id or self.identity.environment_id,
                valkyrie_id=str(
                    action.get("valkyrie_id") or item.valkyrie_id or self.identity.valkyrie_id
                ),
                action_id=str(action.get("action_id") or item.item_id),
                capability=str(action.get("capability") or "unknown"),
                authority_boundary="operator_approved",
                target=target if isinstance(target, dict) else {},
                dry_run=bool(action.get("dry_run", False)),
                source=self._source,
                correlation_id=item.correlation_id or item.item_id,
                causation_id=item.causation_id or None,
            )
        )
        return "applied", "operator-approved action requested", None

    async def _apply_learning_review(
        self,
        item: ReviewItem,
    ) -> tuple[str, str, ResidentLearningDecision | None]:
        artifact = self._artifact_from_review_item(item)
        relevant, reason = self._policy.evaluate(artifact, self.identity)
        if not relevant and item.audience != "valkyrie":
            # Flock-broadcast commands reach every resident; the irrelevant
            # ones stay quiet instead of flooding the bus with rejections.
            return "ignored", reason, None
        feedback = item.evidence.get("feedback")
        feedback = dict(feedback) if isinstance(feedback, dict) else {}
        if feedback:
            # Whatever the lifecycle effect, the verdict lands in the stored
            # learning so future dreams can weigh operator judgment.
            self._record_operator_feedback(artifact, feedback)
        if item.requested_action == "feedback":
            return await self._apply_learning_feedback(artifact, feedback)
        if item.requested_action == "revise":
            return await self._apply_learning_revision(item, artifact)
        if item.status == ReviewStatus.REJECTED.value:
            decision = ResidentLearningDecision(
                "rejected",
                f"operator {item.decided_by} rejected {item.item_id}: "
                f"{item.decision_reason or 'no reason given'}",
                relevant=True,
            )
            await self._publish_adoption(artifact, decision)
            return "applied", decision.rationale, decision
        if item.requested_action in {"retract", "rollback"}:
            decision = await self.retract(artifact)
            return "applied", decision.rationale, decision
        if item.requested_action == "canary":
            _request, build = review_inputs(artifact, self.identity)
            canary = await self._canary_artifact(build, artifact.canary_sample)
            if canary.ok:
                return "applied", "canary passed", None
            return "apply_failed", f"canary failed: {canary.error}", None
        decision = await self.evaluate_and_apply(artifact, operator_item=item)
        outcome = "applied" if decision.action == "adopted" else "apply_failed"
        return outcome, decision.rationale, decision

    def _artifact_from_review_item(self, item: ReviewItem) -> ResidentLearningArtifact:
        data = item.evidence.get("artifact")
        data = dict(data) if isinstance(data, dict) else {}
        known = {f.name for f in fields(ResidentLearningArtifact)}
        kwargs = {key: value for key, value in data.items() if key in known}
        kwargs.setdefault("learning_id", item.item_id)
        kwargs.setdefault("title", item.title)
        kwargs.setdefault("summary", item.summary)
        kwargs.setdefault("content", "")
        kwargs.setdefault("artifact_type", "ravn_skill_tool")
        kwargs.setdefault(
            "scope",
            "flock" if item.kind == ReviewKind.FLOCK_LEARNING.value else "environment",
        )
        kwargs["confidence"] = float(kwargs.get("confidence") or 0.0)
        canary_sample = kwargs.get("canary_sample")
        kwargs["canary_sample"] = dict(canary_sample) if isinstance(canary_sample, dict) else {}
        kwargs["operator_command"] = True
        kwargs["command_action"] = item.requested_action
        if not kwargs.get("correlation_id"):
            kwargs["correlation_id"] = item.correlation_id or item.item_id
        kwargs["causation_id"] = item.causation_id or ""
        return ResidentLearningArtifact(**kwargs)

    def _learning_record_for(
        self,
        artifact: ResidentLearningArtifact,
    ) -> FlockLearningRecord | None:
        """The durable ledger record for one learning, created when absent."""
        if self._learning_store is None:
            return None
        try:
            return self._learning_store.get(artifact.learning_id)
        except ValueError:
            return FlockLearningRecord(
                exchange_id=artifact.learning_id,
                candidate=_candidate_from_artifact(artifact),
            )

    def _record_operator_feedback(
        self,
        artifact: ResidentLearningArtifact,
        feedback: dict[str, Any],
    ) -> None:
        """Persist the operator verdict on the stored learning."""
        record = self._learning_record_for(artifact)
        if record is None:
            logger.warning(
                "resident_learning: no learning store configured; operator "
                "feedback on %s is not durable",
                artifact.learning_id,
            )
            return
        record.operator_feedback = {
            "verdict": str(feedback.get("verdict") or ""),
            "reason": str(feedback.get("reason") or ""),
            "operator_id": str(feedback.get("operatorId") or feedback.get("operator_id") or ""),
            "recorded_at": str(
                feedback.get("recordedAt")
                or feedback.get("recorded_at")
                or datetime.now(UTC).isoformat()
            ),
        }
        self._learning_store.save(record)

    async def _apply_learning_feedback(
        self,
        artifact: ResidentLearningArtifact,
        feedback: dict[str, Any],
    ) -> tuple[str, str, ResidentLearningDecision | None]:
        """Apply a pure feedback verdict: reinforcement or local rejection.

        ``useful``/``good_action`` raise the stored learning's confidence by
        the configured bump; ``bad_action`` on a not-yet-adopted learning is
        a durable local rejection. Lifecycle-changing verdicts (rollback,
        dismissal, tier moves) arrive as their existing dedicated commands.
        """
        verdict = str(feedback.get("verdict") or "")
        if verdict in {"useful", "good_action"}:
            record = self._learning_record_for(artifact)
            if record is None:
                return (
                    "apply_failed",
                    "no learning store configured; operator reinforcement cannot be applied",
                    None,
                )
            reinforced = min(
                record.candidate.confidence + self._feedback_confidence_bump,
                MAX_LEARNING_CONFIDENCE,
            )
            record.candidate = replace(record.candidate, confidence=reinforced)
            self._learning_store.save(record)
            detail = f"operator feedback {verdict}: confidence reinforced to {reinforced:.2f}"
            await self._publish_learning_update(artifact, record, rationale=detail)
            return "applied", detail, None
        if verdict == "bad_action":
            decision = ResidentLearningDecision(
                "rejected",
                (f"operator feedback bad_action: {feedback.get('reason') or 'no reason given'}"),
                relevant=True,
            )
            await self._publish_adoption(artifact, decision)
            return "applied", decision.rationale, decision
        return "applied", f"operator feedback {verdict or 'unknown'} recorded", None

    async def _apply_learning_revision(
        self,
        item: ReviewItem,
        artifact: ResidentLearningArtifact,
    ) -> tuple[str, str, ResidentLearningDecision | None]:
        """Apply an operator revision command.

        Candidate revisions are edited in place with a revision marker;
        revisions of adopted learnings arrive as superseding candidates that
        re-enter the one review/canary install pipeline.
        """
        revision = item.evidence.get("revision")
        revision = dict(revision) if isinstance(revision, dict) else {}
        superseded_id = str(revision.get("superseded_id") or artifact.supersedes or "")
        if superseded_id:
            decision = await self.evaluate_and_apply(artifact)
            outcome = "applied" if decision.action in {"adopted", "held"} else "apply_failed"
            return outcome, decision.rationale, decision

        record = self._learning_record_for(artifact)
        if record is None:
            return (
                "apply_failed",
                "no learning store configured; candidate revision cannot be applied",
                None,
            )
        updates: dict[str, str] = {}
        if str(revision.get("title") or "").strip():
            updates["title"] = str(revision["title"])
        if str(revision.get("summary") or "").strip():
            updates["summary"] = str(revision["summary"])
        if str(revision.get("content") or "").strip():
            updates["content"] = str(revision["content"])
        if not updates:
            return "apply_failed", "revision command carried no edits", None
        record.candidate = replace(record.candidate, **updates)
        record.revision += 1
        self._learning_store.save(record)
        detail = f"revision {record.revision} applied to candidate {artifact.learning_id}"
        await self._publish_learning_update(artifact, record, rationale=detail)
        return "applied", detail, None

    async def _publish_learning_update(
        self,
        artifact: ResidentLearningArtifact,
        record: FlockLearningRecord,
        *,
        rationale: str,
    ) -> None:
        """Re-emit the learning update event so dashboard mirrors converge."""
        event = learning_adoption_recorded(
            environment_id=self.identity.environment_id,
            learning_id=artifact.learning_id,
            promotion_id=artifact.promotion_id or artifact.learning_id,
            action="updated",
            rationale=rationale,
            source=self._source,
            correlation_id=artifact.correlation_id or artifact.learning_id,
            causation_id=artifact.causation_id,
        )
        event.payload.update(
            {
                "resident_valkyrie_id": self.identity.valkyrie_id,
                "ack_kind": "resident_learning",
                "artifact_type": artifact.artifact_type,
                "scope": _normalise_scope(artifact.scope),
                "title": record.candidate.title,
                "summary_text": record.candidate.summary,
                "confidence": record.candidate.confidence,
                "repetition": record.repetition,
                "revision": record.revision,
                "feedback": dict(record.operator_feedback),
                "additional_nats_subjects": [
                    _flock_nats_subject(self.identity, "learning.adoption.recorded")
                ],
            }
        )
        await self._publisher.publish(event)

    async def evaluate_and_apply(
        self,
        artifact: ResidentLearningArtifact,
        *,
        operator_item: ReviewItem | None = None,
    ) -> ResidentLearningDecision:
        if self._previously_declined(artifact):
            return ResidentLearningDecision(
                "ignored",
                f"learning {artifact.learning_id} was previously declined here; "
                "an operator command is required to re-evaluate it",
                relevant=True,
            )

        relevant, reason = self._policy.evaluate(artifact, self.identity)
        if not relevant:
            decision = ResidentLearningDecision("rejected", reason, relevant=False)
            await self._publish_adoption(artifact, decision)
            return decision

        return await self._review_canary_install(artifact, operator_item=operator_item)

    async def _review_canary_install(
        self,
        artifact: ResidentLearningArtifact,
        *,
        build: BuildResult | None = None,
        request: EvolutionRequest | None = None,
        signal: OperationalSignal | None = None,
        operator_item: ReviewItem | None = None,
    ) -> ResidentLearningDecision:
        """The one install pipeline: review, gate, canary, install, announce.

        Self-built artifacts, peer flock learnings, and operator-approved
        review items all flow through here. Blocking findings always reject;
        a ``needs_approval`` outcome holds the build behind a review request
        unless an operator decision is what brought us here.
        """
        if request is None or build is None:
            request, build = review_inputs(artifact, self.identity)
        self_built = artifact.source_valkyrie_id == self.identity.valkyrie_id
        review = await self._reviewer.review(
            request=request,
            build=build,
            autonomy_mode=self.identity.autonomy_mode,
        )
        await self._publish_odin_decision(artifact, review)

        allowed = review_allows_install(review, self.identity.autonomy_mode)
        if not allowed and review.blocking_findings:
            decision = ResidentLearningDecision(
                "rejected",
                f"Odin review blocked install: {review.rationale}",
                review=review,
                relevant=True,
            )
            await self._publish_adoption(artifact, decision)
            return decision
        if not allowed and operator_item is None:
            decision = ResidentLearningDecision(
                "held",
                f"Held for operator review: {review.rationale}",
                review=review,
                relevant=True,
            )
            await self._publish_evolution_event(
                "valkyrie.evolution.held",
                f"Held resident skill {build.skill_name}",
                {
                    "skill_name": build.skill_name,
                    "learning_id": artifact.learning_id,
                    "held_kind": "self_build" if self_built else "peer_adoption",
                    "review_outcome": review.outcome,
                    "findings": list(review.findings),
                },
                artifact.correlation_id or artifact.learning_id,
                urgency=0.6,
            )
            await self._file_install_review(artifact, build, review, signal)
            return decision

        verify_rejection = await self._verify_peer_artifact(artifact, review)
        if verify_rejection is not None:
            return verify_rejection

        canary_payload = artifact.canary_sample or (
            dict(signal.payload) if signal is not None else {}
        )
        canary = await self._canary_artifact(build, canary_payload)
        if not canary.ok:
            if self_built and operator_item is None:
                await self._publish_evolution_event(
                    "valkyrie.evolution.held",
                    f"Held resident skill {build.skill_name}: canary failed",
                    {
                        "skill_name": build.skill_name,
                        "learning_id": artifact.learning_id,
                        "held_kind": "self_build",
                        "review_outcome": review.outcome,
                        "canary_passed": False,
                        "canary_error": canary.error,
                    },
                    artifact.correlation_id or artifact.learning_id,
                    urgency=0.6,
                )
                return ResidentLearningDecision(
                    "held",
                    f"Canary execution failed before install: {canary.error}",
                    review=review,
                    relevant=True,
                    canary_passed=False,
                    canary_error=canary.error,
                )
            decision = ResidentLearningDecision(
                "rejected",
                f"Canary execution failed before install: {canary.error}",
                review=review,
                relevant=True,
                canary_passed=False,
                canary_error=canary.error,
            )
            await self._publish_adoption(artifact, decision)
            return decision

        skill_name = await self._install_skill(artifact, build)
        if operator_item is not None:
            authorization_rationale = (
                f"operator {operator_item.decided_by} approved "
                f"{operator_item.item_id}: {review.rationale}"
            )
        else:
            authorization_rationale = _install_authorization_rationale(
                review,
                self.identity.autonomy_mode,
            )
        decision = ResidentLearningDecision(
            "adopted",
            f"Installed {skill_name}: {authorization_rationale}",
            installed_skill_name=skill_name,
            review=review,
            relevant=True,
            canary_passed=True,
        )
        await self._publish_activation(artifact, skill_name, review)
        await self._publish_adoption(artifact, decision)
        # Refresh the dashboard's inventory the moment a new skill lands.
        await self._refresh_skill_inventory()
        if self_built and artifact.flock_id:
            await self._publish_flock_learning_proposal(artifact, build, review)
        return decision

    async def _verify_peer_artifact(
        self,
        artifact: ResidentLearningArtifact,
        review: ReviewResult,
    ) -> ResidentLearningDecision | None:
        """Independently re-verify a peer artifact before install (P6.2).

        Never trust the teacher's own "it works": when a peer proposal carries
        a self-contained test module, it re-runs from scratch in a throwaway
        venv here — with the tool's declared requirements installed — before
        anything touches this resident. A failure is a durable rejection in
        the ledger (same shape as every other rejection, so NIU-1034 dedupe
        keeps working). Artifacts without test_code keep the canary-only path:
        old proposals are weaker evidence, not punishable offences. Self-built
        artifacts were already verified (and repaired) by build_tool.
        """
        if artifact.source_valkyrie_id == self.identity.valkyrie_id:
            return None
        if not artifact.test_code.strip() or not artifact.tool_code.strip():
            return None
        result = await asyncio.to_thread(
            verify_learned_tool_in_ephemeral_venv,
            tool_name=artifact.title or "learned_tool",
            tool_code=artifact.tool_code,
            test_code=artifact.test_code,
            requirements=list(artifact.requirements),
            entry_point=artifact.tool_entry_point or "run",
        )
        if result.ok:
            return None
        logs_tail = result.logs[-DEFAULT_VERIFY_LOG_EVIDENCE_CHARS:]
        decision = ResidentLearningDecision(
            "rejected",
            f"Peer re-verification failed before install: {logs_tail}",
            review=review,
            relevant=True,
        )
        await self._publish_adoption(artifact, decision)
        return decision

    async def _file_install_review(
        self,
        artifact: ResidentLearningArtifact,
        build: BuildResult,
        review: ReviewResult,
        signal: OperationalSignal | None,
    ) -> None:
        """File the held build/adoption as a ReviewItem for the operator."""
        if self._review_requester is None:
            logger.warning(
                "resident_learning: %s needs operator approval but no review "
                "requester is configured; the build stays held locally",
                build.skill_name,
            )
            return
        self_built = artifact.source_valkyrie_id == self.identity.valkyrie_id
        kind = ReviewKind.EVOLUTION_BUILD if self_built else ReviewKind.FLOCK_LEARNING
        capability = _capability_from_content(build.skill_content) or _slug(build.skill_name)
        dedupe_key = (
            self._evolution_dedupe_key(capability)
            if self_built
            else f"{kind.value}:{self.identity.environment_id}:{artifact.learning_id}"
        )
        safety_class = _safety_class_from_content(build.skill_content)
        item = ReviewItem.new(
            kind=kind.value,
            requested_action="install" if self_built else "adopt",
            environment_id=self.identity.environment_id,
            valkyrie_id=self.identity.valkyrie_id,
            title=build.skill_name,
            summary=build.description or artifact.summary,
            flock_id=artifact.flock_id,
            domain=artifact.domain or self.identity.domain,
            risk_class=risk_class_for_safety(safety_class),
            safety_class=safety_class,
            urgency=0.6,
            dedupe_key=dedupe_key,
            evidence={
                "artifact": asdict(artifact),
                "review": {
                    "outcome": review.outcome,
                    "rationale": review.rationale,
                    "findings": list(review.findings),
                },
                "build_evidence": dict(build.evidence),
                "learned_tool_manifest": dict(artifact.learned_tool_manifest),
                "signal_ids": [signal.signal_id] if signal is not None else [],
            },
            requested_by=self.identity.valkyrie_id,
            correlation_id=artifact.correlation_id or artifact.learning_id,
            causation_id=artifact.causation_id,
        )
        await self._review_requester.request(item)

    async def _canary_artifact(
        self,
        build: BuildResult,
        sample_payload: dict[str, Any],
    ) -> ToolRunResult:
        """Exercise an artifact's tool implementation before ACKing adoption.

        Instruction-only skills were already validated structurally by the
        reviewer, so they canary as a pass.  Tool implementations execute once
        in the sandbox against the sample payload carried in the proposal.
        """
        if not build.has_tool_implementation:
            return ToolRunResult(ok=True)
        import tempfile  # noqa: PLC0415

        with tempfile.TemporaryDirectory(prefix="valkyrie-canary-") as canary_dir:
            if build.artifact_type == "agent_tool":
                artifact = _learned_tool_artifact_from_build(build)
                tool_path = write_learned_tool(tools_dir=canary_dir, artifact=artifact)
                return await run_tool(
                    tool_path,
                    sample_payload,
                    entry_point=artifact.manifest.entry_point,
                    timeout_seconds=self._tool_timeout_seconds,
                )
            tool_path = write_tool(
                tools_dir=canary_dir,
                skill_name=build.skill_name or "canary",
                tool_code=build.tool_code,
            )
            return await run_tool(
                tool_path,
                {"payload": sample_payload},
                entry_point=build.tool_entry_point or "run",
                timeout_seconds=self._tool_timeout_seconds,
            )

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
            # The archived skill is gone from disk — refresh so the dashboard drops it.
            await self._refresh_skill_inventory()
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
        skill_name = build.skill_name
        if build.artifact_type == "agent_tool":
            skill_name = _install_learned_tool_artifact(
                tools_dir=self._tools_dir,
                artifact=artifact,
                build=build,
            )

        await self._record_installed_skill(artifact, build, skill_name=skill_name)
        if (
            build.artifact_type != "agent_tool"
            and build.has_tool_implementation
            and self._tools_dir is not None
        ):
            write_tool(
                tools_dir=self._tools_dir,
                skill_name=skill_name,
                tool_code=build.tool_code,
            )
        return skill_name

    async def _record_installed_skill(
        self,
        artifact: ResidentLearningArtifact,
        build: BuildResult,
        *,
        skill_name: str | None = None,
    ) -> None:
        skill_name = skill_name or build.skill_name
        scope = _normalise_scope(artifact.scope)
        try:
            await self._skills.create(
                name=skill_name,
                content=build.skill_content,
                description=build.description,
                scope=scope,
                environment_id=self.identity.environment_id,
                domain=self.identity.domain or artifact.domain,
                source=f"flock-learning:{artifact.learning_id}",
                source_environment_id=artifact.source_environment_id,
                source_valkyrie_id=artifact.source_valkyrie_id,
                action_safety_class=_safety_class_from_content(build.skill_content),
            )
        except ValueError:
            await self._skills.update(
                name=skill_name,
                content=build.skill_content,
                description=build.description,
                source=f"flock-learning:{artifact.learning_id}",
                source_environment_id=artifact.source_environment_id,
                source_valkyrie_id=artifact.source_valkyrie_id,
            )
            await self._skills.promote(
                skill_name,
                scope=scope,
                environment_id=self.identity.environment_id,
                domain=self.identity.domain or artifact.domain,
            )

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
        capabilities = [capability]
        if capability.startswith("inspect.kubernetes."):
            capabilities.append(capability.replace("inspect.kubernetes.", "inspect.", 1))
        markers = [f"capability: {candidate}" for candidate in capabilities]
        for row in rows:
            skill = row["skill"]
            if any(marker in str(skill.get("content", "")) for marker in markers):
                return type("RunnableSkill", (), skill)()

        # Agent-tool artifacts installed before they were represented in the
        # managed skill registry still need to be discoverable and metered.
        # Register the durable envelope lazily, then use it for this signal.
        for record in self._iter_learned_tool_inventory():
            if not any(marker in record["skill_content"] for marker in markers):
                continue
            try:
                skill = await self._skills.create(
                    name=record["skill_name"],
                    content=record["skill_content"],
                    description=record["summary_text"],
                    scope=_normalise_scope(record["learning_scope"]),
                    environment_id=self.identity.environment_id,
                    domain=self.identity.domain,
                    source=record["learning_source"],
                    source_environment_id=record["source_environment_id"],
                    source_valkyrie_id=record["source_valkyrie_id"],
                    action_safety_class=_safety_class_from_content(record["skill_content"]),
                )
            except ValueError:
                continue
            return skill
        return None

    async def _publish_odin_decision(
        self,
        artifact: ResidentLearningArtifact,
        review: ReviewResult,
    ) -> None:
        decision = (
            "learning_adoption_allowed"
            if review_allows_install(review, self.identity.autonomy_mode)
            else "learning_adoption_blocked"
        )
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
        event.payload["install_authorization"] = _install_authorization(
            review,
            self.identity.autonomy_mode,
        )
        event.payload["install_authorization_rationale"] = _install_authorization_rationale(
            review,
            self.identity.autonomy_mode,
        )
        await self._publisher.publish(event)

    async def _publish_activation(
        self,
        artifact: ResidentLearningArtifact,
        skill_name: str,
        review: ReviewResult,
    ) -> None:
        await self._publisher.publish(
            SleipnirEvent(
                event_type=EVOLUTION_ACTIVATED_EVENT,
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
                    # Full artifact body so the dashboard can show the skill an
                    # operator sees referenced in judgment evidence without
                    # reaching into the resident's disk.
                    "skill_content": artifact.content,
                    "learned_tool_manifest": dict(artifact.learned_tool_manifest or {}),
                    "tool_code": artifact.tool_code,
                    "test_code": artifact.test_code,
                    "requirements": list(artifact.requirements),
                    "summary_text": artifact.summary,
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
                event_type=EVOLUTION_ROLLED_BACK_EVENT,
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
            flock_learning_proposed_event(
                source=self._source,
                learning_id=artifact.learning_id,
                title=artifact.title,
                summary=artifact.summary,
                flock_id=artifact.flock_id,
                artifact_type=artifact.artifact_type,
                content=artifact.content,
                domain=artifact.domain,
                environment_id=artifact.source_environment_id,
                source_valkyrie_id=artifact.source_valkyrie_id,
                confidence=artifact.confidence,
                redaction_status=artifact.redaction_status,
                promotion_id=artifact.promotion_id,
                artifact_path=artifact.artifact_path,
                tool_code=build.tool_code,
                tool_entry_point=build.tool_entry_point,
                learned_tool_manifest=artifact.learned_tool_manifest,
                test_code=artifact.test_code,
                requirements=list(artifact.requirements),
                canary_sample=artifact.canary_sample,
                review_outcome=review.outcome,
                builder_evidence=build.evidence,
                subject_domain=self.identity.domain or self.identity.environment_type,
                correlation_id=artifact.correlation_id,
                causation_id=artifact.causation_id,
            )
        )

    def _previously_declined(self, artifact: ResidentLearningArtifact) -> bool:
        """True when this environment durably declined the learning before.

        Keeps rejected/rolled-back learnings from reappearing across restarts
        (NIU-1034). Operator commands always bypass the ledger.
        """
        if artifact.operator_command:
            return False
        return self._is_declined(artifact.learning_id)

    def _is_declined(self, learning_id: str) -> bool:
        if self._learning_store is None:
            return False
        try:
            record = self._learning_store.get(learning_id)
        except ValueError:
            return False
        decision = record.decision_for(self.identity.environment_id)
        return decision is not None and decision.action in {
            "rejected",
            "overridden",
            "rolled_back",
        }

    def _persist_learning_decision(
        self,
        artifact: ResidentLearningArtifact,
        decision: ResidentLearningDecision,
    ) -> FlockLearningRecord | None:
        """Record the decision in the durable flock-learning ledger.

        A learning re-produced under a fresh id (a repeated dream) folds
        into the existing non-rejected record by fingerprint: repetition
        counts up instead of duplicate records piling up.
        """
        if self._learning_store is None:
            return None
        if decision.action not in {"adopted", "rejected", "rolled_back"}:
            return None
        if decision.action == "rejected" and not decision.relevant:
            # Irrelevant learnings (wrong flock/domain) stay out of the ledger;
            # they were never candidates for this environment.
            return None
        try:
            record = self._learning_store.get(artifact.learning_id)
        except ValueError:
            # Only adoptions fold: a rejection folded onto the fingerprint
            # match would silently decline the original adopted learning.
            record = (
                self._learning_store.fold_duplicate(_candidate_from_artifact(artifact))
                if decision.action == "adopted"
                else None
            )
        if record is None:
            record = FlockLearningRecord(
                exchange_id=artifact.learning_id,
                candidate=_candidate_from_artifact(artifact),
            )
        record.peer_decisions.append(
            FlockPeerDecision(
                environment_id=self.identity.environment_id,
                action=decision.action,
                rationale=decision.rationale,
                canary_passed=decision.canary_passed,
            )
        )
        if decision.action == "adopted":
            record.status = "adopted"
            if self.identity.environment_id not in record.active_environment_ids:
                record.active_environment_ids.append(self.identity.environment_id)
        elif decision.action == "rolled_back":
            record.active_environment_ids = [
                environment_id
                for environment_id in record.active_environment_ids
                if environment_id != self.identity.environment_id
            ]
        return self._learning_store.save(record)

    async def _publish_adoption(
        self,
        artifact: ResidentLearningArtifact,
        decision: ResidentLearningDecision,
    ) -> None:
        record = self._persist_learning_decision(artifact, decision)
        event = learning_adoption_recorded(
            environment_id=self.identity.environment_id,
            learning_id=artifact.learning_id,
            promotion_id=artifact.promotion_id or artifact.learning_id,
            action=_adoption_event_action(decision.action),
            rationale=decision.rationale,
            canary_passed=decision.canary_passed,
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
                "canary_error": decision.canary_error,
                "artifact_type": artifact.artifact_type,
                "scope": _normalise_scope(artifact.scope),
                "ack_kind": "resident_learning",
                "source_environment_id": artifact.source_environment_id,
                "source_valkyrie_id": artifact.source_valkyrie_id,
                "command_action": artifact.command_action,
                "repetition": record.repetition if record is not None else 1,
                "additional_nats_subjects": [
                    _flock_nats_subject(self.identity, "learning.adoption.recorded")
                ],
            }
        )
        await self._publisher.publish(event)


def _candidate_from_artifact(artifact: ResidentLearningArtifact) -> FlockLearningCandidate:
    """Project a resident learning artifact into the durable ledger candidate."""
    return FlockLearningCandidate(
        learning_id=artifact.learning_id,
        title=artifact.title,
        # Resident artifacts are always skill+tool pairs in ledger vocabulary.
        artifact_type="tool_skill",
        summary=artifact.summary,
        content=artifact.content,
        flock_id=artifact.flock_id,
        source_environment_id=artifact.source_environment_id,
        source_valkyrie_id=artifact.source_valkyrie_id,
        confidence=artifact.confidence,
        redaction_status=artifact.redaction_status or "redacted",
        promotion_id=artifact.promotion_id,
        metadata={
            "domain": artifact.domain,
            "artifact_type": artifact.artifact_type,
            "scope": _normalise_scope(artifact.scope),
            "supersedes": artifact.supersedes,
            "learned_tool_manifest": dict(artifact.learned_tool_manifest),
        },
    )


def _artifact_from_event(event: SleipnirEvent) -> ResidentLearningArtifact:
    payload = event.payload
    event_type = event.event_type
    if event_type == registry.FLOCK_LEARNING_PROPOSED:
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
        tool_code=str(payload.get("tool_code") or ""),
        tool_entry_point=str(payload.get("tool_entry_point") or "run"),
        learned_tool_manifest=(
            dict(payload["learned_tool_manifest"])
            if isinstance(payload.get("learned_tool_manifest"), dict)
            else {}
        ),
        test_code=str(payload.get("test_code") or ""),
        requirements=[str(item) for item in list(payload.get("requirements") or [])],
        canary_sample=(
            dict(payload["canary_sample"]) if isinstance(payload.get("canary_sample"), dict) else {}
        ),
        causation_id=event.event_id,
        correlation_id=event.correlation_id or event.event_id,
        operator_command=bool(payload.get("operator_id") or payload.get("action_kind")),
        command_action=str(payload.get("action_kind") or payload.get("action") or ""),
    )


def review_inputs(
    artifact: ResidentLearningArtifact,
    identity: ResidentLearningIdentity,
) -> tuple[EvolutionRequest, BuildResult]:
    """Project any resident artifact into the one reviewer's (request, build).

    Shared by the resident install pipeline and build_tool authoring so both
    gate through the same PolicyCourtReviewer / AutonomyPolicy decision.
    """
    manifest: LearnedToolManifest | None = None
    if artifact.artifact_type == "agent_tool":
        manifest = LearnedToolManifest.from_dict(artifact.learned_tool_manifest)
    capability = (
        f"tool.{manifest.name}"
        if manifest is not None
        else _capability_from_content(artifact.content) or _slug(artifact.title)
    )
    safety_class = _artifact_safety_class(artifact, manifest)
    risk_boundaries = manifest_review_boundaries(manifest) if manifest is not None else []
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
            safety_class=safety_class,
        ),
        autonomy_mode=identity.autonomy_mode,
        target_scope=_normalise_scope(artifact.scope),
        risk_boundaries=risk_boundaries,
    )
    artifact_type = _build_artifact_type(artifact)
    skill_content = artifact.content
    if not skill_content and manifest is not None:
        skill_content = _agent_tool_content(artifact, manifest, safety_class)
    build = BuildResult(
        request_id=request.request_id,
        skill_name=artifact.title,
        skill_content=skill_content,
        description=artifact.summary,
        artifact_type=artifact_type,
        artifact_path=artifact.artifact_path,
        tool_code=artifact.tool_code,
        tool_entry_point=artifact.tool_entry_point,
        evidence={
            "learning_id": artifact.learning_id,
            "promotion_id": artifact.promotion_id,
            "source_environment_id": artifact.source_environment_id,
            "source_valkyrie_id": artifact.source_valkyrie_id,
            "scope": _normalise_scope(artifact.scope),
            "learned_tool_manifest": dict(artifact.learned_tool_manifest),
        },
    )
    return request, build


def _build_artifact_type(artifact: ResidentLearningArtifact) -> str:
    if artifact.artifact_type == "tool_skill":
        return "ravn_skill_tool"
    return artifact.artifact_type


def _learning_origin(
    *,
    source_valkyrie_id: str,
    resident_valkyrie_id: str,
    learning_source: str = "",
    learning_scope: str = "",
) -> str:
    """Classify installed learning without inventing missing historic provenance."""
    if source_valkyrie_id:
        return "local" if source_valkyrie_id == resident_valkyrie_id else "peer"
    if learning_source.startswith("flock-learning:") or learning_scope in {"flock", "shared"}:
        return "unknown"
    return "local"


def _agent_tool_content(
    artifact: ResidentLearningArtifact,
    manifest: LearnedToolManifest,
    safety_class: str,
) -> str:
    return (
        f"# learned tool: {manifest.name}\n"
        "metadata:\n"
        f"  capability: {manifest.name}\n"
        f"  source: {artifact.source_valkyrie_id or 'resident-agent'}\n"
        f"  safety_class: {safety_class}\n"
        f"  tool_entry_point: {manifest.entry_point}\n"
        f"  required_permission: {manifest.required_permission}\n\n"
        f"{manifest.description}\n"
    )


def _artifact_safety_class(
    artifact: ResidentLearningArtifact,
    manifest: LearnedToolManifest | None,
) -> str:
    if manifest is None:
        return _safety_class_from_content(artifact.content)
    return manifest_safety_class(manifest)


def _learned_tool_artifact_from_build(build: BuildResult) -> LearnedToolArtifact:
    manifest = LearnedToolManifest.from_dict(
        dict(build.evidence.get("learned_tool_manifest") or {})
    )
    return LearnedToolArtifact(
        artifact_id=str(build.evidence.get("learning_id") or f"learned-tool:{manifest.name}"),
        manifest=manifest,
        tool_code=build.tool_code,
        source_build_id=build.request_id,
        provenance=dict(build.evidence),
    )


def _install_learned_tool_artifact(
    *,
    tools_dir: Path | None,
    artifact: ResidentLearningArtifact,
    build: BuildResult,
) -> str:
    if tools_dir is None:
        raise ValueError("agent_tool install requires a resident tools directory")
    learned = replace(
        _learned_tool_artifact_from_build(build),
        # Contract v2 payload travels with the proposal, not the build
        # projection: persist it so re-verification and per-tool venv
        # provisioning survive the install.
        test_code=artifact.test_code,
        requirements=list(artifact.requirements),
        supersedes=artifact.supersedes,
    )
    # The resident tools dir lives under the state dir; learned tools live in
    # the one canonical location beside it, shared with build_tool authoring.
    code_dir, artifacts_dir = learned_tool_storage(tools_dir.parent)
    write_learned_tool(tools_dir=code_dir, artifact=learned)
    write_learned_tool_artifact(artifacts_dir=artifacts_dir, artifact=learned)
    return learned.manifest.name


def review_allows_install(review: ReviewResult, autonomy_mode: str) -> bool:
    """The reviewer is the authority: install only what it approved.

    YOLO semantics live inside the policy reviewer (which approves with the
    ``yolo_approved`` outcome); blocking findings are never overridable.
    """
    return review.approved and not review.blocking_findings


def _install_authorization(review: ReviewResult, autonomy_mode: str) -> str:
    if not review_allows_install(review, autonomy_mode):
        return "blocked"
    if review.outcome == "yolo_approved":
        return "yolo_override"
    return "odin_approved"


def _install_authorization_rationale(review: ReviewResult, autonomy_mode: str) -> str:
    authorization = _install_authorization(review, autonomy_mode)
    if authorization == "yolo_override":
        findings = "; ".join(review.findings) if review.findings else review.rationale
        return (
            "YOLO override installed after non-blocking Odin findings; "
            f"review outcome={review.outcome}; findings={findings}"
        )
    return review.rationale


def risk_class_for_safety(safety_class: str) -> str:
    """ReviewItem risk class for an artifact's safety class.

    Shared by every producer that files a review item (the resident install
    pipeline, build_tool) so operators see one consistent risk vocabulary.
    """
    return {
        "read_only": "low",
        "mutating": "high",
        "destructive": "critical",
    }.get(safety_class, "medium")


def _is_retraction_event(event: SleipnirEvent) -> bool:
    event_type = event.event_type
    payload = event.payload
    if event_type in {registry.FLOCK_LEARNING_REJECTED, registry.FLOCK_LEARNING_ROLLED_BACK}:
        return True
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


def _tool_entry_point_from_content(content: str) -> str:
    match = re.search(r"^metadata:\n(?:.*\n)*?\s*tool_entry_point:\s*([^\n]+)", content, re.M)
    return match.group(1).strip() if match else "run"


def _authority_boundary(autonomy_mode: str) -> str:
    return "yolo" if autonomy_mode.lower() == "yolo" else "human_review_required"


def flock_learning_proposed_event(
    *,
    source: str,
    learning_id: str,
    title: str,
    summary: str,
    flock_id: str,
    artifact_type: str,
    content: str,
    domain: str,
    environment_id: str,
    source_valkyrie_id: str,
    confidence: float,
    redaction_status: str,
    promotion_id: str,
    artifact_path: str = "",
    tool_code: str = "",
    tool_entry_point: str = "run",
    learned_tool_manifest: dict[str, Any] | None = None,
    test_code: str = "",
    requirements: list[str] | None = None,
    canary_sample: dict[str, Any] | None = None,
    review_outcome: str = "",
    builder_evidence: dict[str, Any] | None = None,
    subject_domain: str = "",
    correlation_id: str = "",
    causation_id: str = "",
) -> SleipnirEvent:
    """The one shape of a flock learning proposal.

    Every proposer (the resident install pipeline, build_tool) builds the
    event here so the payload contract — including the scoped flock NATS
    fan-out subject — cannot drift between publishers.
    """
    return SleipnirEvent(
        event_type=registry.FLOCK_LEARNING_PROPOSED,
        source=source,
        payload={
            "learning_id": learning_id,
            "title": title,
            "summary": summary,
            "flock_id": flock_id,
            "artifact_type": artifact_type,
            "content": content,
            "artifact_content": content,
            "status": "candidate",
            "domain": domain,
            "source_environment_id": environment_id,
            "source_valkyrie_id": source_valkyrie_id,
            "confidence": confidence,
            "redaction_status": redaction_status,
            "promotion_id": promotion_id,
            "artifact_path": artifact_path,
            "tool_code": tool_code,
            "tool_entry_point": tool_entry_point,
            "learned_tool_manifest": dict(learned_tool_manifest or {}),
            "test_code": test_code,
            "requirements": list(requirements or []),
            "canary_sample": dict(canary_sample or {}),
            "review_outcome": review_outcome,
            "builder_evidence": dict(builder_evidence or {}),
            "nats_subject": "ravn.environment.flock.learning.proposed",
            "additional_nats_subjects": [
                _scoped_flock_subject(
                    subject_domain or domain,
                    environment_id,
                    registry.FLOCK_LEARNING_PROPOSED,
                )
            ],
        },
        summary=f"flock.learning.proposed: {title}",
        urgency=0.2,
        domain="infrastructure",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or learning_id,
        causation_id=causation_id,
    )


def _scoped_flock_subject(domain: str, environment_id: str, event_type: str) -> str:
    return (
        f"flock.{_nats_token(domain or 'environment')}.{_nats_token(environment_id)}.{event_type}"
    )


def _flock_nats_subject(identity: ResidentLearningIdentity, event_type: str) -> str:
    return _scoped_flock_subject(
        identity.domain or identity.environment_type,
        identity.environment_id,
        event_type,
    )


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
