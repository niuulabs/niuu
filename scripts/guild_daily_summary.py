#!/usr/bin/env python3
"""The morning Guild summary: one message to the coordinator, one dated section of
docs/operator/guild-daily.md on the integration branch. Read-only on every host.

For each Forge host it reports the build served, health, ledger and session counts, and
5xx answers since the previous summary. For the push gateway it reports the counters
moved since the previous summary (a restart resets them: then it says "since restart"),
APNs rejections by reason from the gateway's log, and feedless or unreachable nodes. It
also counts promotions rolled back or completed in the rollout folders.

The host specifics (URLs, how to read a ledger or a log) live in a JSON config, never in
this file; see ``docs/operator/guild-daily.md``. Nothing printed or written may hold a
secret: health, counts, build names and reasons only.

    guild_daily_summary.py run CONFIG [--dry-run]

``--dry-run`` prints the summary and changes nothing (no message, no commit, no state).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

#: Counters of ``GET /push/status`` the summary reports as "moved since".
PUSH_COUNTERS = (
    "alerts_sent",
    "alerts_failed",
    "alerts_suppressed",
    "live_activity_updates",
    "live_activity_starts",
    "widget_reloads",
    "background_wakes",
    "apns_rejections",
    "tokens_pruned",
    "node_failures",
    "loop_errors",
)
SERVER_ERROR = re.compile(r'HTTP/1\.[01]" 5\d\d')
REJECTION = re.compile(
    r"(?:Deleted a dead \S+ token .*\(APNs: (?P<dead>[^)]+)\))"
    r"|(?:APNs refused a \S+ push for owner \S+: (?P<refused>.+)$)"
)
ROLLOUT_LINE = re.compile(r"^(?P<at>\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ) (?P<text>.*)$")
FIRST_WINDOW = timedelta(hours=24)


class SummaryError(RuntimeError):
    """The summary could not be gathered or delivered; the message says what to fix."""


# -- pure parts -----------------------------------------------------------------------


def counter_moves(now: dict[str, Any], before: dict[str, Any] | None) -> tuple[dict, bool]:
    """Counters moved since ``before``, and whether the gateway restarted in between.

    A counter that went down means a restart: the current values are then the moves
    since that restart.
    """
    if before is None:
        return {key: now.get(key, 0) for key in PUSH_COUNTERS}, True
    restarted = any(now.get(key, 0) < before.get(key, 0) for key in PUSH_COUNTERS)
    if restarted:
        return {key: now.get(key, 0) for key in PUSH_COUNTERS}, True
    return {key: now.get(key, 0) - before.get(key, 0) for key in PUSH_COUNTERS}, False


def environment_moves(
    now: dict[str, dict[str, int]], before: dict[str, dict[str, int]] | None, restarted: bool
) -> dict[str, dict[str, int]]:
    """APNs answers per environment since ``before`` (all of them after a restart)."""
    if before is None or restarted:
        return {env: dict(counts) for env, counts in now.items()}
    return {
        env: {
            outcome: count - before.get(env, {}).get(outcome, 0)
            for outcome, count in counts.items()
        }
        for env, counts in now.items()
    }


def rejection_reasons(lines: list[str]) -> Counter[str]:
    """APNs rejections by reason from the gateway's log lines (pruned ones marked)."""
    reasons: Counter[str] = Counter()
    for line in lines:
        found = REJECTION.search(line)
        if found is None:
            continue
        if found["dead"]:
            reasons[f"{found['dead'].strip()} (token pruned)"] += 1
        else:
            reasons[found["refused"].strip()] += 1
    return reasons


def server_errors(lines: list[str]) -> int:
    return sum(1 for line in lines if SERVER_ERROR.search(line))


