#!/usr/bin/env python3
"""fakeagent — a scriptable stand-in for the interactive ``claude`` CLI.

PURE STANDARD LIBRARY ONLY. This module is launched by
``TmuxInteractiveTransport`` as the ``claude`` binary, so it must run under any
Python interpreter with no third-party imports (no aiohttp, no httpx).

It speaks the same TTY + hook protocol the real Claude Code CLI does, but driven
by a tiny line-oriented directive grammar (see ``run`` / ``handle_line``) so
tests can make the agent say things, work, ask questions, request permissions,
render screens, or crash on demand.

Directive grammar (one user line may chain several with ``' ;; '``)::

    say:<text>
    work:<seconds>
    ask:<header>|<question>|<opt1>;<opt2>;...[|delay=<s>]
    perm:<tool>|<detail>[|delay=<s>]
    tool:<name>
    screen:<name>
    pick:<name>      render a picker screen, read a digit, echo "selected: <label>"
    pane:<name>      render a pane screen, then echo each input as "steer received: <text>"
    menudelay:<seconds>
    crash
    exit:<n>

A line with no recognized directive is treated as ``say:<line>``.

Menu-render delay (E3 race)
---------------------------
``ask:`` and ``perm:`` normally render the numbered on-screen menu, then fire the
structured hook, then block for the resolving keystroke. With a non-zero menu
delay the order changes to expose a race window for clients that answer the
*structured* question before the on-screen menu exists:

    1. fire the hook FIRST (PreToolUse AskUserQuestion / PermissionRequest),
    2. sleep <delay> seconds (NO menu on screen yet),
    3. THEN render the numbered menu to the pane,
    4. block for the resolving keystroke.

Two backward-compatible ways to set the delay (default 0 = legacy behavior:
render menu first, then hook, then block — unchanged for all Phase 0/1 usage):

  * a trailing ``|delay=<s>`` field on a single directive, e.g.
    ``ask:Pick|Which?|a;b|delay=0.4`` or ``perm:Bash|rm -rf|delay=0.4``;
  * a ``menudelay:<s>`` prelude directive that sets the delay for every
    subsequent ``ask:``/``perm:`` in the SAME chained line, e.g.
    ``menudelay:0.4 ;; ask:Pick|Which?|a;b``. A per-directive ``delay=`` field
    overrides the prelude for that one directive.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
import urllib.request
from pathlib import Path

_SCREENS_DIR = Path(__file__).resolve().parent / "screens"

# --- regex-free menu rendering -------------------------------------------------
# The transport parses numbered rows with r"^\s*[❯>\s]*([1-9])[.)]\s+(.+?)\s*$".
# We render " 1. <opt>" lines that match it exactly.
_PERMISSION_ROWS = ["Allow", "Allow & don't ask again", "Deny"]

_interrupted = False


def _emit(text: str) -> None:
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.flush()


# ──────────────────────────── hook plumbing ────────────────────────────


def _hook_url_from_settings(settings_path: str | None) -> str | None:
    env_override = os.environ.get("FORGE_FAKEAGENT_HOOK_URL")
    if env_override:
        return env_override
    if not settings_path:
        return None
    try:
        data = json.loads(Path(settings_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return None
    for entries in hooks.values():
        if not isinstance(entries, list) or not entries:
            continue
        inner = entries[0].get("hooks")
        if not isinstance(inner, list) or not inner:
            continue
        url = inner[0].get("url")
        if isinstance(url, str):
            return url
    return None


class _Hooks:
    """Best-effort hook POSTer. Swallows connection errors so a missing server
    never crashes the agent."""

    def __init__(self, url: str | None, session_id: str) -> None:
        self._url = url
        self._session_id = session_id

    @property
    def enabled(self) -> bool:
        return self._url is not None

    def post(self, payload: dict) -> None:
        if self._url is None:
            return
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self._url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                response.read()
        except Exception:  # noqa: BLE001 - hooks are best-effort by design
            return

    def user_prompt_submit(self, prompt: str) -> None:
        self.post(
            {
                "hook_event_name": "UserPromptSubmit",
                "prompt": prompt,
                "session_id": self._session_id,
            }
        )

    def pre_tool_use(self, tool_name: str, tool_input: dict, tool_use_id: str) -> None:
        self.post(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_use_id": tool_use_id,
                "session_id": self._session_id,
            }
        )

    def post_tool_use(self, tool_name: str, tool_use_id: str, response: str) -> None:
        self.post(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": tool_name,
                "tool_use_id": tool_use_id,
                "tool_response": response,
                "session_id": self._session_id,
            }
        )

    def permission_request(
        self, tool_name: str, tool_input: dict, suggestions: list | None = None
    ) -> None:
        self.post(
            {
                "hook_event_name": "PermissionRequest",
                "tool_name": tool_name,
                "tool_input": tool_input,
                "permission_suggestions": suggestions or [],
                "session_id": self._session_id,
            }
        )

    def stop(self, last_assistant_message: str) -> None:
        self.post(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": last_assistant_message,
                "session_id": self._session_id,
            }
        )


# ──────────────────────────── argv parsing ────────────────────────────


def _parse_settings_path(argv: list[str]) -> str | None:
    for index, token in enumerate(argv):
        if token == "--settings" and index + 1 < len(argv):
            return argv[index + 1]
        if token.startswith("--settings="):
            return token.split("=", 1)[1]
    return None


def _session_name() -> str:
    return os.environ.get("FORGE_FAKEAGENT_SESSION", "fakeagent")


# ──────────────────────────── directives ────────────────────────────


def _read_keystroke() -> str | None:
    """Block for the next stdin line (the transport answers a menu by sending a
    digit + Enter, or Escape). Returns the stripped line, or None at EOF."""
    line = sys.stdin.readline()
    if line == "":
        return None
    return line.rstrip("\n")


def _render_menu(options: list[str]) -> None:
    for index, label in enumerate(options, start=1):
        _emit(f" {index}. {label}")


def _do_say(text: str, hooks: _Hooks) -> None:
    _emit(text)
    _finish_turn(text, hooks)


def _do_work(seconds: float, hooks: _Hooks) -> None:
    global _interrupted
    _interrupted = False
    deadline = time.monotonic() + seconds
    tick = 0
    while time.monotonic() < deadline:
        if _interrupted:
            _emit("interrupted")
            _finish_turn("interrupted", hooks)
            return
        tick += 1
        _emit(f"...working {tick}")
        time.sleep(0.2)
    if _interrupted:
        _emit("interrupted")
        _finish_turn("interrupted", hooks)
        return
    _emit("done")
    _finish_turn("done", hooks)


def _split_delay_field(parts: list[str], default_delay: float) -> tuple[list[str], float]:
    """Strip a trailing ``delay=<s>`` field from a ``|``-split spec.

    Returns the remaining parts and the resolved delay (the trailing field wins
    over ``default_delay``; an absent/blank field falls back to default)."""
    if parts and parts[-1].strip().lower().startswith("delay="):
        raw = parts[-1].strip()[len("delay=") :].strip()
        try:
            return parts[:-1], float(raw or "0")
        except ValueError:
            return parts[:-1], default_delay
    return parts, default_delay


def _render_after_delay(options: list[str], delay: float) -> None:
    """Hook already fired: optionally wait, THEN render the on-screen menu.

    With delay <= 0 the menu was already rendered by the caller (legacy order),
    so this is a no-op. With delay > 0 we sleep first to open the race window
    (structured question exists, menu not yet on screen), then render."""
    if delay <= 0:
        return
    time.sleep(delay)
    _render_menu(options)


def _do_ask(spec: str, hooks: _Hooks, menu_delay: float = 0.0) -> None:
    fields = spec.split("|")
    fields, delay = _split_delay_field(fields, menu_delay)
    header = fields[0] if fields else ""
    question = fields[1] if len(fields) > 1 else ""
    opt_blob = fields[2] if len(fields) > 2 else ""
    options = [opt.strip() for opt in opt_blob.split(";") if opt.strip()]
    if not options:
        options = ["Yes", "No"]
    # delay == 0 -> legacy order: render menu, then fire hook, then block.
    if delay <= 0:
        _render_menu(options)
    if hooks.enabled:
        tool_input = {
            "questions": [
                {
                    "header": header.strip(),
                    "question": question.strip(),
                    "options": [{"label": label} for label in options],
                    "multiSelect": False,
                }
            ]
        }
        hooks.pre_tool_use("AskUserQuestion", tool_input, "fakeagent-ask")
    # delay > 0 -> hook fired above; wait, THEN render the menu (race window).
    _render_after_delay(options, delay)
    keystroke = _read_keystroke()
    chosen = _resolve_choice(keystroke, options)
    _emit(f"chose: {chosen}")
    _finish_turn(f"chose: {chosen}", hooks)


def _do_perm(spec: str, hooks: _Hooks, menu_delay: float = 0.0) -> None:
    fields = spec.split("|")
    fields, delay = _split_delay_field(fields, menu_delay)
    tool_name = (fields[0] if fields else "").strip() or "Bash"
    detail = (fields[1] if len(fields) > 1 else "").strip()
    # delay == 0 -> legacy order: render menu, then fire hook, then block.
    if delay <= 0:
        _render_menu(_PERMISSION_ROWS)
    if hooks.enabled:
        tool_input = {"command": detail} if tool_name == "Bash" else {"detail": detail}
        hooks.permission_request(tool_name, tool_input)
    # delay > 0 -> hook fired above; wait, THEN render the menu (race window).
    _render_after_delay(_PERMISSION_ROWS, delay)
    keystroke = _read_keystroke()
    outcome = _resolve_permission(keystroke)
    _emit(f"permission: {outcome}")
    _finish_turn(f"permission: {outcome}", hooks)


def _do_tool(name: str, hooks: _Hooks) -> None:
    name = name.strip() or "Tool"
    tool_use_id = f"fakeagent-{name}"
    if hooks.enabled:
        hooks.pre_tool_use(name, {}, tool_use_id)
        hooks.post_tool_use(name, tool_use_id, "ok")
    _emit(f"ran {name}")
    _finish_turn(f"ran {name}", hooks)


def _render_screen_file(name: str) -> bool:
    """Write ``screens/<name>.txt`` verbatim to the pane. Returns False if missing."""
    name = name.strip()
    path = _SCREENS_DIR / f"{name}.txt"
    if not path.exists():
        _emit(f"[missing screen: {name}]")
        return False
    body = path.read_text(encoding="utf-8")
    sys.stdout.write(body)
    if not body.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.flush()
    return True


def _screen_menu_rows(name: str) -> list[str]:
    """Parse the " 1. <label>" rows of a screen fixture, ordered by digit."""
    path = _SCREENS_DIR / f"{name.strip()}.txt"
    if not path.exists():
        return []
    rows: list[tuple[int, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        # match " 1. label" / " 1) label", first token a single digit.
        if len(stripped) < 3 or stripped[0] not in "123456789" or stripped[1] not in ".)":
            continue
        rows.append((int(stripped[0]), stripped[2:].strip()))
    rows.sort()
    return [label for _digit, label in rows]


def _do_screen(name: str, hooks: _Hooks) -> None:
    _render_screen_file(name)
    # Stay on screen until a line is read (navigation), then finish.
    _read_keystroke()
    _finish_turn("", hooks)


def _do_pick(name: str, hooks: _Hooks) -> None:
    """Render a picker screen, read one digit, echo the chosen menu label.

    Models the Claude-Code ``/agents`` flow: render the numbered list, the user
    presses a digit, the chosen agent label is reflected back on screen.
    """
    _render_screen_file(name)
    options = _screen_menu_rows(name)
    keystroke = _read_keystroke()
    chosen = _resolve_choice(keystroke, options) if options else ""
    _emit(f"selected: {chosen}")
    _finish_turn(f"selected: {chosen}", hooks)


def _do_pane(name: str, hooks: _Hooks) -> None:
    """Render a pane's screen, then ECHO every subsequent input line as a steer.

    Each team pane runs one of these. A steer typed into THIS pane (and only this
    pane) lands here and is echoed as ``steer received: <text>``, so a test can
    read each pane independently to prove input routed to the targeted agent.
    """
    _render_screen_file(name)
    while True:
        line = _read_keystroke()
        if line is None:
            _finish_turn("", hooks)
            return
        if line.strip() == "":
            continue
        _emit(f"steer received: {line.strip()}")
        _finish_turn(f"steer received: {line.strip()}", hooks)


def _resolve_choice(keystroke: str | None, options: list[str]) -> str:
    if keystroke is None:
        return options[0]
    stripped = keystroke.strip()
    if stripped.isdigit():
        index = int(stripped) - 1
        if 0 <= index < len(options):
            return options[index]
    if stripped.lower() in {"escape", "esc"}:
        return "cancelled"
    return options[0]


def _resolve_permission(keystroke: str | None) -> str:
    if keystroke is None:
        return "allow"
    stripped = keystroke.strip().lower()
    if stripped in {"escape", "esc"}:
        return "deny"
    if stripped.isdigit():
        index = int(stripped) - 1
        if 0 <= index < len(_PERMISSION_ROWS):
            return _PERMISSION_ROWS[index].lower()
    return "allow"


def _finish_turn(assistant_text: str, hooks: _Hooks) -> None:
    if hooks.enabled:
        hooks.stop(assistant_text)


def handle_line(line: str, hooks: _Hooks) -> None:
    """Interpret one user line (possibly several ' ;; '-chained directives).

    A ``menudelay:<s>`` prelude sets the menu-render delay for every subsequent
    ``ask:``/``perm:`` in the SAME line; it resets to 0 for each new line."""
    menu_delay = 0.0
    for directive in line.split(" ;; "):
        directive = directive.strip()
        if directive == "":
            continue
        if directive.startswith("menudelay:"):
            try:
                menu_delay = float(directive[len("menudelay:") :].strip() or "0")
            except ValueError:
                menu_delay = 0.0
            continue
        _dispatch(directive, hooks, menu_delay)


def _dispatch(directive: str, hooks: _Hooks, menu_delay: float = 0.0) -> None:
    if directive == "crash":
        sys.stdout.flush()
        os._exit(137)
    if directive.startswith("exit:"):
        sys.stdout.flush()
        sys.exit(int(directive[len("exit:") :].strip() or "0"))
    if directive.startswith("say:"):
        _do_say(directive[len("say:") :], hooks)
        return
    if directive.startswith("work:"):
        _do_work(float(directive[len("work:") :].strip() or "1"), hooks)
        return
    if directive.startswith("ask:"):
        _do_ask(directive[len("ask:") :], hooks, menu_delay)
        return
    if directive.startswith("perm:"):
        _do_perm(directive[len("perm:") :], hooks, menu_delay)
        return
    if directive.startswith("tool:"):
        _do_tool(directive[len("tool:") :], hooks)
        return
    if directive.startswith("screen:"):
        _do_screen(directive[len("screen:") :], hooks)
        return
    if directive.startswith("pick:"):
        _do_pick(directive[len("pick:") :], hooks)
        return
    if directive.startswith("pane:"):
        _do_pane(directive[len("pane:") :], hooks)
        return
    # Unrecognized -> echo as assistant text.
    _do_say(directive, hooks)


def _install_sigint_handler() -> None:
    def _handler(_signum, _frame) -> None:
        global _interrupted
        _interrupted = True

    signal.signal(signal.SIGINT, _handler)


def run(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    settings_path = _parse_settings_path(argv)
    hook_url = _hook_url_from_settings(settings_path)
    hooks = _Hooks(hook_url, _session_name())

    _install_sigint_handler()

    _emit("fakeagent ready")

    boot = os.environ.get("FORGE_FAKEAGENT_BOOT")
    if boot:
        handle_line(boot, hooks)

    while True:
        line = sys.stdin.readline()
        if line == "":
            return 0
        handle_line(line.rstrip("\n"), hooks)


if __name__ == "__main__":
    raise SystemExit(run())
