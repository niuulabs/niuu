"""Tests for durable Valkyrie history: records, stores, service, endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ravn.adapters.review import FileReviewQueueStore
from ravn.adapters.valkyrie_history import (
    InMemoryValkyrieHistoryStore,
    build_valkyrie_history_store_from_env,
)
from ravn.adapters.valkyrie_history.postgres import PostgresValkyrieHistoryStore
from ravn.api.valkyrie_history_service import (
    ValkyrieHistoryService,
    review_item_for_judgment,
)
from ravn.api.valkyries import ValkyrieDashboardProjection, create_valkyrie_router
from ravn.domain.valkyrie_history import (
    action_record_from_event,
    canonical_environment_id,
    decision_record_from_event,
    decision_requires_review,
    signal_record_from_event,
)
from ravn.odin.review import ReviewKind, ReviewStatus
from ravn.odin.review_service import OdinReviewService


def _judgment_event(
    *,
    event_id: str = "evt-judgment-1",
    authority: str = "autonomous",
    operational_state: str = "investigating",
    correlation_id: str = "corr-1",
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "event_type": "valkyrie.judgment.proposed",
        "source": "ravn:valkyrie:env-k8s-prod",
        "summary": "Valkyrie proposed judgment",
        "correlation_id": correlation_id,
        "timestamp": "2026-07-03T09:00:00+00:00",
        "payload": {
            "environment_id": "env-k8s-prod",
            "valkyrie_id": "valkyrie-prod",
            "signal_refs": ["sig-1", "sig-2"],
            "tier": "present",
            "confidence": 0.82,
            "operational_state": operational_state,
            "rationale": "Pod restart storm in namespace payments",
            "evidence": evidence if evidence is not None else [{"event_id": "sig-1"}],
            "recommended_action": "restart_deployment",
            "action_authority": authority,
            "action_capability": "k8s.rollout.restart",
        },
    }


def _action_event(
    *,
    event_id: str = "evt-action-1",
    event_type: str = "valkyrie.action.executed",
    correlation_id: str = "corr-1",
    outcome: str = "rollout restarted",
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "event_type": event_type,
        "source": "ravn:valkyrie:env-k8s-prod",
        "summary": f"action {event_type}",
        "correlation_id": correlation_id,
        "timestamp": "2026-07-03T09:05:00+00:00",
        "payload": {
            "environment_id": "env-k8s-prod",
            "valkyrie_id": "valkyrie-prod",
            "action_id": "act-1",
            "capability": "k8s.rollout.restart",
            "outcome": outcome,
        },
    }


def _signal_event(
    *,
    event_id: str = "sig-1",
    severity: str = "warning",
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "event_type": "signal.kubernetes.event",
        "source": "adapter:k8s-events",
        "summary": "Pod payments-7f9 restarted",
        "correlation_id": "corr-1",
        "timestamp": "2026-07-03T08:59:00+00:00",
        "tenant_id": "env-k8s-prod",
        "payload": {
            "environment_id": "env-k8s-prod",
            "severity": severity,
            "subject": "payments-7f9",
        },
    }


# ---------------------------------------------------------------------------
# Domain record extraction
# ---------------------------------------------------------------------------


def test_canonical_environment_id_bridges_daemon_and_dashboard_forms() -> None:
    # The daemon stamps its raw environment.id ("valhalla"); the fleet registry
    # and every dashboard query use env-k8s-<slug>. Records must land under the
    # canonical key or the UI reads an empty store while data sits one key away.
    assert canonical_environment_id("valhalla") == "env-k8s-valhalla"
    assert canonical_environment_id("env-k8s-valhalla") == "env-k8s-valhalla"
    assert canonical_environment_id("env-custom") == "env-custom"
    assert canonical_environment_id("") == "unknown"
    assert canonical_environment_id(None) == "unknown"
    assert canonical_environment_id("Prod Cluster!") == "env-k8s-prod-cluster"


def test_decision_record_canonicalizes_raw_daemon_environment_id() -> None:
    event = _judgment_event()
    event["payload"]["environment_id"] = "valhalla"

    record = decision_record_from_event(event)

    assert record is not None
    assert record["environmentId"] == "env-k8s-valhalla"


def test_decision_record_extracts_full_judgment_contract() -> None:
    record = decision_record_from_event(_judgment_event())

    assert record is not None
    assert record["decisionId"] == "evt-judgment-1"
    assert record["environmentId"] == "env-k8s-prod"
    assert record["valkyrieId"] == "valkyrie-prod"
    assert record["operationalState"] == "investigating"
    assert record["tier"] == "present"
    assert record["confidence"] == pytest.approx(0.82)
    assert record["rationale"] == "Pod restart storm in namespace payments"
    assert record["recommendedAction"] == "restart_deployment"
    assert record["actionAuthority"] == "autonomous"
    assert record["signalRefs"] == ["sig-1", "sig-2"]
    assert record["correlationId"] == "corr-1"
    assert not decision_requires_review(record)


def test_decision_record_reads_wrapped_fields_payloads() -> None:
    event = _judgment_event()
    event["payload"] = {"fields": event.pop("payload")}
    record = decision_record_from_event(event)

    assert record is not None
    assert record["operationalState"] == "investigating"
    assert record["signalRefs"] == ["sig-1", "sig-2"]


def test_decision_requires_review_only_when_a_human_is_actually_asked() -> None:
    # Attention-seeking judgment with a real recommendation: escalate.
    record = decision_record_from_event(_judgment_event(authority="human_review_required"))
    assert record is not None
    assert record["tier"] == "present"
    assert decision_requires_review(record)

    # court_required is the ODIN court's path — never centrally filed.
    court = decision_record_from_event(_judgment_event(authority="court_required"))
    assert court is not None
    assert not decision_requires_review(court)

    # The resident files ask_operator through its exact case-aware review path.
    asking = _judgment_event(authority="human_review_required")
    asking["payload"]["continuation"] = "ask_operator"
    asking["payload"]["question"] = "May I proceed?"
    question = decision_record_from_event(asking)
    assert question is not None
    assert question["question"] == "May I proceed?"
    assert not decision_requires_review(question)


def test_ambient_and_observational_judgments_never_reach_the_inbox() -> None:
    """Guarded residents stamp human_review_required on ALL judgments —
    including 'used a learned skill, all fine' telemetry. Only judgments
    pitched at present/urgent with a real recommended action escalate."""
    ambient = _judgment_event(authority="human_review_required")
    ambient["payload"]["tier"] = "ambient"
    ambient["payload"]["operational_state"] = "using_adopted_learning"
    ambient["payload"]["recommended_action"] = "inspect_with_adopted_learning"
    record = decision_record_from_event(ambient)
    assert record is not None
    assert not decision_requires_review(record)

    watching = _judgment_event(authority="human_review_required")
    watching["payload"]["recommended_action"] = "none"
    record = decision_record_from_event(watching)
    assert record is not None
    assert not decision_requires_review(record)

    failure = _judgment_event(authority="human_review_required")
    failure["payload"]["tier"] = "present"
    failure["payload"]["operational_state"] = "adopted_learning_failed"
    failure["payload"]["recommended_action"] = "review_adopted_learning_failure"
    record = decision_record_from_event(failure)
    assert record is not None
    assert decision_requires_review(record)


def test_decision_requires_review_honours_configured_attention_tiers() -> None:
    """P5a: the attention-tier gate is configurable. An ambient judgment is
    telemetry under the defaults but escalates when a deployment widens the
    configured tiers."""
    ambient = _judgment_event(authority="human_review_required")
    ambient["payload"]["tier"] = "ambient"
    record = decision_record_from_event(ambient)
    assert record is not None

    assert not decision_requires_review(record)
    assert decision_requires_review(
        record, attention_tiers=frozenset({"ambient", "present", "urgent"})
    )


def test_decision_requires_review_honours_configured_observational_actions() -> None:
    """P5a: the observational-action gate is configurable. 'watch' is
    observation under the defaults but escalates when a deployment narrows
    the configured set."""
    watching = _judgment_event(authority="human_review_required")
    watching["payload"]["recommended_action"] = "watch"
    record = decision_record_from_event(watching)
    assert record is not None

    assert not decision_requires_review(record)
    assert decision_requires_review(record, observational_actions=frozenset({"", "none"}))


def test_court_required_never_escalates_even_with_custom_gates() -> None:
    """The authority gate is NOT configurable: court_required stays the ODIN
    court's path regardless of any tier/action overrides."""
    court = decision_record_from_event(_judgment_event(authority="court_required"))
    assert court is not None
    assert not decision_requires_review(
        court,
        attention_tiers=frozenset({"ambient", "present", "urgent"}),
        observational_actions=frozenset(),
    )


