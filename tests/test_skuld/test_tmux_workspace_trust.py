"""Claude startup trust must consume exactly one confirmation before chat."""

from __future__ import annotations

import asyncio
import shutil
import uuid
from collections import deque
from pathlib import Path

import pytest

from skuld.delivery_errors import DeliveryNotAcceptedError
from skuld.transports.tmux_interactive import TmuxInteractiveTransport
from tests.support.forge.fakeclaude_shim import install_fake_claude
from tests.support.forge.hook_server import HookServer
from tests.test_skuld.test_tmux_interactive_transport import (
    FakeTmuxInteractiveTransport,
    _collect_events,
    _wait_until,
)


def trust_screen(workspace: Path, *, yes=False, reverse=False, numbered=False) -> str:
    choices = ["No, exit", "Yes, I trust this folder"]
    if reverse:
        choices.reverse()
    rows = []
    for index, label in enumerate(choices, start=1):
        selected = label.startswith("Yes,") == yes
        rows.append(("❯ " if selected else "  ") + (f"{index}. " if numbered else "") + label)
    return (
        f"Accessing workspace:\n\n{workspace}\n\nQuick safety check\n"
        + "\n".join(rows)
        + "\nEnter to confirm · Esc to cancel"
    )


class StartupTransport(FakeTmuxInteractiveTransport):
    """Replays captured frames in order (the last one repeats).

    Stability and settle windows are zero here, so a key needs exactly two identical
    consecutive captures of the menu — the frame lists below repeat a menu frame
    wherever a key is expected.
    """

    def __init__(self, workspace: Path, screens: list[str], **kwargs):
        kwargs.setdefault("workspace_trust_stable_s", 0.0)
        kwargs.setdefault("workspace_trust_settle_s", 0.0)
        super().__init__(str(workspace), **kwargs)
        self.screens = deque(screens)
        self._repl_ready_timeout_s = 0.15
        self._menu_poll_step_s = 0.001

    async def _capture_pane_text(self, pane_id=None):
        if self.screens:
            self.capture_stdout = self.screens.popleft()
        return self.capture_stdout

    @property
    def keys(self):
        return [args[-1] for args, _ in self.commands if args[0] == "send-keys"]


@pytest.mark.parametrize("reverse,numbered", [(False, False), (True, False), (False, True)])
async def test_start_without_seed_monitors_delayed_trust_and_confirms_once(
    tmp_path, reverse, numbered
):
    no = trust_screen(tmp_path, reverse=reverse, numbered=numbered)
    yes = trust_screen(tmp_path, yes=True, reverse=reverse, numbered=numbered)
    transport = StartupTransport(tmp_path, ["Booting", no, no, yes, yes, "Loading", "❯"])
    try:
        await transport.start()
        assert transport.keys == ["Up" if reverse else "Down", "Enter"]
        assert not transport.loaded_buffers
        assert not transport.is_turn_active
        assert transport._startup_ready
        await transport.start()  # Reconnecting must not repeat confirmation.
        assert len(transport.keys) == 2
    finally:
        await transport.stop()


async def test_trust_already_selected_only_sends_enter_before_seed(tmp_path):
    yes = trust_screen(tmp_path, yes=True)
    transport = StartupTransport(
        tmp_path, [yes, yes, "Loading", "❯"], initial_prompt="Start the review"
    )
    try:
        await transport.start()
        assert transport.keys == ["Enter", "Enter"]  # Trust, then chat submit.
        assert transport.loaded_buffers == ["Start the review"]
        commands = [args[0] for args, _ in transport.commands]
        assert commands.index("send-keys") < commands.index("load-buffer")
    finally:
        await transport.stop()


async def test_start_and_chat_serialize_trust_confirmation(tmp_path):
    no, yes = trust_screen(tmp_path), trust_screen(tmp_path, yes=True)
    transport = StartupTransport(tmp_path, [no, no, yes, yes, "❯"])
    try:
        await asyncio.gather(transport.start(), transport.send_message("Review this"))
        assert transport.keys == ["Down", "Enter", "Enter"]
        assert transport.loaded_buffers == ["Review this"]
    finally:
        await transport.stop()


async def test_attach_to_existing_trust_screen_does_not_create_session(tmp_path):
    yes = trust_screen(tmp_path, yes=True)
    transport = StartupTransport(tmp_path, [yes, yes, "❯"])
    transport.session_exists = True
    try:
        await transport.start()
        assert transport.keys == ["Enter"]
        assert not any(args[0] == "new-session" for args, _ in transport.commands)
    finally:
        await transport.stop()


