"""The replay corpus is real wire data with review and mutation guards."""

import json
from pathlib import Path
from uuid import uuid4

import pytest

from scripts.forge_corpus import entries, inspect_bundle, promote, replay_fixture, write_viewer
from scripts.forge_trace import digest, write_json
from volundr.domain.services.transcript_rebuild import rebuild_turns


def bundle_at(path):
    path.mkdir()
    sid = str(uuid4())
    frames = [
        {
            "seq": 1,
            "session_id": sid,
            "kind": "user_confirmed",
            "ts": "2026-09-07T00:00:00Z",
            "payload": {"type": "user_confirmed", "id": "u1", "content": "Hello"},
        },
        {
            "seq": 2,
            "session_id": sid,
            "kind": "assistant",
            "ts": "2026-09-07T00:00:01Z",
            "payload": {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Hello from the replay"}]},
            },
        },
        {
            "seq": 3,
            "session_id": sid,
            "kind": "result",
            "ts": "2026-09-07T00:00:02Z",
            "payload": {"type": "result", "result": "Hello from the replay", "is_error": False},
        },
    ]
    manifest = {
        "provider": "codex",
        "model": "test",
        "run_id": "run",
        "session_id": sid,
        "review": {"status": "pending"},
        "passed": False,
        "errors": ["deployment replay route absent"],
        "scenarios": [
            {
                "scenario": "hello",
                "passed": True,
                "after_seq": 0,
                "through_seq": 3,
                "checks": [],
                "prompt": "Hello",
            }
        ],
    }
    write_json(path / "manifest.json", manifest)
    write_json(path / "session.frames.json", frames)
    write_json(path / "live.json", [{"payload": r["payload"]} for r in frames])
    return frames


def test_inspector_exercises_production_replay_router_full_and_cursor(tmp_path):
    bundle = tmp_path / "bundle"
    frames = bundle_at(bundle)
    audit = inspect_bundle(bundle, frames)
    assert audit["database_verified"]
    assert audit["fixture_replay"]["passed"]
    assert audit["cursor_fixture_replay"]["passed"]
    assert audit["live_persistence"]["passed"]
    assert (bundle / "review.html").exists()
    assert (bundle / "observed.turns.json").exists()
    replay = replay_fixture(bundle, "session", after=1, preamble=True)
    assert [f["type"] for f in replay[:3]] == ["system", "capabilities", "conversation_history"]
    assert replay[1]["send_message"] is False


def test_database_projection_mismatch_stays_failed(tmp_path):
    bundle = tmp_path / "bundle"
    frames = bundle_at(bundle)
    audit = inspect_bundle(bundle, frames[:-1])
    assert not audit["database_verified"]


def test_inspecting_without_database_never_claims_database_verification(tmp_path):
    bundle = tmp_path / "bundle"
    bundle_at(bundle)
    assert not inspect_bundle(bundle)["database_verified"]


def test_promotion_requires_review_bound_to_actual_evidence(tmp_path):
    bundle = tmp_path / "bundle"
    frames = bundle_at(bundle)
    inspect_bundle(bundle, frames)
    review_path = tmp_path / "review.json"
    review = {
        "status": "accepted",
        "reviewer": "test-reviewer",
        "notes": ["Inspected the response"],
        "source_frames_sha256": digest(frames),
    }
    write_json(review_path, review)
    promoted = promote(bundle, tmp_path / "corpus", "hello", review_path)
    assert json.loads(promoted.read_text()) == frames
    expectation = json.loads(
        promoted.with_name(promoted.name.replace(".frames.", ".expectations.")).read_text()
    )
    assert expectation["source_run"]["passed"] is False
    assert expectation["source_run"]["errors"] == ["deployment replay route absent"]
    review["source_frames_sha256"] = "wrong"
    write_json(review_path, review)
    with pytest.raises(ValueError, match="exact captured frames"):
        promote(bundle, tmp_path / "corpus", "hello", review_path)


@pytest.mark.parametrize("change", ["pending", "no-notes", "no-database", "mutated", "secret"])
def test_unreviewed_unverified_mutated_or_sensitive_data_cannot_be_promoted(tmp_path, change):
    bundle = tmp_path / "bundle"
    frames = bundle_at(bundle)
    audit = inspect_bundle(bundle, frames)
    review = {
        "status": "accepted",
        "reviewer": "test",
        "notes": ["checked"],
        "source_frames_sha256": digest(frames),
    }
    if change == "pending":
        review["status"] = "pending"
    elif change == "no-notes":
        review["notes"] = []
    elif change == "no-database":
        audit["database_verified"] = False
    elif change == "secret":
        audit["sensitive_scan"] = ["credential-like"]
    else:
        write_json(bundle / "session.frames.json", frames[:-1])
    write_json(bundle / "audit.json", audit)
    review_path = tmp_path / "review.json"
    write_json(review_path, review)
    with pytest.raises(ValueError):
        promote(bundle, tmp_path / "corpus", "hello", review_path)


def test_html_cannot_execute_markup_from_model_payloads(tmp_path):
    hostile = {"text": '</script><script>alert("injected")</script>'}
    write_viewer(tmp_path, hostile, [], {})
    html = (tmp_path / "review.html").read_text()
    assert '</script><script>alert("injected")' not in html
    assert "\\u003c/script>" in html


CORPUS = Path(__file__).parents[1] / "fixtures/forge-corpus"


@pytest.mark.parametrize("fixture", sorted(CORPUS.glob("*.frames.json")), ids=lambda p: p.stem)
def test_reviewed_live_corpus_replays_exactly_and_keeps_review_hash(fixture):
    rows = json.loads(fixture.read_text())
    expectation_path = fixture.with_name(fixture.name.replace(".frames.", ".expectations."))
    expectation = json.loads(expectation_path.read_text())
    assert expectation["source"] == "live-provider"
    assert expectation["review"]["status"] == "accepted"
    assert digest(rows) == expectation["sha256"]
    assert replay_fixture(fixture.parent, fixture.name) == [r["payload"] for r in rows]
    for cursor in (rows[0]["seq"], rows[len(rows) // 2]["seq"], rows[-1]["seq"]):
        assert replay_fixture(fixture.parent, fixture.name, after=cursor) == [
            r["payload"] for r in rows if r["seq"] > cursor
        ]
    state = rebuild_turns(entries(rows))
    assert state.turns == expectation["expected_turns"]
    assert state.partial == expectation["expected_partial"]
    if review := expectation.get("projection_review"):
        assert review["status"] == "accepted"
        assert review["source_frames_sha256"] == digest(rows)
        assert review["projection_sha256"] == digest(state.turns)