def test_decision_record_ignores_other_events_and_missing_ids() -> None:
    assert decision_record_from_event({"event_type": "signal.k8s.event"}) is None
    event = _judgment_event()
    event["event_id"] = ""
    assert decision_record_from_event(event) is None


def test_action_record_maps_status_by_event_type() -> None:
    executed = action_record_from_event(_action_event())
    failed = action_record_from_event(
        _action_event(event_id="evt-action-2", event_type="valkyrie.action.failed")
    )
    proposed = action_record_from_event(
        _action_event(event_id="evt-action-3", event_type="valkyrie.action.proposed")
    )

    assert executed is not None and executed["status"] == "executed"
    assert failed is not None and failed["status"] == "failed"
    assert proposed is not None and proposed["status"] == "proposed"
    assert executed["actionId"] == "act-1"
    assert executed["capability"] == "k8s.rollout.restart"
    assert action_record_from_event({"event_type": "signal.k8s.event"}) is None


def test_signal_record_extracts_severity_and_subject() -> None:
    record = signal_record_from_event(_signal_event())

    assert record is not None
    assert record["signalId"] == "sig-1"
    assert record["severity"] == "warning"
    assert record["subject"] == "payments-7f9"
    assert record["environmentId"] == "env-k8s-prod"
    assert signal_record_from_event(_judgment_event()) is None


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_store_lists_decisions_with_filters_and_pagination() -> None:
    store = InMemoryValkyrieHistoryStore()
    for index in range(5):
        record = decision_record_from_event(
            _judgment_event(event_id=f"evt-{index}", correlation_id=f"corr-{index}")
        )
        assert record is not None
        record["decidedAt"] = f"2026-07-03T09:0{index}:00+00:00"
        await store.record_decision(record)

    rows, total = await store.list_decisions(environment_id="env-k8s-prod", limit=2)
    assert total == 5
    assert [row["decisionId"] for row in rows] == ["evt-4", "evt-3"]

    rows, total = await store.list_decisions(limit=2, offset=4)
    assert total == 5
    assert [row["decisionId"] for row in rows] == ["evt-0"]

    rows, total = await store.list_decisions(environment_id="env-other")
    assert total == 0 and rows == []

    assert await store.get_decision("evt-2") is not None
    assert await store.get_decision("missing") is None


