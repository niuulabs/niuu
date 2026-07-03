"""TmuxPage — a Playwright-for-tmux page object for asserting and navigating a
live tmux session on a private socket.

Dependency-free: shells out to ``tmux`` via ``asyncio.create_subprocess_exec``.
Menu parsing reuses the *product's own* parser so tests assert against the same
regex the shipped transport uses.
"""

from __future__ import annotations

import asyncio
import time

# Single source of truth: import the product's own menu-row regex so the page
# object parses menus EXACTLY the way the shipped transport does and can never
# drift (its _capture_menu_rows is bound to a transport's own tmux plumbing, so
# we can't call it against an arbitrary socket). Digit matching reuses the real
# _match_menu_digit, see match_digit().
from skuld.transports.tmux_interactive import _MENU_ROW_RE, TmuxInteractiveTransport

_DEFAULT_POLL_INTERVAL_S = 0.05


class TmuxPage:
    """Drive and assert against a tmux session on ``socket_path``."""

    def __init__(self, socket_path: str, session: str, target: str | None = None) -> None:
        self._socket_path = str(socket_path)
        self._session = session
        self._target = target or session

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

    async def snapshot(self) -> str:
        _, stdout, _ = await self._tmux("capture-pane", "-t", self._target, "-p")
        return stdout

    async def wait_for_text(self, substr: str, timeout: float = 2.0) -> str:
        deadline = time.monotonic() + timeout
        last = ""
        while time.monotonic() < deadline:
            last = await self.snapshot()
            if substr in last:
                return last
            await asyncio.sleep(_DEFAULT_POLL_INTERVAL_S)
        raise AssertionError(f"text {substr!r} not found within {timeout}s. Last screen:\n{last}")

    async def menu_rows(self) -> list[tuple[int, str]]:
        """Numbered option rows on screen, parsed with the transport's OWN regex."""
        _, stdout, _ = await self._tmux("capture-pane", "-t", self._target, "-p", "-S", "-50")
        out: list[tuple[int, str]] = []
        seen: set[int] = set()
        for line in stdout.splitlines():
            match = _MENU_ROW_RE.match(line)
            if not match:
                continue
            digit = int(match.group(1))
            if digit in seen:
                continue
            seen.add(digit)
            out.append((digit, match.group(2).strip()))
        return sorted(out)

    @staticmethod
    def match_digit(chosen: str, rows: list[tuple[int, str]]) -> int | None:
        """Map a label to its on-screen digit via the transport's own matcher."""
        return TmuxInteractiveTransport._match_menu_digit(chosen, rows)

    async def press(self, key: str) -> None:
        await self._tmux("send-keys", "-t", self._target, key)

    async def type(self, text: str) -> None:
        """Paste ``text`` then Enter, the way a real terminal paste lands."""
        await self._tmux("set-buffer", "-b", "tmuxpage", text)
        await self._tmux("paste-buffer", "-t", self._target, "-b", "tmuxpage")
        await self._tmux("send-keys", "-t", self._target, "Enter")

    async def panes(self) -> list[dict]:
        _, stdout, _ = await self._tmux(
            "list-panes",
            "-t",
            self._session,
            "-F",
            (
                "#{pane_id}\t#{pane_index}\t#{window_name}\t#{pane_active}\t"
                "#{pane_current_command}\t#{pane_width}\t#{pane_height}"
            ),
        )
        panes: list[dict] = []
        for line in stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            panes.append(
                {
                    "id": parts[0],
                    "index": parts[1],
                    "window": parts[2],
                    "active": parts[3] == "1",
                    "command": parts[4],
                    "width": int(parts[5]) if parts[5].isdigit() else 0,
                    "height": int(parts[6]) if parts[6].isdigit() else 0,
                }
            )
        return panes

    async def select_pane(self, pane_id: str) -> None:
        await self._tmux("select-pane", "-t", pane_id)

    async def select_window(self, name: str) -> None:
        await self._tmux("select-window", "-t", f"{self._session}:{name}")
