"""Operator feedback and revision endpoints for Valkyrie learnings."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ravn.api.valkyries import (
    OdinReviewCommandPublisher,
    ValkyrieDashboardProjection,
    create_valkyrie_router,
)
from sleipnir.domain.events import SleipnirEvent


class FakeSleipnirPublisher:
    def __init__(self) -> None:
        self.events: list[SleipnirEvent] = []

    async def publish(self, event: SleipnirEvent) -> None:
        self.events.append(event)


def _skill_content() -> str:
    return "\n".join(
        [
            "# skill: valkyrie-inspect-printer-printer-resin-low",
            "metadata:",
            "  capability: inspect.printer.printer.resin-low",
            "  safety_class: read_only",
        ]
    )


def _learning_client() -> tuple[TestClient, ValkyrieDashboardProjection, FakeSleipnirPublisher]:
    projection = ValkyrieDashboardProjection()
    publisher = FakeSleipnirPublisher()
    app = FastAPI()
    app.include_router(
        create_valkyrie_router(
            projection,
            review_command_publisher=OdinReviewCommandPublisher(publisher),
        )
    )
    return TestClient(app), projection, publisher


def _seed_learning(
    client: TestClient,
    *,
    scope: str = "flock",
    event_id: str = "proof-built-feedback",
) -> dict:
    event = {
        "event_id": event_id,
        "event_type": "valkyrie.evolution.built",
        "source": "ravn:valkyrie-evolution-proof",
        "payload": {
            "environment_id": "local-proof",
            "request_id": "evolve-gap-printer",
            "skill_name": "valkyrie-inspect-printer-printer-resin-low",
            "artifact_type": "ravn_skill_tool",
            "skill_content": _skill_content(),
            "target_scope": scope,
            "confidence": 0.7,
            "gap": {
                "capability_name": "inspect.printer.printer.resin-low",
                "environment_id": "local-proof",
                "source_valkyrie_id": "valkyrie-local",
                "signal_ids": ["sig-printer-resin-low"],
            },
        },
        "summary": "Built skill valkyrie-inspect-printer-printer-resin-low",
        "timestamp": "2026-06-04T20:09:00+00:00",
    }
    dashboard = client.post("/api/v1/ravn/valkyrie/telemetry/events", json=event).json()
    return next(
        entry
        for entry in dashboard["learnings"]
        if entry["title"] == "valkyrie-inspect-printer-printer-resin-low"
    )


def _feedback(client: TestClient, learning_id: str, verdict: str, **extra):
    return client.post(
        f"/api/v1/ravn/valkyrie/learnings/{learning_id}/feedback",
        json={"verdict": verdict, "operatorId": "test-operator", **extra},
    )


def _adopt(client: TestClient, learning_id: str) -> dict:
    return client.post(
        f"/api/v1/ravn/valkyrie/learnings/{learning_id}/adopt",
        json={"learningId": learning_id, "operatorId": "test-operator"},
    ).json()


def test_feedback_unknown_learning_returns_404():
    client, _projection, _publisher = _learning_client()

    response = _feedback(client, "missing-learning", "useful")

    assert response.status_code == 404


def test_feedback_unknown_verdict_returns_422():
    client, _projection, _publisher = _learning_client()
    learning = _seed_learning(client)

    response = _feedback(client, learning["id"], "meh")

    assert response.status_code == 422
    assert "verdict" in response.json()["detail"]


def test_feedback_wrong_tier_without_target_scope_returns_422():
    client, _projection, _publisher = _learning_client()
    learning = _seed_learning(client)

    response = _feedback(client, learning["id"], "wrong_tier")

    assert response.status_code == 422
    assert "targetScope" in response.json()["detail"]


def test_feedback_useful_records_verdict_and_publishes_command():
    client, _projection, publisher = _learning_client()
    learning = _seed_learning(client)

    response = _feedback(client, learning["id"], "useful", reason="caught a real gap")

    assert response.status_code == 200
    updated = response.json()
    assert updated["status"] == learning["status"]
    assert updated["feedback"]["verdict"] == "useful"
    assert updated["feedback"]["reason"] == "caught a real gap"
    assert updated["feedback"]["operatorId"] == "test-operator"
    assert updated["feedback"]["recordedAt"]
    assert any(entry["eventType"] == "valkyrie.learning.feedback" for entry in updated["history"])
    assert updated["commandDelivery"]["published"] is True
    assert updated["commandDelivery"]["eventType"] == "odin.review.decided"
    command = publisher.events[-1]
    assert command.event_type == "odin.review.decided"
    assert command.payload["requested_action"] == "feedback"
    assert command.payload["status"] == "approved"
    assert command.payload["evidence"]["feedback"]["verdict"] == "useful"

    refreshed = client.get(f"/api/v1/ravn/valkyrie/learnings/{learning['id']}").json()
    assert refreshed["feedback"]["verdict"] == "useful"
    assert refreshed["repetition"] == 1
    assert refreshed["supersedes"] == ""


def test_feedback_good_action_keeps_status_unchanged():
    client, _projection, publisher = _learning_client()
    learning = _seed_learning(client)
    adopted = _adopt(client, learning["id"])
    assert adopted["status"] == "adopted"

    updated = _feedback(client, learning["id"], "good_action").json()

    assert updated["status"] == "adopted"
    assert updated["active"] is True
    assert updated["feedback"]["verdict"] == "good_action"
    assert publisher.events[-1].payload["requested_action"] == "feedback"


def test_feedback_dismissed_rejects_the_learning():
    client, _projection, publisher = _learning_client()
    learning = _seed_learning(client)

    updated = _feedback(client, learning["id"], "dismissed", reason="not needed").json()

    assert updated["status"] == "rejected"
    assert updated["active"] is False
    assert updated["feedback"]["verdict"] == "dismissed"
    command = publisher.events[-1]
    assert command.payload["requested_action"] == "adopt"
    assert command.payload["status"] == "rejected"
    assert command.payload["evidence"]["feedback"]["verdict"] == "dismissed"


def test_feedback_bad_action_on_candidate_rejects():
    client, _projection, publisher = _learning_client()
    learning = _seed_learning(client)

    updated = _feedback(client, learning["id"], "bad_action").json()

    assert updated["status"] == "rejected"
    assert updated["active"] is False
    command = publisher.events[-1]
    assert command.payload["requested_action"] == "adopt"
    assert command.payload["status"] == "rejected"


def test_feedback_bad_action_on_adopted_rolls_back():
    client, _projection, publisher = _learning_client()
    learning = _seed_learning(client)
    _adopt(client, learning["id"])

    updated = _feedback(client, learning["id"], "bad_action", reason="broke things").json()

    assert updated["status"] == "rolled_back"
    assert updated["active"] is False
    assert updated["feedback"]["verdict"] == "bad_action"
    command = publisher.events[-1]
    assert command.payload["requested_action"] == "retract"
    assert command.payload["status"] == "approved"
    assert any(
        entry["eventType"] == "valkyrie.learning.rolled_back" for entry in updated["history"]
    )


def test_feedback_wrong_tier_promotes_to_adjacent_scope():
    client, _projection, publisher = _learning_client()
    learning = _seed_learning(client)
    assert learning["scope"] == "flock"

    updated = _feedback(client, learning["id"], "wrong_tier", targetScope="shared").json()

    assert updated["scope"] == "shared"
    assert updated["feedback"]["verdict"] == "wrong_tier"
    command = publisher.events[-1]
    assert command.payload["kind"] == "skill_promotion"
    assert command.payload["requested_action"] == "promote"
    assert command.payload["evidence"]["to_scope"] == "shared"
    assert command.payload["evidence"]["feedback"]["verdict"] == "wrong_tier"


def test_feedback_wrong_tier_demotes_to_lower_scope():
    client, _projection, publisher = _learning_client()
    learning = _seed_learning(client)

    updated = _feedback(client, learning["id"], "wrong_tier", targetScope="domain").json()

    assert updated["scope"] == "domain"
    command = publisher.events[-1]
    assert command.payload["kind"] == "skill_promotion"
    assert command.payload["requested_action"] == "demote"
    assert command.payload["evidence"]["to_scope"] == "domain"


def test_feedback_wrong_tier_surfaces_promotion_adjacency_422():
    client, _projection, publisher = _learning_client()
    learning = _seed_learning(client, scope="environment")
    assert learning["scope"] == "environment"

    response = _feedback(client, learning["id"], "wrong_tier", targetScope="shared")

    assert response.status_code == 422
    assert "one step" in response.json()["detail"]
    refreshed = client.get(f"/api/v1/ravn/valkyrie/learnings/{learning['id']}").json()
    assert refreshed["feedback"] is None
    assert refreshed["scope"] == "environment"
    assert not publisher.events


def test_feedback_survives_projection_refresh():
    client, projection, _publisher = _learning_client()
    learning = _seed_learning(client)
    _feedback(client, learning["id"], "useful", reason="repeat proof")

    # Force several live re-ingest cycles; the feedback must not evaporate.
    projection.dashboard()
    refreshed = client.get(f"/api/v1/ravn/valkyrie/learnings/{learning['id']}").json()

    assert refreshed["feedback"]["verdict"] == "useful"
    assert any(entry["eventType"] == "valkyrie.learning.feedback" for entry in refreshed["history"])


def test_dashboard_learnings_carry_repetition_from_telemetry():
    client, _projection, _publisher = _learning_client()
    event = {
        "event_id": "proof-repetition",
        "event_type": "learning.adoption.recorded",
        "source": "ravn:test",
        "payload": {
            "environment_id": "local-proof",
            "learning_id": "learn-repeated",
            "action": "adopted",
            "repetition": 3,
        },
        "summary": "repeat learning observed",
        "timestamp": "2026-06-04T21:00:00+00:00",
    }

    dashboard = client.post("/api/v1/ravn/valkyrie/telemetry/events", json=event).json()

    learning = next(
        entry for entry in dashboard["learnings"] if entry["id"] == "live-learn-repeated"
    )
    assert learning["repetition"] == 3
    assert learning["supersedes"] == ""
    assert learning["feedback"] is None
