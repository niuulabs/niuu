"""Which Forge lanes on this host are busy (LEX-1706). Runs on the host itself (python3 >= 3.9).

usage: forge_lane_busy.py FORGE_BASE TMUX_BIN [SESSION_ID ...]
A running lane is busy when (1) Forge does not report it idle at the prompt, or (2) its tmux
pane runs a tool command (a shell the agent spawned, e.g. a background release train in a
lane Forge reports idle) or a train process. A lane restart or a host switch ends the lane's
tmux server and with it everything in the pane's process tree; launchd and cron jobs (a
nightly qa.sh) and other detached processes are not in it and survive, so they are reported
as ``host_trains`` but do not make the host busy. With SESSION_IDs only those lanes are
judged. Prints JSON; exit 1 when a lane is busy.

With ``--background`` (Linux, LEX-1957) a lane is also busy when a restart would drop work
that runs inside its Claude process, which the process tree cannot show: a background
subagent with no ``SubagentStop`` yet, a pending ``ScheduleWakeup``, or a session cron
(``CronCreate``) that has not fired or been deleted. Every pane counts: a second pane (a
teammate agent) makes the lane busy. So does a direct child of Claude that started well
after it (a background shell that ``exec``ed a worker), and any process outside the panes
that carries the session's id (a detached worker Forge's stop would also kill). A lane
whose pane, task directory or transcript cannot be found is busy, because its background
work cannot be seen.

A Codex lane (LEX-1967, Linux only: it reads /proc; elsewhere a Codex lane has no pane and
is judged busy) has no pane: its broker runs ``codex app-server``. It is busy when
any thread the app-server has open is mid-turn (its rollout file, the app-server's own
record, ends in ``task_started``), when a process under the app-server is not one of its
helpers (an ``exec`` tool command, or a worker one detached), or when a process elsewhere
carries the session id. A Codex lane whose app-server or main thread's rollout cannot be
found is busy.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

TRAIN = re.compile(r"release\.py|qa\.sh|xcodebuild|journey|\barchive\b|fastlane|altool|notarytool")
# A tool command (Claude's Bash tool, a background task, a Codex exec) runs in a shell the
# agent spawned; MCP servers and other long-lived helpers are direct executables.
TOOL_SHELL = re.compile(r"^(\S*/)?(ba|z|da)?sh\s+(-\S*c\S*\s|-c\s)")


def parse_ps(text: str) -> dict[str, tuple[str, str, str]]:
    """``ps -o pid=,ppid=,etime=,command=`` output as {pid: (ppid, etime, command)}."""
    procs = {}
    for line in text.splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4:
            procs[parts[0]] = (parts[1], parts[2], parts[3])
    return procs


# Claude Code runs a tool command as ``… && eval 'COMMAND' < /dev/null && pwd -P >| …``,
# with single quotes inside COMMAND written as '"'"'.
_EVAL = re.compile(r"\beval '(?P<cmd>.*)' < /dev/null && pwd -P", re.S)
# A loop that only waits: ``until|while CONDITION; do sleep N; done``, maybe followed by more.
_POLL = re.compile(
    r"^(?:until|while)\b.*?;\s*do\s+sleep\s+\S+\s*;?\s*done\b\s*;?\s*(?P<after>.*)$", re.S
)


def agent_command(cmd: str) -> str:
    """The command the agent ran, without the shell wrapper its harness adds."""
    match = _EVAL.search(cmd)
    text = match.group("cmd").replace("'\"'\"'", "'") if match else cmd
    return " ".join(text.split())


def describe_tool_command(
    pid: str, procs: dict[str, tuple[str, str, str]], kids: dict[str, list[str]]
) -> str:
    """A blocking tool command, named so an operator can stop it without guessing."""
    _, elapsed, cmd = procs[pid]
    command = agent_command(cmd)
    now = ", ".join(procs[kid][2][:40] for kid in kids.get(pid, [])) or "nothing"
    poll = _POLL.match(command)
    if poll is None:
        kind = "tool command"
    elif poll.group("after").strip():
        kind = f"polling loop, then runs: {poll.group('after').strip()[:80]}"
    else:
        kind = "polling loop, only waiting"
    return f"pid {pid}, {elapsed}, {kind}: {command[:160]} (now: {now})"


def lane_reasons(
    state: str | None, pane_pid: str | None, procs: dict[str, tuple[str, str, str]]
) -> tuple[list[str], set[str]]:
    """Why one lane is busy (empty when it may be restarted), and the pids in its pane.

    One reason per blocking process: a train (a running process of the train, not a
    shell that only names one) or a tool command, with its pid, age and command.
    """
    kids: dict[str, list[str]] = {}
    for pid, (ppid, _, _) in procs.items():
        kids.setdefault(ppid, []).append(pid)

    def tree(pid: str) -> list[str]:
        out = []
        for kid in kids.get(pid, []):
            out += [kid, *tree(kid)]
        return out

    reasons = [] if state == "idle" else [f"forge activity {state}"]
    pids: set[str] = set()
    for child in kids.get(pane_pid, []) if pane_pid else []:
        sub = tree(child)
        pids.update([child, *sub])
        for pid in [child, *sub]:
            cmd = procs[pid][2]
            if TRAIN.search(cmd) and not TOOL_SHELL.search(cmd):
                reasons.append(f"train: pid {pid}, {procs[pid][1]}: {cmd[:120]}")
        for pid in [child, *sub]:
            if TOOL_SHELL.search(procs[pid][2]) and (
                pid == child or not TOOL_SHELL.search(procs[procs[pid][0]][2])
            ):
                reasons.append(describe_tool_command(pid, procs, kids))
    return reasons, pids


def host_trains(procs: dict[str, tuple[str, str, str]], lane_pids: set[str]) -> list[str]:
    """Train processes outside every lane's pane (launchd or cron jobs, detached runs)."""
    return [
        f"{pid} {etime} {cmd[:100]}"
        for pid, (_, etime, cmd) in procs.items()
        if TRAIN.search(cmd) and pid not in lane_pids and "lane_busy.py" not in cmd
    ]


