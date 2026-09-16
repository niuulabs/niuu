"""Repair must prove chronology from captured boundaries without rewriting history."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from niuu.domain.text_projection import (
    REPAIR_KEY,
    apply_repair_markers,
    projection_revision,
    repair_legacy_turn,
    repair_legacy_turns,
    repair_marker,
)
from niuu.domain.transcript_reducer import NON_BROADCAST_KINDS, reduce_frames
from skuld.history_hydration import merge_history_turns
from volundr.domain.services.transcript_rebuild import rebuild_turns


def captured():
    """Sanitized incident shape: queued human precedes prior native completion."""
    payloads = [
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        {"type": "content_block_delta", "delta": {"text": "Checking café.\n"}},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "call", "name": "exec", "input": {"cmd": "true"}},
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "call", "content": "ok"},
                ]
            },
        },
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        {"type": "content_block_delta", "delta": {"text": "**Done.** 東京"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "result", "stop_reason": "end_turn"},
    ]
    frames = [
        SimpleNamespace(
            session_id="session",
            seq=index,
            kind=payload["type"],
            ts=None,
            request_id=None,
            payload={**payload, "thread_id": "thread", "turn_id": "native-turn"},
        )
        for index, payload in enumerate(payloads, 1)
    ]
    [candidate] = reduce_frames(frames).turns
    original = {
        **candidate,
        "content": "Checking café.\n**Done.** 東京",
        "parts": [part for part in candidate["parts"] if part["type"] != "text"],
        "created_at": "2026-09-08T00:00:00Z",
        "metadata": {"cost": 0.25, "usage": {"output_tokens": 17}},
    }
    return frames, original, candidate


def test_exact_repair_keeps_all_nonprojection_fields_and_source_objects():
    frames, original, _ = captured()
    before = deepcopy(original)
    [repaired] = repair_legacy_turns([original], frames)
    assert repaired["id"] == original["id"]
    assert repaired["content"] == "Checking café.\n\n\n**Done.** 東京"
    assert [part["type"] for part in repaired["parts"]] == [
        "text",
        "tool_use",
        "tool_result",
        "text",
    ]
    assert [part for part in repaired["parts"] if part["type"] != "text"] == original["parts"]
    assert all(
        part["id_source"] == "synthetic" and "phase" not in part
        for part in repaired["parts"]
        if part["type"] == "text"
    )
    assert repaired["created_at"] == original["created_at"]
    assert {key: repaired["metadata"][key] for key in original["metadata"]} == original["metadata"]
    assert original == before
    assert repair_legacy_turns([repaired], frames) == [repaired]


@pytest.mark.parametrize("mutation", ["text", "tool", "identity", "incomplete", "private"])
def test_ambiguous_repair_is_rejected(mutation):
    _, original, candidate = captured()
    if mutation == "text":
        candidate["parts"][0]["text"] += "!"
    elif mutation == "tool":
        candidate["parts"][1]["input"] = {"cmd": "false"}
    elif mutation == "identity":
        candidate["id"] = "different-turn"
    elif mutation == "incomplete":
        candidate["parts"][0]["complete"] = False
    else:
        candidate["parts"][0]["phase"] = "analysis"
    # Do not share mutable tool dictionaries with the original fixture.
    _, original, _ = captured()
    assert repair_legacy_turn(original, candidate) is None


def test_cold_seed_repair_groups_native_turn_across_queued_human():
    frames, original, _ = captured()
    queued = SimpleNamespace(
        session_id="session",
        seq=8.5,
        kind="user",
        ts=None,
        request_id=None,
        payload={
            "uuid": "queued",
            "message": {"content": "Next task"},
            "thread_id": "thread",
            "turn_id": "native-turn",
        },
    )
    seed = SimpleNamespace(
        session_id="session",
        seq=10,
        kind="conversation.turn",
        ts=None,
        request_id=None,
        payload={"turn": original},
    )
    result = rebuild_turns([*frames, queued, seed])
    assert len(result.turns) == 1
    assert result.turns[0]["id"] == original["id"]
    assert len([part for part in result.turns[0]["parts"] if part["type"] == "text"]) == 2


def test_gap_or_missing_boundaries_preserve_legacy_without_guessing():
    frames, original, _ = captured()
    gap = SimpleNamespace(kind="log_gap", payload={}, seq=12)
    assert repair_legacy_turns([original], [*frames, gap]) == [original]
    assert repair_legacy_turns(
        [original], [frame for frame in frames if frame.kind != "content_block_stop"]
    ) == [original]


def test_marker_is_idempotent_and_rejected_after_original_projection_changes():
    frames, original, candidate = captured()
    repaired = repair_legacy_turn(original, candidate, source_head=9)
    marker = SimpleNamespace(
        kind="conversation.projection", seq=11, payload=repair_marker([repaired])
    )
    assert apply_repair_markers([original], [marker]) == [repaired]
    assert apply_repair_markers([repaired], [marker, marker]) == [repaired]
    changed = {**original, "content": original["content"] + "new"}
    assert apply_repair_markers([changed], [marker]) == [changed]
    marker.seq = 8
    assert apply_repair_markers([original], [marker]) == [original]
    assert "conversation.projection" in NON_BROADCAST_KINDS


def test_revision_changes_once_for_repair_and_remains_stable_on_normal_appends():
    _, original, candidate = captured()
    repaired = repair_legacy_turn(original, candidate, source_head=9)
    assert projection_revision([original]) == "text-items-1:0"
    revision = projection_revision([repaired])
    assert revision != projection_revision([original])
    assert projection_revision([repaired, {"id": "later", "role": "user"}]) == revision


def test_hydration_repairs_flat_cache_but_retains_verified_native_enrichment():
    _, original, candidate = captured()
    [repaired] = merge_history_turns([candidate], [original])
    assert REPAIR_KEY in repaired["metadata"]
    native = deepcopy(candidate)
    native["parts"][0].update(id="native-message", phase="commentary")
    native["parts"][0].pop("id_source")
    enriched = repair_legacy_turn(original, native, source_head=9, source="native_log_verified")
    assert merge_history_turns([candidate], [enriched]) == [enriched]


def test_cold_rebuild_prefers_verified_native_marker_over_synthetic_reconstruction():
    frames, original, candidate = captured()
    candidate["parts"][0].update(id="native-message", phase="commentary")
    repaired = repair_legacy_turn(original, candidate, source_head=9, source="native_log_verified")
    rows = [
        *frames,
        SimpleNamespace(
            session_id="session",
            seq=10,
            kind="conversation.turn",
            request_id=None,
            ts=None,
            payload={"turn": original},
        ),
        SimpleNamespace(
            session_id="session",
            seq=11,
            kind="conversation.projection",
            request_id=None,
            ts=None,
            payload=repair_marker([repaired]),
        ),
    ]
    assert rebuild_turns(rows).turns == [repaired]


@pytest.mark.parametrize("identity", [[], {}, 1, ""])
def test_malformed_text_or_marker_identity_cannot_break_history(identity):
    _, original, candidate = captured()
    candidate["parts"][0]["id"] = identity
    assert repair_legacy_turn(original, candidate) is None
    marker = SimpleNamespace(
        kind="conversation.projection",
        seq=11,
        payload={"schema": 1, "repairs": [{"turn_id": identity}]},
    )
    assert apply_repair_markers([original], [marker]) == [original]


def test_duplicate_item_identity_cannot_create_two_collapsing_client_anchors():
    _, original, candidate = captured()
    candidate["parts"][-1]["id"] = candidate["parts"][0]["id"]
    assert repair_legacy_turn(original, candidate) is None


def test_malformed_metadata_and_messages_do_not_break_repair_or_revision():
    frames, original, _ = captured()
    original["metadata"] = "old malformed metadata"
    malformed = SimpleNamespace(
        seq=11, kind="user", payload={"message": None, "thread_id": "thread", "turn_id": "turn"}
    )
    [repaired] = repair_legacy_turns([original], [*frames, malformed])
    assert REPAIR_KEY in repaired["metadata"]
    assert projection_revision([original, None]) == "text-items-1:0"
