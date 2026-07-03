"""Group G — custom tmux SURFACE tests for the forge harness.

These prove the ``TmuxPage`` navigation harness generalizes from the single
agent window to MULTI-PANE / MULTI-SCREEN surfaces: agent pickers, running-agent
lists, workflow boards, and teams-of-agents layouts with per-pane steer routing.

POLICY (forward-looking surfaces): the custom multi-agent surfaces in the
shipped product are largely ASPIRATIONAL today. Each scenario asserts only what
the harness can REALLY observe against ``fakeagent`` screen fixtures — reach the
screen, read its structured content, act on it, observe the effect — and carries
a TODO to re-point at the real screen as it lands. Where a scenario crosses REAL
product code (pane-discovery ``terminal_pane_opened`` events, the chrome filter),
it exercises that code for real.

All scenarios are @pytest.mark.integration (the default addopts deselect them)
and skip when tmux is unavailable. Timeouts are kept small.

Run (real-tmux tier)::

    SKULD__TMUX_REMOTE_CONTROL=0 uv run pytest -m integration \
        tests/test_skuld/test_forge_surfaces.py -p no:cacheprovider -q -rs
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

import pytest

from skuld.transports.tmux_interactive import TmuxInteractiveTransport
from tests.support.forge import (
    BrokerHarness,
    HookServer,
    TmuxPage,
    install_fake_claude,
    split_into_panes,
)


def _require_tmux() -> None:
    if shutil.which("tmux") is None:
        pytest.skip("tmux is not installed")


async def _wait_until(predicate, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met before timeout")


async def _wait_async(coro_predicate, timeout: float = 5.0) -> None:
    """Poll an ASYNC predicate (one that awaits tmux) until it returns truthy."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if await coro_predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("async condition not met before timeout")


class _StandaloneTransport:
    """Spins a single standalone ``TmuxInteractiveTransport`` against fakeagent.

    Used by surface tests that only need a live pane + a ``TmuxPage`` (no broker).
    Yields ``(transport, page)`` and tears everything down.
    """

    def __init__(self, tmp_path: Path, *, boot: str | None = None) -> None:
        self._tmp_path = tmp_path
        self._boot = boot
        self.transport: TmuxInteractiveTransport | None = None
        self.page: TmuxPage | None = None
        self.events: list[dict[str, Any]] = []
        self._hook_server: HookServer | None = None
        self._old_path: str | None = None

    async def __aenter__(self) -> _StandaloneTransport:
        env = install_fake_claude(self._tmp_path / "bin", boot=self._boot)

        async def handler(payload: dict[str, Any]) -> Any:
            await self.transport.handle_claude_hook(payload)

        self._hook_server = await HookServer(handler).start()

        async def on_event(event: dict[str, Any]) -> None:
            self.events.append(event)

        self.transport = TmuxInteractiveTransport(
            workspace_dir=str(self._tmp_path),
            session_id=f"surf-{uuid.uuid4().hex[:8]}",
            sdk_port=self._hook_server.port,
            turn_idle_timeout_s=0.3,
            turn_no_output_timeout_s=2.0,
            pane_poll_interval_s=0.15,
        )
        self.transport.on_event(on_event)

        self._old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = env["PATH"]
        os.environ["SKULD__TMUX_REMOTE_CONTROL"] = "0"
        if self._boot is not None:
            os.environ["FORGE_FAKEAGENT_BOOT"] = self._boot

        await self.transport.start()
        self.page = TmuxPage(
            str(self.transport._socket_path),  # noqa: SLF001 - test seam
            self.transport.session_id or "",
        )
        await self.page.wait_for_text("fakeagent ready", timeout=5.0)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self.transport is not None:
            await self.transport.stop()
        if self._hook_server is not None:
            await self._hook_server.stop()
        if self._old_path is not None:
            os.environ["PATH"] = self._old_path
        os.environ.pop("SKULD__TMUX_REMOTE_CONTROL", None)
        os.environ.pop("FORGE_FAKEAGENT_BOOT", None)


# ════════════════════════════════════════════════════ G1 — agent selection