def etime_seconds(etime: str) -> int:
    """``ps`` elapsed time (``[[dd-]hh:]mm:ss``) in seconds."""
    days, _, clock = etime.rpartition("-")
    seconds = 0
    for part in clock.split(":"):
        seconds = seconds * 60 + int(part)
    return seconds + int(days or 0) * 86400


def _timestamp(record: dict) -> float | None:
    stamp = record.get("timestamp")
    if not isinstance(stamp, str):
        return None
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


def running_agents(tasks_dir: Path, started: float) -> list[str]:
    """Background subagents of the process started at ``started`` with no SubagentStop yet."""
    reasons = []
    for output in sorted(tasks_dir.glob("*.output")):
        if not output.is_symlink():
            continue  # a background shell's log; the shell itself is in the process tree
        target = output.resolve()
        if not target.exists() or target.stat().st_mtime < started:
            continue
        text = target.read_text(errors="replace")
        if '"hookEvent":"SubagentStop"' in text or '"hookEvent": "SubagentStop"' in text:
            continue
        age = int(time.time() - target.stat().st_mtime)
        reasons.append(f"background agent {output.stem} running (last write {age}s ago)")
    return reasons


_WAKEUP = re.compile(r"Next wakeup scheduled for \S+ \(in (\d+)s\)")
_CRON_ID = re.compile(r"Scheduled (one-shot|recurring) task (\w+) \((.+?)\)")


def _cron_due(spec: str, created: float) -> float | None:
    """When a one-shot ``M H D Mo *`` job fires (host local time), or None if not that shape."""
    fields = spec.split()
    if len(fields) != 5 or not all(f.isdigit() for f in fields[:4]):
        return None
    minute, hour, day, month = (int(f) for f in fields[:4])
    year = time.localtime(created).tm_year
    due = time.mktime((year, month, day, hour, minute, 0, 0, 0, -1))
    return (
        due
        if due >= created - 60
        else time.mktime((year + 1, month, day, hour, minute, 0, 0, 0, -1))
    )


