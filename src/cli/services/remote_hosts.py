"""`niuu remote`: join a machine to a Forge as a registered host over the operator's SSH.

Nothing here re-implements the join. The CLI mints a code as the operator, runs the
``command`` the mint response carries (the same line the web console shows) on the
target over the operator's own ``ssh``, then follows the host through the steps the
console shows (``joinSteps`` in ``plugin-volundr/src/domain/computeHosts.ts``).
"""

from __future__ import annotations

import asyncio
import io
import os
import shlex
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, BinaryIO, Literal
from urllib.parse import urlsplit

from cli.api.compute_hosts import ComputeHostsAPI
from volundr.adapters.inbound.host_join_script import HOST_CONFIG_PATH
from volundr.adapters.outbound.host_checks import LEAVE_PATH

#: Display states in which a joined host has finished joining (the console's READY_STATES).
READY_STATES = frozenset({"ready", "in_service", "draining", "out_of_service"})

StepStatus = Literal["done", "now", "todo", "failed"]
Echo = Callable[[str], None]
Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


class RemoteError(Exception):
    """A `niuu remote` step failed; the message says why and what to do."""


# ── the target ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SshTarget:
    """Where the operator's own ``ssh`` goes: a destination as ssh takes it."""

    destination: str
    port: int | None = None
    jump: str = ""

    def argv(self, remote_command: str, *, tty: bool) -> list[str]:
        args = ["ssh"]
        if tty:
            args.append("-t")
        if self.port is not None:
            args += ["-p", str(self.port)]
        if self.jump:
            args += ["-J", self.jump]
        return [*args, "--", self.destination, remote_command]


def parse_target(raw: str, *, port: int | None = None, jump: str = "") -> SshTarget:
    """``user@host``, an ``~/.ssh/config`` alias, or ``ssh://user@host:port``."""
    value = raw.strip()
    if not value:
        raise RemoteError("The SSH destination is empty")
    if value.startswith("-"):
        raise RemoteError(f"Refusing an SSH destination that starts with '-': {value!r}")
    if jump.startswith("-"):
        raise RemoteError(f"Refusing a jump host that starts with '-': {jump!r}")
    if not value.startswith("ssh://"):
        if any(ch.isspace() for ch in value):
            raise RemoteError(f"An SSH destination has no spaces: {value!r}")
        return SshTarget(destination=value, port=port, jump=jump)
    parts = urlsplit(value)
    if not parts.hostname or parts.path not in ("", "/") or parts.query or parts.fragment:
        raise RemoteError(f"Not an ssh:// destination (ssh://[user@]host[:port]): {value!r}")
    if parts.port is not None and port is not None and parts.port != port:
        raise RemoteError(f"{value} names port {parts.port}, but --port is {port}")
    host = parts.hostname
    destination = f"{parts.username}@{host}" if parts.username else host
    return SshTarget(destination=destination, port=parts.port or port, jump=jump)


def remote_sh(script: str) -> str:
    """The script run by ``sh`` on the target, whatever the user's login shell is."""
    return f"sh -c {shlex.quote(script)}"


@dataclass(frozen=True)
class RunResult:
    returncode: int
    last_line: str


