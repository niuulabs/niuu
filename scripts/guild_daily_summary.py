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
CUTOVER_SPAN = re.compile(
    r"(?:restart|stop)=(?P<start>\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ) "
    r"end=(?P<end>\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)"
)
JOURNAL_ERROR = re.compile(
    r"^(?P<at>\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d) .*\"(?P<method>[A-Z]+) "
    r"(?P<path>\S+) HTTP/1\.[01]\" 5\d\d"
)
UUID_SEGMENT = re.compile(r"/[0-9a-fA-F]{8}-[0-9a-fA-F-]{27}(?=/|$)")
ROLLOUT_LINE = re.compile(r"^(?P<at>\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ) (?P<text>.*)$")
GATEWAY_STARTED = "Push gateway started:"
DOC = "docs/operator/guild-daily.md"


class SummaryError(RuntimeError):
    """The summary could not be gathered or delivered; the message says what to fix."""


# -- pure parts -----------------------------------------------------------------------


def counter_moves(
    now: dict[str, Any], before: dict[str, Any] | None, restarted: bool
) -> dict[str, int]:
    """Counters moved since ``before``; after a restart (or with no ``before``), all of them.

    ``restarted`` comes from the gateway's own "started" log line: counters that grew past
    their previous values after a restart cannot tell it by themselves.
    """
    if before is None or restarted:
        return {key: now.get(key, 0) for key in PUSH_COUNTERS}
    return {key: now.get(key, 0) - before.get(key, 0) for key in PUSH_COUNTERS}


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


def window_trend(moves: dict[str, int], previous: dict[str, int] | None) -> str | None:
    """One line on how this window's moves compare with the previous window's."""
    if previous is None:
        return None
    changed = [
        f"{key} {previous.get(key, 0)} → {moves.get(key, 0)}"
        for key in PUSH_COUNTERS
        if moves.get(key, 0) != previous.get(key, 0)
    ]
    return ", ".join(changed) or "the same"


def gateway_restarted(lines: list[str]) -> bool:
    return any(GATEWAY_STARTED in line for line in lines)


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


def _at(stamp: str) -> datetime:
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def cutover_windows(chain_lines: list[tuple[str, str]], margin: timedelta) -> list[tuple]:
    """(start, end) of every cutover or rollback, widened by ``margin`` on both sides."""
    windows = []
    for _, line in chain_lines:
        if "CUTOVER_OK" not in line and "ROLLED_BACK" not in line:
            continue
        found = CUTOVER_SPAN.search(line)
        if found is not None:
            windows.append((_at(found["start"]) - margin, _at(found["end"]) + margin))
    return windows


def route_of(method: str, path: str) -> str:
    """``GET /api/v1/forge/sessions/{id}``: query dropped, ids folded."""
    return f"{method} {UUID_SEGMENT.sub('/{id}', path.split('?', 1)[0])}"


def classify_server_errors(
    lines: list[str], cutovers: list[tuple], storms: list[dict[str, str]]
) -> tuple[int, int, Counter[str], int]:
    """5xx answers split into (during cutovers, during known storms, the rest by route).

    Returns the cutover count, the storm count, the remaining routes, and lines whose
    time could not be read (counted with the rest).
    """
    storm_spans = [
        (datetime.fromisoformat(storm["from"]), datetime.fromisoformat(storm["to"]))
        for storm in storms
    ]
    during_cutovers = during_storms = unread = 0
    rest: Counter[str] = Counter()
    for line in lines:
        if not SERVER_ERROR.search(line):
            continue
        found = JOURNAL_ERROR.search(line)
        if found is None:
            unread += 1
            rest["(unparsed line)"] += 1
            continue
        at = datetime.fromisoformat(found["at"])
        if any(start <= at <= end for start, end in cutovers):
            during_cutovers += 1
        elif any(start <= at <= end for start, end in storm_spans):
            during_storms += 1
        else:
            rest[route_of(found["method"], found["path"])] += 1
    return during_cutovers, during_storms, rest, unread


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
    errors_breakdown: str = ""