def pending_schedules(transcript: Path, started: float, now: float) -> list[str]:
    """ScheduleWakeup and session crons this process armed that would die with it."""
    calls: dict[str, str] = {}
    wakeup: float | None = None
    crons: dict[str, tuple[str, float | None]] = {}
    deleted: set[str] = set()
    for line in transcript.read_text(errors="replace").splitlines():
        if not any(key in line for key in ("ScheduleWakeup", "Cron", "wakeup", "Scheduled ")):
            continue  # a cheap pre-filter: tool uses name the tool, results say what was armed
        try:
            record = json.loads(line)
        except ValueError:
            continue
        at = _timestamp(record)
        content = (record.get("message") or {}).get("content")
        if at is None or at < started or not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") in (
                "ScheduleWakeup",
                "CronCreate",
                "CronDelete",
            ):
                calls[block.get("id", "")] = block["name"]
                if block["name"] == "CronDelete":
                    deleted.add(str((block.get("input") or {}).get("id")))
            if block.get("type") != "tool_result" or block.get("tool_use_id") not in calls:
                continue
            result = json.dumps(block.get("content"))
            if calls[block["tool_use_id"]] == "ScheduleWakeup":
                match = _WAKEUP.search(result)
                wakeup = at + int(match.group(1)) if match else None
                continue
            match = _CRON_ID.search(result)
            if match and match.group(1) == "one-shot":
                crons[match.group(2)] = (match.group(3), _cron_due(match.group(3), at))
            elif match:
                crons[match.group(2)] = (match.group(3), None)
    reasons = []
    if wakeup is not None and wakeup > now:
        reasons.append(f"ScheduleWakeup pending, due in {int(wakeup - now)}s")
    for job, (spec, due) in crons.items():
        if job in deleted or (due is not None and due <= now):
            continue
        when = f"due in {int(due - now)}s" if due is not None else "recurring"
        reasons.append(f"session cron {job} ({spec}) {when}")
    return reasons


# A helper (an MCP server) starts with Claude; a direct child started later is work a
# background shell ``exec``ed into, which no longer looks like a tool shell.
HELPER_START_GRACE_S = 120


def late_children(pane: str, procs: dict[str, tuple[str, str, str]]) -> list[str]:
    """Non-shell direct children of the pane's Claude that started well after it."""
    if pane not in procs:
        return []
    age = etime_seconds(procs[pane][1])
    return [
        f"pid {pid} started {age - etime_seconds(etime)}s after Claude: {command[:100]}"
        for pid, (ppid, etime, command) in procs.items()
        if ppid == pane
        and not TOOL_SHELL.search(command)
        and etime_seconds(etime) < age - HELPER_START_GRACE_S
    ]


# What legitimately carries a session's id outside its panes: its broker, its tmux server,
# and the pane logger (``tmux pipe-pane`` -> ``sh -c cat >> …/panes/N.log`` -> ``cat``).
_SESSION_INFRA = re.compile(
    r"-m skuld(\s|$)|platform skuld$|^tmux -S |^sh -c cat >> \S*/tmux-interactive/|^cat$"
)


def stray_session_processes(sid: str, in_panes: set[str], proc: Path = Path("/proc")) -> list[str]:
    """Processes outside the panes that carry ``SKULD__SESSION__ID=<sid>``: Forge's stop
    kills them too (``session_runtime_pids``), so a detached worker among them is work."""
    marker = f"SKULD__SESSION__ID={sid}".encode()
    reasons = []
    for entry in proc.glob("[0-9]*"):
        if entry.name in in_panes or entry.name == str(os.getpid()):
            continue
        try:
            if marker not in (entry / "environ").read_bytes().split(b"\0"):
                continue
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if not _SESSION_INFRA.search(command.strip()):
            reasons.append(
                f"pid {entry.name} outside the panes carries the session: {command[:100]}"
            )
    return reasons


# What runs under a Codex app-server and is not work: its MCP servers (started with it, or
# again for a thread) and its code-mode host.
_CODEX_HELPER = re.compile(r"codex-code-mode-host$|-m skuld\.forge_mcp(\s|$)")
# The app-server's own record of a thread's turns (``event_msg`` payload types).
_TURN_MARKERS = ("task_started", "task_complete", "turn_aborted")


def _environ(pid: str, proc: Path) -> list[bytes] | None:
    try:
        return (proc / pid / "environ").read_bytes().split(b"\0")
    except OSError:
        return None