def run_streaming(
    argv: Sequence[str],
    *,
    out: BinaryIO | None = None,
    indent: bytes = b"  ",
) -> RunResult:
    """Run ``argv`` with the terminal's stdin, passing its output through as it arrives.

    Output is forwarded per read, not per line, so a ``sudo`` password prompt shows
    before its newline. The last non-empty line is kept to repeat a failure's reason.
    """
    sink = out or sys.stdout.buffer
    try:
        process = subprocess.Popen(  # noqa: S603 - argv is built here, never a shell string
            list(argv), stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
    except FileNotFoundError as exc:
        raise RemoteError(f"Cannot run {argv[0]!r}: {exc.strerror}. Install OpenSSH.") from exc
    with process:
        assert process.stdout is not None
        fd = process.stdout.fileno()
        at_line_start = True
        current = bytearray()
        last = b""
        while chunk := os.read(fd, io.DEFAULT_BUFFER_SIZE):
            shown = bytearray()
            for byte in chunk:
                if at_line_start and byte not in b"\r\n":
                    shown += indent
                    at_line_start = False
                shown.append(byte)
                if byte == ord("\n"):
                    at_line_start = True
                    if current.strip():
                        last = bytes(current)
                    current.clear()
                elif byte != ord("\r"):
                    current.append(byte)
            sink.write(bytes(shown))
            sink.flush()
        if current.strip():
            last = bytes(current)
        returncode = process.wait()
    return RunResult(returncode=returncode, last_line=last.decode("utf-8", "replace").strip())


def leave_script(host_id: str) -> str:
    """Run niuu-host-leave on the target only if it joined as ``host_id``.

    Forge removes its own setup when it can reach the host, so a missing config is
    already clean. A config naming another host is refused, never undone.
    """
    config = shlex.quote(HOST_CONFIG_PATH)
    wanted = shlex.quote(host_id)
    as_root = (
        f". {config}; "
        f'if [ "$HOST_ID" != {wanted} ]; then '
        f'echo "niuu: this machine joined as host $HOST_ID, not {host_id}; leaving it alone" >&2; '
        "exit 1; fi; "
        f"exec {shlex.quote(LEAVE_PATH)}"
    )
    return (
        f"if [ ! -e {config} ]; then "
        'echo "niuu: Forge already removed its setup from this machine"; exit 0; fi; '
        f"exec sudo sh -c {shlex.quote(as_root)}"
    )


# ── Forge: which Forge, which host ──────────────────────────────────────────


def resolve_forge(forges: list[dict[str, Any]], wanted: str) -> dict[str, Any]:
    """The Forge named by id, slug or name; or the only one when none is named."""
    if wanted:
        matches = [f for f in forges if wanted in (f.get("id"), f.get("slug"), f.get("name"))]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise RemoteError(f"No Forge {wanted!r} here. Forges: {_forge_list(forges)}")
        raise RemoteError(f"{wanted!r} names several Forges; use its id: {_forge_list(matches)}")
    if len(forges) == 1:
        return forges[0]
    if not forges:
        raise RemoteError("This Niuu host shows you no enabled Forge")
    raise RemoteError(f"Several Forges are visible; pick one with --forge: {_forge_list(forges)}")


def _forge_list(forges: list[dict[str, Any]]) -> str:
    return ", ".join(f"{f.get('slug') or f.get('name')} ({f.get('id')})" for f in forges) or "none"


def pick_offer(kind: str, flag: str, given: str, offered: list[str], refused: list[str]) -> str:
    """The flag's value, or the Forge's only offer when it was left out."""
    if given:
        return given
    if len(offered) == 1:
        return offered[0]
    if not offered:
        why = f" ({'; '.join(refused)})" if refused else ""
        raise RemoteError(f"This Forge offers no {kind} for hosts{why}")
    raise RemoteError(
        f"This Forge offers several {kind}s; pick one with {flag}: {', '.join(offered)}"
    )


def mint_body(
    overview: dict[str, Any],
    *,
    execution: str,
    connector: str,
    tags: list[str],
    slots: int | None,
    isolation: str,
) -> dict[str, Any]:
    runtimes = overview.get("runtimes") or []
    executions = [r["execution"] for r in runtimes if r.get("allowed")]
    refusals = [f"{r['execution']}: {r['refusal']}" for r in runtimes if r.get("refusal")]
    connectors = [c["binding_id"] for c in overview.get("connectors") or []]
    body: dict[str, Any] = {
        "execution": pick_offer("runtime", "--execution", execution, executions, refusals),
        "connector": pick_offer("connector", "--connector", connector, connectors, []),
        "tags": tags,
    }
    if slots is not None:
        body["slots"] = slots
    if isolation:
        body["isolation"] = isolation
    return body


def find_host(hosts: list[dict[str, Any]], ref: str) -> dict[str, Any]:
    """A host by id or name."""
    by_id = [h for h in hosts if h.get("id") == ref]
    if by_id:
        return by_id[0]
    by_name = [h for h in hosts if h.get("name") == ref]
    if len(by_name) == 1:
        return by_name[0]
    if not by_name:
        names = ", ".join(sorted(str(h.get("name")) for h in hosts)) or "none"
        raise RemoteError(f"This Forge has no host {ref!r}. Hosts: {names}")
    raise RemoteError(
        f"Several hosts are named {ref!r}; use the id: {', '.join(h['id'] for h in by_name)}"
    )


def new_host(before: set[str], hosts: list[dict[str, Any]], destination: str) -> dict[str, Any]:
    """The host the run registered: the one Forge lists now and did not before."""
    added = [h for h in hosts if h.get("id") not in before]
    if len(added) == 1:
        return added[0]
    if not added:
        raise RemoteError(
            f"The join on {destination} did not register a host with this Forge. "
            "The host's output is above (a failed download makes `… | sudo sh` exit 0)."
        )
    names = ", ".join(f"{h.get('name')} ({h.get('id')})" for h in added)
    raise RemoteError(
        f"Several hosts joined this Forge during the run, so which is {destination} is "
        f"ambiguous: {names}. Check them with `niuu remote hosts`."
    )


# ── following a host ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Step:
    id: str
    title: str
    detail: str
    status: StepStatus


def join_steps(host: dict[str, Any]) -> list[Step]:
    """Where a joining host is, derived exactly as the console's ``joinSteps``."""
    connection = host.get("connection") or {}
    connected = bool(connection.get("connected"))
    state = host.get("state")
    prepared = host.get("health") == "healthy"
    sized = host.get("slots") is not None

    connected_status: StepStatus = "now"
    if connected:
        connected_status = "done"
    elif state == "unreachable":
        connected_status = "failed"

    preparing_status: StepStatus = "now"
    if prepared:
        preparing_status = "done"
    elif not connected:
        preparing_status = "todo"
    elif state == "quarantined":
        preparing_status = "failed"

    capacity_status: StepStatus = "todo"
    if prepared:
        capacity_status = "done" if sized else "now"

    connected_detail = "key pinned"
    if connected_status == "failed":
        connected_detail = connection.get("last_error") or "Forge cannot reach the host"
    slots = host.get("slots")
    return [
        Step("joined", "joined", str(host.get("name") or ""), "done"),
        Step("connected", "connected", connected_detail, connected_status),
        Step("preparing", "preparing", f"recipe {host.get('execution')}", preparing_status),
        Step("audit", "audit", "", "done" if prepared else "todo"),
        Step("capacity", "capacity", f"{slots} slots" if sized else "", capacity_status),
        Step("ready", "ready", _tags_line(host), "done" if state in READY_STATES else "todo"),
    ]


def needs_capacity(host: dict[str, Any]) -> bool:
    return host.get("health") == "healthy" and host.get("slots") is None


def _tags_line(host: dict[str, Any]) -> str:
    tags = host.get("tags") or []
    return f"tags: {', '.join(tags)}" if tags else "no tags"


_MARK = {"done": "✓", "now": "…", "failed": "✗", "todo": "·"}


def step_line(step: Step) -> str:
    detail = f"  {step.detail}" if step.detail else ""
    return f"{_MARK[step.status]} {step.title:<11}{detail}".rstrip()


@dataclass(frozen=True)
class FollowResult:
    host: dict[str, Any]
    outcome: Literal["ready", "needs_capacity"]


async def follow_join(
    api: ComputeHostsAPI,
    instance_id: str,
    host_id: str,
    *,
    slots_suggested: bool,
    timeout_seconds: float,
    poll_seconds: float,
    echo: Echo,
    sleep: Sleep = asyncio.sleep,
    clock: Clock = time.monotonic,
) -> FollowResult:
    """Follow one host until it is ready or waits for its slot count; raise on a failure."""
    deadline = clock() + timeout_seconds
    shown: dict[str, StepStatus] = {}
    applied_suggestion = False
    while True:
        host = find_host((await api.overview(instance_id)).get("hosts") or [], host_id)
        waiting = needs_capacity(host)
        for step in join_steps(host):
            # Forge shows a host without a slot count as ready; it is not ready to claim.
            if step.id == "ready" and waiting:
                continue
            if step.status != "todo" and shown.get(step.id) != step.status:
                shown[step.id] = step.status
                echo(step_line(step))
        state = host.get("state")
        if state == "quarantined":
            raise RemoteError(_failure(host, "is quarantined"))
        if state == "unreachable":
            raise RemoteError(_failure(host, "is unreachable (Forge keeps retrying it)"))
        if not waiting and state in READY_STATES:
            return FollowResult(host=host, outcome="ready")
        if waiting:
            if not slots_suggested:
                return FollowResult(host=host, outcome="needs_capacity")
            if not applied_suggestion:
                suggestion = host.get("suggested_slots")
                if not suggestion:
                    raise RemoteError(
                        f"Forge has no slot suggestion for {host.get('name')}; set one with "
                        f"`niuu remote slots {host.get('name')} <n>`"
                    )
                await api.set_slots(instance_id, host_id, int(suggestion["slots"]))
                echo(f"  set {suggestion['slots']} slots (Forge's suggestion)")
                applied_suggestion = True
        if clock() >= deadline:
            current = next((s.title for s in join_steps(host) if s.status == "now"), state)
            raise RemoteError(
                f"{host.get('name')} is still at '{current}' after {timeout_seconds:g} s "
                "(--timeout, remote.follow_timeout_seconds). Forge keeps going; follow it "
                f"with `niuu remote hosts {host.get('name')}`."
            )
        await sleep(poll_seconds)


def _failure(host: dict[str, Any], what: str) -> str:
    lines = [f"{host.get('name')} {what}:"]
    findings = host.get("findings") or []
    lines += [f"  - {finding}" for finding in findings]
    error = (host.get("connection") or {}).get("last_error")
    if error and error not in findings:
        lines.append(f"  - {error}")
    if len(lines) == 1:
        lines.append("  - Forge reported no reason")
    return "\n".join(lines)


async def wait_removed(
    api: ComputeHostsAPI,
    instance_id: str,
    host_id: str,
    *,
    timeout_seconds: float,
    poll_seconds: float,
    echo: Echo,
    sleep: Sleep = asyncio.sleep,
    clock: Clock = time.monotonic,
) -> None:
    """Wait until Forge no longer lists the host (drain and remove ends when it is empty)."""
    deadline = clock() + timeout_seconds
    last = ""
    while True:
        hosts = (await api.overview(instance_id)).get("hosts") or []
        host = next((h for h in hosts if h.get("id") == host_id), None)
        if host is None:
            return
        sessions = len(host.get("sessions") or [])
        status = f"{host.get('state')}, {sessions} session{'s' if sessions != 1 else ''} left"
        if status != last:
            echo(f"… {status}")
            last = status
        if clock() >= deadline:
            raise RemoteError(
                f"{host.get('name')} is still {status} after {timeout_seconds:g} s "
                "(--timeout, remote.leave_timeout_seconds). Forge removes it once it is empty; "
                f"run `niuu remote leave {host.get('name')}` again then."
            )
        await sleep(poll_seconds)
