"""Central ODIN review queue: ingest, decide, expire, REST (NIU-1045)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from ravn.adapters.review import FileReviewQueueStore, PostgresReviewQueueStore
from ravn.api.odin_reviews import create_odin_review_router
from ravn.odin.review import (
    ReviewItem,
    ReviewKind,
    review_decided_event,
    review_requested_event,
    review_resolved_event,
)
from ravn.odin.review_service import OdinReviewService, ReviewDecisionError
from sleipnir.domain.events import SleipnirEvent


def _item(**overrides) -> ReviewItem:
    data = {
        "kind": ReviewKind.EVOLUTION_BUILD.value,
        "requested_action": "install",
        "environment_id": "cluster-a",
        "valkyrie_id": "valkyrie:k8s-a",
        "title": "probe",
        "summary": "a probe",
    }
    data.update(overrides)
    return ReviewItem.new(**data)


class _RecordingPublisher:
    def __init__(self) -> None:
        self.items: list[ReviewItem] = []

    async def publish_review_decision(
        self,
        item: ReviewItem,
    ) -> tuple[dict[str, Any], SleipnirEvent | None]:
        self.items.append(item)
        return {"published": True, "message": "published"}, None


def _service(tmp_path, **kwargs) -> tuple[OdinReviewService, _RecordingPublisher]:
    publisher = _RecordingPublisher()
    service = OdinReviewService(
        FileReviewQueueStore(tmp_path / "queue.json"),
        publisher=publisher,
        **kwargs,
    )
    return service, publisher


# ---------------------------------------------------------------------------
# Service: ingest / decide / expire
# ---------------------------------------------------------------------------


async def test_requested_events_land_pending_and_decide_publishes(tmp_path) -> None:
    service, publisher = _service(tmp_path)
    item = _item()
    consumed = await service.ingest_event(review_requested_event(item, source="valkyrie:k8s-a"))
    assert consumed
    assert not await service.ingest_event(
        SleipnirEvent(
            event_type="valkyrie.state.changed",
            source="x",
            payload={},
            summary="",
            urgency=0.1,
            domain="infrastructure",
            timestamp=datetime.now(UTC),
        )
    )

    pending = await service.list_items(status="pending")
    assert [p.item_id for p in pending] == [item.item_id]

    decided, delivery = await service.decide(
        item.item_id,
        decision="approved",
        operator_id="human:jozef",
        reason="fine",
    )
    assert decided.status == "approved"
    assert delivery["published"] is True
    assert [p.item_id for p in publisher.items] == [item.item_id]


async def test_review_list_filters_searches_and_pages(tmp_path) -> None:
    service, _publisher = _service(tmp_path)
    for minute, title in enumerate(("Restart checkout", "Restart payments", "Ignore noise")):
        item = _item(
            environment_id="cluster-b",
            risk_class="high" if title.startswith("Restart") else "low",
            title=title,
        )
        item.requested_at = f"2026-07-14T12:0{minute}:00+00:00"
        await service.store.upsert(item)

    first = await service.list_items(
        status="pending",
        environment_id="cluster-b",
        risk_class="high",
        query="restart",
        limit=1,
    )
    second = await service.list_items(
        status="pending",
        environment_id="cluster-b",
        risk_class="high",
        query="restart",
        limit=1,
        offset=1,
    )

    assert [item.title for item in first] == ["Restart payments"]
    assert [item.title for item in second] == ["Restart checkout"]


async def test_reannounce_never_regresses_a_settled_item(tmp_path) -> None:
    service, _publisher = _service(tmp_path)
    item = _item()
    await service.ingest_event(review_requested_event(item, source="v"))
    await service.decide(item.item_id, decision="approved", operator_id="op")

    # The resident restarts and re-announces the same pending item.
    await service.ingest_event(review_requested_event(item, source="v"))
    stored = await service.get(item.item_id)
    assert stored.status == "approved"


async def test_resolved_events_close_the_loop(tmp_path) -> None:
    service, publisher = _service(tmp_path)
    item = _item()
    await service.ingest_event(review_requested_event(item, source="v"))
    await service.decide(item.item_id, decision="approved", operator_id="op")

    # The resident applies the decided copy it received over the bus.
    resident_copy = publisher.items[0]
    resident_copy.resolve(outcome="applied", detail="installed probe")
    await service.ingest_event(review_resolved_event(resident_copy, source="v"))
    stored = await service.get(item.item_id)
    assert stored.status == "applied"
    assert stored.apply_detail == "installed probe"


async def test_redelivered_decided_event_never_regresses_a_resolved_item(tmp_path) -> None:
    service, publisher = _service(tmp_path)
    item = _item()
    await service.ingest_event(review_requested_event(item, source="v"))
    await service.decide(item.item_id, decision="approved", operator_id="op")
    decided = review_decided_event(publisher.items[0], source="odin")
    await service.ingest_event(decided)

    resident_copy = publisher.items[0]
    resident_copy.resolve(outcome="applied", detail="installed probe")
    await service.ingest_event(review_resolved_event(resident_copy, source="v"))
    # At-least-once delivery hands the decided event over again after the
    # resolution already landed.
    await service.ingest_event(decided)

    stored = await service.get(item.item_id)
    assert stored.status == "applied"
    assert stored.apply_outcome == "applied"
    assert stored.decided_by == "op"


async def test_reject_requires_a_reason_and_settled_items_conflict(tmp_path) -> None:
    service, _publisher = _service(tmp_path)
    item = _item()
    await service.ingest_event(review_requested_event(item, source="v"))

    with pytest.raises(ReviewDecisionError) as no_reason:
        await service.decide(item.item_id, decision="rejected", operator_id="op")
    assert no_reason.value.status_code == 422

    with pytest.raises(ReviewDecisionError) as unknown:
        await service.decide("review:none", decision="approved", operator_id="op")
    assert unknown.value.status_code == 404

    await service.decide(item.item_id, decision="approved", operator_id="op")
    with pytest.raises(ReviewDecisionError) as settled:
        await service.decide(item.item_id, decision="approved", operator_id="op")
    assert settled.value.status_code == 409


def _question(**overrides) -> ReviewItem:
    data = {
        "kind": ReviewKind.COURT_ESCALATION.value,
        "requested_action": "answer_operator_question",
        "title": "muninn asks: which window?",
        "summary": "needs a window",
        "evidence": {"operator_question": {"case_id": "case-1", "question": "Which window?"}},
    }
    data.update(overrides)
    return _item(**data)


async def test_answering_a_question_settles_it_with_the_verbatim_answer(tmp_path) -> None:
    service, publisher = _service(tmp_path)
    item = _question()
    await service.ingest_event(review_requested_event(item, source="v"))

    decided, delivery = await service.decide(
        item.item_id,
        decision="answered",
        operator_id="op",
        answer="  Tuesday, after the backup.\n",
    )

    assert decided.status == "approved"
    assert decided.decision_answer == "  Tuesday, after the backup.\n"
    assert decided.decision_reason == ""
    assert delivery["published"] is True
    assert publisher.items[0].decision_answer == "  Tuesday, after the backup.\n"
    stored = await service.get(item.item_id)
    assert stored.decision_answer == "  Tuesday, after the backup.\n"


async def test_answer_is_422_when_empty_or_on_a_non_question(tmp_path) -> None:
    service, publisher = _service(tmp_path)
    question = _question()
    other = _item()
    await service.ingest_event(review_requested_event(question, source="v"))
    await service.ingest_event(review_requested_event(other, source="v"))

    for blank in ("", "   \n"):
        with pytest.raises(ReviewDecisionError) as empty:
            await service.decide(
                question.item_id, decision="answered", operator_id="op", answer=blank
            )
        assert empty.value.status_code == 422
        assert "answer is required" in str(empty.value)

    with pytest.raises(ReviewDecisionError) as wrong_kind:
        await service.decide(other.item_id, decision="answered", operator_id="op", answer="yes")
    assert wrong_kind.value.status_code == 422
    assert "can be answered" in str(wrong_kind.value)

    with pytest.raises(ReviewDecisionError) as stray_answer:
        await service.decide(question.item_id, decision="approved", operator_id="op", answer="yes")
    assert stray_answer.value.status_code == 422

    assert publisher.items == []
    assert (await service.get(question.item_id)).status == "pending"
    assert (await service.get(other.item_id)).status == "pending"


async def test_approved_and_rejected_still_settle_a_question(tmp_path) -> None:
    service, _publisher = _service(tmp_path)
    approved, rejected = _question(), _question()
    await service.ingest_event(review_requested_event(approved, source="v"))
    await service.ingest_event(review_requested_event(rejected, source="v"))

    yes, _ = await service.decide(approved.item_id, decision="approved", operator_id="op")
    no, _ = await service.decide(
        rejected.item_id, decision="rejected", operator_id="op", reason="not now"
    )

    assert (yes.status, yes.decision_answer) == ("approved", "")
    assert (no.status, no.decision_answer, no.decision_reason) == ("rejected", "", "not now")


async def test_an_answered_question_is_not_answerable_twice(tmp_path) -> None:
    service, _publisher = _service(tmp_path)
    item = _question()
    await service.ingest_event(review_requested_event(item, source="v"))
    await service.decide(item.item_id, decision="answered", operator_id="op", answer="a")

    with pytest.raises(ReviewDecisionError) as settled:
        await service.decide(item.item_id, decision="answered", operator_id="op", answer="b")
    assert settled.value.status_code == 409
    assert (await service.get(item.item_id)).decision_answer == "a"


async def test_decided_event_carries_the_answer_into_the_ledger(tmp_path) -> None:
    service, _publisher = _service(tmp_path)
    item = _question()
    await service.ingest_event(review_requested_event(item, source="v"))
    answered = ReviewItem.from_payload(item.to_payload())
    answered.decide(decision="answered", operator_id="human:jozef", answer="Friday")

    await service.ingest_event(review_decided_event(answered, source="resident"))

    stored = await service.get(item.item_id)
    assert (stored.status, stored.decision_answer) == ("approved", "Friday")


async def test_expiry_sweep_uses_per_kind_ttls(tmp_path) -> None:
    service, _publisher = _service(
        tmp_path,
        ttl_seconds_by_kind={"court_escalation": 60.0},
        default_ttl_seconds=0.0,
    )
    stale = datetime.now(UTC) - timedelta(seconds=300)
    escalation = _item(kind=ReviewKind.COURT_ESCALATION.value, requested_action="execute_action")
    escalation.requested_at = stale.isoformat()
    build = _item()
    build.requested_at = stale.isoformat()
    await service.store.upsert(escalation)
    await service.store.upsert(build)

    assert await service.sweep_expired() == 1
    assert (await service.get(escalation.item_id)).status == "expired"
    # No default TTL: evolution builds wait for the operator indefinitely.
    assert (await service.get(build.item_id)).status == "pending"


async def test_operator_initiated_decisions_land_in_the_ledger(tmp_path) -> None:
    service, _publisher = _service(tmp_path)
    item = _item(kind=ReviewKind.AUTONOMY_CHANGE.value, requested_action="set_autonomy_mode")
    item.decide(decision="approved", operator_id="human:jozef", reason="go")
    await service.record_decided(item)
    stored = await service.get(item.item_id)
    assert stored.status == "approved"
    assert stored.decided_by == "human:jozef"


# ---------------------------------------------------------------------------
# Postgres adapter (raw SQL against a fake pool — no docker)
# ---------------------------------------------------------------------------


class _FakePool:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.executed: list[str] = []
        self.fetched: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, sql: str, *params: Any) -> None:
        self.executed.append(" ".join(sql.split()))
        item_id = params[0]
        self.rows[item_id] = {
            "item_id": params[0],
            "kind": params[1],
            "status": params[2],
            "environment_id": params[3],
            "requested_at": params[5],
            "payload": params[6],
        }

    async def fetchrow(self, sql: str, *params: Any) -> dict[str, Any] | None:
        return self.rows.get(params[0])

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        self.fetched.append((" ".join(sql.split()), params))
        rows = list(self.rows.values())
        if "GROUP BY status" in sql:
            totals: dict[str, int] = {}
            for row in rows:
                totals[row["status"]] = totals.get(row["status"], 0) + 1
            return [{"status": status, "total": total} for status, total in totals.items()]
        if "status = $1" in sql:
            rows = [row for row in rows if row["status"] == params[0]]
        return sorted(rows, key=lambda row: row["requested_at"], reverse=True)


async def test_postgres_store_round_trips_items() -> None:
    pool = _FakePool()
    store = PostgresReviewQueueStore(pool)
    item = _item()
    await store.upsert(item)

    assert "INSERT INTO odin_review_items" in pool.executed[0]
    assert "ON CONFLICT (item_id) DO UPDATE" in pool.executed[0]

    loaded = await store.get(item.item_id)
    assert loaded == item
    assert await store.get("review:none") is None

    listed = await store.list_items(status="pending")
    assert [entry.item_id for entry in listed] == [item.item_id]

    await store.list_items(risk_class="high", query="probe", limit=20, offset=20)
    sql, params = pool.fetched[-1]
    assert "payload->>'risk_class' = $1" in sql
    assert "payload->>'title' ILIKE $2" in sql
    assert "LIMIT $3 OFFSET $4" in sql
    assert params == ("high", "%probe%", 20, 20)

    assert await store.counts() == {"pending": 1}


# ---------------------------------------------------------------------------
# REST surface
# ---------------------------------------------------------------------------


class _DenyingRoom:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def require_capability(self, participant_id: str, capability: str) -> None:
        self.calls.append((participant_id, capability))
        raise HTTPException(status_code=403, detail="missing capability")


def _client(service: OdinReviewService, room_client: Any | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(create_odin_review_router(service, room_client=room_client))
    return TestClient(app)


async def test_rest_lists_decides_and_summarises(tmp_path) -> None:
    service, publisher = _service(tmp_path)
    item = _item()
    await service.ingest_event(review_requested_event(item, source="v"))
    client = _client(service)

    listed = client.get("/api/v1/ravn/odin/reviews", params={"status": "pending"}).json()
    assert [entry["item_id"] for entry in listed] == [item.item_id]

    summary = client.get("/api/v1/ravn/odin/reviews/summary").json()
    assert summary["pendingTotal"] == 1
    assert summary["pendingByKind"] == {"evolution_build": 1}
    assert summary["pendingByEnvironment"] == {"cluster-a": 1}

    detail = client.get(f"/api/v1/ravn/odin/reviews/{item.item_id}").json()
    assert detail["evidence"] == item.evidence

    decided = client.post(
        f"/api/v1/ravn/odin/reviews/{item.item_id}/decide",
        json={"decision": "approved", "participantId": "human:jozef"},
    ).json()
    assert decided["item"]["status"] == "approved"
    assert decided["commandDelivery"]["published"] is True
    assert [p.item_id for p in publisher.items] == [item.item_id]

    conflict = client.post(
        f"/api/v1/ravn/odin/reviews/{item.item_id}/decide",
        json={"decision": "approved", "participantId": "human:jozef"},
    )
    assert conflict.status_code == 409

    missing = client.get("/api/v1/ravn/odin/reviews/review:none")
    assert missing.status_code == 404


async def test_rest_filters_and_pages_reviews(tmp_path) -> None:
    service, _publisher = _service(tmp_path)
    for minute, title, risk in (
        (0, "Restart checkout", "high"),
        (1, "Restart payments", "high"),
        (2, "Ignore noise", "low"),
    ):
        item = _item(environment_id="cluster-b", title=title, risk_class=risk)
        item.requested_at = f"2026-07-14T12:0{minute}:00+00:00"
        await service.store.upsert(item)
    client = _client(service)

    response = client.get(
        "/api/v1/ravn/odin/reviews",
        params={
            "status": "pending",
            "environment_id": "cluster-b",
            "risk_class": "high",
            "q": "restart",
            "limit": 1,
            "offset": 1,
        },
    )
    summary = client.get(
        "/api/v1/ravn/odin/reviews/summary",
        params={"risk_class": "high", "q": "restart"},
    ).json()

    assert [item["title"] for item in response.json()] == ["Restart checkout"]
    assert summary["pendingTotal"] == 2
    assert summary["pendingByEnvironment"] == {"cluster-b": 2}


async def test_rest_reject_requires_reason(tmp_path) -> None:
    service, _publisher = _service(tmp_path)
    item = _item()
    await service.ingest_event(review_requested_event(item, source="v"))
    client = _client(service)

    refused = client.post(
        f"/api/v1/ravn/odin/reviews/{item.item_id}/decide",
        json={"decision": "rejected", "participantId": "human:jozef"},
    )
    assert refused.status_code == 422


async def test_rest_answers_a_question_and_validates(tmp_path) -> None:
    service, publisher = _service(tmp_path)
    question = _question()
    other = _item()
    await service.ingest_event(review_requested_event(question, source="v"))
    await service.ingest_event(review_requested_event(other, source="v"))
    client = _client(service)
    decide = "/api/v1/ravn/odin/reviews/{}/decide"

    empty = client.post(
        decide.format(question.item_id),
        json={"decision": "answered", "answer": "  ", "participantId": "human:jozef"},
    )
    assert empty.status_code == 422
    assert "answer is required" in empty.json()["detail"]

    missing = client.post(decide.format(question.item_id), json={"decision": "answered"})
    assert missing.status_code == 422

    wrong_kind = client.post(
        decide.format(other.item_id),
        json={"decision": "answered", "answer": "yes", "participantId": "human:jozef"},
    )
    assert wrong_kind.status_code == 422

    answered = client.post(
        decide.format(question.item_id),
        json={"decision": "answered", "answer": "Tuesday", "participantId": "human:jozef"},
    )
    assert answered.status_code == 200
    body = answered.json()
    assert body["item"]["status"] == "approved"
    assert body["item"]["decision_answer"] == "Tuesday"
    assert body["commandDelivery"]["published"] is True
    assert publisher.items[0].decision_answer == "Tuesday"

    again = client.post(
        decide.format(question.item_id),
        json={"decision": "answered", "answer": "Wednesday", "participantId": "human:jozef"},
    )
    assert again.status_code == 409


async def test_rest_enforces_the_items_capability(tmp_path) -> None:
    service, _publisher = _service(tmp_path)
    autonomy = _item(kind=ReviewKind.AUTONOMY_CHANGE.value, requested_action="set_autonomy_mode")
    await service.ingest_event(review_requested_event(autonomy, source="v"))
    room = _DenyingRoom()
    client = _client(service, room_client=room)

    anonymous = client.post(
        f"/api/v1/ravn/odin/reviews/{autonomy.item_id}/decide",
        json={"decision": "approved"},
    )
    assert anonymous.status_code == 403

    denied = client.post(
        f"/api/v1/ravn/odin/reviews/{autonomy.item_id}/decide",
        json={"decision": "approved", "participantId": "human:jozef"},
    )
    assert denied.status_code == 403
    assert ("human:jozef", "change_autonomy") in room.calls
    assert (await service.get(autonomy.item_id)).status == "pending"


async def test_telemetry_ingestion_routes_review_events_into_the_queue(tmp_path) -> None:
    from ravn.api.valkyries import ValkyrieDashboardProjection, create_valkyrie_router

    service, _publisher = _service(tmp_path)
    app = FastAPI()
    app.include_router(
        create_valkyrie_router(
            ValkyrieDashboardProjection(),
            review_service=service,
        )
    )
    client = TestClient(app)

    item = _item()
    event = review_requested_event(item, source="valkyrie:k8s-a")
    response = client.post(
        "/api/v1/ravn/valkyrie/telemetry/events",
        params={"minimal": "true"},
        json=event.to_dict(),
    )
    assert response.status_code == 200
    assert (await service.get(item.item_id)).status == "pending"