def codex_app_servers(sid: str, procs: dict[str, tuple[str, str, str]], proc: Path) -> list[str]:
    """The pids of every ``codex app-server`` carrying the session (one, normally)."""
    marker = f"SKULD__SESSION__ID={sid}".encode()
    found = []
    for pid, (_, _, command) in procs.items():
        if " app-server" in f" {command}" and "codex" in command.split()[0]:
            env = _environ(pid, proc)
            if env is not None and marker in env:
                found.append(pid)
    return sorted(found, key=int)


def codex_app_server(sid: str, procs: dict[str, tuple[str, str, str]], proc: Path) -> str | None:
    """The pid of the session's ``codex app-server``, or None (also when there are two)."""
    found = codex_app_servers(sid, procs, proc)
    return found[0] if len(found) == 1 else None


def codex_home(app_pid: str, proc: Path = Path("/proc")) -> Path:
    """The app-server's ``CODEX_HOME`` (where its rollout files live)."""
    for item in _environ(app_pid, proc) or []:
        if item.startswith(b"CODEX_HOME="):
            return Path(item.split(b"=", 1)[1].decode())
    return Path.home() / ".codex"


def open_rollouts(app_pid: str, proc: Path = Path("/proc")) -> list[Path]:
    """The rollout files (one per loaded thread) the app-server holds open."""
    out = set()
    try:
        fds = list((proc / app_pid / "fd").iterdir())
    except OSError:
        return []
    for fd in fds:
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        name = Path(target).name
        if name.startswith("rollout-") and name.endswith(".jsonl"):
            out.add(Path(target))
    return sorted(out)


def last_turn_marker(path: Path, chunk: int = 1 << 20) -> tuple[str, str, str] | None:
    """(type, turn id, timestamp) of the rollout's last turn record, read from its end."""
    needles = [f'"{kind}"'.encode() for kind in _TURN_MARKERS]
    with open(path, "rb") as stream:
        pos = stream.seek(0, 2)
        carry = b""
        while pos > 0:
            step = min(chunk, pos)
            pos -= step
            stream.seek(pos)
            lines = (stream.read(step) + carry).split(b"\n")
            carry = lines.pop(0) if pos > 0 else b""
            for raw in reversed(lines):
                if not any(needle in raw for needle in needles):
                    continue
                try:
                    record = json.loads(raw)
                except ValueError:
                    continue
                payload = record.get("payload") if isinstance(record, dict) else None
                if isinstance(payload, dict) and payload.get("type") in _TURN_MARKERS:
                    return (
                        payload["type"],
                        str(payload.get("turn_id")),
                        str(record.get("timestamp")),
                    )
    return None


def _ends_complete(path: Path) -> bool:
    """The file ends with a whole record (a newline), so no write is half done."""
    with open(path, "rb") as stream:
        if stream.seek(0, 2) == 0:
            return True
        stream.seek(-1, 2)
        return stream.read(1) == b"\n"


def _descendants(root: str, procs: dict[str, tuple[str, str, str]]) -> list[str]:
    kids: dict[str, list[str]] = {}
    for pid, (ppid, _, _) in procs.items():
        kids.setdefault(ppid, []).append(pid)
    out, todo = [], [root]
    while todo:
        for kid in kids.get(todo.pop(), []):
            out.append(kid)
            todo.append(kid)
    return out


def mcp_commands(app_command: str, home: Path) -> list[list[str]]:
    """The full argv of each MCP server the app-server starts (command plus args): its
    ``-c mcp_servers.<name>.command/args`` overrides over its config.toml sections.

    Codex lanes exist only on Linux hosts, whose python3 has ``tomllib`` (3.11+).
    """
    import tomllib  # noqa: PLC0415 - only Codex lanes need it; the mini's 3.9 never does

    try:
        config = tomllib.loads((home / "config.toml").read_text(errors="replace"))
    except FileNotFoundError:
        config = {}
    servers = {
        name: dict(server)
        for name, server in (config.get("mcp_servers") or {}).items()
        if isinstance(server, dict)
    }
    for name, key, value in re.findall(
        r"mcp_servers\.([\w-]+)\.(command|args)=(\"[^\"]*\"|\[[^\]]*\])", app_command
    ):
        servers.setdefault(name, {})[key] = tomllib.loads(f"v = {value}")["v"]
    out = []
    for server in servers.values():
        command, args = server.get("command"), server.get("args", [])
        if (
            isinstance(command, str)
            and isinstance(args, list)
            and all(isinstance(a, str) for a in args)
        ):
            out.append([command, *args])
    return out