@pytest.mark.asyncio
async def test_memory_store_outcome_stamping_and_review_link() -> None:
    store = InMemoryValkyrieHistoryStore()
    record = decision_record_from_event(_judgment_event())
    assert record is not None
    await store.record_decision(record)

    updated = await store.record_decision_outcome(
        correlation_id="corr-1",
        outcome="executed",
        detail="rollout restarted",
        outcome_at="2026-07-03T09:05:00+00:00",
    )
    await store.link_review_item("evt-judgment-1", "review:court_escalation:evt-judgment-1")

    assert updated == 1
    stored = await store.get_decision("evt-judgment-1")
    assert stored is not None
    assert stored["outcome"] == "executed"
    assert stored["outcomeDetail"] == "rollout restarted"
    assert stored["reviewItemId"] == "review:court_escalation:evt-judgment-1"
    assert (
        await store.record_decision_outcome(
            correlation_id="", outcome="executed", detail="", outcome_at=""
        )
        == 0
    )


@pytest.mark.asyncio
async def test_memory_store_signals_actions_and_trim() -> None:
    store = InMemoryValkyrieHistoryStore(max_records_per_kind=2)
    for index in range(3):
        signal = signal_record_from_event(_signal_event(event_id=f"sig-{index}"))
        assert signal is not None
        signal["receivedAt"] = f"2026-07-03T08:5{index}:00+00:00"
        await store.record_signal(signal)
    action = action_record_from_event(_action_event())
    assert action is not None
    await store.record_action(action)

    rows, total = await store.list_signals()
    assert total == 2  # oldest trimmed
    assert [row["signalId"] for row in rows] == ["sig-2", "sig-1"]
    assert await store.signals_by_ids(["sig-2", "missing"]) == [rows[0]]
    assert await store.actions_for_correlation("corr-1") == [action]
    assert await store.actions_for_correlation("") == []

    filtered, _ = await store.list_signals(severity="critical")
    assert filtered == []