def rollout_outcomes(
    chain_lines: list[tuple[str, str]], since: datetime
) -> tuple[list[str], list[str]]:
    """Rolled-back and completed cutovers since ``since``, oldest first.

    ``chain_lines`` are (rollout folder, line) pairs; a cutover is named by its folder,
    the release it was taking the host to, and the host.
    """
    rolled: list[tuple[datetime, str]] = []
    completed: list[tuple[datetime, str]] = []
    for folder, line in chain_lines:
        found = ROLLOUT_LINE.match(line.strip())
        if found is None:
            continue
        at = datetime.strptime(found["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        if at < since:
            continue
        text = found["text"]
        host = (
            "Thor" if text.startswith("Thor run:") else "mini" if text.startswith("mini:") else ""
        )
        if not host:
            continue
        what = (at, f"{at:%m-%d %H:%M} {host} {folder}")
        if "ROLLED_BACK" in text:
            rolled.append(what)
        elif "CUTOVER_OK" in text:
            completed.append(what)
    return [w for _, w in sorted(rolled)], [w for _, w in sorted(completed)]


def new_log_bytes(size: int, offset: int | None) -> tuple[int, int]:
    """Where to start reading a growing log, and the new offset (a shrink is a rotation)."""
    if offset is None or offset > size:
        return (size if offset is None else 0), size
    return offset, size


@dataclass
class HostSnapshot:
    name: str
    status: str
    build: str
    revision: str
    dirty: bool | None
    failed_plugins: list[str]
    ledger: str
    sessions: int | None
    running: int | None
    server_errors: int | None
    errors_note: str = ""


def render(
    when: datetime,
    since: datetime,
    hosts: list[HostSnapshot],
    push: dict[str, Any] | None,
    previous_push: dict[str, Any] | None,
    reasons: Counter[str],
    rolled: list[str],
    completed: list[str],
) -> str:
    """The summary text: the message and the document section are the same."""
    window = f"{since:%Y-%m-%d %H:%M} to {when:%Y-%m-%d %H:%M} UTC"
    lines = [f"## Guild summary {when:%Y-%m-%d} ({window})", ""]
    for host in hosts:
        dirty = "not dirty" if host.dirty is False else "DIRTY" if host.dirty else "dirty ?"
        plugins = (
            f", failed plugins {', '.join(host.failed_plugins)}" if host.failed_plugins else ""
        )
        sessions = (
            f"{host.sessions} sessions ({host.running} running)"
            if host.sessions is not None
            else "sessions unreadable"
        )
        errors = (
            f"5xx {host.server_errors}{host.errors_note}"
            if host.server_errors is not None
            else f"5xx n/a{host.errors_note}"
        )
        lines.append(
            f"- **{host.name}**: {host.status}, {host.build} ({host.revision[:9]}, {dirty})"
            f"{plugins}; ledger {host.ledger}; {sessions}; {errors}"
        )
    lines.append("")
    if push is None:
        lines.append("- **Push gateway**: status unreadable")
    else:
        stats = {key: push.get(key, 0) for key in PUSH_COUNTERS}
        moves, restarted = counter_moves(stats, previous_push and previous_push.get("stats"))
        envs = environment_moves(
            push.get("apns_by_environment") or {},
            previous_push and previous_push.get("apns_by_environment"),
            restarted,
        )
        label = "since its restart" if restarted else "since the previous summary"
        moved = ", ".join(f"{key} {value}" for key, value in moves.items() if value) or "nothing"
        per_env = (
            "; ".join(
                f"{env}: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
                for env, counts in sorted(envs.items())
            )
            or "none"
        )
        why = ", ".join(f"{reason} ×{n}" for reason, n in reasons.most_common()) or "none"
        tokens = "; ".join(
            f"{env}: " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items()))
            for env, kinds in sorted((push.get("tokens_by_environment") or {}).items())
        )
        lines += [
            f"- **Push gateway** ({'running' if push.get('running') else 'NOT running'}), "
            f"{label}: {moved}",
            f"  - APNs answers per environment, {label}: {per_env}",
            f"  - Rejections by reason in this window (gateway log): {why}",
            f"  - Registered tokens: {tokens or 'none'}",
            f"  - Feedless nodes: {', '.join(sorted(push.get('feedless_nodes') or {})) or 'none'}; "
            f"unreachable: {', '.join(sorted(push.get('unreachable_nodes') or {})) or 'none'}",
        ]
    lines += [
        "",
        f"- **Promotions**: rolled back {len(rolled)}"
        + (f" ({'; '.join(rolled)})" if rolled else "")
        + f"; completed {len(completed)}"
        + (f" ({'; '.join(completed)})" if completed else ""),
        "",
    ]
    return "\n".join(lines)


