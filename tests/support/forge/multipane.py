"""Multi-pane tmux helper for the forge surfaces (Group G) tests.

Given a LIVE tmux session on a private socket, ``split_into_panes`` splits the
active window into N panes, each running ``fakeagent`` rendering/serving a named
screen (via the same fake-claude shim the transport spawns). A test can then use
``TmuxPage.panes()`` / ``select_pane`` / ``select_window`` to navigate and read
each pane independently — proving the navigation harness generalizes from the
single-agent window to a multi-agent / teams-of-agents layout.

The helper deliberately speaks ONLY to ``tmux`` over the socket (no transport
internals), so it composes with a standalone tmux session OR with a running
``TmuxInteractiveTransport`` (whose pane watcher then discovers the new panes and
emits the real ``terminal_pane_opened`` events).
"""

from __future__ import annotations

import asyncio
import shlex
import sys
from pathlib import Path

_FAKEAGENT = Path(__file__).resolve().parent / "fakeagent.py"


def _fakeagent_command(boot: str) -> str:
    """A shell command line that runs fakeagent with ``boot`` as its FORGE boot
    directive, using the SAME interpreter the test runs under (pure stdlib)."""
    interp = shlex.quote(sys.executable)
    script = shlex.quote(str(_FAKEAGENT))
    boot_q = shlex.quote(boot)
    return f"FORGE_FAKEAGENT_BOOT={boot_q} exec {interp} {script}"


class MultiPaneLayout:
    """Lightweight handle over a multi-pane tmux layout on ``socket_path``.

    ``panes`` maps a caller label (e.g. "builder") to its tmux ``pane_id`` so a
    test can target a specific agent's pane for steering/reading.
    """

    def __init__(self, socket_path: str, session: str) -> None:
        self._socket_path = str(socket_path)
        self._session = session
        self.panes: dict[str, str] = {}

    async def _tmux(self, *args: str) -> tuple[int, str, str]:
        process = await asyncio.create_subprocess_exec(
            "tmux",
            "-S",
            self._socket_path,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        return (
            process.returncode or 0,
            stdout.decode("utf-8", "replace"),
            stderr.decode("utf-8", "replace"),
        )

    async def _active_pane_id(self, window: str) -> str:
        _, stdout, _ = await self._tmux(
            "display-message", "-p", "-t", f"{self._session}:{window}", "#{pane_id}"
        )
        return stdout.strip()

    async def split(self, label: str, boot: str, *, window: str = "main") -> str:
        """Split the named window, launching ``fakeagent`` with ``boot`` in the
        new pane. Returns (and records) the new pane id under ``label``."""
        command = _fakeagent_command(boot)
        await self._tmux(
            "split-window",
            "-t",
            f"{self._session}:{window}",
            "-d",
            "-P",
            "-F",
            "#{pane_id}",
            command,
        )
        # ``-P -F`` prints the new pane id, but split-window with a command runs a
        # shell; read the freshly created pane id off the layout instead.
        _, stdout, _ = await self._tmux(
            "list-panes",
            "-t",
            f"{self._session}:{window}",
            "-F",
            "#{pane_id}\t#{pane_start_command}",
        )
        pane_id = ""
        for line in stdout.splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2 and boot in parts[1]:
                pane_id = parts[0]
        if not pane_id:
            # Fallback: newest pane id is the highest %N.
            ids = [line.split("\t", 1)[0] for line in stdout.splitlines() if line]
            pane_id = sorted(ids, key=lambda v: int(v.lstrip("%") or "0"))[-1]
        self.panes[label] = pane_id
        return pane_id

    async def even_layout(self, window: str = "main") -> None:
        await self._tmux("select-layout", "-t", f"{self._session}:{window}", "even-horizontal")

    async def send_to(self, label: str, text: str) -> None:
        """Type ``text`` + Enter into the pane registered under ``label`` ONLY."""
        pane_id = self.panes[label]
        await self._tmux("send-keys", "-t", pane_id, text, "Enter")

    async def capture(self, label: str) -> str:
        pane_id = self.panes[label]
        _, stdout, _ = await self._tmux("capture-pane", "-t", pane_id, "-p")
        return stdout


async def split_into_panes(
    socket_path: str,
    session: str,
    specs: list[tuple[str, str]],
    *,
    window: str = "main",
    even: bool = True,
) -> MultiPaneLayout:
    """Split ``window`` into one pane per spec.

    ``specs`` is a list of ``(label, boot)`` pairs; each new pane runs
    ``fakeagent`` with ``boot`` (e.g. ``"pane:team_builder"``). The original pane
    (the agent already in the window) is left untouched. Returns the layout
    handle with ``panes`` populated by label.
    """
    layout = MultiPaneLayout(socket_path, session)
    for label, boot in specs:
        await layout.split(label, boot, window=window)
    if even:
        await layout.even_layout(window)
    return layout