# ---------------------------------------------------------------------------
# Postgres store (mocked asyncpg pool)
# ---------------------------------------------------------------------------


class FakePool:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.fetch_results: list[list[dict[str, Any]]] = []
        self.fetchrow_results: list[dict[str, Any] | None] = []
        self.execute_result = "UPDATE 2"

    async def execute(self, sql: str, *params: Any) -> str:
        self.executed.append((sql, params))
        return self.execute_result

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        self.executed.append((sql, params))
        return self.fetch_results.pop(0) if self.fetch_results else []

    async def fetchrow(self, sql: str, *params: Any) -> dict[str, Any] | None:
        self.executed.append((sql, params))
        return self.fetchrow_results.pop(0) if self.fetchrow_results else None


@pytest.mark.asyncio
async def test_postgres_store_round_trips_records() -> None:
    pool = FakePool()
    store = PostgresValkyrieHistoryStore(pool)
    decision = decision_record_from_event(_judgment_event())
    action = action_record_from_event(_action_event())
    signal = signal_record_from_event(_signal_event())
    assert decision and action and signal

    await store.record_decision(decision)
    await store.record_action(action)
    await store.record_signal(signal)

    assert "INSERT INTO valkyrie_decisions" in pool.executed[0][0]
    assert pool.executed[0][1][0] == "evt-judgment-1"
    assert "INSERT INTO valkyrie_actions" in pool.executed[1][0]
    assert "INSERT INTO valkyrie_signals" in pool.executed[2][0]


@pytest.mark.asyncio
async def test_postgres_store_lists_and_updates() -> None:
    pool = FakePool()
    store = PostgresValkyrieHistoryStore(pool)
    pool.fetchrow_results = [{"total": 3}]
    pool.fetch_results = [[{"payload": '{"decisionId": "evt-1"}'}]]

    rows, total = await store.list_decisions(environment_id="env-k8s-prod", limit=1, offset=1)
    assert total == 3
    assert rows == [{"decisionId": "evt-1"}]
    list_sql = pool.executed[-1][0]
    assert "environment_id = $1" in list_sql and "OFFSET" in list_sql

    pool.fetchrow_results = [{"payload": {"decisionId": "evt-1"}}]
    assert await store.get_decision("evt-1") == {"decisionId": "evt-1"}
    pool.fetchrow_results = [None]
    assert await store.get_decision("missing") is None

    updated = await store.record_decision_outcome(
        correlation_id="corr-1", outcome="executed", detail="ok", outcome_at="t"
    )
    assert updated == 2
    assert (
        await store.record_decision_outcome(
            correlation_id="", outcome="executed", detail="", outcome_at=""
        )
        == 0
    )

    pool.fetch_results = [[{"signal_id": "sig-1", "payload": {"signalId": "sig-1"}}]]
    assert await store.signals_by_ids(["sig-1"]) == [{"signalId": "sig-1"}]
    assert await store.signals_by_ids([]) == []

    pool.fetch_results = [[{"payload": {"actionId": "act-1"}}]]
    assert await store.actions_for_correlation("corr-1") == [{"actionId": "act-1"}]
    assert await store.actions_for_correlation("") == []

    await store.link_review_item("evt-1", "review:x")
    assert "review_item_id" in pool.executed[-1][0]

    pool.fetchrow_results = [{"total": 1}]
    pool.fetch_results = [[{"payload": {"signalId": "sig-1"}}]]
    rows, total = await store.list_signals(severity="warning")
    assert total == 1 and rows == [{"signalId": "sig-1"}]