async def test_already_trusted_startup_sends_no_keys(tmp_path):
    transport = StartupTransport(tmp_path, ["❯"])
    try:
        await transport.start()
        assert transport.keys == []
    finally:
        await transport.stop()


QUESTION_SCREEN = " ☐ Db\n\nWhich database?\n\n❯ 1. Postgres\n  2. SQLite"


async def test_native_control_at_boot_returns_from_start_without_keys(tmp_path):
    # The control hides the composer; start() must still return so a browser
    # can attach and answer it. Startup stays unready so nothing is typed into it.
    transport = StartupTransport(tmp_path, [QUESTION_SCREEN])
    transport._repl_ready_markers = ("fakeagent ready",)
    transport._pending_tty_prompts["tty-1"] = {"kind": "ask_user_question"}
    try:
        await transport.start()
        assert not transport._startup_ready
        assert transport.keys == []
    finally:
        await transport.stop()


async def test_native_control_does_not_satisfy_callers_about_to_type(tmp_path):
    transport = StartupTransport(tmp_path, [QUESTION_SCREEN])
    transport._repl_ready_markers = ("fakeagent ready",)
    transport._pending_tty_prompts["tty-1"] = {"kind": "ask_user_question"}
    try:
        with pytest.raises(DeliveryNotAcceptedError, match="not ready for input"):
            await transport._wait_for_repl_ready()
        assert transport.keys == []
    finally:
        await transport.stop()


async def test_trust_for_different_workspace_does_not_send_keys_or_chat(tmp_path):
    transport = StartupTransport(
        tmp_path, [trust_screen(tmp_path / "different")], initial_prompt="Review"
    )
    try:
        with pytest.raises(RuntimeError, match="different folder"):
            await transport.start()
        assert transport.keys == []
        assert transport.loaded_buffers == []
        assert not transport._send_lock.locked()
    finally:
        await transport.stop()


@pytest.mark.parametrize("selected_yes", [False, True])
async def test_stuck_trust_screen_retries_a_bounded_number_of_times(tmp_path, selected_yes):
    transport = StartupTransport(
        tmp_path, [trust_screen(tmp_path, yes=selected_yes)], workspace_trust_max_attempts=3
    )
    try:
        with pytest.raises(RuntimeError, match="after 3"):
            await transport.start()
        with pytest.raises(RuntimeError, match="after 3"):
            await transport.send_message("Must remain unsent")
        assert transport.keys == ["Enter" if selected_yes else "Down"] * 3
        assert not transport.loaded_buffers
        assert not transport.is_turn_active
    finally:
        await transport.stop()


@pytest.mark.parametrize("selected_yes", [False, True])
async def test_stuck_trust_screen_waits_out_the_settle_interval(tmp_path, selected_yes):
    """Within one settle interval a dropped key is never repeated."""
    transport = StartupTransport(
        tmp_path, [trust_screen(tmp_path, yes=selected_yes)], workspace_trust_settle_s=60.0
    )
    try:
        with pytest.raises(RuntimeError, match="Workspace trust did not complete"):
            await transport.start()
        assert transport.keys == ["Enter" if selected_yes else "Down"]
        assert not transport.loaded_buffers
    finally:
        await transport.stop()


async def test_incomplete_trust_menu_waits_for_selection_before_enter(tmp_path):
    partial = trust_screen(tmp_path).replace("❯", " ")
    yes = trust_screen(tmp_path, yes=True)
    transport = StartupTransport(tmp_path, [partial, partial, yes, yes, "❯"])
    try:
        await transport.start()
        assert transport.keys == ["Enter"]
    finally:
        await transport.stop()


async def test_partial_trust_screen_does_not_look_ready_before_yes_renders(tmp_path):
    partial = f"Accessing workspace:\n\n{tmp_path}\n\n❯ No, exit"
    no, yes = trust_screen(tmp_path), trust_screen(tmp_path, yes=True)
    transport = StartupTransport(tmp_path, [partial, partial, no, no, yes, yes, "❯"])
    try:
        await transport.start()
        assert transport.keys == ["Down", "Enter"]
        assert transport._startup_ready
        assert not transport.screens
    finally:
        await transport.stop()


