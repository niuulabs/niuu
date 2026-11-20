#!/usr/bin/env python3
"""Bring running sessions' brokers up to this release's broker contract (LEX-1957).

An API-only promotion restarts Forge, not the session brokers, which keep the release
they were launched from. When a release raises the broker contract
(``niuu.domain.broker_contract``), every session still on an older broker silently
misses it: a broker without LEX-1910 stores no sender, so the apps show "Unknown sender".
Runs on the Forge host itself (python3 >= 3.9).

usage:
  forge_broker_catchup.py audit FORGE_BASE
      Every running local session's broker and contract state, as JSON. Exit 1 when a
      broker is below the contract or could not be probed.
  forge_broker_catchup.py restart FORGE_BASE TMUX_BIN --until ISO8601 [--hold NAME ...]
                          [--only SESSION_ID ...] [--evidence PATH]
      Restart each session below the contract onto this release, resuming its
      conversation, once it is idle at its prompt with no tool command or train in its
      pane (``forge_lane_busy``). A session named in ``--hold`` is listed, never
      restarted; ``--only`` restricts the run to the named sessions (an owner's GO), and
      a held session named there is restarted. Busy sessions are retried until
      ``--until``. Exit 0 when no session outside ``--hold`` is left below the contract.
      ``--even-if-met`` also restarts a named ``--only`` session that meets the contract (a
      proof on a scratch session).
  forge_broker_catchup.py codex-appended EVIDENCE SESSION_ID
      After a restarted Codex session's next turn: its thread's rollout is the same file,
      grown, with the records' ordinals continuing from before the restart. Exit 1 if not.

A restart is stop then start. The broker resumes the engine's own conversation id, so a
session keeps its context; a session with no recorded conversation id cannot resume and
is never restarted. A restart counts only when the session runs again, its broker meets
the contract, and it kept the same conversation id. A failure is reported, never retried.
A Codex session (LEX-1967) counts only when its new app-server reopened the same rollout
file (same inode, no smaller), no second rollout exists for its thread, it stays up, and
its turn count is not lower; ``codex-appended`` checks the append after its next turn.
Run it detached (``systemd-run``): a run started from inside a session's pane is busy
work in that pane, so the gate never restarts the session that runs it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import forge_lane_busy  # noqa: E402

# datetime.UTC needs 3.11; the host's python3 may be 3.9 (mini-m6).
UTC = timezone.utc  # noqa: UP017

REQUEST_TIMEOUT_S = 60


def _call(method: str, url: str) -> tuple[int, object]:
    """(HTTP status, body); status 0 when Forge could not be reached at all."""
    request = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:300]
        return exc.code, body
    except (OSError, ValueError) as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def _api(base: str) -> str:
    return f"{base.rstrip('/')}/api/v1/forge"


def audit(base: str) -> list[dict]:
    """One row per running local session: its broker build and contract state."""
    api = _api(base)
    code, rows = _call("GET", f"{api}/sessions?scope=local")
    if code != 200 or not isinstance(rows, list):
        raise RuntimeError(f"listing sessions failed: HTTP {code} {rows}")
    out = []
    for row in rows:
        if row.get("status") != "running":
            continue
        code, version = _call("GET", f"{api}/sessions/{row['id']}/runtime-version")
        version = version if code == 200 and isinstance(version, dict) else {}
        current = version.get("current") or {}
        out.append(
            {
                "id": row["id"],
                "name": row.get("name"),
                "activity": row.get("activity_state"),
                "cli_session_id": row.get("cli_session_id"),
                "build": current.get("build"),
                "revision": current.get("revision"),
                "contract": (version.get("contract") or {}).get("state", f"probe HTTP {code}"),
            }
        )
    return out


def _session(api: str, sid: str) -> dict:
    code, row = _call("GET", f"{api}/sessions/{sid}")
    if code != 200 or not isinstance(row, dict):
        raise RuntimeError(f"reading session {sid} failed: HTTP {code} {row}")
    return row


def _wait(check, deadline: float, poll_s: float):
    while True:
        value = check()
        if value:
            return value
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll_s)


def transcript_exists(cli_session_id: str) -> bool:
    """Claude Code keeps a conversation as ~/.claude/projects/<cwd>/<id>.jsonl."""
    return any(Path.home().glob(f".claude/projects/*/{cli_session_id}.jsonl"))


def live_resume(tmux: str, socket_dir: str, sid: str) -> tuple[str, str] | None:
    """(pid, conversation id) of the Claude process in the session's first pane, if resuming."""
    panes = subprocess.run(
        [tmux, "-S", f"{socket_dir}/skuld-{sid}.sock", "list-panes", "-s", "-t", f"skuld-{sid}"]
        + ["-F", "#{pane_pid}"],
        capture_output=True,
        text=True,
    )
    pids = panes.stdout.split()
    if panes.returncode != 0 or not pids:
        return None
    ps = subprocess.run(["ps", "-o", "command=", "-p", pids[0]], capture_output=True, text=True)
    match = re.search(r"--resume\s+(\S+)", ps.stdout)
    return (pids[0], match.group(1)) if match else None