def test_store_builder_prefers_configured_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RAVN_VALKYRIE_HISTORY_DATABASE_URL", raising=False)
    monkeypatch.delenv("RAVN_ODIN_REVIEW_DATABASE_URL", raising=False)
    assert isinstance(build_valkyrie_history_store_from_env(), InMemoryValkyrieHistoryStore)

    monkeypatch.setenv("RAVN_ODIN_REVIEW_DATABASE_URL", "postgres://example/db")
    store = build_valkyrie_history_store_from_env()
    assert not isinstance(store, InMemoryValkyrieHistoryStore)


# ---------------------------------------------------------------------------
# History service: ingest, review filing, outcomes, lineage, stats, briefs
# ---------------------------------------------------------------------------


def _review_service(tmp_path: Any) -> OdinReviewService:
    return OdinReviewService(FileReviewQueueStore(tmp_path / "reviews.json"))


@pytest.mark.asyncio
async def test_ingest_persists_signals_decisions_and_actions(tmp_path: Any) -> None:
    service = ValkyrieHistoryService(
        InMemoryValkyrieHistoryStore(),
        review_service=_review_service(tmp_path),
    )
    await service.ingest_event(_signal_event())
    await service.ingest_event(_judgment_event())
    await service.ingest_event(_action_event())
    await service.ingest_event({"event_type": "valkyrie.presence.heartbeat", "event_id": "hb-1"})

    decisions, total = await service.store.list_decisions()
    assert total == 1
    assert decisions[0]["outcome"] == "executed"  # C12: action stamped the decision
    signals, _ = await service.store.list_signals()
    assert [row["signalId"] for row in signals] == ["sig-1"]


@pytest.mark.asyncio
async def test_action_outcome_log_escapes_forged_newline(
    caplog: pytest.LogCaptureFixture,
) -> None:
    correlation_id = "corr\nforged-entry"
    service = ValkyrieHistoryService(InMemoryValkyrieHistoryStore())
    caplog.set_level("INFO", logger="ravn.api.valkyrie_history_service")

    await service.ingest_event(_judgment_event(correlation_id=correlation_id))
    await service.ingest_event(_action_event(correlation_id=correlation_id))

    messages = [record.getMessage() for record in caplog.records]
    outcome_message = next(message for message in messages if "stamped" in message)
    assert correlation_id not in outcome_message
    assert "corr\\nforged-entry" in outcome_message


@pytest.mark.asyncio
async def test_review_required_judgment_lands_in_inbox_once(tmp_path: Any) -> None:
    reviews = _review_service(tmp_path)
    service = ValkyrieHistoryService(InMemoryValkyrieHistoryStore(), review_service=reviews)
    event = _judgment_event(authority="human_review_required")

    await service.ingest_event(event)
    await service.ingest_event(event)  # replay must not duplicate

    pending = await reviews.list_items(status=ReviewStatus.PENDING.value)
    assert len(pending) == 1
    item = pending[0]
    assert item.kind == ReviewKind.COURT_ESCALATION.value
    assert item.environment_id == "env-k8s-prod"
    assert item.evidence["action"]["capability"] == "k8s.rollout.restart"
    assert item.evidence["rationale"] == "Pod restart storm in namespace payments"
    stored = await service.store.get_decision("evt-judgment-1")
    assert stored is not None
    assert stored["reviewItemId"] == item.item_id