@pytest.mark.integration
@pytest.mark.asyncio
async def test_g1_agent_picker_lists_and_selects(tmp_path: Path) -> None:
    """G1: render the agent picker, enumerate it via ``menu_rows()``, pick one by
    digit, and observe the chosen agent label reflected on screen.

    TODO(real-screen): re-point at the real Claude-Code ``/agents`` picker once
    the product renders it in the tmux pane. Today we drive the ``agents.txt``
    fixture through fakeagent's ``pick:`` directive.
    """
    _require_tmux()

    async with _StandaloneTransport(tmp_path) as h:
        await h.transport.send_message("pick:agents")
        await h.page.wait_for_text("Select an agent", timeout=5.0)

        rows = await h.page.menu_rows()
        labels = [label for _digit, label in rows]
        assert labels == [
            "general-purpose",
            "Explore",
            "code-reviewer",
            "backend-architect",
            "test-writer",
        ], f"agent picker rows not enumerated as expected: {rows}"

        # Resolve a label to its on-screen digit using the transport's OWN matcher.
        digit = TmuxPage.match_digit("code-reviewer", rows)
        assert digit == 3, f"match_digit mapped code-reviewer to {digit}, expected 3"

        # Press that digit + Enter; fakeagent echoes the chosen agent label.
        await h.page.type(str(digit))
        screen = await h.page.wait_for_text("selected: code-reviewer", timeout=5.0)

    assert "selected: code-reviewer" in screen


# ════════════════════════════════════════════════════ G2 — see running agents


def _parse_running_agents(snapshot: str) -> list[dict[str, str]]:
    """Parse the ``running_agents.txt`` table body into id/agent/status rows."""
    out: list[dict[str, str]] = []
    for line in snapshot.splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        ident, agent, status = parts
        if not ident.startswith("a") or status not in {"running", "idle", "done"}:
            continue
        out.append({"id": ident, "agent": agent, "status": status})
    return out


@pytest.mark.integration
@pytest.mark.asyncio
async def test_g2_running_agents_list_single_pane(tmp_path: Path) -> None:
    """G2a: a single pane renders the running-agents list; the harness parses the
    rows (id / agent / status).

    TODO(real-screen): re-point at the real running-agents surface when it ships.
    """
    _require_tmux()

    async with _StandaloneTransport(tmp_path) as h:
        await h.transport.send_message("screen:running_agents")
        snapshot = await h.page.wait_for_text("Running agents", timeout=5.0)

    rows = _parse_running_agents(snapshot)
    by_id = {r["id"]: r for r in rows}
    assert set(by_id) == {"a1", "a2", "a3", "a4"}, f"running-agent rows: {rows}"
    assert by_id["a1"] == {"id": "a1", "agent": "general-purpose", "status": "running"}
    assert by_id["a2"]["status"] == "idle"
    assert by_id["a3"]["status"] == "done"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_g2_running_agents_as_multiple_panes(tmp_path: Path) -> None:
    """G2b: render running agents as MULTIPLE panes — one fake agent per pane —
    and assert ``TmuxPage.panes()`` enumerates the N panes with their commands and
    active flags. This crosses REAL pane-discovery product code: the transport's
    pane watcher emits a ``terminal_pane_opened`` event per discovered pane.
    """
    _require_tmux()

    async with BrokerHarness(hooks=True, idle_timeout_s=0.3, start_transport=True) as h:
        page = TmuxPage(str(h.transport._socket_path), h.transport.session_id or "")  # noqa: SLF001
        await page.wait_for_text("fakeagent ready", timeout=5.0)

        # One pane per running agent — each renders its own one-line screen.
        await split_into_panes(
            str(h.transport._socket_path),  # noqa: SLF001
            h.transport.session_id or "",
            [
                ("a1", "screen:team_architect"),
                ("a2", "screen:team_builder"),
                ("a3", "screen:team_reviewer"),
            ],
        )

        # TmuxPage enumerates all 4 panes (1 original agent + 3 split).
        async def _has_four() -> bool:
            return len(await page.panes()) >= 4

        await _wait_async(_has_four, timeout=6.0)
        panes = await page.panes()

    assert len(panes) >= 4, f"expected >=4 panes (1 agent + 3 split), got {panes}"
    actives = [p for p in panes if p["active"]]
    assert len(actives) == 1, f"exactly one pane must be active, got {actives}"
    assert all(p["command"] for p in panes), f"every pane must report a command: {panes}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_g2_multipane_emits_real_pane_opened_events(tmp_path: Path) -> None:
    """G2c: tie the multi-pane case to the transport's REAL ``terminal_pane_opened``
    events — splitting the window must drive one new pane_opened event per pane
    through the broker's CLI pipeline.
    """
    _require_tmux()

    async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
        client = await h.connect()
        await client.wait_for(
            lambda frames: any(f.get("type") == "terminal_pane_opened" for f in frames),
            timeout=5.0,
        )
        before = len(client.frames_of_type("terminal_pane_opened"))

        await split_into_panes(
            str(h.transport._socket_path),  # noqa: SLF001
            h.transport.session_id or "",
            [("b1", "pane:team_builder"), ("b2", "pane:team_reviewer")],
        )

        await client.wait_for(
            lambda frames: (
                len([f for f in frames if f.get("type") == "terminal_pane_opened"]) >= before + 2
            ),
            timeout=6.0,
        )
        opened = client.frames_of_type("terminal_pane_opened")

    assert len(opened) >= before + 2, (
        f"splitting into 2 panes must emit 2 real pane_opened events; got {opened}"
    )


