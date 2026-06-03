"""Environment contracts for long-running resident Valkyries.

The product term is ``Environment``.  ``realm`` remains a topology/internal
synonym through ``realm_id`` so existing Observatory and deployment vocabulary
continues to work without a parallel registry.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from observatory.contracts import ObservatoryFragment
from skuld.room_models import ParticipantMeta
from sleipnir.domain.events import SleipnirEvent

EnvironmentType = Literal[
    "k8s",
    "host.inbox",
    "printer.pi",
    "host",
    "service",
    "home_lab",
    "local",
]
SignalSourceKind = Literal[
    "kubernetes",
    "email",
    "printer_telemetry",
    "host",
    "webhook",
    "metrics",
    "logs",
]
OperationalHealth = Literal[
    "unknown",
    "nominal",
    "watching",
    "degraded",
    "incident",
    "recovering",
    "maintenance",
]
WakefulnessMode = Literal["sleeping", "watching", "wakeful", "dreaming", "unknown"]
AutonomyMode = Literal["observe", "suggest", "supervised", "autonomous", "yolo"]
LearningScopeName = Literal["private", "environment", "flock", "domain", "shared"]

DEFAULT_ENVIRONMENT_SUBJECT_PREFIX = "ravn.environment"
_VALID_EVENT_METADATA_PREFIXES = (
    "signal.",
    "environment.",
    "valkyrie.",
    "odin.",
    "attention.",
    "feedback.",
    "learning.",
    "participant.",
    "room.",
)


def _stable_now() -> datetime:
    return datetime.now(UTC)


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in values if item))


class TopologyRef(BaseModel):
    """Pointer into the existing Observatory topology vocabulary."""

    node_id: str
    type_id: str
    parent_id: str | None = None
    realm_id: str = ""
    cluster_id: str = ""
    host_id: str = ""
    zone: str = ""


class SignalSource(BaseModel):
    """A normalized external signal feed consumed by a resident Valkyrie."""

    id: str
    name: str
    kind: SignalSourceKind
    adapter: str = ""
    subject_patterns: list[str] = Field(default_factory=list)
    enabled: bool = True
    replay_cursor: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("subject_patterns")
    @classmethod
    def _dedupe_subjects(cls, value: list[str]) -> list[str]:
        return _dedupe(value)

    def nats_subjects(self, *, prefix: str = DEFAULT_ENVIRONMENT_SUBJECT_PREFIX) -> list[str]:
        """Return existing Sleipnir/NATS subjects for this source."""
        subjects = self.subject_patterns or [f"signal.{self.kind}.*"]
        return [f"{prefix}.{subject}" for subject in subjects]


class ActionCapability(BaseModel):
    """A scoped action the resident Valkyrie may propose or execute."""

    name: str
    authority: Literal[
        "observe_only",
        "autonomous",
        "yolo_allowed",
        "court_required",
        "human_review_required",
    ] = "human_review_required"
    targets: list[str] = Field(default_factory=list)
    dry_run_supported: bool = True
    description: str = ""
    risk: Literal["low", "medium", "high"] = "medium"


class AutonomyPolicy(BaseModel):
    """Authority boundary for an Environment resident."""

    mode: AutonomyMode = "supervised"
    allowed_authorities: list[str] = Field(
        default_factory=lambda: ["autonomous", "court_required", "human_review_required"]
    )
    yolo_enabled: bool = False
    max_autonomous_risk: Literal["low", "medium", "high"] = "medium"
    escalation_surfaces: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _align_yolo_mode(self) -> AutonomyPolicy:
        if self.mode == "yolo":
            self.yolo_enabled = True
            if "yolo_allowed" not in self.allowed_authorities:
                self.allowed_authorities.append("yolo_allowed")
        self.allowed_authorities = _dedupe(self.allowed_authorities)
        return self


class WakefulnessState(BaseModel):
    """Lifecycle projection reused by Warden-style wake/dream operators."""

    state: WakefulnessMode = "watching"
    wakefulness_enabled: bool = True
    dream_cycle_enabled: bool = True
    since: datetime = Field(default_factory=_stable_now)
    last_wake_at: datetime | None = None
    last_dream_at: datetime | None = None
    next_dream_after: datetime | None = None
    summary: str = ""


class FlockMembership(BaseModel):
    """Attachment to the existing Ting/Flock composition system."""

    flock_id: str
    role: str = "resident"
    ting_flow: str = ""
    learning_exchange_subject: str = ""
    adoption_policy: Literal["observe", "canary", "adopt", "manual"] = "canary"
    peer_environment_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _default_learning_subject(self) -> FlockMembership:
        if not self.learning_exchange_subject:
            self.learning_exchange_subject = f"flock.{self.flock_id}.learning.*"
        self.peer_environment_ids = _dedupe(self.peer_environment_ids)
        return self


class LearningScope(BaseModel):
    """Mimir memory and promotion scope for an Environment resident."""

    name: LearningScopeName
    memory_scope: str = ""
    mimir_mounts: list[str] = Field(default_factory=list)
    can_promote_to: list[LearningScopeName] = Field(default_factory=list)

    @model_validator(mode="after")
    def _default_memory_scope(self) -> LearningScope:
        if not self.memory_scope:
            self.memory_scope = self.name
        self.mimir_mounts = _dedupe(self.mimir_mounts)
        self.can_promote_to = list(dict.fromkeys(self.can_promote_to))
        return self


class OperationalState(BaseModel):
    """Current operating posture learned from signals and outcomes."""

    health: OperationalHealth = "unknown"
    baselines: dict[str, Any] = Field(default_factory=dict)
    active_incidents: list[dict[str, Any]] = Field(default_factory=list)
    suppressed_noise: list[dict[str, Any]] = Field(default_factory=list)
    pending_actions: list[dict[str, Any]] = Field(default_factory=list)
    learned_normal: dict[str, Any] = Field(default_factory=dict)
    last_transition_at: datetime | None = None


class Environment(BaseModel):
    """Bounded place a resident Valkyrie inhabits and learns from."""

    id: str
    name: str
    type: EnvironmentType
    tenant_id: str = ""
    realm_id: str = ""
    topology: TopologyRef
    resident_valkyrie_ids: list[str] = Field(default_factory=list)
    signal_sources: list[SignalSource] = Field(default_factory=list)
    operational_state: OperationalState = Field(default_factory=OperationalState)
    action_capabilities: list[ActionCapability] = Field(default_factory=list)
    autonomy: AutonomyPolicy = Field(default_factory=AutonomyPolicy)
    wakefulness: WakefulnessState = Field(default_factory=WakefulnessState)
    flock_memberships: list[FlockMembership] = Field(default_factory=list)
    learning_scopes: list[LearningScope] = Field(default_factory=list)
    nats_subject_prefix: str = DEFAULT_ENVIRONMENT_SUBJECT_PREFIX
    skuld_room_prefix: str = "environment"

    @model_validator(mode="after")
    def _normalize_environment(self) -> Environment:
        self.resident_valkyrie_ids = _dedupe(self.resident_valkyrie_ids)
        if not self.realm_id:
            self.realm_id = self.topology.realm_id
        if not self.tenant_id:
            self.tenant_id = self.realm_id
        if not self.learning_scopes:
            self.learning_scopes = [
                LearningScope(
                    name="private",
                    memory_scope=f"private:{self.id}",
                    mimir_mounts=[f"valkyrie-private-{self.id}"],
                    can_promote_to=["environment"],
                ),
                LearningScope(
                    name="environment",
                    memory_scope=f"environment:{self.id}",
                    mimir_mounts=[f"environment-{self.id}"],
                    can_promote_to=["flock"],
                ),
            ]
        return self

    @property
    def flock_ids(self) -> list[str]:
        """Existing Flock identifiers this Environment participates in."""
        return [membership.flock_id for membership in self.flock_memberships]

    @property
    def room_id(self) -> str:
        """Default Skuld huddle room id for the Environment."""
        return f"{self.skuld_room_prefix}:{self.id}"

    @property
    def mimir_mount_names(self) -> list[str]:
        """Flattened Mimir mount list for Warden/Ravn runtime config."""
        return _dedupe(
            mount for scope in self.learning_scopes for mount in scope.mimir_mounts
        )

    def signal_subjects(self) -> list[str]:
        """NATS subjects resident Valkyries should subscribe to for signals."""
        subjects: list[str] = []
        for source in self.signal_sources:
            if source.enabled:
                subjects.extend(source.nats_subjects(prefix=self.nats_subject_prefix))
        return _dedupe(subjects)

    def subject_for_event_type(self, event_type: str) -> str:
        """Return the Sleipnir/NATS subject for an event type."""
        return f"{self.nats_subject_prefix}.{event_type}"

    def event_metadata(self) -> dict[str, Any]:
        """Metadata copied into signals, judgments, actions, rooms, and learning records."""
        return {
            "environment_id": self.id,
            "environment_name": self.name,
            "environment_type": self.type,
            "tenant_id": self.tenant_id,
            "realm_id": self.realm_id,
            "topology_ref": self.topology.model_dump(mode="json"),
            "flock_ids": self.flock_ids,
            "learning_scopes": [scope.memory_scope for scope in self.learning_scopes],
            "mimir_mounts": self.mimir_mount_names,
        }

    def to_ravn_environment_config(self) -> dict[str, Any]:
        """Project to the existing ``ravn.config.EnvironmentConfig`` shape."""
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "flocks": self.flock_ids,
            "signal_subjects": self.signal_subjects(),
        }

    def to_ting_flock_refs(self) -> list[dict[str, Any]]:
        """Project memberships into existing Ting/Flock composition references."""
        return [
            {
                "flock_id": membership.flock_id,
                "role": membership.role,
                "ting_flow": membership.ting_flow,
                "learning_exchange_subject": membership.learning_exchange_subject,
                "adoption_policy": membership.adoption_policy,
                "peer_environment_ids": list(membership.peer_environment_ids),
            }
            for membership in self.flock_memberships
        ]

    def to_participant_meta(
        self,
        *,
        valkyrie_id: str | None = None,
        persona: str = "valkyrie",
        color: str = "amber",
    ) -> ParticipantMeta:
        """Project a resident Valkyrie into existing Skuld participant metadata."""
        default_peer_id = self.resident_valkyrie_ids[0] if self.resident_valkyrie_ids else ""
        peer_id = valkyrie_id or default_peer_id
        return ParticipantMeta(
            peer_id=peer_id or f"valkyrie:{self.id}",
            persona=persona,
            color=color,
            participant_type="ravn",
            display_name=f"{self.name} Valkyrie",
            subscribes_to=tuple(self.signal_subjects()),
            emits=tuple(
                self.subject_for_event_type(event_type)
                for event_type in (
                    "environment.state.changed",
                    "valkyrie.judgment.proposed",
                    "valkyrie.action.proposed",
                    "learning.promoted",
                )
            ),
            tools=tuple(capability.name for capability in self.action_capabilities),
            status=self.operational_state.health,
            environment_id=self.id,
            participant_kind="valkyrie",
            capabilities=tuple(capability.name for capability in self.action_capabilities),
            surfaces=tuple(self.autonomy.escalation_surfaces),
            wakefulness=self.wakefulness.state,
            attention_state="available" if self.wakefulness.state != "sleeping" else "offline",
            authority_role=self.autonomy.mode,
            room_ids=(self.room_id,),
        )

    def to_observatory_fragment(self) -> ObservatoryFragment:
        """Project to existing Observatory node/edge contracts."""
        environment_node = {
            "id": self.topology.node_id,
            "typeId": self.topology.type_id,
            "label": self.name,
            "parentId": self.topology.parent_id,
            "status": self.operational_state.health,
            "activity": self.wakefulness.state,
            "zone": self.topology.zone,
            "cluster": self.topology.cluster_id or None,
            "hostId": self.topology.host_id or None,
            "flockId": self.flock_ids[0] if self.flock_ids else None,
            "sourceId": self.id,
            "sourceKind": "environment",
        }
        nodes = [environment_node]
        edges = []
        for valkyrie_id in self.resident_valkyrie_ids:
            node_id = f"valkyrie:{valkyrie_id}"
            nodes.append(
                {
                    "id": node_id,
                    "typeId": "valkyrie",
                    "label": valkyrie_id.removeprefix("valkyrie:"),
                    "parentId": self.topology.node_id,
                    "status": self.operational_state.health,
                    "activity": self.wakefulness.state,
                    "zone": self.topology.zone,
                    "cluster": self.topology.cluster_id or None,
                    "hostId": self.topology.host_id or None,
                    "flockId": self.flock_ids[0] if self.flock_ids else None,
                    "sourceId": valkyrie_id,
                    "sourceKind": "valkyrie",
                }
            )
            edges.append(
                {
                    "id": f"{self.topology.node_id}->{node_id}",
                    "sourceId": self.topology.node_id,
                    "targetId": node_id,
                    "kind": "soft",
                }
            )
        return {
            "nodes": nodes,
            "edges": edges,
            "meta": {
                "sourceId": self.id,
                "sourceKind": "environment",
                "sourceName": self.name,
                "realmId": self.realm_id,
                "clusterId": self.topology.cluster_id,
                "revision": "environment.v1",
            },
        }


def apply_environment_metadata(
    event: SleipnirEvent,
    environment: Environment,
    *,
    root_correlation_id: str | None = None,
) -> SleipnirEvent:
    """Enrich a standard Sleipnir event with Environment provenance."""
    if not event.event_type.startswith(_VALID_EVENT_METADATA_PREFIXES):
        return event
    metadata = environment.event_metadata()
    payload = dict(event.payload)
    payload.update({key: value for key, value in metadata.items() if key not in payload})
    payload["nats_subject"] = environment.subject_for_event_type(event.event_type)
    if root_correlation_id:
        payload.setdefault("root_correlation_id", root_correlation_id)
    event.payload = payload
    if event.tenant_id is None and environment.tenant_id:
        event.tenant_id = environment.tenant_id
    return event


def k8s_environment_fixture() -> Environment:
    """Representative Kubernetes cluster Environment."""
    return Environment(
        id="cluster-prod-a",
        name="Production Cluster A",
        type="k8s",
        tenant_id="niuu",
        realm_id="prod",
        topology=TopologyRef(
            node_id="cluster:prod-a",
            type_id="cluster",
            parent_id="realm:prod",
            realm_id="prod",
            cluster_id="prod-a",
            zone="prod",
        ),
        resident_valkyrie_ids=["valkyrie:k8s-prod-a"],
        signal_sources=[
            SignalSource(
                id="kubernetes-events",
                name="Kubernetes Events",
                kind="kubernetes",
                adapter="ravn.adapters.environment.kubernetes",
                subject_patterns=["signal.kubernetes.*", "environment.state"],
                metadata={"cluster": "prod-a"},
            ),
            SignalSource(
                id="prometheus-alerts",
                name="Prometheus Alerts",
                kind="metrics",
                adapter="ravn.adapters.environment.metrics",
                subject_patterns=["signal.metrics.*"],
            ),
        ],
        operational_state=OperationalState(
            health="watching",
            baselines={"restart_noise_window": "10m", "normal_queue_depth": 200},
            suppressed_noise=[{"kind": "DeploymentScaled", "window": "during rollout"}],
            learned_normal={"deploy_restarts": "suppress if replicas recover within 5m"},
        ),
        action_capabilities=[
            ActionCapability(
                name="k8s.inspect_pod",
                authority="autonomous",
                targets=["pod", "namespace"],
                risk="low",
            ),
            ActionCapability(
                name="k8s.restart_deployment",
                authority="court_required",
                targets=["deployment", "namespace"],
                risk="high",
            ),
        ],
        autonomy=AutonomyPolicy(
            mode="autonomous",
            max_autonomous_risk="medium",
            escalation_surfaces=["skuld:huddle", "ui:valkyries"],
        ),
        wakefulness=WakefulnessState(state="watching", summary="Watching cluster events"),
        flock_memberships=[
            FlockMembership(
                flock_id="k8s-valkyries",
                role="cluster-resident",
                ting_flow="k8s-environment-flock",
                peer_environment_ids=["cluster-prod-b"],
            )
        ],
        learning_scopes=[
            LearningScope(
                name="private",
                memory_scope="private:cluster-prod-a",
                mimir_mounts=["valkyrie-private-cluster-prod-a"],
                can_promote_to=["environment"],
            ),
            LearningScope(
                name="environment",
                memory_scope="environment:cluster-prod-a",
                mimir_mounts=["environment-cluster-prod-a"],
                can_promote_to=["flock"],
            ),
            LearningScope(
                name="flock",
                memory_scope="flock:k8s-valkyries",
                mimir_mounts=["flock-k8s-valkyries"],
                can_promote_to=["domain", "shared"],
            ),
        ],
    )


def inbox_environment_fixture() -> Environment:
    """Representative host/inbox Environment."""
    return Environment(
        id="host-jozef-mail",
        name="Jozef Mail Host",
        type="host.inbox",
        tenant_id="niuu",
        realm_id="personal",
        topology=TopologyRef(
            node_id="host:jozef-mail",
            type_id="host",
            parent_id="realm:personal",
            realm_id="personal",
            host_id="jozef-mac",
            zone="personal",
        ),
        resident_valkyrie_ids=["valkyrie:inbox-host"],
        signal_sources=[
            SignalSource(
                id="gmail-inbox",
                name="Gmail Inbox",
                kind="email",
                adapter="gmail.connector",
                subject_patterns=["signal.inbox.*"],
            ),
            SignalSource(
                id="host-events",
                name="Host Events",
                kind="host",
                adapter="ravn.adapters.environment.host",
                subject_patterns=["signal.host.*"],
            ),
        ],
        operational_state=OperationalState(
            health="nominal",
            baselines={"expected_important_threads_per_day": 6},
            learned_normal={"newsletters": "suppress unless sender is pinned"},
        ),
        action_capabilities=[
            ActionCapability(
                name="email.classify",
                authority="autonomous",
                targets=["message"],
                risk="low",
            ),
            ActionCapability(
                name="email.draft_reply",
                authority="human_review_required",
                targets=["thread"],
                risk="medium",
            ),
        ],
        autonomy=AutonomyPolicy(
            mode="supervised",
            escalation_surfaces=["ui:valkyries", "email:drafts"],
        ),
        wakefulness=WakefulnessState(state="watching", summary="Watching high-signal mail"),
        flock_memberships=[
            FlockMembership(
                flock_id="personal-host-valkyries",
                role="host-resident",
                ting_flow="host-environment-flock",
            )
        ],
    )


def printer_environment_fixture() -> Environment:
    """Representative printer/Pi cell Environment."""
    return Environment(
        id="printer-cell-basement",
        name="Basement Printer Cell",
        type="printer.pi",
        tenant_id="niuu",
        realm_id="home",
        topology=TopologyRef(
            node_id="printer:basement-cell",
            type_id="printer",
            parent_id="realm:home",
            realm_id="home",
            host_id="pi:basement-printer",
            zone="basement",
        ),
        resident_valkyrie_ids=["valkyrie:printer-cell-basement"],
        signal_sources=[
            SignalSource(
                id="moonraker-telemetry",
                name="Printer Telemetry",
                kind="printer_telemetry",
                adapter="moonraker",
                subject_patterns=["signal.printer.*"],
            ),
            SignalSource(
                id="pi-host-events",
                name="Pi Host Events",
                kind="host",
                adapter="ravn.adapters.environment.host",
                subject_patterns=["signal.host.*"],
            ),
        ],
        operational_state=OperationalState(
            health="watching",
            baselines={"resin_low_threshold": 12, "bed_temp_tolerance_c": 3},
            learned_normal={"print_complete": "notify only if next queued job waits for resin"},
        ),
        action_capabilities=[
            ActionCapability(
                name="printer.pause_print",
                authority="autonomous",
                targets=["printer"],
                risk="medium",
            ),
            ActionCapability(
                name="printer.notify_operator",
                authority="autonomous",
                targets=["surface", "mobile"],
                risk="low",
            ),
        ],
        autonomy=AutonomyPolicy(
            mode="autonomous",
            escalation_surfaces=["surface:workbench-display", "ui:valkyries"],
        ),
        wakefulness=WakefulnessState(state="watching", summary="Watching printer telemetry"),
        flock_memberships=[
            FlockMembership(
                flock_id="printer-cell-valkyries",
                role="device-resident",
                ting_flow="printer-environment-flock",
            )
        ],
    )


def example_environments() -> list[Environment]:
    """Return deterministic Environment fixtures for tests, docs, and UI mocks."""
    return [
        k8s_environment_fixture(),
        inbox_environment_fixture(),
        printer_environment_fixture(),
    ]