@pytest.mark.asyncio
async def test_autonomous_judgment_stays_out_of_inbox(tmp_path: Any) -> None:
    reviews = _review_service(tmp_path)
    service = ValkyrieHistoryService(InMemoryValkyrieHistoryStore(), review_service=reviews)

    await service.ingest_event(_judgment_event(authority="autonomous"))

    assert await reviews.list_items(status=ReviewStatus.PENDING.value) == []


def test_review_item_for_judgment_contract() -> None:
    record = decision_record_from_event(_judgment_event(authority="human_review_required"))
    assert record is not None
    item = review_item_for_judgment(record)

    assert item.item_id == "review:court_escalation:evt-judgment-1"
    assert item.requested_action == "execute_action"
    assert item.risk_class == "medium"
    assert item.evidence["action"]["action_id"] == "evt-judgment-1"
    assert item.evidence["action"]["dry_run"] is True
    assert item.correlation_id == "corr-1"


@pytest.mark.asyncio
async def test_decision_detail_returns_lineage(tmp_path: Any) -> None:
    reviews = _review_service(tmp_path)
    service = ValkyrieHistoryService(InMemoryValkyrieHistoryStore(), review_service=reviews)
    await service.ingest_event(_signal_event())
    await service.ingest_event(_judgment_event(authority="human_review_required"))
    await service.ingest_event(_action_event())

    detail = await service.decision_detail("evt-judgment-1")

    assert detail is not None
    assert detail["decision"]["decisionId"] == "evt-judgment-1"
    assert [row["signalId"] for row in detail["lineage"]["signals"]] == ["sig-1"]
    assert [row["actionId"] for row in detail["lineage"]["actions"]] == ["act-1"]
    assert detail["lineage"]["review"] is not None
    assert detail["lineage"]["review"]["kind"] == ReviewKind.COURT_ESCALATION.value
    assert await service.decision_detail("missing") is None


@pytest.mark.asyncio
async def test_skill_stats_aggregate_learned_skill_judgments(tmp_path: Any) -> None:
    service = ValkyrieHistoryService(
        InMemoryValkyrieHistoryStore(), review_service=_review_service(tmp_path)
    )
    used = _judgment_event(
        event_id="evt-used",
        operational_state="using_adopted_learning",
        evidence=[{"skill_name": "inspect-disk", "capability_name": "inspect.host.disk"}],
    )
    failed = _judgment_event(
        event_id="evt-failed",
        operational_state="adopted_learning_failed",
        evidence=[{"skill_name": "inspect-disk", "capability_name": "inspect.host.disk"}],
    )
    regressed = _judgment_event(
        event_id="evt-regressed",
        operational_state="adopted_learning_regressed",
        evidence=[{"skill_name": "inspect-disk", "capability_name": "inspect.host.disk"}],
    )
    await service.ingest_event(used)
    await service.ingest_event(failed)
    await service.ingest_event(regressed)

    stats = await service.skill_stats()

    assert len(stats) == 1
    entry = stats[0]
    assert entry["skillName"] == "inspect-disk"
    assert entry["capability"] == "inspect.host.disk"
    assert entry["uses"] == 3
    assert entry["successes"] == 1
    assert entry["failures"] == 2
    assert entry["rolledBackAt"] != ""