# ════════════════════════════════════════════════════ G3 — workflow view


def _parse_workflow(snapshot: str) -> tuple[str, dict[str, str]]:
    """Return (workflow_name, {step_name: status}) from the workflow board."""
    name = ""
    steps: dict[str, str] = {}
    for line in snapshot.splitlines():
        stripped = line.strip()
        if stripped.startswith("Workflow:"):
            name = stripped.split(":", 1)[1].strip()
            continue
        parts = stripped.split()
        # rows look like " 2. implement running"
        if len(parts) == 3 and parts[0][:-1].isdigit() and parts[0][-1] == ".":
            steps[parts[1]] = parts[2]
    return name, steps


@pytest.mark.integration
@pytest.mark.asyncio
async def test_g3_workflow_view_reads_and_advances(tmp_path: Path) -> None:
    """G3: render the workflow board, read the workflow name + per-step statuses,
    then act (send ``/workflows``-style follow-up) and observe a state change.

    TODO(real-screen): re-point at the real workflow surface + the real
    ``/workflows`` slash-command once the product renders the board in-pane.
    """
    _require_tmux()

    async with _StandaloneTransport(tmp_path) as h:
        await h.transport.send_message("screen:workflow")
        snapshot = await h.page.wait_for_text("Workflow:", timeout=5.0)

        name, steps = _parse_workflow(snapshot)
        assert name == "ship-feature", f"workflow name not read: {name!r}"
        assert steps == {
            "plan": "done",
            "implement": "running",
            "review": "pending",
            "merge": "pending",
        }, f"workflow steps not read: {steps}"

        # Act: navigate to the advanced board (models pressing 'n' / /workflows).
        await h.page.type("ignored")  # release the current screen
        await h.transport.send_message("screen:workflow_advanced")
        advanced = await h.page.wait_for_text("review            running", timeout=5.0)

    _name2, steps2 = _parse_workflow(advanced)
    assert steps2["implement"] == "done", f"workflow did not advance: {steps2}"
    assert steps2["review"] == "running", f"workflow did not advance: {steps2}"
    assert steps2 != steps, "acting on the workflow produced no observable state change"


# ════════════════════════════════════════════════════ G4 — teams + steer routing


@pytest.mark.integration
@pytest.mark.asyncio
async def test_g4_team_panes_steer_routes_to_targeted_agent(tmp_path: Path) -> None:
    """G4: build a 3-pane team layout (architect / builder / reviewer), navigate
    between panes, and prove a steer typed into a TARGETED pane lands in THAT pane
    ONLY — each pane's output is captured independently.

    This is the multi-agent "which agent did the steer reach" test. It uses REAL
    tmux pane targeting (``send-keys -t <pane_id>``) and reads each pane's snapshot
    independently.

    TODO(real-screen): re-point at the real teams-of-agents surface + the real
    steer-target routing once the product ships per-agent panes.
    """
    _require_tmux()

    async with _StandaloneTransport(tmp_path) as h:
        page = h.page

        layout = await split_into_panes(
            str(h.transport._socket_path),  # noqa: SLF001
            h.transport.session_id or "",
            [
                ("architect", "pane:team_architect"),
                ("builder", "pane:team_builder"),
                ("reviewer", "pane:team_reviewer"),
            ],
        )

        # Each pane rendered its own distinct team screen.
        async def _pane_has(label: str, needle: str) -> bool:
            return needle in await layout.capture(label)

        await _wait_async(lambda: _pane_has("architect", "I own the system design"), timeout=6.0)
        await _wait_async(lambda: _pane_has("builder", "I write the code"), timeout=6.0)
        await _wait_async(lambda: _pane_has("reviewer", "I review the diffs"), timeout=6.0)

        # Navigation: select each pane by id, then by window name.
        await page.select_pane(layout.panes["builder"])
        await page.select_window("main")
        panes = await page.panes()
        assert len(panes) >= 4, f"team layout must have >=4 panes: {panes}"

        # Steer ONLY the builder pane.
        await layout.send_to("builder", "make the button blue")
        await _wait_async(
            lambda: _pane_has("builder", "steer received: make the button blue"),
            timeout=6.0,
        )

        builder_screen = await layout.capture("builder")
        architect_screen = await layout.capture("architect")
        reviewer_screen = await layout.capture("reviewer")

    # The steer landed in the builder pane ONLY.
    assert "steer received: make the button blue" in builder_screen
    assert "make the button blue" not in architect_screen, (
        "steer leaked into the architect pane — input must route to the targeted agent only"
    )
    assert "make the button blue" not in reviewer_screen, (
        "steer leaked into the reviewer pane — input must route to the targeted agent only"
    )
    # Each pane's output is captured independently and distinctly.
    assert "I own the system design" in architect_screen
    assert "I review the diffs" in reviewer_screen