async def test_startup_menu_without_trust_does_not_receive_seed_or_chat(tmp_path):
    transport = StartupTransport(
        tmp_path,
        ["Choose login method\n❯ Claude account\n  API key"],
        initial_prompt="Review this",
    )
    try:
        with pytest.raises(RuntimeError, match="not ready for input"):
            await transport.start()
        with pytest.raises(RuntimeError, match="not ready for input"):
            await transport.send_message("Still must remain unsent")
        assert transport.keys == []
        assert not transport.loaded_buffers
        assert not transport._initial_prompt_sent
        assert not transport._startup_ready
    finally:
        await transport.stop()


async def test_custom_ready_marker_is_required_after_trust(tmp_path, monkeypatch):
    monkeypatch.setenv("SKULD__TMUX_REPL_READY_MARKER", "Ready for input")
    yes = trust_screen(tmp_path, yes=True)
    transport = StartupTransport(tmp_path, [yes, yes, "❯"])
    try:
        with pytest.raises(RuntimeError, match="Workspace trust did not complete"):
            await transport.start()
        assert transport.keys == ["Enter"]
        transport.screens.append("Ready for input")
        await transport.start()
        assert transport._startup_ready
        assert transport.keys == ["Enter"]
    finally:
        await transport.stop()


@pytest.mark.parametrize("seed", ["", "Review this"])
@pytest.mark.parametrize("pane", ["❯", "Which database?\n❯ 1. Postgres\n  2. SQLite"])
async def test_known_question_during_startup_allows_connection_but_blocks_chat(
    tmp_path, monkeypatch, seed, pane
):
    transport = StartupTransport(tmp_path, [pane], initial_prompt=seed)
    capture = transport._capture_pane_text
    question_received = False

    async def receive_question(pane_id=None):
        nonlocal question_received
        text = await capture(pane_id)
        if not question_received:
            question_received = True
            await transport.handle_claude_hook(
                {
                    "hook_event_name": "PreToolUse",
                    "tool_name": "AskUserQuestion",
                    "tool_input": {
                        "questions": [
                            {
                                "question": "Which database?",
                                "options": [{"label": "Postgres"}, {"label": "SQLite"}],
                            }
                        ]
                    },
                }
            )
        return text

    monkeypatch.setattr(transport, "_capture_pane_text", receive_question)
    try:
        await transport.start()
        assert transport._pending_tty_prompts
        assert not transport._startup_ready
        assert not transport._send_lock.locked()
        with pytest.raises(RuntimeError, match="native control"):
            await transport.send_message("Must remain unsent")
        assert transport.keys == []
        assert not transport.loaded_buffers
        assert not transport._initial_prompt_sent

        # Once the native question resolves, startup can be retried normally.
        transport._pending_tty_prompts.clear()
        transport.screens.append("❯")
        await transport.start()
        assert transport._startup_ready
        assert transport.loaded_buffers == ([seed] if seed else [])
    finally:
        await transport.stop()


async def test_trust_confirmation_does_not_answer_a_following_startup_menu(tmp_path):
    yes = trust_screen(tmp_path, yes=True)
    transport = StartupTransport(
        tmp_path,
        [yes, yes, "Choose login method\n❯ Claude account\n  API key"],
        initial_prompt="Review this",
    )
    try:
        with pytest.raises(RuntimeError, match="Workspace trust did not complete"):
            await transport.start()
        assert transport.keys == ["Enter"]
        assert not transport.loaded_buffers
    finally:
        await transport.stop()


async def test_failed_confirmation_send_is_not_replayed_before_it_settles(tmp_path, monkeypatch):
    transport = StartupTransport(
        tmp_path, [trust_screen(tmp_path, yes=True)], workspace_trust_settle_s=60.0
    )
    attempts = []

    async def uncertain_send(key, *, pane_id=None):
        attempts.append(key)
        raise RuntimeError("Lost tmux response")

    monkeypatch.setattr(transport, "_send_key", uncertain_send)
    try:
        with pytest.raises(RuntimeError, match="Lost tmux response"):
            await transport.start()
        with pytest.raises(RuntimeError, match="Workspace trust did not complete"):
            await transport.start()
        assert attempts == ["Enter"]
        assert not transport.loaded_buffers
    finally:
        await transport.stop()