def _argv(pid: str, proc: Path) -> list[str] | None:
    """The process's exact argv (``ps`` joins it with spaces, losing argument boundaries)."""
    try:
        raw = (proc / pid / "cmdline").read_bytes()
    except OSError:
        return None
    return [part.decode(errors="replace") for part in raw.rstrip(b"\0").split(b"\0")]


def _is_helper(command: str, mcp: list[list[str]], argv: list[str] | None = None) -> bool:
    """One of the app-server's own processes: its code-mode host, a Forge MCP server, or a
    process whose whole argv is a configured MCP server's (an interpreter may run it)."""
    if _CODEX_HELPER.search(command):
        return True
    words = argv if argv is not None else command.split()
    return any(words == server or words[1:] == server for server in mcp)


def codex_reasons(
    sid: str,
    cli_session_id: str | None,
    procs: dict[str, tuple[str, str, str]],
    proc: Path = Path("/proc"),
) -> tuple[list[str], set[str]] | None:
    """Why a Codex lane is busy, and the pids it owns; None when the lane is not Codex."""
    apps = codex_app_servers(sid, procs, proc)
    if not apps:
        return None
    if len(apps) > 1:
        return [f"{len(apps)} Codex app-servers carry the session: {', '.join(apps)}"], set(apps)
    app = apps[0]
    reasons = []
    tree = _descendants(app, procs)
    mcp = mcp_commands(procs[app][2], codex_home(app, proc))
    for pid in tree:
        ppid, elapsed, command = procs[pid]
        # One reason per piece of work: a direct child of the app-server that is not one of
        # its helpers (a tool command, or a worker it detached), and anything the code-mode
        # host runs (code the agent executes). What runs under either is its.
        parent = procs.get(ppid, ("", "", ""))[2]
        helper = _is_helper(command, mcp, _argv(pid, proc))
        if (ppid == app and not helper) or "codex-code-mode-host" in parent:
            reasons.append(f"pid {pid}, {elapsed}, under the Codex app-server: {command[:120]}")
    rollouts = open_rollouts(app, proc)
    if not cli_session_id or not any(r.name.endswith(f"-{cli_session_id}.jsonl") for r in rollouts):
        reasons.append(
            f"the app-server has no rollout open for thread {cli_session_id}: its turns "
            "cannot be seen"
        )
    for rollout in rollouts:
        try:
            marker = last_turn_marker(rollout)
        except OSError as exc:
            reasons.append(f"cannot read {rollout.name}: {exc}")
            continue
        thread = rollout.stem[-36:]
        if marker is None:
            reasons.append(f"thread {thread} has no turn record: its state cannot be seen")
        elif marker[0] == "task_started":
            reasons.append(f"thread {thread} turn {marker[1]} running since {marker[2]}")
        elif not _ends_complete(rollout):
            reasons.append(f"thread {thread}'s rollout is mid-write: a record is being appended")
    return reasons, {app, *tree}


def _proc_link(pid: str, name: str) -> str | None:
    try:
        return os.readlink(f"/proc/{pid}/{name}")
    except OSError:
        return None


def claude_tasks_dir(pid: str, command: str) -> Path | None:
    """The task directory of the Claude process ``pid`` (Linux /proc), or None."""
    fd_dir = Path(f"/proc/{pid}/fd")
    try:
        links = [_proc_link(pid, f"fd/{fd.name}") for fd in fd_dir.iterdir()]
    except OSError:
        return None
    for link in links:
        if link and link.endswith("/tasks"):
            return Path(link)
    cwd = _proc_link(pid, "cwd")
    resume = re.search(r"--(?:resume|session-id)\s+(\S+)", command)
    if cwd is None or resume is None:
        return None
    slug = re.sub(r"[^A-Za-z0-9]", "-", cwd)
    return Path(f"/tmp/claude-{os.getuid()}/{slug}/{resume.group(1)}/tasks")


