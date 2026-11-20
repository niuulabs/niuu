"""Measure history delivery for an overnight-shaped session (LEX-1937).

    uv run --extra dev python -m tests.perf.bench_long_turn_history [--calls 450]

Not a test (no ``test_`` prefix, never collected). It builds a deterministic
synthetic session shaped like an overnight run: ten ordinary earlier exchanges,
then one assistant turn of ~1 h 40 min with hundreds of tool calls whose results
follow a realistic size mix (small inline results, multi-KB elided results, a
few very large ones, Edit/Write inputs above the inline limit). It reports:

* the broker/WebSocket projection (``prepare_history_page`` and
  ``prepare_recent_snapshot``): wire bytes, time, whether the long turn arrived
  whole and how many of its tool calls are present;
* the Forge REST route through a FastAPI TestClient (archive source), for the
  first recent page and, when the server supports ``part_paging``, every
  within-turn page until the long turn is loaded completely.

Runs against any revision: it detects whether ``part_paging`` exists.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import inspect
import json
import random
import statistics
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from skuld.conversation_snapshot import (
    prepare_history_page,
    prepare_recent_snapshot,
    snapshot_byte_size,
)
from tests.conftest import (
    InMemorySessionRepository,
    MockPodManager,
    make_session_participant_service,
)
from volundr.adapters.inbound import rest
from volundr.domain.models import Session, SessionStatus
from volundr.domain.services import SessionService

BUDGET = 256 * 1024
CLIENT_REQUEST_BYTES = BUDGET - 4 * 1024
MAX_TURNS = 15


def _text(rng: random.Random, low: int, high: int) -> str:
    words = ["build", "the", "test", "fails", "because", "forge", "history", "page", "cursor"]
    out: list[str] = []
    size = rng.randint(low, high)
    while sum(len(w) + 1 for w in out) < size:
        out.append(rng.choice(words))
    return " ".join(out)


def _result_size(rng: random.Random) -> int:
    roll = rng.random()
    if roll < 0.40:
        return rng.randint(40, 1000)  # inline (<= 1 KiB): ls, git status, short greps
    if roll < 0.85:
        return rng.randint(2_000, 20_000)  # file reads, test output
    if roll < 0.98:
        return rng.randint(20_000, 80_000)  # long test logs, diffs
    return rng.randint(200_000, 600_000)  # a huge build log now and then


def overnight_session(calls: int, *, seed: int = 1937) -> list[dict]:
    rng = random.Random(seed)
    start = datetime(2026, 11, 5, 22, 0, tzinfo=UTC)
    turns: list[dict] = []
    for i in range(10):
        turns.append(
            {
                "id": f"user-{i}",
                "role": "user",
                "content": _text(rng, 40, 300),
                "parts": [],
                "created_at": (start + timedelta(minutes=i)).isoformat(),
            }
        )
        parts: list[dict] = []
        for j in range(rng.randint(2, 8)):
            call = f"early-{i}-{j}"
            parts.append({"type": "text", "text": _text(rng, 80, 300)})
            parts.append(
                {"type": "tool_use", "id": call, "name": "Bash", "input": {"command": "ls -la"}}
            )
            parts.append(
                {"type": "tool_result", "tool_use_id": call, "content": "x" * _result_size(rng)}
            )
        turns.append(
            {
                "id": f"assistant-{i}",
                "role": "assistant",
                "content": " ".join(p["text"] for p in parts if p["type"] == "text"),
                "parts": parts,
                "created_at": (start + timedelta(minutes=i, seconds=30)).isoformat(),
                "metadata": {"status": "completed"},
            }
        )
    long_start = start + timedelta(minutes=20)
    turns.append(
        {
            "id": "user-overnight",
            "role": "user",
            "content": "Run the overnight perf investigation and fix what you find.",
            "parts": [],
            "created_at": long_start.isoformat(),
        }
    )
    parts = []
    elapsed = timedelta(hours=1, minutes=40)
    for j in range(calls):
        at = long_start + elapsed * (j / calls)
        if j % 3 == 0:
            parts.append({"type": "thinking", "thinking": _text(rng, 100, 600)})
        if j % 2 == 0:
            parts.append({"type": "text", "text": _text(rng, 60, 400)})
        call = f"overnight-{j}"
        tool = rng.choice(["Bash", "Bash", "Read", "Grep", "Edit"])
        if tool == "Edit":
            tool_input = {
                "file_path": f"/src/module_{j}.py",
                "old_string": "a" * rng.randint(200, 3000),
                "new_string": "b" * rng.randint(200, 3000),
            }
        elif tool == "Read":
            tool_input = {"file_path": f"/src/module_{j}.py"}
        elif tool == "Grep":
            tool_input = {"pattern": "history_preview", "path": "/src"}
        else:
            tool_input = {
                "command": "uv run pytest -q " + _text(rng, 20, 200),
                "description": "Run the focused tests",
            }
        parts.append(
            {
                "type": "tool_use",
                "id": call,
                "name": tool,
                "input": tool_input,
                "started_at": at.isoformat(),
            }
        )
        parts.append(
            {
                "type": "tool_result",
                "tool_use_id": call,
                "content": "y" * _result_size(rng),
                "is_error": rng.random() < 0.05,
                "ended_at": (at + timedelta(seconds=5)).isoformat(),
            }
        )
    parts.append({"type": "text", "text": _text(rng, 2_000, 6_000)})
    turns.append(
        {
            "id": "assistant-overnight",
            "role": "assistant",
            "content": " ".join(p["text"] for p in parts if p["type"] == "text"),
            "parts": parts,
            "created_at": (long_start + elapsed).isoformat(),
            "metadata": {"status": "completed"},
        }
    )
    return turns


def _timed(fn, repeat: int) -> tuple[object, float]:
    samples = []
    result = None
    for _ in range(repeat):
        began = time.perf_counter()
        result = fn()
        samples.append((time.perf_counter() - began) * 1000.0)
    return result, statistics.median(samples)


def _tool_calls(turn: dict) -> int:
    return sum(1 for p in turn.get("parts") or [] if p.get("type") == "tool_use")


def _describe(label: str, page: dict, ms: float, calls: int) -> None:
    last = page["turns"][-1] if page.get("turns") else {}
    window = last.get("part_window")
    print(
        f"{label}: {snapshot_byte_size(page):>8} B  {ms:7.1f} ms  turns={len(page['turns'])}"
        f"  long-turn tool calls {_tool_calls(last)}/{calls}"
        f"  history_preview={bool(page.get('history_preview') or last.get('history_preview'))}"
        f"  part_window={window}"
    )


async def _router_app(source: list[dict]):
    session = Session(
        id=uuid4(), name="bench", model="test", status=SessionStatus.ARCHIVED, chat_endpoint=None
    )
    repo = InMemorySessionRepository()
    await repo.create(session)
    sessions = SessionService(repository=repo, pod_manager=MockPodManager(), validate_repos=False)
    archive = AsyncMock()
    archive.get_transcript.side_effect = lambda sid: {"turns": copy.deepcopy(source)}
    archive.latest_event_seq.return_value = 0
    app = FastAPI()
    app.include_router(
        rest.create_router(
            sessions,
            archive_service=archive,
            history_max_turns=MAX_TURNS,
            history_max_bytes=BUDGET,
            session_participant_service=make_session_participant_service(sessions),
        )
    )
    return app, session


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--calls", type=int, default=450)
    parser.add_argument("--repeat", type=int, default=5)
    args = parser.parse_args()
    source = overnight_session(args.calls)
    supports_parts = "part_paging" in inspect.signature(prepare_history_page).parameters
    raw = len(json.dumps(source, separators=(",", ":")).encode())
    print(f"source: {len(source)} turns, {raw} B full JSON, long turn {args.calls} tool calls")
    print(f"budget {BUDGET} B, max_turns {MAX_TURNS}, part_paging supported: {supports_parts}")

    frame = {"turns": source, "head_seq": 1, "history_source": "gateway"}
    page, ms = _timed(
        lambda: prepare_history_page(
            frame, session_id="bench", max_bytes=BUDGET, max_turns=MAX_TURNS
        ),
        args.repeat,
    )
    _describe("broker page (protocol 2)", page, ms, args.calls)
    if supports_parts:
        page, ms = _timed(
            lambda: prepare_history_page(
                frame, session_id="bench", max_bytes=BUDGET, max_turns=MAX_TURNS, part_paging=True
            ),
            args.repeat,
        )
        _describe("broker page (part_paging)", page, ms, args.calls)
    recent, ms = _timed(
        lambda: prepare_recent_snapshot(
            {"type": "conversation_history", "turns": source}, max_bytes=BUDGET, max_turns=15
        ),
        args.repeat,
    )
    _describe("ws recent snapshot", recent, ms, args.calls)

    app, session = asyncio.run(_router_app(source))
    url = f"/api/v1/forge/sessions/{session.id}/conversation"
    params = {
        "history_protocol": 2,
        "detail": "shallow",
        "limit": MAX_TURNS,
        "max_bytes": CLIENT_REQUEST_BYTES,
    }
    with TestClient(app) as client:
        client.get(url, params=params)  # warm up
        samples = []
        for _ in range(args.repeat):
            began = time.perf_counter()
            response = client.get(url, params=params)
            samples.append((time.perf_counter() - began) * 1000.0)
        body = response.json()
        print(
            f"REST first page: HTTP {response.status_code} {len(response.content)} B"
            f"  {statistics.median(samples):.1f} ms"
            f"  long-turn tool calls {_tool_calls(body['turns'][-1])}/{args.calls}"
            f"  history_preview={bool(body.get('history_preview'))}"
        )
        if body.get("history_preview"):
            began = time.perf_counter()
            item = client.get(url, params={"turn_id": "assistant-overnight", "detail": "shallow"})
            print(
                f"REST expand item (turn_id): {len(item.content)} B"
                f"  {(time.perf_counter() - began) * 1000.0:.1f} ms"
                f"  tool calls {_tool_calls(item.json()['turn'])}/{args.calls}"
            )
        if not supports_parts:
            return
        began = time.perf_counter()
        pages, total_bytes, calls = 0, 0, 0
        current = client.get(url, params={**params, "part_paging": 1})
        while True:
            pages += 1
            total_bytes += len(current.content)
            data = current.json()
            last = data["turns"][-1]
            if last["id"] != "assistant-overnight":
                break
            calls += _tool_calls(last)
            window = last.get("part_window")
            if not window or window["offset"] == 0:
                break
            current = client.get(
                url, params={**params, "part_paging": 1, "cursor": data["older_cursor"]}
            )
        print(
            f"REST part_paging until the long turn is whole: {pages} pages {total_bytes} B"
            f"  {(time.perf_counter() - began) * 1000.0:.1f} ms  tool calls {calls}/{args.calls}"
        )


if __name__ == "__main__":
    main()
