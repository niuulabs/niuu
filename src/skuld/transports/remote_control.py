"""RemoteControlTransport — launch ``claude remote-control`` and surface its pairing URL.

Unlike the broker-driven transports (which spawn ``claude -p --stream-json`` and
drive the conversation themselves), this transport runs Claude Code in its native
**Remote Control** mode: a long-lived server that pairs with Anthropic's relay so
the Claude mobile app / claude.ai/code can attach to and DRIVE the session.

The broker is NOT the conversation driver here — it is a supervisor. This
transport's jobs are:

  * spawn ``claude remote-control --name <name> --spawn same-dir [--permission-mode ...]``
    in the session workspace,
  * scrape the pairing URL (``https://claude.ai/code?environment=env_...``) out of
    the TUI's ANSI-wrapped stdout,
  * surface it — emit it both as a visible assistant turn (so the link lands in
    the transcript / durable-log replay) and as a structured ``remote_control``
    event the broker can persist as a session field,
  * keep the process alive (the broker's heartbeat keeps the Volundr session
    live) and terminate it on stop.

``send_message`` does not reach the agent — the native app owns the conversation —
so it just re-surfaces the pairing link. Auth: ``claude remote-control`` requires
claude.ai OAuth (``~/.claude/.credentials.json``), inherited from the broker env.
"""

from __future__ import annotations

import asyncio
import glob
import logging
import os
import re
import signal
import subprocess
from contextlib import suppress

from niuu.ports.cli import CLITransport, TransportCapabilities

logger = logging.getLogger("skuld.transport")

# Strip terminal control sequences from the remote-control TUI before scraping.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# The pairing handle Claude prints once connected to the relay.
_URL_RE = re.compile(r"https://claude\.ai/code\?environment=env_[A-Za-z0-9_-]+")
# Lines worth surfacing when a launch fails before pairing.
_ERROR_HINT = re.compile(r"error|failed|not (logged in|authenticated)|unauthor|denied|enable", re.I)
_STDOUT_LIMIT = 1 * 1024 * 1024


class RemoteControlTransport(CLITransport):
    """Launch ``claude remote-control`` and surface its pairing URL.

    A degenerate transport: it does not drive a conversation (the native app
    does), it launches the Remote Control server and reports the pairing link.
    """

    def __init__(
        self,
        workspace_dir: str,
        model: str = "",
        session_id: str = "",
        skip_permissions: bool = False,
        **_kwargs: object,
    ) -> None:
        super().__init__()
        self.workspace_dir = workspace_dir
        self._model = model
        self._session_id_hint = session_id
        self._skip_permissions = skip_permissions
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._url: str | None = None
        # Unique token baked into --name so stop() can find + kill this session's
        # foreground client AND its detached per-environment worker (which
        # reparents to a subreaper and survives the foreground) — without
        # touching the shared singleton daemon (~/.claude/daemon).
        self._token = (str(session_id).strip() or "")[:12]

    # ------------------------------------------------------------------
    # CLITransport interface
    # ------------------------------------------------------------------

    @property
    def capabilities(self) -> TransportCapabilities:
        # The native app drives the conversation; the broker can't send turns.
        return TransportCapabilities(send_message=False)

    @property
    def session_id(self) -> str | None:
        return None

    @property
    def last_result(self) -> dict | None:
        return None

    @property
    def is_alive(self) -> bool:
        proc = self._process
        return proc is not None and proc.returncode is None

    @property
    def remote_control_url(self) -> str | None:
        return self._url

    def _build_command(self) -> list[str]:
        binary = os.environ.get("SKULD__CLI_BINARY", "claude")
        base = os.environ.get("SKULD__SESSION__NAME") or "volundr"
        # Bake the unique token into the display name so the worker process
        # carries it in argv (the handle stop() uses to target only this session).
        name = f"{base}-{self._token}" if self._token else base
        # The native app uses the host permission mode for the sessions it spawns.
        perm = os.environ.get("SKULD__REMOTE_CONTROL_PERMISSION_MODE") or (
            "bypassPermissions" if self._skip_permissions else "default"
        )
        return [
            binary,
            "remote-control",
            "--name",
            name,
            "--spawn",
            "same-dir",
            "--permission-mode",
            perm,
        ]

    async def start(self) -> None:
        if self.is_alive:
            return
        cmd = self._build_command()
        logger.info("RemoteControlTransport: launching %s", " ".join(cmd))
        # Remote Control REQUIRES claude.ai OAuth. An inherited ANTHROPIC_API_KEY
        # (set in the deploy/agent-service env) forces API-key auth and Claude
        # refuses to start RC ("ANTHROPIC_API_KEY is set … unset it"). Strip the
        # API-key auth vars so RC uses the host's OAuth credentials (~/.claude).
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
        }
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self.workspace_dir,
            env=env,
            limit=_STDOUT_LIMIT,
        )
        self._reader_task = asyncio.create_task(self._read_output())

    async def _read_output(self) -> None:
        proc = self._process
        if proc is None or proc.stdout is None:
            return
        lines = 0
        tail: list[str] = []
        try:
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                line = _ANSI_RE.sub("", raw.decode("utf-8", errors="replace")).strip()
                if not line:
                    continue
                lines += 1
                tail.append(line)
                del tail[:-8]
                logger.debug("rc> %s", line[:200])
                if _ERROR_HINT.search(line):
                    logger.warning("RemoteControlTransport: %s", line[:300])
                if self._url is None:
                    match = _URL_RE.search(line)
                    if match:
                        await self._on_url(match.group(0))
        except asyncio.CancelledError:
            return
        except Exception:
            logger.error("RemoteControlTransport reader failed", exc_info=True)
        # Reader ended = stdout EOF = process exiting. Surface why, since a silent
        # early exit (before the pairing URL) is the failure we need to debug.
        with suppress(Exception):
            rc = await asyncio.wait_for(proc.wait(), timeout=5)
            if self._url is None:
                logger.warning(
                    "RemoteControlTransport: claude remote-control exited rc=%s after %d "
                    "output line(s) WITHOUT a pairing URL. Last output: %s",
                    rc,
                    lines,
                    " | ".join(tail) or "(none)",
                )
            else:
                logger.info("RemoteControlTransport: process exited rc=%s", rc)

    async def _on_url(self, url: str) -> None:
        self._url = url
        logger.info("RemoteControlTransport: pairing URL captured: %s", url)
        # Structured event — the broker surfaces it as an assistant turn across
        # the conversation history, durable-log replay, and live channels.
        with suppress(Exception):
            await self._emit({"type": "remote_control", "subtype": "paired", "url": url})

    async def send_message(
        self, content: str, *, msg_id: str | None = None, request_id: str | None = None
    ) -> None:
        # The native app owns the conversation; re-surface the pairing link.
        if self._url:
            with suppress(Exception):
                await self._emit(
                    {"type": "remote_control", "subtype": "resurface", "url": self._url}
                )

    @staticmethod
    def _iter_processes() -> list[tuple[int, str]]:
        """Yield (pid, command line) for every visible process.

        Reads ``/proc`` where it exists (Linux); falls back to ``ps`` output on
        macOS, where mini-mode brokers run and ``/proc`` is absent — without the
        fallback ``stop()`` would silently leave the detached RC worker running.
        """
        entries: list[tuple[int, str]] = []
        proc_paths = glob.glob("/proc/[0-9]*/cmdline")
        if proc_paths:
            for path in proc_paths:
                try:
                    with open(path, "rb") as handle:
                        raw = handle.read()
                except Exception:
                    continue
                cl = raw.replace(b"\0", b" ").decode("utf-8", "replace")
                entries.append((int(path.rsplit("/", 2)[1]), cl))
            return entries
        try:
            out = subprocess.run(
                ["ps", "-axo", "pid=,command="],
                capture_output=True,
                text=True,
                check=False,
            ).stdout
        except Exception:
            return entries
        for line in out.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) != 2 or not parts[0].isdigit():
                continue
            entries.append((int(parts[0]), parts[1]))
        return entries

    def _sweep_kill(self, sig: int) -> int:
        """Signal every ``remote-control`` process carrying our unique token.

        Catches both the foreground client and the detached per-environment
        worker (it reparents away from the broker, so a plain child-kill misses
        it). The token is specific to this session, so the shared singleton
        daemon and other sessions' RC processes are untouched.
        """
        if not self._token:
            return 0
        killed = 0
        for pid, cl in self._iter_processes():
            if "remote-control" in cl and self._token in cl:
                with suppress(Exception):
                    os.kill(pid, sig)
                    killed += 1
        return killed

    async def stop(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            with suppress(Exception, asyncio.CancelledError):
                await self._reader_task
            self._reader_task = None
        # Kill the foreground client first (graceful), then sweep any token-tagged
        # survivor (the detached worker), escalating to SIGKILL.
        proc = self._process
        if proc is not None and proc.returncode is None:
            with suppress(ProcessLookupError):
                proc.send_signal(signal.SIGTERM)
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=5)
        self._sweep_kill(signal.SIGTERM)
        await asyncio.sleep(1)
        self._sweep_kill(signal.SIGKILL)
        self._process = None