# Per-session resume evidence for the run's outcome file (a Codex session's rollout).
PROOF: dict[str, dict] = {}


def _procs() -> dict[str, tuple[str, str, str]]:
    ps = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,etime=,command="], capture_output=True, text=True, check=True
    )
    return forge_lane_busy.parse_ps(ps.stdout)


def codex_rollouts(home: Path, thread_id: str) -> list[Path]:
    """Every rollout file of a Codex thread under ``home``/sessions (one, normally)."""
    return sorted((home / "sessions").glob(f"*/*/*/rollout-*-{thread_id}.jsonl"))


def last_ordinal(path: Path, end: int | None = None, chunk: int = 1 << 20) -> int | None:
    """The ``ordinal`` of the last record that has one in the first ``end`` bytes (the whole
    file by default), read backwards."""
    with open(path, "rb") as stream:
        pos = stream.seek(0, 2) if end is None else end
        carry = b""
        while pos > 0:
            step = min(chunk, pos)
            pos -= step
            stream.seek(pos)
            lines = (stream.read(step) + carry).split(b"\n")
            carry = lines.pop(0) if pos > 0 else b""
            for raw in reversed(lines):
                if b'"ordinal"' not in raw:
                    continue
                try:
                    record = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(record, dict) and isinstance(record.get("ordinal"), int):
                    return record["ordinal"]
    return None


def record_boundary(path: Path, size: int) -> int:
    """The end of the last complete record within ``size`` bytes (a newline ends one), so a
    recorded offset never lands inside a record still being written."""
    with open(path, "rb") as stream:
        pos = size
        while pos > 0:
            step = min(1 << 16, pos)
            stream.seek(pos - step)
            block = stream.read(step)
            cut = block.rfind(b"\n")
            if cut >= 0:
                return pos - step + cut + 1
            pos -= step
    return 0


def prefix_digest(path: Path, size: int, chunk: int = 1 << 20) -> str:
    """A digest of the file's first ``size`` bytes: an append keeps it, a rewrite does not."""
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        left = size
        while left > 0:
            block = stream.read(min(chunk, left))
            if not block:
                break
            digest.update(block)
            left -= len(block)
    return digest.hexdigest()


def turn_count(api: str, sid: str) -> int:
    code, body = _call("GET", f"{api}/sessions/{sid}/conversation?detail=shallow")
    turns = body.get("turns") if isinstance(body, dict) else body
    if code != 200 or not isinstance(turns, list):
        raise RuntimeError(f"reading {sid}'s conversation failed: HTTP {code}")
    return sum(1 for turn in turns if turn.get("id") != "in-progress")