def render(
    when: datetime,
    since: datetime,
    hosts: list[HostSnapshot],
    push: dict[str, Any] | None,
    previous_push: dict[str, Any] | None,
    gateway_log: list[str] | None,
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
            f"5xx {host.server_errors}{host.errors_note}{host.errors_breakdown}"
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
        before = previous_push and previous_push.get("stats")
        restarted = gateway_log is not None and gateway_restarted(gateway_log)
        moves = counter_moves(stats, before, restarted)
        envs = environment_moves(
            push.get("apns_by_environment") or {},
            previous_push and previous_push.get("apns_by_environment"),
            restarted or before is None,
        )
        if restarted or before is None:
            label = "since the gateway started"
        else:
            label = f"since {datetime.fromisoformat(previous_push['at']):%m-%d %H:%M}"
        moved = ", ".join(f"{key} {value}" for key, value in moves.items() if value) or "nothing"
        per_env = (
            "; ".join(
                f"{env}: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
                for env, counts in sorted(envs.items())
            )
            or "none"
        )
        why = (
            "gateway log unreadable"
            if gateway_log is None
            else ", ".join(
                f"{reason} ×{n}" for reason, n in rejection_reasons(gateway_log).most_common()
            )
            or "none"
        )
        tokens = "; ".join(
            f"{env}: " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items()))
            for env, kinds in sorted((push.get("tokens_by_environment") or {}).items())
        )
        trend = window_trend(moves, previous_push and previous_push.get("moves"))
        lines += [
            f"- **Push gateway** ({'running' if push.get('running') else 'NOT running'}), "
            f"{label}: {moved}",
            *([f"  - Against the previous window: {trend}"] if trend is not None else []),
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


def _get_json(url: str, timeout: float) -> Any:
    opener = build_opener(ProxyHandler({}))
    with opener.open(Request(url), timeout=timeout) as response:
        return json.load(response)


def _run(command: list[str] | str, **kwargs: Any) -> str:
    argv = shlex.split(command) if isinstance(command, str) else command
    return subprocess.run(argv, check=True, capture_output=True, text=True, **kwargs).stdout


def _host(
    spec: dict[str, Any],
    since: datetime,
    when: datetime,
    state: dict,
    timeout: float,
    context: dict[str, Any],
) -> HostSnapshot:
    try:
        health = _get_json(spec["url"] + "/health", timeout)
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
        listed = _get_json(spec["url"] + "/api/v1/forge/sessions?scope=local", timeout)
        sessions = len(listed)
        running = sum(1 for item in listed if item.get("status") == "running")
    except (URLError, OSError, ValueError):
        sessions = running = None
    errors, note, breakdown = _server_errors(spec, since, when, state, context)
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
        breakdown,
    )


def _breakdown(lines: list[str], context: dict[str, Any]) -> tuple[int, str]:
    cutovers, storms, rest, _ = classify_server_errors(
        lines, context["cutovers"], context["storms"]
    )
    total = cutovers + storms + sum(rest.values())
    top = ", ".join(f"{route} ×{n}" for route, n in rest.most_common(context["top_routes"]))
    return total, (
        f" ({cutovers} during cutovers or rollbacks, {storms} in known storms, "
        f"{sum(rest.values())} other" + (f": {top}" if top else "") + ")"
    )


def _server_errors(
    spec: dict[str, Any],
    since: datetime,
    when: datetime,
    state: dict[str, Any],
    context: dict[str, Any],
) -> tuple[int | None, str, str]:
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
                    "short-iso",
                    "--since",
                    since.strftime("%Y-%m-%d %H:%M:%S UTC"),
                ]
            ).splitlines()
            total, breakdown = _breakdown(lines, context)
            return total, "", breakdown
        size = int(_run(source["size_command"]).strip())
        offsets = state.setdefault("log_offsets", {})
        counted_from = state.setdefault("log_since", {})
        if spec["name"] not in offsets:
            # The log has no timestamps: count only what is written from now on.
            offsets[spec["name"]] = size
            counted_from[spec["name"]] = when.isoformat()
            return None, " (counted from this summary on)", ""
        start, end = new_log_bytes(size, offsets[spec["name"]])
        counted = int(
            _run(source["count_command"].format(start=start + 1, length=end - start)).strip() or 0
        )
        # Advanced only once counted: a failed count is retried over the same bytes.
        offsets[spec["name"]] = end
        began = datetime.fromisoformat(counted_from.get(spec["name"], since.isoformat()))
        counted_from[spec["name"]] = when.isoformat()
        return counted, (f" (since {began:%m-%d %H:%M})" if began > since else ""), ""
    except (subprocess.CalledProcessError, OSError, ValueError) as exc:
        return None, f" ({type(exc).__name__})", ""


def gather(config: dict[str, Any], state: dict[str, Any], when: datetime) -> tuple[str, dict]:
    first_window = timedelta(hours=config["first_window_hours"])
    timeout = config["http_timeout_seconds"]
    since = datetime.fromisoformat(state["at"]) if state.get("at") else when - first_window
    chains: list[tuple[str, str]] = []
    for chain in Path(os.path.expanduser(config["rollouts_dir"])).glob("*/chain.log"):
        folder = chain.parent.name
        chains += [(folder, line) for line in chain.read_text(errors="replace").splitlines()]
    context = {
        "cutovers": cutover_windows(chains, timedelta(seconds=config["cutover_margin_seconds"])),
        "storms": config["known_storms"],
        "top_routes": config["top_routes"],
    }
    hosts = [_host(spec, since, when, state, timeout, context) for spec in config["hosts"]]
    try:
        push = _get_json(config["push_status_url"], timeout)
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
        log = None  # reported as unreadable, never as "no rejections"
    rolled, completed = rollout_outcomes(chains, since)
    text = render(when, since, hosts, push, state.get("push"), log, rolled, completed)
    if push is not None:
        stats = {key: push.get(key, 0) for key in PUSH_COUNTERS}
        before = (state.get("push") or {}).get("stats")
        restarted = log is not None and gateway_restarted(log)
        state["push"] = {
            "at": when.isoformat(),
            "stats": stats,
            "moves": counter_moves(stats, before, restarted),
            "apns_by_environment": push.get("apns_by_environment") or {},
        }
    state["at"] = when.isoformat()
    return text, state


