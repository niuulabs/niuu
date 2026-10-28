"""Each resident's last consolidation dream on the Valkyrie dashboard."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ravn.api.valkyrie_projection import CONTROL_TELEMETRY_LIMIT, ValkyrieDashboardProjection
from sleipnir.domain.events import SleipnirEvent

VALKYRIE_ID = "valkyrie-ymir-k8s"
T0 = datetime(2026, 10, 27, 1, 0, tzinfo=UTC)


def _event(event_type: str, payload: dict, *, at: datetime) -> SleipnirEvent:
    return SleipnirEvent(
        event_type=event_type,
        source=f"valkyrie:{VALKYRIE_ID}",
        payload={"environment_id": "ymir", "valkyrie_id": VALKYRIE_ID, **payload},
        summary=event_type,
        urgency=0.2,
        domain="infrastructure",
        timestamp=at,
    )


def _completed_outcome(*, finished_at: datetime) -> dict:
    return {
        "dream_kind": "consolidation",
        "dream_id": "dream:consolidation:ymir:abc",
        "status": "completed",
        "started_at": (finished_at - timedelta(seconds=95)).isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": 95.25,
        "error": "",
        "skills_reviewed": 12,
        "promoted": ["probe"],
        "marked_stale": ["old-probe", "older-probe"],
        "promotion_candidates": ["held-probe"],
        "learnings": {
            "lessons_reviewed": 40,
            "neighbourhoods_judged": 6,
            "lessons_remaining": 120,
            "rewrites_remaining": 2,
            "merged": ["lessons/a.md", "lessons/b.md"],
            "superseded": ["lessons/c.md"],
            "pruned": [],
            "promoted": [],
            "rewritten": ["lessons/d.md"],
            "audit_path": "curation/2026-10-27.md",
            "self_improvement": {
                "evaluated": ["friction-1:kept", "friction-2:dropped"],
                "actions": ["commission_tool:friction-1"],
                "rejected": 1,
                "open_friction": 3,
                "audit_path": "",
            },
        },
        "learned_tools": {
            "installed_before": 30,
            "retired": [
                {
                    "name": "k8s_quantity_parse_v1",
                    "reason": "superseded",
                    "detail": "k8s_quantity_parse does this",
                    "superseded_by": "k8s_quantity_parse",
                },
                {"name": "debug_dump", "reason": "debug_leftover", "detail": "scratch"},
                {"name": "probe_x", "reason": "superseded", "detail": "", "superseded_by": "p"},
            ],
            "restored": ["revived_tool"],
            "backfilled": {"tool_a": "cap-a"},
            "kept": 26,
        },
        "feedback": {"positive": 4, "negative": 1, "delivery": 0, "implicated_skills": []},
    }


def _valkyrie(projection: ValkyrieDashboardProjection) -> dict:
    return next(
        entry for entry in projection.dashboard()["valkyries"] if entry["id"] == VALKYRIE_ID
    )


def test_completed_dream_carries_time_duration_and_summary_counts() -> None:
    projection = ValkyrieDashboardProjection()
    finished = T0 + timedelta(minutes=2)
    projection.record_event(
        _event("valkyrie.dream.completed", _completed_outcome(finished_at=finished), at=finished)
    )

    valkyrie = _valkyrie(projection)
    dream = valkyrie["lastDream"]
    assert valkyrie["lastDreamAt"] == finished.isoformat()
    assert dream["status"] == "completed"
    assert dream["dreamId"] == "dream:consolidation:ymir:abc"
    assert dream["durationSeconds"] == 95.25
    assert dream["startedAt"] == (finished - timedelta(seconds=95)).isoformat()
    assert dream["error"] == ""
    assert dream["skills"] == {
        "reviewed": 12,
        "promoted": ["probe"],
        "markedStale": ["old-probe", "older-probe"],
        "promotionCandidates": ["held-probe"],
    }
    assert dream["lessons"]["reviewed"] == 40
    assert dream["lessons"]["remaining"] == 120
    assert dream["lessons"]["rewritesRemaining"] == 2
    assert dream["lessons"]["merged"] == ["lessons/a.md", "lessons/b.md"]
    assert dream["lessons"]["superseded"] == ["lessons/c.md"]
    assert dream["lessons"]["rewritten"] == ["lessons/d.md"]
    assert dream["learnedTools"]["retiredByReason"] == {"superseded": 2, "debug_leftover": 1}
    assert dream["learnedTools"]["retired"][0] == {
        "name": "k8s_quantity_parse_v1",
        "reason": "superseded",
        "detail": "k8s_quantity_parse does this",
        "supersededBy": "k8s_quantity_parse",
    }
    assert dream["learnedTools"]["restored"] == ["revived_tool"]
    assert dream["learnedTools"]["installedBefore"] == 30
    assert dream["learnedTools"]["kept"] == 26
    assert dream["learnedTools"]["backfilled"] == 1
    assert dream["selfImprovement"] == {
        "evaluated": 2,
        "actions": ["commission_tool:friction-1"],
        "rejected": 1,
        "openFriction": 3,
    }
    assert dream["feedback"] == {"positive": 4, "negative": 1, "delivery": 0}


def test_last_dream_survives_the_bounded_event_window() -> None:
    """The live failure: a busy fleet evicted the completed event within minutes."""
    projection = ValkyrieDashboardProjection()
    finished = T0
    projection.record_event(
        _event("valkyrie.dream.completed", _completed_outcome(finished_at=finished), at=finished)
    )
    projection.record_event(
        _event(
            "valkyrie.state.changed",
            {"previous_state": "dreaming", "new_state": "watching"},
            at=finished + timedelta(seconds=1),
        )
    )
    for index in range(CONTROL_TELEMETRY_LIMIT + 10):
        projection.record_event(
            _event(
                "odin.review.requested",
                {"item_id": f"review-{index}"},
                at=finished + timedelta(seconds=2 + index),
            )
        )

    valkyrie = _valkyrie(projection)
    assert valkyrie["wakefulness"] == "watching"
    assert valkyrie["lastDreamAt"] == finished.isoformat()
    assert valkyrie["lastDream"]["status"] == "completed"


def test_failed_dream_is_shown_as_failed_with_its_error() -> None:
    projection = ValkyrieDashboardProjection()
    finished = T0 + timedelta(minutes=5)
    projection.record_event(
        _event(
            "valkyrie.dream.failed",
            {
                "dream_kind": "consolidation",
                "dream_id": "dream:consolidation:ymir:bad",
                "status": "failed",
                "started_at": T0.isoformat(),
                "finished_at": finished.isoformat(),
                "duration_seconds": 300.0,
                "error": "MimirUnavailable: 503 from mimir-shared",
            },
            at=finished,
        )
    )

    dream = _valkyrie(projection)["lastDream"]
    assert dream["status"] == "failed"
    assert dream["error"] == "MimirUnavailable: 503 from mimir-shared"
    assert dream["durationSeconds"] == 300.0
    assert dream["lessons"] is None
    assert dream["learnedTools"] is None
    assert dream["selfImprovement"] is None


def test_restarted_dashboard_recovers_the_dream_from_the_snapshot() -> None:
    projection = ValkyrieDashboardProjection()
    finished = T0
    projection.record_event(
        _event(
            "valkyrie.state.changed",
            {"previous_state": "sleeping", "new_state": "watching"},
            at=finished + timedelta(minutes=30),
        )
    )
    snapshot = _event(
        "valkyrie.dream.snapshot",
        {"last_dream": _completed_outcome(finished_at=finished)},
        at=finished + timedelta(minutes=31),
    )
    projection.record_event(snapshot)

    valkyrie = _valkyrie(projection)
    # The dream finished when it finished, not when the snapshot was sent.
    assert valkyrie["lastDreamAt"] == finished.isoformat()
    assert valkyrie["lastDream"]["learnedTools"]["retiredByReason"]["superseded"] == 2
    # Snapshots are a recovery channel, not activity.
    telemetry = projection.dashboard()["telemetry"]
    assert all(
        entry["eventType"] != "valkyrie.dream.snapshot" for entry in telemetry["recentEvents"]
    )


def test_snapshot_before_any_dream_records_nothing() -> None:
    projection = ValkyrieDashboardProjection()
    projection.record_event(
        _event(
            "valkyrie.state.changed",
            {"previous_state": "sleeping", "new_state": "watching"},
            at=T0,
        )
    )
    projection.record_event(_event("valkyrie.dream.snapshot", {"last_dream": None}, at=T0))

    valkyrie = _valkyrie(projection)
    assert "lastDream" not in valkyrie
    assert valkyrie["lastDreamAt"] == ""


def test_an_older_snapshot_never_replaces_a_newer_dream() -> None:
    projection = ValkyrieDashboardProjection()
    newer = T0 + timedelta(hours=1)
    projection.record_event(
        _event(
            "valkyrie.dream.failed",
            {
                "status": "failed",
                "finished_at": newer.isoformat(),
                "error": "RuntimeError: boom",
            },
            at=newer,
        )
    )
    projection.record_event(
        _event(
            "valkyrie.dream.snapshot",
            {"last_dream": _completed_outcome(finished_at=T0)},
            at=newer + timedelta(minutes=1),
        )
    )

    dream = _valkyrie(projection)["lastDream"]
    assert dream["status"] == "failed"
    assert dream["finishedAt"] == newer.isoformat()


def test_noop_dream_without_timing_finishes_at_its_event_time() -> None:
    projection = ValkyrieDashboardProjection()
    projection.record_event(_event("valkyrie.dream.noop", {"dream_id": "noop-1"}, at=T0))

    dream = _valkyrie(projection)["lastDream"]
    assert dream["status"] == "noop"
    assert dream["finishedAt"] == T0.isoformat()
    assert dream["durationSeconds"] == 0.0


def test_dream_events_without_a_valkyrie_id_are_not_attributed() -> None:
    projection = ValkyrieDashboardProjection()
    projection.record_event(
        SleipnirEvent(
            event_type="valkyrie.dream.completed",
            source="ravn:valkyrie:ymir",
            payload={"environment_id": "ymir", "dream_id": "dream:ymir:1"},
            summary="dream complete",
            urgency=0.2,
            domain="infrastructure",
            timestamp=T0,
        )
    )

    assert projection._last_dreams == {}