# ════════════════════════════════════════════════════ G5 — navigation invariants


_AGENTS_GOLDEN = """\
  Agents

  Select an agent to run:

   1. general-purpose
   2. Explore
   3. code-reviewer
   4. backend-architect
   5. test-writer

  ↑/↓ to navigate · Enter to select · Esc to close"""

_WORKFLOW_GOLDEN = """\
  Workflow: ship-feature

  STEP   NAME              STATUS
  ----   ---------------   -----------
   1.    plan              done
   2.    implement         running
   3.    review            pending
   4.    merge             pending

  press n for next step · /workflows to switch"""


def _screen_region(snapshot: str, header: str, footer_marker: str) -> str:
    """Slice the rendered SCREEN out of the full pane snapshot.

    The pane scrollback also holds boot chrome ("fakeagent ready") and the echoed
    input line; the golden compares only the screen body, from its header row
    through the footer row that contains ``footer_marker``.
    """
    lines = [line.rstrip() for line in snapshot.splitlines()]
    start = next((i for i, line in enumerate(lines) if line.strip().startswith(header)), None)
    if start is None:
        return "\n".join(lines)
    end = next(
        (i for i in range(start, len(lines)) if footer_marker in lines[i]),
        len(lines) - 1,
    )
    return "\n".join(lines[start : end + 1])


@pytest.mark.integration
@pytest.mark.asyncio
async def test_g5_golden_snapshots_of_key_screens(tmp_path: Path) -> None:
    """G5a: golden-snapshot the key surfaces via ``TmuxPage.snapshot()`` so the
    structured content of each screen stays stable as the harness evolves.
    """
    _require_tmux()

    async with _StandaloneTransport(tmp_path) as h:
        await h.transport.send_message("screen:agents")
        await h.page.wait_for_text("Select an agent", timeout=5.0)
        agents_snapshot = _screen_region(await h.page.snapshot(), "Agents", "Esc to close")

        await h.page.type("ignored")
        await h.transport.send_message("screen:workflow")
        await h.page.wait_for_text("Workflow:", timeout=5.0)
        workflow_snapshot = _screen_region(
            await h.page.snapshot(), "Workflow:", "/workflows to switch"
        )

    assert agents_snapshot == _AGENTS_GOLDEN, (
        f"agents screen drifted from golden:\n---got---\n{agents_snapshot}\n"
    )
    assert workflow_snapshot == _WORKFLOW_GOLDEN, (
        f"workflow screen drifted from golden:\n---got---\n{workflow_snapshot}\n"
    )


def test_g5_chrome_filter_separates_assistant_from_menus_and_spinners() -> None:
    """G5b: exercise the product's REAL chrome filter. Feed a representative mix of
    banner / spinner / "❯" prompt / "? for shortcuts · ← for agents" / a real
    assistant line to ``_extract_assistant_response`` and assert it returns ONLY
    the assistant text — guarding that the menus/spinners/chrome on these custom
    surfaces stay separable from assistant content.

    Modeled on test_tmux_interactive_transport.py::
    test_extract_assistant_response_filters_claude_terminal_chrome.
    """
    rows = [
        " ▐▛███▜▌   Claude Code v2.1.172",
        "❯ which agent should review this?",
        "✶ Gesticulating...",
        " 1. general-purpose",
        " 2. code-reviewer",
        "● I recommend code-reviewer for this diff.",
        "",
        "  It focuses on correctness and reuse.",
        "* Lollygagging… (4s · ↓ 167 tokens)",
        "✻ Cogitated for 4s",
        "◉ xhigh · /effort",
        "❯ ",
        "? for shortcuts · ← for agents",
    ]

    result = TmuxInteractiveTransport._extract_assistant_response(rows)  # noqa: SLF001

    assert result == (
        "I recommend code-reviewer for this diff.\n\nIt focuses on correctness and reuse."
    ), f"chrome filter did not isolate assistant text; got:\n{result!r}"
    # Explicitly: the numbered menu rows and the agents-nav chrome are gone.
    assert "general-purpose" not in result
    assert "for shortcuts" not in result
    assert "← for agents" not in result
    assert "Gesticulating" not in result