# -- delivery ---------------------------------------------------------------------------


def unlanded_sections(landed: str, local: str) -> str:
    """What the local copy appended beyond the landed one (a section that never landed)."""
    return local[len(landed) :] if local.startswith(landed) else ""


def append_to_doc(config: dict[str, Any], text: str, when: datetime) -> str:
    """Append the section on the integration branch: docs only, a plain push.

    A section a previous run could not commit or push is carried into this commit, ahead
    of today's, so no day goes missing from the document.
    """
    tree = os.path.expanduser(config["docs_worktree"])
    branch = config["branch"]
    git = ["git", "-C", tree]
    changed = {line[3:] for line in _run([*git, "status", "--porcelain"]).splitlines()}
    if changed - {DOC}:
        raise SummaryError(f"{tree} has local changes; it must be the summary's own clean worktree")
    _run([*git, "fetch", "-q", "origin", branch])
    landed = _run([*git, "show", f"origin/{branch}:{DOC}"])
    carried = unlanded_sections(landed, (Path(tree) / DOC).read_text())
    _run([*git, "reset", "-q", "--hard"])
    _run([*git, "checkout", "-q", "-B", "guild-daily", f"origin/{branch}"])
    doc = Path(tree) / DOC
    doc.write_text(landed + carried + "\n" + text)
    _run([*git, "add", DOC])
    _run(
        [*git, "commit", "-q", "-m", f"docs: Guild summary {when:%Y-%m-%d}\n\n{config['trailer']}"]
    )
    pushed = subprocess.run(
        [*git, "push", "origin", f"HEAD:{branch}"], capture_output=True, text=True
    )
    if pushed.returncode != 0:
        # Never forced: a refused push is reported; the next run carries this section.
        # Git's own text is not passed on (it can name the remote with credentials).
        return f"NOT pushed (git push exit {pushed.returncode}; see guild-daily.service)"
    return _run([*git, "rev-parse", "--short", "HEAD"]).strip()


def send(config: dict[str, Any], text: str, when: datetime) -> str:
    body = json.dumps(
        {"content": text, "request_id": f"guild-daily-{when:%Y%m%d}-{uuid.uuid4().hex[:6]}"}
    ).encode()
    url = f"{config['forge_url']}/api/v1/forge/sessions/{config['coordinator_session']}/messages"
    opener = build_opener(ProxyHandler({}))
    request = Request(url, data=body, headers={"content-type": "application/json"})
    with opener.open(request, timeout=config["http_timeout_seconds"]) as response:
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
                now = datetime.now(UTC).replace(microsecond=0)
                _server_errors(
                    spec, now, now, state, {"cutovers": [], "storms": [], "top_routes": 0}
                )
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2))
        print("primed:", ", ".join(sorted(state.get("log_offsets", {}))) or "nothing")
        return 0
    when = datetime.now(UTC).replace(microsecond=0)
    text, state = gather(config, json.loads(json.dumps(state)), when)
    if args.dry_run:
        print(text)
        return 0
    # The message first: a failure recording it in the repository never withholds it.
    delivery = send(config, text, when)
    # Saved once the coordinator has it, so a window is never sent twice or skipped.
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2))
    recorded = record(config, text, when)
    if not recorded.startswith("recorded"):
        send(config, f"Guild summary {when:%Y-%m-%d}: {recorded}", when)
    print(f"summary sent ({delivery}); {recorded}")
    return 0 if recorded.startswith("recorded") else 1


def record(config: dict[str, Any], text: str, when: datetime) -> str:
    """Append the section, and say how that went; a failure is described, never raised."""
    try:
        commit = append_to_doc(config, text, when)
    except subprocess.CalledProcessError as exc:
        # git's own output can name the remote with credentials: the step and code only.
        step = exc.cmd[3] if isinstance(exc.cmd, list) and len(exc.cmd) > 3 else "git"
        return (
            f"NOT recorded in docs/operator/guild-daily.md (git {step} exit {exc.returncode}; "
            "see guild-daily.service); the next run carries this section"
        )
    except (SummaryError, OSError) as exc:
        return f"NOT recorded in docs/operator/guild-daily.md ({exc})"
    if commit.startswith("NOT"):
        return f"{commit} in docs/operator/guild-daily.md"
    return f"recorded in docs/operator/guild-daily.md as {commit}"


if __name__ == "__main__":
    sys.exit(main())