async def test_failed_confirmation_sends_count_against_the_budget(tmp_path, monkeypatch):
    transport = StartupTransport(
        tmp_path, [trust_screen(tmp_path, yes=True)], workspace_trust_max_attempts=2
    )
    attempts = []

    async def uncertain_send(key, *, pane_id=None):
        attempts.append(key)
        raise RuntimeError("Lost tmux response")

    monkeypatch.setattr(transport, "_send_key", uncertain_send)
    try:
        for _ in range(2):
            with pytest.raises(RuntimeError, match="Lost tmux response"):
                await transport.start()
        with pytest.raises(RuntimeError, match="not accepted after 2 confirmations"):
            await transport.start()
        assert attempts == ["Enter", "Enter"]
        assert not transport.loaded_buffers
    finally:
        await transport.stop()


async def test_new_tmux_process_can_confirm_its_own_trust_prompt(tmp_path):
    yes = trust_screen(tmp_path, yes=True)
    transport = StartupTransport(tmp_path, [yes, yes, "❯"])
    try:
        await transport.start()
        await transport.stop()
        transport.session_exists = False
        transport.screens.extend([yes, yes, "❯"])
        await transport.start()
        assert transport.keys == ["Enter", "Enter"]
    finally:
        await transport.stop()


class TrustDialogTransport(FakeTmuxInteractiveTransport):
    """A trust dialog that reacts to keys like Claude's, with scripted dropped keys.

    ``drop`` holds indices of keys a transient render swallows; ``resets`` is how many
    Enter presses land while Claude re-mounts the dialog, which comes back with No
    highlighted (the 2.1.281 startup race seen live). Enter on No would exit Claude.
    """

    def __init__(self, workspace: Path, *, drop=(), resets=0, **kwargs):
        kwargs.setdefault("workspace_trust_stable_s", 0.005)
        kwargs.setdefault("workspace_trust_settle_s", 0.02)
        super().__init__(str(workspace), **kwargs)
        self.workspace = workspace
        self.yes_highlighted = False
        self.trusted = False
        self.sent: list[tuple[str, bool]] = []
        self.drop = set(drop)
        self.resets = resets
        self._repl_ready_timeout_s = 3.0
        self._menu_poll_step_s = 0.001

    async def _capture_pane_text(self, pane_id=None):
        return "❯ " if self.trusted else trust_screen(self.workspace, yes=self.yes_highlighted)

    async def _send_key(self, key, *, pane_id=None):
        index = len(self.sent)
        self.sent.append((key, self.yes_highlighted))
        if index in self.drop:
            return
        if key in {"Down", "Up"}:
            self.yes_highlighted = not self.yes_highlighted
            return
        if key != "Enter":
            return
        if self.resets:
            self.resets -= 1
            self.yes_highlighted = False
            return
        if not self.yes_highlighted:
            raise AssertionError("Enter on 'No, exit' would quit Claude")
        self.trusted = True


def _enter_only_on_yes(transport: TrustDialogTransport) -> bool:
    return all(yes for key, yes in transport.sent if key == "Enter")


async def test_enter_swallowed_by_a_redraw_is_renavigated_and_retried(tmp_path):
    """The live failure: Down, Enter, then the dialog re-renders on No and waits."""
    transport = TrustDialogTransport(tmp_path, resets=1, initial_prompt="Start")
    try:
        await transport.start()
        trust_keys, seed_submit = transport.sent[:4], transport.sent[4:]
        assert trust_keys == [
            ("Down", False),
            ("Enter", True),
            ("Down", False),
            ("Enter", True),
        ]
        assert transport.trusted and transport._startup_ready
        assert transport.loaded_buffers == ["Start"]
        assert [key for key, _ in seed_submit] == ["Enter"]  # the seed prompt, after trust
    finally:
        await transport.stop()


async def test_dropped_navigation_is_resent_after_settling(tmp_path):
    transport = TrustDialogTransport(tmp_path, drop={0})
    try:
        await transport.start()
        assert [key for key, _ in transport.sent] == ["Down", "Down", "Enter"]
        assert _enter_only_on_yes(transport) and transport.trusted
    finally:
        await transport.stop()


async def test_a_dialog_that_keeps_resetting_fails_loudly_within_budget(tmp_path):
    transport = TrustDialogTransport(
        tmp_path, resets=99, workspace_trust_max_attempts=2, initial_prompt="Never pasted"
    )
    try:
        with pytest.raises(RuntimeError, match="did not move to Yes after 2 attempts"):
            await transport.start()
        assert [key for key, _ in transport.sent] == ["Down", "Enter", "Down", "Enter"]
        assert _enter_only_on_yes(transport)
        assert not transport.loaded_buffers
    finally:
        await transport.stop()