def codex_baseline(api: str, sid: str, resume_id: str, app: str) -> dict | str:
    """What the Codex session's thread looks like before the stop, or why it cannot resume."""
    home = forge_lane_busy.codex_home(app)
    files = codex_rollouts(home, resume_id)
    if len(files) != 1:
        return f"not restarted: {len(files)} rollout files for Codex thread {resume_id} in {home}"
    stat = files[0].stat()
    size = record_boundary(files[0], stat.st_size)
    ordinal = last_ordinal(files[0], size)
    if ordinal is None:
        return f"not restarted: {files[0].name} has no record ordinals to prove an append with"
    return {
        "engine": "codex",
        "thread": resume_id,
        "app_server_pid": app,
        "codex_home": str(home),
        "rollout": str(files[0].resolve()),
        "prefix_digest": prefix_digest(files[0], size),
        "inode": stat.st_ino,
        "size": size,
        "last_ordinal": ordinal,
        "turns": turn_count(api, sid),
    }


def restart_one(base: str, row: dict, args: argparse.Namespace) -> str:
    """Re-check, stop and start one session; ``restarted`` or why it did not count.

    The busy gate runs again right before the stop, so the decision is seconds old. After
    the stop every failure says the session may be left STOPPED.
    """
    api, sid = _api(base), row["id"]
    resume_id = row.get("cli_session_id")
    if not resume_id:
        return "not restarted: no recorded conversation id to resume"
    app = forge_lane_busy.codex_app_server(sid, _procs(), Path("/proc"))
    baseline = None
    if app is not None:
        baseline = codex_baseline(api, sid, resume_id, app)
        if isinstance(baseline, str):
            return baseline
    elif not transcript_exists(resume_id):
        return f"not restarted: no transcript for conversation {resume_id}, so it cannot resume"
    verdict = forge_lane_busy.judge(
        base, args.tmux, [sid], background=True, socket_dir=args.socket_dir
    )
    if sid not in verdict["judged"]:
        return "not restarted: no longer running"
    if verdict["busy"]:
        return "busy: " + "; ".join(verdict["busy"][0]["reasons"])
    code, body = _call("POST", f"{api}/sessions/{sid}/stop")
    if code != 200:
        return f"stop failed: HTTP {code} {body}"
    try:
        if baseline is not None:
            PROOF[sid] = {"before": baseline}
            return _start_codex_again(api, sid, resume_id, baseline, args)
        return _start_again(api, sid, resume_id, args)
    except Exception as exc:  # noqa: BLE001 - a stopped session must be reported, never lost
        return f"STOPPED? {type(exc).__name__} after the stop: {exc}; check it and start it by hand"


def _start_again(api: str, sid: str, resume_id: str, args: argparse.Namespace) -> str:
    deadline = time.monotonic() + args.start_timeout
    if not _wait(lambda: _session(api, sid).get("status") == "stopped", deadline, args.poll):
        return f"did not stop within {args.start_timeout:g}s; check it and start it by hand"
    code, body = _call("POST", f"{api}/sessions/{sid}/start")
    if code != 200:
        return f"STOPPED, start failed: HTTP {code} {body}; start it by hand"
    deadline = time.monotonic() + args.start_timeout

    def contract_met():
        code, version = _call("GET", f"{api}/sessions/{sid}/runtime-version")
        return (
            code == 200
            and isinstance(version, dict)
            and (version.get("contract") or {}).get("state") == "met"
        )

    if not _wait(contract_met, deadline, args.poll):
        return f"started, but its broker did not meet the contract within {args.start_timeout:g}s"
    if not _wait(lambda: _session(api, sid).get("status") == "running", deadline, args.poll):
        return f"started, but it was not running within {args.start_timeout:g}s"
    live = _wait(lambda: live_resume(args.tmux, args.socket_dir, sid), deadline, args.poll)
    if live is None or live[1] != resume_id:
        return f"started, but its Claude is not resuming {resume_id} (live: {live}): context lost?"
    time.sleep(args.settle)
    again = live_resume(args.tmux, args.socket_dir, sid)
    if again != live:
        return f"started, but its resumed Claude (pid {live[0]}) exited within {args.settle:g}s"
    return "restarted"


