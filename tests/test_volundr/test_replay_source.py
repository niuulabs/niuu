"""Unit tests for fixture loading and the path-traversal guard."""

from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4

import pytest

from volundr.replay.fixtures import default_fixtures_dir, resolve_fixture
from volundr.replay.source import load_fixture_entries

# Use the SINGLE packaged fixture corpus (what prod serves) — no duplicate copy.
_FIXTURES = default_fixtures_dir()


@pytest.mark.parametrize("path", sorted(_FIXTURES.glob("*.frames.json")), ids=lambda p: p.name)
def test_every_packaged_fixture_loads_and_is_seq_ordered(path):
    # Locks the whole replay corpus the endpoint serves (web + iOS QA): every
    # checked-in fixture must parse and yield seq-ordered, non-empty frames.
    entries = load_fixture_entries(path, session_id=uuid4())
    assert entries, f"{path.name} produced no entries"
    seqs = [e.seq for e in entries]
    assert seqs == sorted(seqs), f"{path.name} is not seq-ordered"


def test_load_fixture_entries_parses_and_sorts_by_seq(tmp_path):
    sid = uuid4()
    rows = [
        {"seq": 3, "kind": "result", "ts": "2026-06-18T09:00:16Z", "payload": {"type": "result"}},
        {"seq": 1, "kind": "user", "ts": "2026-06-18T09:00:00Z", "payload": {"type": "user"}},
        {
            "seq": 2,
            "kind": "assistant",
            "ts": "2026-06-18T09:00:03Z",
            "payload": {"type": "assistant"},
        },
    ]
    p = tmp_path / "x.frames.json"
    p.write_text(json.dumps(rows), encoding="utf-8")

    entries = load_fixture_entries(p, session_id=sid)
    assert [e.seq for e in entries] == [1, 2, 3]
    # Route-derived session id stamped on every entry (fixture's own id ignored).
    assert all(e.session_id == sid for e in entries)


def test_kind_falls_back_to_payload_type(tmp_path):
    sid = uuid4()
    rows = [{"seq": 1, "ts": "2026-06-18T09:00:00Z", "payload": {"type": "content_block_delta"}}]
    p = tmp_path / "k.frames.json"
    p.write_text(json.dumps(rows), encoding="utf-8")

    entries = load_fixture_entries(p, session_id=sid)
    assert entries[0].kind == "content_block_delta"


def test_missing_ts_is_tolerated(tmp_path):
    sid = uuid4()
    rows = [{"seq": 1, "kind": "user", "payload": {"type": "user"}}]
    p = tmp_path / "no-ts.frames.json"
    p.write_text(json.dumps(rows), encoding="utf-8")

    entries = load_fixture_entries(p, session_id=sid)
    assert entries[0].ts is None


def test_z_suffix_ts_is_parsed_to_aware_datetime(tmp_path):
    sid = uuid4()
    rows = [
        {"seq": 1, "kind": "user", "ts": "2026-06-18T09:00:00.000Z", "payload": {"type": "user"}}
    ]
    p = tmp_path / "z.frames.json"
    p.write_text(json.dumps(rows), encoding="utf-8")

    entries = load_fixture_entries(p, session_id=sid)
    ts = entries[0].ts
    assert isinstance(ts, datetime)
    assert ts.tzinfo is not None
    assert ts.utcoffset().total_seconds() == 0


def test_non_array_fixture_raises_value_error(tmp_path):
    p = tmp_path / "bad.frames.json"
    p.write_text(json.dumps({"not": "an array"}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_fixture_entries(p, session_id=uuid4())


def test_checked_in_two_turn_fixture_loads():
    # The committed corpus that backs both repos in the offline tests.
    p = _FIXTURES / "two-turn.frames.json"
    entries = load_fixture_entries(p, session_id=uuid4())
    assert len(entries) >= 2
    assert [e.seq for e in entries] == sorted(e.seq for e in entries)


def test_resolve_fixture_accepts_bare_slug_and_full_name():
    by_slug = resolve_fixture("two-turn", _FIXTURES)
    by_full = resolve_fixture("two-turn.frames.json", _FIXTURES)
    assert by_slug == by_full
    assert by_slug.name == "two-turn.frames.json"


def test_resolve_fixture_rejects_traversal():
    with pytest.raises(ValueError):
        resolve_fixture("../secret", _FIXTURES)


def test_resolve_fixture_rejects_slashes():
    with pytest.raises(ValueError):
        resolve_fixture("sub/two-turn", _FIXTURES)


def test_resolve_fixture_rejects_encoded_traversal():
    # "..%2f..." style — the % and slash are not in the whitelist.
    with pytest.raises(ValueError):
        resolve_fixture("..%2fsecret", _FIXTURES)


def test_resolve_fixture_missing_file_raises_not_found():
    with pytest.raises(FileNotFoundError):
        resolve_fixture("does-not-exist", _FIXTURES)


def test_default_fixtures_dir_points_at_packaged_dir():
    d = default_fixtures_dir()
    assert d.name == "fixtures"
    assert d.parent.name == "replay"
