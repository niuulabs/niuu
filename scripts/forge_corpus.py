"""Inspect, review, promote and serve real Forge traces without invoking an LLM."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from scripts.forge_trace import (
    compare_streams,
    digest,
    evidence,
    scan_sensitive,
    stream_frames,
    validate_rows,
    write_json,
)
from volundr.adapters.inbound.ws_session_replay import create_session_replay_router
from volundr.config import ReplayConfig
from volundr.domain.models import SessionLogEntry
from volundr.domain.services.transcript_rebuild import rebuild_turns

DEFAULT_CORPUS = Path("tests/fixtures/forge-corpus")
CHECKPOINT_TYPES = {"result", "ask_user_question", "ask_user_resolved", "plan", "agent_update"}
MAX_CHECKPOINTS = 40


def fixture_app(directory: Path) -> FastAPI:
    """Use the production replay router; fixture mode needs no DB or credentials."""
    app = FastAPI(title="Forge Trace Lab")
    config = ReplayConfig(fixtures_enabled=True, fixtures_dir=str(directory.resolve()))
    app.include_router(create_session_replay_router(None, config=config))

    @app.get("/api/v1/forge/replay/catalog")
    async def catalog() -> dict:
        cases = []
        for path in sorted(directory.glob("*.expectations.json")):
            expected = json.loads(path.read_text())
            cases.append(
                {
                    "fixture": expected["fixture"],
                    "provider": expected["provider"],
                    "scenario": expected["scenario"]["scenario"],
                    "sha256": expected["sha256"],
                    "checkpoints": expected["checkpoints"],
                }
            )
        return {"schema_version": 1, "cases": cases}

    return app


def replay_fixture(
    directory: Path, name: str, *, after: int = 0, preamble: bool = False
) -> list[dict]:
    result = []
    with TestClient(fixture_app(directory)) as client:
        path = (
            f"/api/v1/forge/replay/fixtures/{name}?max_gap=0&speed=1000"
            f"&show_internal=true&after={after}&preamble={str(preamble).lower()}"
        )
        with client.websocket_connect(path) as ws:
            try:
                while True:
                    result.append(ws.receive_json())
            except WebSocketDisconnect as exc:
                if exc.code != 1000:
                    raise
    return result


def entries(rows: list[dict]) -> list[SessionLogEntry]:
    return [
        SessionLogEntry(
            session_id=UUID(r["session_id"]),
            seq=r["seq"],
            kind=r["kind"],
            payload=r["payload"],
            role=r.get("role"),
            request_id=r.get("request_id"),
            ts=datetime.fromisoformat(r["ts"]) if r.get("ts") else None,
        )
        for r in rows
    ]


async def read_database(bundle: Path, variable: str) -> list[dict]:
    """Read ONLY this captured session; credentials never enter artifacts or argv."""
    import asyncpg

    manifest = json.loads((bundle / "manifest.json").read_text())
    sid = UUID(manifest["session_id"])
    dsn = os.environ.get(variable)
    if not dsn:
        raise ValueError(f"database connection environment variable {variable} is missing")
    connection = await asyncpg.connect(dsn)
    try:
        async with connection.transaction(readonly=True):
            records = await connection.fetch(
                "SELECT session_id, seq, kind, role, request_id, payload, ts "
                "FROM session_event_log WHERE session_id=$1 ORDER BY seq",
                sid,
            )
        rows = []
        for record in records:
            row = dict(record)
            row["session_id"] = str(row["session_id"])
            row["ts"] = row["ts"].isoformat() if row["ts"] else None
            if isinstance(row["payload"], str):
                row["payload"] = json.loads(row["payload"])
            rows.append(row)
        return rows
    finally:
        await connection.close()


def live_persistence(live: list[dict], rows: list[dict]) -> dict:
    """Prove each captured event exists in DB order; gaps may be observer downtime.

    Reconnect snapshots are separately recorded UI seeds, not broadcast events.
    This is a one-way persistence assertion, not proof every DB event was live.
    """
    persisted = stream_frames(rows)
    cursor = 0
    missing = []
    excluded = {"conversation_history", "conversation_state", "terminal_snapshot"}
    snapshots = {"available_commands", "plan", "agent_update"}
    reconciled_snapshots = 0
    checked = 0
    for index, record in enumerate(live):
        frame = record["payload"]
        if frame.get("type") in excluded or not stream_frames(
            [{"kind": frame.get("type", ""), "payload": frame}]
        ):
            continue
        match = next((i for i in range(cursor, len(persisted)) if persisted[i] == frame), None)
        checked += 1
        if match is None:
            if frame.get("type") in snapshots and frame in persisted[:cursor]:
                reconciled_snapshots += 1
                continue
            missing.append({"index": index, "type": frame.get("type"), "hash": digest(frame)})
        else:
            cursor = match + 1
    return {
        "passed": checked > 0 and not missing,
        "checked": checked,
        "missing": missing,
        "scope": "captured live events are an ordered subsequence of persisted events",
        "excluded_connection_seed_types": sorted(excluded),
        "reconciled_snapshots": reconciled_snapshots,
    }


def inspect_bundle(bundle: Path, database_rows: list[dict] | None = None) -> dict:
    manifest = json.loads((bundle / "manifest.json").read_text())
    rows = json.loads((bundle / "session.frames.json").read_text())
    raw = database_rows
    if raw is None and (bundle / "database.frames.json").exists():
        raw = json.loads((bundle / "database.frames.json").read_text())
    audit = {
        "schema_version": 1,
        "source_frames_sha256": digest(rows),
        "row_errors": validate_rows(rows, manifest["session_id"]),
        "database_verified": False,
        "fixture_replay": compare_streams(stream_frames(rows), replay_fixture(bundle, "session")),
    }
    if raw is not None:
        write_json(bundle / "database.frames.json", raw)
        audit["database_projection"] = compare_streams(stream_frames(raw), stream_frames(rows))
        audit["database_verified"] = bool(raw) and audit["database_projection"]["passed"]
        audit["database_row_errors"] = validate_rows(raw, manifest["session_id"])
        audit["database_rows"] = len(raw)
        audit["database_sha256"] = digest(raw)
    source = raw if raw is not None else rows
    folded = rebuild_turns(entries(source))
    write_json(bundle / "observed.turns.json", {"turns": folded.turns, "partial": folded.partial})
    live = json.loads((bundle / "live.json").read_text())
    audit["live_persistence"] = live_persistence(live, source)
    audit["sensitive_scan"] = scan_sensitive({"frames": source, "manifest": manifest, "live": live})
    checkpoints = []
    candidates = [r for r in source if r["kind"] in CHECKPOINT_TYPES]
    for r in candidates[:MAX_CHECKPOINTS]:
        prefix = [item for item in source if item["seq"] <= r["seq"]]
        state = rebuild_turns(entries(prefix))
        checkpoints.append(
            {
                "after_seq": r["seq"],
                "event_type": r["kind"],
                "turn_count": len(state.turns),
                "partial": state.partial,
                "turns_sha256": digest(state.turns),
            }
        )
    audit["checkpoints"] = checkpoints
    cursor = rows[len(rows) // 2]["seq"] if rows else 0
    audit["cursor_fixture_replay"] = compare_streams(
        stream_frames([r for r in rows if r["seq"] > cursor]),
        replay_fixture(bundle, "session", after=cursor),
    )
    write_json(bundle / "audit.json", audit)
    write_viewer(bundle, manifest, rows, audit)
    return audit


def write_viewer(bundle: Path, manifest: dict, rows: list[dict], audit: dict) -> None:
    template = Path(__file__).with_name("forge_trace_viewer.html").read_text()
    payload = json.dumps({"manifest": manifest, "rows": rows, "audit": audit}, ensure_ascii=False)
    # Safe in an inert script element, including model-produced </script> text.
    payload = payload.replace("<", "\\u003c").replace("&", "\\u0026")
    (bundle / "review.html").write_text(template.replace("__TRACE_DATA__", payload))


def promote(bundle: Path, destination: Path, scenario_id: str, review_path: Path) -> Path:
    """Pin a reviewed scenario. Run-level deployment failures remain in provenance."""
    manifest = json.loads((bundle / "manifest.json").read_text())
    audit = json.loads((bundle / "audit.json").read_text())
    review = json.loads(review_path.read_text())
    rows = json.loads((bundle / "session.frames.json").read_text())
    scenario = next(s for s in manifest["scenarios"] if s["scenario"] == scenario_id)
    if digest(rows) != audit["source_frames_sha256"]:
        raise ValueError("trace changed since audit")
    if (
        not audit.get("database_verified")
        or audit.get("row_errors")
        or audit.get("database_row_errors")
        or not audit["fixture_replay"]["passed"]
    ):
        raise ValueError("promotion requires verified database capture and fixture replay")
    if review.get("status") != "accepted" or not review.get("reviewer") or not review.get("notes"):
        raise ValueError("promotion requires an explicit, attributed review with notes")
    if review.get("source_frames_sha256") != digest(rows):
        raise ValueError("review must be bound to the exact captured frames hash")
    if not scenario["passed"] and not review.get("expected_failures"):
        raise ValueError("failed scenario requires explicit regression expectations")
    if audit["sensitive_scan"]:
        raise ValueError("sensitive-data scan failed; redact and re-audit before promotion")
    selected = [r for r in rows if scenario["after_seq"] < r["seq"] <= scenario["through_seq"]]
    if not selected:
        raise ValueError("empty scenario cannot be promoted")
    source_selected_hash = digest(selected)
    redactions = review.get("redactions", {})
    if not isinstance(redactions, dict) or any(
        not key or not isinstance(value, str) for key, value in redactions.items()
    ):
        raise ValueError("redactions must map non-empty literal strings to replacement strings")
    selected = redact_values(selected, redactions)
    slug = f"{manifest['provider']}-{scenario_id}-{digest(selected)[:10]}"
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f"{slug}.frames.json"
    if target.exists():
        raise FileExistsError(target)
    write_json(target, selected)
    selected_entries = entries(selected)
    final_state = rebuild_turns(selected_entries)
    checkpoints = []
    for row in selected:
        if row["kind"] not in CHECKPOINT_TYPES:
            continue
        state = rebuild_turns([entry for entry in selected_entries if entry.seq <= row["seq"]])
        checkpoints.append(
            {
                "after_seq": row["seq"],
                "event_type": row["kind"],
                "turn_count": len(state.turns),
                "partial": state.partial,
                "turns_sha256": digest(state.turns),
            }
        )
    expected = {
        "schema_version": 1,
        "fixture": target.name,
        "sha256": digest(selected),
        "source_slice_sha256": source_selected_hash,
        "redacted": bool(redactions),
        "source": "live-provider",
        "provider": manifest["provider"],
        "model": manifest["model"],
        "scenario": redact_values(scenario, redactions),
        "review": {k: v for k, v in review.items() if k != "redactions"},
        "checkpoints": checkpoints,
        "expected_turns": final_state.turns,
        "expected_partial": final_state.partial,
        "source_run": {k: manifest.get(k) for k in ("run_id", "session_id", "passed", "errors")},
        "observed": evidence(stream_frames(selected)),
    }
    write_json(destination / f"{slug}.expectations.json", expected)
    return target


def redact_values(value, replacements: dict[str, str]):
    """Explicit review-time literal redaction; never rewrites field names or seqs."""
    if isinstance(value, str):
        for original, replacement in replacements.items():
            value = value.replace(original, replacement)
        return value
    if isinstance(value, list):
        return [redact_values(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: redact_values(item, replacements) for key, item in value.items()}
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("bundle", type=Path)
    inspect.add_argument("--database-url-env")
    promotion = sub.add_parser("promote")
    promotion.add_argument("bundle", type=Path)
    promotion.add_argument("--scenario", required=True)
    promotion.add_argument("--review", required=True, type=Path)
    promotion.add_argument("--destination", type=Path, default=DEFAULT_CORPUS)
    serve = sub.add_parser("serve")
    serve.add_argument("--directory", type=Path, default=DEFAULT_CORPUS)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    if args.command == "inspect":
        raw = (
            asyncio.run(read_database(args.bundle, args.database_url_env))
            if args.database_url_env
            else None
        )
        audit = inspect_bundle(args.bundle, raw)
        print(
            json.dumps(
                {k: audit[k] for k in ("database_verified", "fixture_replay", "live_persistence")},
                indent=2,
            )
        )
    elif args.command == "promote":
        print(promote(args.bundle, args.destination, args.scenario, args.review))
    else:
        import uvicorn

        uvicorn.run(fixture_app(args.directory), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