def background_reasons(
    panes: list[str], procs: dict[str, tuple[str, str, str]], cli_session_id: str | None
) -> list[str]:
    """Why a restart would drop work inside the lane's Claude process (see the module doc)."""
    if not panes:
        return ["no tmux pane: its background work cannot be seen"]
    reasons = []
    main, *others = panes
    for pane in others:
        command = procs.get(pane, ("", "", "?"))[2]
        reasons.append(f"second pane runs pid {pane}: {command[:100]}")
    if main not in procs or "claude" not in procs[main][2].split()[0]:
        command = procs.get(main, ("", "", "gone"))[2]
        return [
            *reasons,
            f"pane runs {command[:80]}, not Claude: its background work cannot be seen",
        ]
    now = time.time()
    started = now - etime_seconds(procs[main][1])
    tasks = claude_tasks_dir(main, procs[main][2])
    if tasks is None:
        reasons.append(f"cannot find the task directory of Claude pid {main}")
    elif tasks.is_dir():
        reasons += running_agents(tasks, started)
    transcripts = (
        sorted(Path.home().glob(f".claude/projects/*/{cli_session_id}.jsonl"))
        if cli_session_id
        else []
    )
    if len(transcripts) != 1:
        reasons.append(f"cannot find the transcript of conversation {cli_session_id}")
    else:
        reasons += pending_schedules(transcripts[0], started, now)
    return reasons


def judge(
    base: str,
    tmux: str,
    only: list[str],
    background: bool = False,
    socket_dir: str | None = None,
) -> dict:
    """{lanes, judged, busy, host_trains} for the running local lanes (only those in ``only``).

    ``judged`` lists the lane ids looked at: a requested id missing from it is not running.
    A lane whose panes cannot be listed, or whose pane process is gone, is busy.
    """
    socket_dir = socket_dir or f"/tmp/skuld-tmux-{os.getuid()}"
    url = f"{base.rstrip('/')}/api/v1/forge/sessions?scope=local"
    rows = json.load(urllib.request.urlopen(url, timeout=20))
    lanes = [r for r in rows if r.get("status") == "running" and (not only or r["id"] in only)]
    ps = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,etime=,command="], capture_output=True, text=True, check=True
    )
    procs = parse_ps(ps.stdout)
    busy, lane_pids = [], set()
    for lane in lanes:
        sid = lane["id"]
        codex = codex_reasons(sid, lane.get("cli_session_id"), procs)
        if codex is not None:
            # A Codex lane has no pane; with or without --background its app-server shows
            # what it is doing (LEX-1967).
            reasons, pids = codex
            if lane.get("activity_state") != "idle":
                reasons.insert(0, f"forge activity {lane.get('activity_state')}")
            reasons += stray_session_processes(sid, pids)
            lane_pids |= pids
            if reasons:
                busy.append({"id": sid, "name": lane.get("name"), "reasons": reasons})
            continue
        sock = f"{socket_dir}/skuld-{sid}.sock"
        listed = subprocess.run(
            [tmux, "-S", sock, "list-panes", "-s", "-t", f"skuld-{sid}", "-F", "#{pane_pid}"],
            capture_output=True,
            text=True,
        )
        pane = listed.stdout.split() if listed.returncode == 0 else []
        reasons, pids = lane_reasons(lane.get("activity_state"), pane[0] if pane else None, procs)
        if not pane:
            reasons.append(f"its panes cannot be listed ({sock}): nothing in them can be seen")
        reasons += [f"pane pid {p} is not running" for p in pane if p not in procs]
        for extra in pane[1:]:
            more, extra_pids = lane_reasons("idle", extra, procs)
            reasons += more
            pids |= extra_pids | {extra}
        if background:
            reasons += background_reasons(pane, procs, lane.get("cli_session_id"))
            for each in pane:
                reasons += late_children(each, procs)
            reasons += stray_session_processes(sid, pids | set(pane))
        lane_pids |= pids
        if reasons:
            busy.append({"id": sid, "name": lane.get("name"), "reasons": reasons})
    return {
        "lanes": len(lanes),
        "judged": [lane["id"] for lane in lanes],
        "busy": busy,
        "host_trains": host_trains(procs, lane_pids),
    }


def main(argv: list[str]) -> int:
    background = "--background" in argv
    base, tmux, *only = [arg for arg in argv if arg != "--background"]
    verdict = judge(base, tmux, only, background=background)
    print(json.dumps(verdict))
    return 1 if verdict["busy"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