async def test_a_single_transient_yes_frame_never_triggers_enter(tmp_path):
    no, yes = trust_screen(tmp_path), trust_screen(tmp_path, yes=True)
    # Down lands, Claude flashes Yes for one frame and redraws No: re-navigate instead.
    transport = StartupTransport(tmp_path, [no, no, yes, no, no, yes, yes, "❯"])
    try:
        await transport.start()
        assert transport.keys == ["Down", "Down", "Enter"]
        assert transport._startup_ready
    finally:
        await transport.stop()


async def test_keys_wait_for_the_menu_to_be_stable(tmp_path):
    transport = TrustDialogTransport(tmp_path, workspace_trust_stable_s=0.2)
    transport._repl_ready_timeout_s = 0.1
    try:
        with pytest.raises(RuntimeError, match="Workspace trust did not complete"):
            await transport.start()
        assert transport.sent == []  # shown for less than the stability window
    finally:
        await transport.stop()


def test_trust_settings_are_validated(tmp_path):
    with pytest.raises(ValueError, match="at least one attempt"):
        TmuxInteractiveTransport(str(tmp_path), workspace_trust_max_attempts=0)
    with pytest.raises(ValueError, match=">= 0"):
        TmuxInteractiveTransport(str(tmp_path), workspace_trust_settle_s=-1)


@pytest.mark.integration
@pytest.mark.tmux
@pytest.mark.parametrize(
    ("resets", "expected_keys"),
    [(0, ["Down", "Enter"]), (1, ["Down", "Enter", "Down", "Enter"])],
)
async def test_real_tmux_workspace_trust_then_chat(tmp_path, monkeypatch, resets, expected_keys):
    """Real TTY widget exits on No; only arrow + observed Yes + Enter can reach chat.

    With ``resets=1`` the widget swallows the first Enter and redraws on No (the
    Claude 2.1.281 startup race); the transport must re-select Yes and confirm again.
    """
    if shutil.which("tmux") is None:
        pytest.skip("tmux is not installed")
    for key, value in install_fake_claude(tmp_path / "bin").items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("FORGE_FAKEAGENT_WORKSPACE_TRUST", "1")
    monkeypatch.setenv("FORGE_FAKEAGENT_WORKSPACE_TRUST_RESETS", str(resets))
    monkeypatch.setenv("SKULD__TMUX_SOCKET_DIR", str(tmp_path / "sockets"))
    monkeypatch.setenv("SKULD__TMUX_REMOTE_CONTROL", "0")

    async def handle_hook(payload):
        await transport.handle_claude_hook(payload)

    hook_server = await HookServer(handle_hook).start()
    transport = TmuxInteractiveTransport(
        str(tmp_path),
        session_id=f"trust-{uuid.uuid4().hex[:8]}",
        turn_idle_timeout_s=0.1,
        turn_no_output_timeout_s=1,
        sdk_port=hook_server.port,
    )
    events = await _collect_events(transport)
    try:
        await transport.start()  # No seed: the first page must still be handled.
        keys = [event["key"] for event in events if event["type"] == "terminal_key_sent"]
        assert keys == expected_keys
        assert "fakeagent ready" in await transport._capture_pane_text()
        await transport.send_message("say:trust passed, chat received")
        await _wait_until(
            lambda: any(
                event["type"] == "result"
                and "trust passed, chat received" in event.get("result", "")
                for event in events
            ),
            timeout=10,
        )
    finally:
        await transport.stop()
        await hook_server.stop()


def test_trust_settings_flow_from_skuld_settings(tmp_path, monkeypatch):
    from skuld.broker import Broker
    from skuld.config import SkuldSettings

    monkeypatch.setenv("SKULD__TMUX_WORKSPACE_TRUST_MAX_ATTEMPTS", "5")
    settings = SkuldSettings(
        session={"id": str(uuid.uuid4()), "workspace_dir": str(tmp_path)},
        transport_adapter="skuld.transports.tmux_interactive.TmuxInteractiveTransport",
        tmux_workspace_trust_stable_s=0.25,
        tmux_workspace_trust_settle_s=2.5,
    )
    transport = Broker(settings=settings)._create_transport()
    assert transport._workspace_trust_stable_s == 0.25
    assert transport._workspace_trust_settle_s == 2.5
    assert transport._workspace_trust_max_attempts == 5
