"""Resumed live turn seeds never replace the native history imported before them."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest

from niuu.domain.transcript_reducer import is_read_path_excluded, reduce_frames
from skuld.history_hydration import fetch_durable_history
from volundr.domain.models import SessionLogEntry
from volundr.domain.services.transcript_rebuild import rebuild_turns

_SID = UUID("5d87d7a8-5f0f-4cc3-ae03-d5f015a8a15c")
_TS = datetime(2026, 9, 8, tzinfo=UTC)
_NATIVE_FINAL = "The original native analysis is complete."


def _row(seq, kind, payload):
    return SessionLogEntry(session_id=_SID, seq=seq, kind=kind, payload=payload, ts=_TS)


def _native_history():
    rows = [
        _row(1, "user", {"type": "user", "uuid": "native-user-1", "content": "First task"}),
        _row(2, "result", {"type": "result", "result": "First reply", "stop_reason": "end_turn"}),
        _row(3, "user", {"type": "user", "uuid": "native-user-2", "content": "Full review"}),
    ]
    for index in range(112):
        identifier = f"native-tool-{index}"
        rows.append(
            _row(
                len(rows) + 1,
                "assistant",
                {
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": identifier,
                                "name": "Bash",
                                "input": {"command": "pwd"},
                            }
                        ]
                    }
                },
            )
        )
        rows.append(
            _row(
                len(rows) + 1,
                "user",
                {
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": identifier,
                                "content": f"/fixture/{index}",
                            }
                        ]
                    }
                },
            )
        )
    rows.append(_row(len(rows) + 1, "result", {"result": _NATIVE_FINAL, "stop_reason": "end_turn"}))
    return rows


def _imported_history_with_live_tail(*, partial=False):
    # The target already emitted ten init/catalog/handshake rows. Existing seqs
    # remain immutable and imported assistant identities use the shifted seqs.
    chrome = [_row(seq, "system", {"subtype": "init"}) for seq in range(1, 11)]
    imported = [
        replace(
            row,
            seq=row.seq + 10,
            payload={
                **row.payload,
                "metadata": {"native_import": {"external_id": "native-thread", "partial": partial}},
            },
        )
        for row in _native_history()
    ]
    marker_seq = imported[-1].seq + 1
    marker = _row(
        marker_seq,
        "history_import",
        {"source_id": "native-thread", "count": len(imported), "partial": partial},
    )
    raw_tail = [
        _row(marker_seq + 1, "user", {"uuid": "live-user", "content": "Continue"}),
        _row(marker_seq + 3, "result", {"result": "New live reply", "stop_reason": "end_turn"}),
    ]
    folded = reduce_frames(raw_tail).turns
    user_seed = _row(marker_seq + 2, "conversation.turn", {"turn": folded[0]})
    assistant_seed = _row(marker_seq + 4, "conversation.turn", {"turn": folded[1]})
    return [*chrome, *imported, marker, raw_tail[0], user_seed, raw_tail[1], assistant_seed]


def test_live_seeds_preserve_all_four_imported_turns_and_112_tool_pairs():
    rows = _imported_history_with_live_tail()
    rebuilt = rebuild_turns(rows)
    public = reduce_frames(
        [row for row in rows if not is_read_path_excluded(row.kind, row.payload)]
    )

    assert len(rebuilt.turns) == 6
    assert rebuilt.turns == public.turns
    assert rebuilt.partial is False
    assert rebuilt.turns[3]["content"] == _NATIVE_FINAL
    assert rebuilt.turns[-1]["content"] == "New live reply"
    assert all(
        turn["metadata"]["native_import"]["external_id"] == "native-thread"
        for turn in rebuilt.turns[:4]
    )
    assert all("native_import" not in turn["metadata"] for turn in rebuilt.turns[4:])
    calls = [
        part["id"] for turn in rebuilt.turns for part in turn["parts"] if part["type"] == "tool_use"
    ]
    results = [
        part["tool_use_id"]
        for turn in rebuilt.turns
        for part in turn["parts"]
        if part["type"] == "tool_result"
    ]
    assert calls == results == [f"native-tool-{index}" for index in range(112)]


def test_partial_native_snapshot_remains_partial_after_successful_live_turn():
    rebuilt = rebuild_turns(_imported_history_with_live_tail(partial=True))
    assert len(rebuilt.turns) == 6
    assert rebuilt.partial is True


def test_unfinished_imported_turn_stays_interrupted_before_later_live_seed():
    rows = _imported_history_with_live_tail()
    marker_index = next(index for index, row in enumerate(rows) if row.kind == "history_import")
    # Simulate a valid imported snapshot ending during work, before native result.
    unfinished = rows[marker_index - 1]
    rows[marker_index - 1] = replace(
        unfinished,
        kind="assistant",
        payload={
            "message": {"content": [{"type": "text", "text": "Still working"}]},
            "metadata": unfinished.payload["metadata"],
        },
    )
    rebuilt = rebuild_turns(rows)
    assert len(rebuilt.turns) == 6
    assert rebuilt.turns[3]["metadata"]["status"] == "interrupted"
    assert rebuilt.turns[-1]["content"] == "New live reply"
    assert rebuilt.partial is True
    assert rebuilt.turns == reduce_frames(rows).turns
    public = [row for row in rows if not is_read_path_excluded(row.kind, row.payload)]
    assert rebuilt.turns == reduce_frames(public).turns


@pytest.mark.asyncio
@pytest.mark.parametrize("unfinished", [False, True])
async def test_stopped_database_rebuild_equals_broker_hydration_after_new_live_turn(unfinished):
    rows = _imported_history_with_live_tail()
    if unfinished:
        index = next(index for index, row in enumerate(rows) if row.kind == "history_import") - 1
        rows[index] = replace(
            rows[index],
            kind="assistant",
            payload={
                "message": {"content": [{"type": "text", "text": "Still working"}]},
                "metadata": rows[index].payload["metadata"],
            },
        )

    def handle(request):
        if request.url.path.endswith("/head"):
            return httpx.Response(200, json={"latest_seq": rows[-1].seq})
        after = int(request.url.params["after"])
        limit = int(request.url.params["limit"])
        raw_page = [row for row in rows if row.seq > after][:limit]
        page = [
            {
                "session_id": str(row.session_id),
                "seq": row.seq,
                "kind": row.kind,
                "payload": row.payload,
                "ts": row.ts.isoformat(),
            }
            for row in raw_page
            if not is_read_path_excluded(row.kind, row.payload)
        ]
        return httpx.Response(200, json=page)

    async with httpx.AsyncClient(
        base_url="http://forge.test", transport=httpx.MockTransport(handle)
    ) as client:
        hydrated = await fetch_durable_history(
            client,
            session_id=str(_SID),
            path="/log",
            timeout_seconds=5,
            page_size=7,
            max_frames=1000,
            max_bytes=1_000_000,
        )

    assert hydrated == rebuild_turns(rows).turns
    assert len(hydrated) == 6