# -- gathering --------------------------------------------------------------------------


def _get_json(url: str) -> Any:
    opener = build_opener(ProxyHandler({}))
    with opener.open(Request(url), timeout=20) as response:
        return json.load(response)


def _run(command: list[str] | str, **kwargs: Any) -> str:
    argv = shlex.split(command) if isinstance(command, str) else command
    return subprocess.run(argv, check=True, capture_output=True, text=True, **kwargs).stdout


def _host(spec: dict[str, Any], since: datetime, state: dict[str, Any]) -> HostSnapshot:
    try:
        health = _get_json(spec["url"] + "/health")
    except (URLError, OSError, ValueError) as exc:
        return HostSnapshot(
            spec["name"], f"UNREACHABLE ({exc})", "?", "?", None, [], "?", None, None, None
        )
    build = str(health.get("build") or "?")
    try:
        ledger = _run([part.format(build=build) for part in spec["ledger_command"]]).strip()
    except (subprocess.CalledProcessError, OSError) as exc:
        ledger = f"unreadable ({type(exc).__name__})"
    try:
        listed = _get_json(spec["url"] + "/api/v1/forge/sessions?scope=local")
        sessions = len(listed)
        running = sum(1 for item in listed if item.get("status") == "running")
    except (URLError, OSError, ValueError):
        sessions = running = None
    errors, note = _server_errors(spec, since, state)
    return HostSnapshot(
        spec["name"],
        str(health.get("status")),
        build,
        str(health.get("revision") or "?"),
        health.get("dirty"),
        list(health.get("failed_plugins") or []),
        ledger,
        sessions,
        running,
        errors,
        note,
    )


def _server_errors(
    spec: dict[str, Any], since: datetime, state: dict[str, Any]
) -> tuple[int | None, str]:
    source = spec["errors"]
    try:
        if "journal_unit" in source:
            lines = _run(
                [
                    "journalctl",
                    "--user",
                    "-u",
                    source["journal_unit"],
                    "-o",
                    "cat",
                    "--since",
                    since.strftime("%Y-%m-%d %H:%M:%S UTC"),
                ]
            ).splitlines()
            return server_errors(lines), ""
        size = int(_run(source["size_command"]).strip())
        offsets = state.setdefault("log_offsets", {})
        counted_from = state.setdefault("log_since", {})
        first = spec["name"] not in offsets
        start, end = new_log_bytes(size, offsets.get(spec["name"]))
        offsets[spec["name"]] = end
        if first:  # the log has no timestamps: count only what is written from now on
            counted_from[spec["name"]] = datetime.now(UTC).isoformat()
            return None, " (counted from this summary on)"
        counted = _run(source["count_command"].format(start=start + 1, length=end - start))
        note = ""
        began = counted_from.get(spec["name"])
        if began and datetime.fromisoformat(began) > since:
            note = f" (since {datetime.fromisoformat(began):%m-%d %H:%M})"
        counted_from[spec["name"]] = datetime.now(UTC).isoformat()
        return int(counted.strip() or 0), note
    except (subprocess.CalledProcessError, OSError, ValueError) as exc:
        return None, f" ({type(exc).__name__})"