def _started(api: str, sid: str, args: argparse.Namespace) -> str | None:
    """Wait for the stop, start, and wait for a running broker that meets the contract."""
    deadline = time.monotonic() + args.start_timeout
    if not _wait(lambda: _session(api, sid).get("status") == "stopped", deadline, args.poll):
        return f"did not stop within {args.start_timeout:g}s; check it and start it by hand"
    code, body = _call("POST", f"{api}/sessions/{sid}/start")
    if code != 200:
        return f"STOPPED, start failed: HTTP {code} {body}; start it by hand"
    deadline = time.monotonic() + args.start_timeout

    def contract_met():
        code, version = _call("GET", f"{api}/sessions/{sid}/runtime-version")
        return (
            code == 200
            and isinstance(version, dict)
            and (version.get("contract") or {}).get("state") == "met"
        )

    if not _wait(contract_met, deadline, args.poll):
        return f"started, but its broker did not meet the contract within {args.start_timeout:g}s"
    if not _wait(lambda: _session(api, sid).get("status") == "running", deadline, args.poll):
        return f"started, but it was not running within {args.start_timeout:g}s"
    return None


def _start_codex_again(
    api: str, sid: str, resume_id: str, before: dict, args: argparse.Namespace
) -> str:
    """Start a stopped Codex session and prove it resumed its own thread (LEX-1967)."""
    failed = _started(api, sid, args)
    if failed:
        return failed
    rollout = Path(before["rollout"])
    deadline = time.monotonic() + args.start_timeout

    def reopened():  # exactly one app-server, a new one, holding the thread's rollout
        apps = forge_lane_busy.codex_app_servers(sid, _procs(), Path("/proc"))
        if len(apps) != 1 or apps[0] == before["app_server_pid"]:
            return None
        held = {p.resolve() for p in forge_lane_busy.open_rollouts(apps[0])}
        return apps[0] if rollout in held else None

    app = _wait(reopened, deadline, args.poll)
    if app is None:
        return f"started, but no new Codex app-server reopened {rollout.name}: context lost?"
    failed = _same_thread(api, sid, resume_id, before, app)
    if failed:
        return failed
    time.sleep(args.settle)
    failed = _same_thread(api, sid, resume_id, before, app)  # still true after settling
    if failed:
        return failed.replace("started, but", f"started, then within {args.settle:g}s")
    turns = turn_count(api, sid)
    size = record_boundary(rollout, rollout.stat().st_size)
    PROOF[sid]["after"] = {
        "app_server_pid": app,
        "size": size,
        "last_ordinal": last_ordinal(rollout, size),
        "prefix_digest": prefix_digest(rollout, size),
        "turns": turns,
    }
    if turns < before["turns"]:
        return f"started, but its turns dropped {before['turns']} -> {turns}"
    return "restarted"


def _same_thread(api: str, sid: str, resume_id: str, before: dict, app: str) -> str | None:
    """Why the resumed session is not on its own thread and file, or None when it is."""
    rollout = Path(before["rollout"])
    if forge_lane_busy.codex_app_servers(sid, _procs(), Path("/proc")) != [app]:
        return f"started, but its resumed app-server (pid {app}) is not the only one running"
    now = _session(api, sid).get("cli_session_id")
    if now != resume_id:
        return f"started, but its thread is {now}, not {resume_id}: context lost?"
    stat = rollout.stat()
    if (
        stat.st_ino != before["inode"]
        or stat.st_size < before["size"]
        or prefix_digest(rollout, before["size"]) != before["prefix_digest"]
    ):
        return f"started, but {rollout.name} was replaced or shrank since the stop"
    files = [f.resolve() for f in codex_rollouts(Path(before["codex_home"]), resume_id)]
    if files != [rollout]:
        return f"started, but thread {resume_id} now has rollouts {[f.name for f in files]}"
    return None