# Path the managed Codex remote-control daemon requires (standalone installer
# only — the npm `@openai/codex` install cannot manage it).
_CODEX_STANDALONE = "~/.codex/packages/standalone/current/codex"


class CodexRemoteControlTransport(CLITransport):
    """Codex Remote Control — experimental, gated on the standalone Codex install.

    `codex remote-control` (managed app-server daemon + control socket that the
    native Codex app attaches to) only runs from the standalone installer's fixed
    path; the npm `codex` errors with "managed standalone Codex install not
    found". Until that install exists this transport fails fast with a clear,
    actionable notice rather than spawning a broken process. The launch/pairing
    path is intentionally not wired yet (the user opted for "Claude now, Codex
    stubbed").
    """

    def __init__(
        self,
        workspace_dir: str,
        model: str = "",
        session_id: str = "",
        **_kwargs: object,
    ) -> None:
        super().__init__()
        self.workspace_dir = workspace_dir
        self._model = model

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(send_message=False)

    @property
    def session_id(self) -> str | None:
        return None

    @property
    def last_result(self) -> dict | None:
        return None

    @property
    def is_alive(self) -> bool:
        return False

    async def _notice(self) -> None:
        if os.path.exists(os.path.expanduser(_CODEX_STANDALONE)):
            text = (
                "Codex Remote Control (experimental): the standalone install was "
                "detected, but the launch/pairing path is not wired yet. Use the "
                "Claude Remote Control session type for now."
            )
        else:
            text = (
                "⚠️ **Codex Remote Control is not available on this host yet.**\n\n"
                "It needs the *standalone* Codex install (`curl … install.sh`) — the "
                "npm `codex` install can't manage the remote-control daemon (missing "
                f"`{_CODEX_STANDALONE}`). Install standalone Codex, then this session "
                "type will pair like Claude Remote Control. (Experimental.)"
            )
        with suppress(Exception):
            await self._emit(
                {
                    "type": "assistant",
                    "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
                }
            )

    async def start(self) -> None:
        await self._notice()

    async def send_message(
        self, content: str, *, msg_id: str | None = None, request_id: str | None = None
    ) -> None:
        await self._notice()

    async def stop(self) -> None:
        return None