def gather(config: dict[str, Any], state: dict[str, Any], when: datetime) -> tuple[str, dict]:
    since = datetime.fromisoformat(state["at"]) if state.get("at") else when - FIRST_WINDOW
    hosts = [_host(spec, since, state) for spec in config["hosts"]]
    try:
        push = _get_json(config["push_status_url"])
    except (URLError, OSError, ValueError):
        push = None
    try:
        log = _run(
            [
                "journalctl",
                "--user",
                "-u",
                config["gateway_journal_unit"],
                "-o",
                "cat",
                "--since",
                since.strftime("%Y-%m-%d %H:%M:%S UTC"),
            ]
        ).splitlines()
    except (subprocess.CalledProcessError, OSError):
        log = []
    chains: list[tuple[str, str]] = []
    for chain in Path(os.path.expanduser(config["rollouts_dir"])).glob("*/chain.log"):
        folder = chain.parent.name
        chains += [(folder, line) for line in chain.read_text(errors="replace").splitlines()]
    rolled, completed = rollout_outcomes(chains, since)
    text = render(
        when, since, hosts, push, state.get("push"), rejection_reasons(log), rolled, completed
    )
    if push is not None:
        state["push"] = {
            "stats": {key: push.get(key, 0) for key in PUSH_COUNTERS},
            "apns_by_environment": push.get("apns_by_environment") or {},
        }
    state["at"] = when.isoformat()
    return text, state


# -- delivery ---------------------------------------------------------------------------


def append_to_doc(config: dict[str, Any], text: str, when: datetime) -> str:
    """Append the section on the integration branch: docs only, a plain push."""
    tree = os.path.expanduser(config["docs_worktree"])
    branch = config["branch"]
    git = ["git", "-C", tree]
    _run([*git, "fetch", "-q", "origin", branch])
    _run([*git, "checkout", "-q", "-B", "guild-daily", f"origin/{branch}"])
    doc = Path(tree) / "docs/operator/guild-daily.md"
    doc.write_text(doc.read_text() + "\n" + text)
    _run([*git, "add", "docs/operator/guild-daily.md"])
    _run(
        [*git, "commit", "-q", "-m", f"docs: Guild summary {when:%Y-%m-%d}\n\n{config['trailer']}"]
    )
    pushed = subprocess.run(
        [*git, "push", "origin", f"HEAD:{branch}"], capture_output=True, text=True
    )
    if pushed.returncode != 0:
        # Never forced: a refused push is reported and left for a person.
        return f"NOT pushed ({pushed.stderr.strip().splitlines()[-1][:160]})"
    return _run([*git, "rev-parse", "--short", "HEAD"]).strip()


def send(config: dict[str, Any], text: str, when: datetime) -> str:
    body = json.dumps(
        {"content": text, "request_id": f"guild-daily-{when:%Y%m%d}-{uuid.uuid4().hex[:6]}"}
    ).encode()
    url = f"{config['forge_url']}/api/v1/forge/sessions/{config['coordinator_session']}/messages"
    opener = build_opener(ProxyHandler({}))
    request = Request(url, data=body, headers={"content-type": "application/json"})
    with opener.open(request, timeout=60) as response:
        return json.load(response).get("delivery", "?")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="mode", required=True)
    run = sub.add_parser("run")
    run.add_argument("config", type=Path)
    run.add_argument("--dry-run", action="store_true")
    prime = sub.add_parser("prime", help="record where timestamp-less logs end, nothing else")
    prime.add_argument("config", type=Path)
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text())
    state_path = Path(os.path.expanduser(config["state_file"]))
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    if args.mode == "prime":
        for spec in config["hosts"]:
            if "size_command" in spec["errors"]:
                _server_errors(spec, datetime.now(UTC), state)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2))
        print("primed:", ", ".join(sorted(state.get("log_offsets", {}))) or "nothing")
        return 0
    when = datetime.now(UTC).replace(microsecond=0)
    text, state = gather(config, json.loads(json.dumps(state)), when)
    if args.dry_run:
        print(text)
        return 0
    commit = append_to_doc(config, text, when)
    message = f"{text}\nRecorded in docs/operator/guild-daily.md: {commit}."
    delivery = send(config, message, when)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2))
    print(f"summary sent ({delivery}); doc {commit}")
    return 0 if not commit.startswith("NOT") else 1


if __name__ == "__main__":
    sys.exit(main())