def codex_appended(evidence: str, sid: str) -> int:
    """After the restarted session's next turn: the same rollout grew, ordinals continuing."""
    record = json.loads(Path(evidence).read_text()).get(sid) or {}
    proof = record.get("proof") or {}
    before, after = proof.get("before"), proof.get("after")
    if not before or not after or record.get("result") != "restarted":
        raise SystemExit(f"{evidence} holds no completed Codex restart for {sid}")
    rollout = Path(before["rollout"])
    problems = []
    stat = rollout.stat()
    if (
        stat.st_ino != before["inode"]
        or stat.st_size < after["size"]
        or prefix_digest(rollout, after["size"]) != after["prefix_digest"]
    ):
        problems.append(f"{rollout.name} was replaced since the restart")
    files = [f.resolve() for f in codex_rollouts(Path(before["codex_home"]), before["thread"])]
    if files != [rollout]:
        problems.append(f"the thread has rollouts {[f.name for f in files]}")
    ordinals, after_restart, turns = [], 0, 0
    with open(rollout, "rb") as stream:
        stream.seek(before["size"])  # everything since the stop: during and after the restart
        while True:
            offset = stream.tell()
            raw = stream.readline()
            if not raw:
                break
            if not raw.endswith(b"\n"):
                break  # a record still being written
            written_after = offset >= after["size"]
            try:
                record = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            if isinstance(record.get("ordinal"), int):
                ordinals.append(record["ordinal"])
                after_restart += written_after
            payload = record.get("payload")
            if (
                written_after
                and isinstance(payload, dict)
                and payload.get("type") == "task_complete"
            ):
                turns += 1
    if not after_restart:
        problems.append(f"nothing was appended to {rollout.name} since the restart")
    elif ordinals != list(
        range(before["last_ordinal"] + 1, before["last_ordinal"] + 1 + len(ordinals))
    ):
        problems.append(
            f"ordinals do not continue from {before['last_ordinal']}: {ordinals[:3]}..."
        )
    if not turns:
        problems.append("no turn has completed in it since the restart")
    print(
        json.dumps(
            {
                "rollout": str(rollout),
                "appended_bytes": stat.st_size - after["size"],
                "records": len(ordinals),
                "records_after_restart": after_restart,
                "first_ordinal": ordinals[0] if ordinals else None,
                "completed_turns": turns,
                "problems": problems,
            },
            indent=2,
        )
    )
    return 1 if problems else 0


def _resolve_holds(rows: list[dict], holds: list[str]) -> set[str]:
    """Session ids for ``--hold`` values (an id, or a name naming exactly one running session)."""
    ids = set()
    for hold in holds:
        matches = [r["id"] for r in rows if hold in (r["id"], r["name"])]
        if len(matches) != 1:
            raise SystemExit(f"--hold {hold} names {len(matches)} running sessions; give its id")
        ids.update(matches)
    return ids


