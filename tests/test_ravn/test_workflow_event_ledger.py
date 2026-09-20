"""Tests for the durable workflow-event dedupe ledger's bounded retention.

R-B: the ledger must be a true bounded insertion-ordered FIFO window — it must
evict the oldest entry to admit a new one rather than raising once full, and
loading an oversized journal (e.g. after lowering the configured limit) must
trim instead of refusing to start. The same applies to the authoritative
workflow-cycle ledger, which has no natural "this scope is finished" signal.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from ravn.adapters.personas.loader import PersonaConfig, PersonaConsumes
from ravn.config import Settings
from ravn.domain.events import RavnEvent, RavnEventType
from tests.test_ravn.conftest import _make_drive_loop


@pytest.mark.asyncio
async def test_record_workflow_event_consumed_evicts_oldest_instead_of_raising(tmp_path) -> None:
    dl = _make_drive_loop(journal_path=str(tmp_path / "queue.json"))
    dl._workflow_event_dedupe_limit = 2

    await dl.record_workflow_event_consumed("event-1")
    await dl.record_workflow_event_consumed("event-2")
    assert dl.workflow_event_consumed("event-1")
    assert dl.workflow_event_consumed("event-2")

    # A third event at capacity must evict the oldest, never raise.
    await dl.record_workflow_event_consumed("event-3")

    assert not dl.workflow_event_consumed("event-1")
    assert dl.workflow_event_consumed("event-2")
    assert dl.workflow_event_consumed("event-3")
    assert len(dl._consumed_workflow_event_ids) == 2


@pytest.mark.asyncio
async def test_record_workflow_event_consumed_rollback_restores_evicted_entry(tmp_path) -> None:
    dl = _make_drive_loop(journal_path=str(tmp_path / "queue.json"))
    dl._workflow_event_dedupe_limit = 1
    await dl.record_workflow_event_consumed("event-1")

    # Make persistence fail so the eviction + insert must both roll back.
    dl._journal_path = dl._journal_path.parent / "unwritable" / "queue.json"
    dl._journal_path.parent.mkdir(parents=True, exist_ok=True)
    dl._journal_path.parent.chmod(0o400)
    try:
        with pytest.raises(RuntimeError, match="failed to persist consumed workflow event"):
            await dl.record_workflow_event_consumed("event-2")
    finally:
        dl._journal_path.parent.chmod(0o700)

    assert dl.workflow_event_consumed("event-1")
    assert not dl.workflow_event_consumed("event-2")
    assert len(dl._consumed_workflow_event_ids) == 1


@pytest.mark.asyncio
async def test_load_journal_trims_oversized_consumed_event_ledger(tmp_path) -> None:
    journal_path = tmp_path / "queue.json"
    journal_path.write_text(
        json.dumps(
            {
                "queue": [],
                "inflight": [],
                "consumed_workflow_events": [
                    {"event_id": f"event-{i}", "consumed_at": f"t{i}"} for i in range(5)
                ],
            }
        )
    )
    dl = _make_drive_loop(journal_path=str(journal_path))
    dl._workflow_event_dedupe_limit = 2

    dl._load_journal()

    # Trims to the newest entries instead of raising and wedging the restart.
    assert len(dl._consumed_workflow_event_ids) == 2
    assert not dl.workflow_event_consumed("event-0")
    assert not dl.workflow_event_consumed("event-2")
    assert dl.workflow_event_consumed("event-3")
    assert dl.workflow_event_consumed("event-4")


@pytest.mark.asyncio
async def test_load_journal_trims_oversized_workflow_cycle_ledger(tmp_path) -> None:
    journal_path = tmp_path / "queue.json"
    journal_path.write_text(
        json.dumps(
            {
                "queue": [],
                "inflight": [],
                "workflow_cycles": [
                    {
                        "scope_id": "scope",
                        "node_id": f"node-{i}",
                        "event_type": "review.completed",
                        "event_id": f"event-{i}",
                        "outcome": {},
                        "timestamp": f"t{i}",
                    }
                    for i in range(5)
                ],
            }
        )
    )
    dl = _make_drive_loop(journal_path=str(journal_path))
    dl._workflow_cycle_limit = 2

    dl._load_journal()

    assert len(dl._workflow_cycles) == 2
    assert ("scope", "node-0", "review.completed") not in dl._workflow_cycles
    assert ("scope", "node-3", "review.completed") in dl._workflow_cycles
    assert ("scope", "node-4", "review.completed") in dl._workflow_cycles


def test_observe_workflow_cycle_evicts_oldest_scope_at_capacity(tmp_path) -> None:
    dl = _make_drive_loop(journal_path=str(tmp_path / "queue.json"))
    dl._workflow_cycle_limit = 2
    dl.set_workflow_review_cycle_sources({("stage", "review.completed")})
    import datetime as dt

    timestamp = dt.datetime.now(dt.UTC)
    dl.observe_workflow_cycle(
        scope_id="scope-1",
        node_id="stage",
        event_type="review.completed",
        event_id="event-1",
        outcome={},
        timestamp=timestamp,
    )
    dl.observe_workflow_cycle(
        scope_id="scope-2",
        node_id="stage",
        event_type="review.completed",
        event_id="event-2",
        outcome={},
        timestamp=timestamp + dt.timedelta(seconds=1),
    )
    assert len(dl._workflow_cycles) == 2

    dl.observe_workflow_cycle(
        scope_id="scope-3",
        node_id="stage",
        event_type="review.completed",
        event_id="event-3",
        outcome={},
        timestamp=timestamp + dt.timedelta(seconds=2),
    )

    assert len(dl._workflow_cycles) == 2
    assert ("scope-1", "stage", "review.completed") not in dl._workflow_cycles
    assert ("scope-2", "stage", "review.completed") in dl._workflow_cycles
    assert ("scope-3", "stage", "review.completed") in dl._workflow_cycles


def _filtered_settings() -> Settings:
    return Settings.model_validate(
        {
            "mesh": {"enabled": True},
            "discovery": {"enabled": False},
            "workflow": {
                "graph": {
                    "nodes": [
                        {
                            "id": "author",
                            "kind": "stage",
                            "joinMode": "any",
                            "stageMembers": [
                                {
                                    "personaId": "analyst",
                                    "consumesEventTypes": ["delivery.requested"],
                                    "eventFilters": {"priority": "urgent"},
                                }
                            ],
                        }
                    ],
                    "edges": [],
                }
            },
        }
    )


@pytest.mark.asyncio
async def test_unmatched_filtered_event_is_not_recorded_as_consumed(tmp_path) -> None:
    """An event a consumer group's filters reject has no dedupe consumer.

    Recording it anyway would only spend ledger retention on events nothing
    here needs to dedupe against redelivery.
    """
    from ravn.cli.commands import _wire_cascade  # type: ignore[attr-defined]

    settings = _filtered_settings()
    dl = _make_drive_loop(journal_path=str(tmp_path / "queue.json"))
    mesh = MagicMock()
    persona = PersonaConfig(
        name="analyst",
        consumes=PersonaConsumes(event_types=["delivery.requested"]),
    )
    with patch("ravn.cli.commands._build_mesh", return_value=mesh):
        _wire_cascade(dl, settings, persona)
    handlers = dict(mesh._pending_outcome_subscriptions)

    event = RavnEvent(
        type=RavnEventType.OUTCOME,
        source="flock-producer",
        payload={
            "event_type": "delivery.requested",
            "persona": "producer",
            "outcome": {},
            "success": True,
            "valid": True,
            # No "priority" field at all, so the group's eventFilters reject it.
        },
        timestamp=datetime.now(UTC),
        urgency=0.5,
        correlation_id="task-filtered-1",
        session_id="session-1",
        root_correlation_id="root-1",
        event_id="filtered-event-1",
    )

    await handlers["delivery.requested"](event)

    assert not dl.workflow_event_consumed("filtered-event-1")
    assert not dl._consumed_workflow_event_ids

    # A matching event with the required field IS recorded.
    matching_event = RavnEvent(
        type=RavnEventType.OUTCOME,
        source="flock-producer",
        payload={
            "event_type": "delivery.requested",
            "persona": "producer",
            "outcome": {},
            "success": True,
            "valid": True,
            "priority": "urgent",
        },
        timestamp=datetime.now(UTC),
        urgency=0.5,
        correlation_id="task-matched-1",
        session_id="session-1",
        root_correlation_id="root-1",
        event_id="matched-event-1",
    )
    await handlers["delivery.requested"](matching_event)
    assert dl.workflow_event_consumed("matched-event-1")