@pytest.mark.asyncio
async def test_morning_briefs_filed_once_per_environment_per_day(tmp_path: Any) -> None:
    reviews = _review_service(tmp_path)
    service = ValkyrieHistoryService(InMemoryValkyrieHistoryStore(), review_service=reviews)
    now = datetime.now(UTC).isoformat()
    signal = signal_record_from_event(_signal_event())
    decision = decision_record_from_event(_judgment_event())
    assert signal and decision
    signal["receivedAt"] = now
    decision["decidedAt"] = now
    await service.store.record_signal(signal)
    await service.store.record_decision(decision)

    filed_first = await service.file_morning_briefs(window=timedelta(days=1))
    filed_second = await service.file_morning_briefs(window=timedelta(days=1))

    assert filed_first == 1
    assert filed_second == 0
    pending = await reviews.list_items(status=ReviewStatus.PENDING.value)
    briefs = [item for item in pending if item.kind == ReviewKind.MORNING_BRIEF.value]
    assert len(briefs) == 1
    brief = briefs[0]
    assert brief.environment_id == "env-k8s-prod"
    assert brief.requested_action == "acknowledge"
    assert "Morning brief" in brief.evidence["brief_markdown"]
    assert brief.evidence["decision_count"] == 1
    assert brief.evidence["signal_count"] == 1


@pytest.mark.asyncio
async def test_morning_briefs_require_review_service_and_activity(tmp_path: Any) -> None:
    bare = ValkyrieHistoryService(InMemoryValkyrieHistoryStore())
    assert await bare.file_morning_briefs(window=timedelta(days=1)) == 0

    service = ValkyrieHistoryService(
        InMemoryValkyrieHistoryStore(), review_service=_review_service(tmp_path)
    )
    assert await service.file_morning_briefs(window=timedelta(days=1)) == 0


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


def _client(tmp_path: Any) -> tuple[TestClient, ValkyrieHistoryService]:
    reviews = _review_service(tmp_path)
    history = ValkyrieHistoryService(InMemoryValkyrieHistoryStore(), review_service=reviews)
    app = FastAPI()
    app.include_router(
        create_valkyrie_router(
            ValkyrieDashboardProjection(),
            review_service=reviews,
            history_service=history,
        )
    )
    return TestClient(app), history


def test_decision_endpoints_serve_history_with_lineage(tmp_path: Any) -> None:
    client, _history = _client(tmp_path)
    base = "/api/v1/ravn/valkyrie"
    client.post(f"{base}/telemetry/events?minimal=true", json=_signal_event())
    client.post(
        f"{base}/telemetry/events?minimal=true",
        json=_judgment_event(authority="human_review_required"),
    )
    client.post(f"{base}/telemetry/events?minimal=true", json=_action_event())

    listing = client.get(f"{base}/decisions", params={"environment_id": "env-k8s-prod"})
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 1
    assert body["items"][0]["rationale"] == "Pod restart storm in namespace payments"
    assert body["items"][0]["outcome"] == "executed"

    detail = client.get(f"{base}/decisions/evt-judgment-1")
    assert detail.status_code == 200
    lineage = detail.json()["lineage"]
    assert [row["signalId"] for row in lineage["signals"]] == ["sig-1"]
    assert lineage["review"]["kind"] == "court_escalation"
    assert client.get(f"{base}/decisions/missing").status_code == 404

    signals = client.get(f"{base}/signals/history", params={"severity": "warning"})
    assert signals.status_code == 200
    assert signals.json()["total"] == 1

    stats = client.get(f"{base}/learnings/stats/skills")
    assert stats.status_code == 200
    assert stats.json() == {"skills": []}


def test_history_endpoints_503_without_store(tmp_path: Any) -> None:
    app = FastAPI()
    app.include_router(create_valkyrie_router(ValkyrieDashboardProjection()))
    client = TestClient(app)
    base = "/api/v1/ravn/valkyrie"

    assert client.get(f"{base}/decisions").status_code == 503
    assert client.get(f"{base}/decisions/x").status_code == 503
    assert client.get(f"{base}/signals/history").status_code == 503
    assert client.get(f"{base}/learnings/stats/skills").status_code == 503