def restart(args: argparse.Namespace) -> int:
    if args.even_if_met and not args.only:
        raise SystemExit("--even-if-met restarts only sessions named with --only")
    until = datetime.fromisoformat(args.until.replace("Z", "+00:00"))
    if until.tzinfo is None:
        raise SystemExit("--until needs a timezone (e.g. 2026-11-06T23:00:00Z)")
    rows = audit(args.base)
    running = {r["id"] for r in rows}
    held, only = _resolve_holds(rows, args.hold), set(args.only)
    unknown = only - running
    if unknown:
        raise SystemExit(f"--only names sessions that are not running here: {sorted(unknown)}")
    outcome: dict[str, dict] = {}
    for r in rows:  # with --only, only the named sessions are in scope, and naming one is its GO
        if r["id"] in held and not only and r["contract"] != "met":
            outcome[r["id"]] = {**r, "result": "held: restart only on its owner's GO"}
    for r in rows:
        if r["id"] in only and r["contract"] == "met" and r["id"] not in outcome:
            if not args.even_if_met:
                outcome[r["id"]] = {**r, "result": "not restarted: already meets the contract"}

    def record():
        if args.evidence:
            Path(args.evidence).write_text(json.dumps(outcome, indent=2) + "\n")

    waiting: dict[str, dict] = {}
    while True:
        last, waiting = waiting, {}
        for r in audit(args.base):
            wanted = r["contract"] == "below" or (args.even_if_met and r["contract"] == "met")
            if r["id"] in outcome or not wanted or (only and r["id"] not in only):
                continue
            if datetime.now(UTC) >= until:
                waiting[r["id"]] = last.get(r["id"]) or {
                    **r,
                    "result": "deadline passed before it was tried",
                }
                continue
            result = restart_one(args.base, r, args)
            if result.startswith("busy: "):
                waiting[r["id"]] = {**r, "result": "busy until the deadline: " + result[6:]}
                continue
            outcome[r["id"]] = {**r, "result": result}
            if r["id"] in PROOF:
                outcome[r["id"]]["proof"] = PROOF[r["id"]]
            print(f"{datetime.now(UTC):%FT%TZ} {r['name']} ({r['id']}): {result}", flush=True)
            record()
        left_s = (until - datetime.now(UTC)).total_seconds()
        if not waiting or left_s <= 0:
            break
        time.sleep(min(args.retry, left_s))
    outcome.update(waiting)
    for r in audit(args.base):
        if r["contract"] not in ("met", "below") and r["id"] not in outcome:
            if not only or r["id"] in only:
                outcome[r["id"]] = {**r, "result": f"broker not probed ({r['contract']})"}
    for sid in only - set(outcome):
        outcome[sid] = {"id": sid, "name": None, "result": "not restarted: left the run"}
    record()
    print(json.dumps(outcome, indent=2))
    if only:
        return 0 if all(outcome[sid]["result"] == "restarted" for sid in only) else 1
    left = [o for sid, o in outcome.items() if o["result"] != "restarted" and sid not in held]
    return 1 if left else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    a = sub.add_parser("audit")
    a.add_argument("base")
    r = sub.add_parser("restart")
    r.add_argument("base")
    r.add_argument("tmux")
    r.add_argument("--until", required=True, help="stop retrying busy sessions at (ISO 8601)")
    r.add_argument(
        "--hold", action="append", default=[], help="session id or unique name never restarted"
    )
    r.add_argument(
        "--only", action="append", default=[], help="restrict to this session id (all must restart)"
    )
    r.add_argument(
        "--socket-dir",
        default=f"/tmp/skuld-tmux-{os.getuid()}",
        help="the brokers' tmux socket directory (skuld tmux_socket_dir)",
    )
    r.add_argument(
        "--settle", type=float, default=15.0, help="seconds a resumed Claude must stay up"
    )
    r.add_argument("--evidence", help="write the per-session outcome here as it goes")
    r.add_argument(
        "--even-if-met",
        action="store_true",
        help="also restart a named --only session that meets the contract (a proof run)",
    )
    r.add_argument("--poll", type=float, default=3.0, help="seconds between state checks")
    r.add_argument("--retry", type=float, default=60.0, help="seconds between busy re-checks")
    r.add_argument(
        "--start-timeout", type=float, default=180.0, help="seconds to stop or start a session"
    )
    c = sub.add_parser("codex-appended")
    c.add_argument("evidence")
    c.add_argument("session_id")
    args = parser.parse_args(argv)
    if args.command == "codex-appended":
        return codex_appended(args.evidence, args.session_id)
    if args.command == "audit":
        rows = audit(args.base)
        print(json.dumps(rows, indent=2))
        return 0 if all(r["contract"] == "met" for r in rows) else 1
    return restart(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
