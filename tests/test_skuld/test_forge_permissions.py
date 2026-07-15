"""Scenario Group F — PERMISSION MODES (forge tmux harness + SDK + broker tiers).

Each test asserts a REAL, current permission-handling contract against the
transport that actually implements that mode — never an invented feature. The
deliberate limitations (the tmux transport is a BINARY bypass-on/off gate with no
plan/acceptEdits parameter; bypassPermissions wins over an enabled
AskUserQuestion callback in the SDK) are documented as precise findings, not
papered over.

  * F1 — default mode surfaces a gate (tmux/real): with hooks on and
    skip_permissions=False, a ``perm:`` directive produces a structured gate that
    reaches the client (``claude_permission_request`` AND the bridged
    ``ask_user_question``) and flips the session to awaiting_input.
  * F2 — allow / allow-&-don't-ask / deny (tmux/real): each structured answer
    drives the correct keystroke (digit for an allow row, Escape for deny) into
    the live menu and the bridge broadcasts ``ask_user_resolved``. NOTE: the tmux
    transport routes a permission gate through the AskUserQuestion bridge, so the
    resolution frame is ``ask_user_resolved`` — NOT the broker's control_request
    ``permission_resolved`` (that path is SDK/persistent-subprocess only). This is
    asserted as the real contract and documented below.
  * F3 — plan mode (SDK, fake client): the SDK transport advertises
    ``set_permission_mode`` and ``send_control('set_permission_mode',
    permissionMode='plan')`` switches it live on the client. The deeper
    edits-gate semantic is real-claude behavior — recorded as a live-tier xfail.
  * F4 — acceptEdits (SDK, fake client): the same control sets ``acceptEdits``
    live. The mode matrix is documented; no real claude required.
  * F5 — bypass vs ask conflict (unit): with skip_permissions=True AND
    ask_user_question_enabled=True the DOCUMENTED precedence is bypass-wins — the
    SDK sets ``permission_mode=bypassPermissions`` which makes the CLI bypass the
    ``can_use_tool`` callback entirely, so AskUserQuestion never surfaces. Asserted
    deterministically on the built ``ClaudeAgentOptions`` (no contradiction).
  * F6 — auto-approval policy (broker, respx-stubbed Volundr): a gate the policy
    ALLOWS is auto-resolved after delay_seconds (``permission_resolved`` with
    auto_approved=True); one the policy does NOT allow stays pending for a human.
  * F-LIMIT — the tmux transport only supports bypass on/off: skip_permissions
    toggles ``--permission-mode bypassPermissions`` on the spawn argv, and there
    is NO plan/acceptEdits parameter and NO ``set_permission_mode`` capability.
    Recorded as a real limitation + followup.

Tiers / how to run::

    # default tier (F3, F4, F5, F6, F-LIMIT unit assertions)
    uv run pytest tests/test_skuld/test_forge_permissions.py -p no:cacheprovider -q

    # real-tmux tier (F1, F2, F-LIMIT live spawn-argv check)
    SKULD__TMUX_REMOTE_CONTROL=0 uv run pytest -m integration \
        tests/test_skuld/test_forge_permissions.py -p no:cacheprovider -q -rs
"""

from __future__ import annotations

import asyncio
import shutil
from typing import Any

import httpx
import pytest
import respx

from skuld.broker import FORGE_PERMISSION_AUTO_APPROVAL_EVALUATE_PATH, Broker
from skuld.config import SkuldSettings
from skuld.transports.sdk import SDKTransport
from tests.support.forge import BrokerHarness


def _require_tmux() -> None:
    if shutil.which("tmux") is None:
        pytest.skip("tmux is not installed")


# --------------------------------------------------------------- SDK fake client


class _FakeClient:
    """Minimal stand-in for ClaudeSDKClient (mirrors tests/.../test_sdk.py)."""

    def __init__(self) -> None:
        from unittest.mock import AsyncMock

        self.options: Any = None
        self.set_model = AsyncMock()
        self.set_permission_mode = AsyncMock()
        self.interrupt = AsyncMock()
        self.query = AsyncMock()
        self.rewind_files = AsyncMock()

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _ClientFactory:
    """Captures the ClaudeAgentOptions the transport builds + the live client."""

    def __init__(self) -> None:
        self.client: _FakeClient | None = None
        self.options: Any = None

    def __call__(self, options: Any) -> _FakeClient:
        self.options = options
        self.client = _FakeClient()
        return self.client


# ------------------------------------------------------------- broker channel spy


class _RecordingChannel:
    """A MessageChannel that records every broadcast frame (F6 observation)."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._open = True

    async def send_event(self, event: dict) -> None:
        self.events.append(event)

    async def close(self) -> None:
        self._open = False

    @property
    def channel_type(self) -> str:
        return "recording"

    @property
    def is_open(self) -> bool:
        return self._open

    def of_type(self, frame_type: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e.get("type") == frame_type]


def _broker(tmp_path, *, volundr_api_url: str | None = None) -> Broker:
    settings = SkuldSettings(
        session={"id": "perm-session"},
        transport="subprocess",
        host="0.0.0.0",
        port=8081,
    )
    settings.session.workspace_dir = str(tmp_path)
    broker = Broker(settings=settings)
    if volundr_api_url is not None:
        broker.volundr_api_url = volundr_api_url
    return broker


# --------------------------------------------------------------------------- F1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_f1_default_mode_surfaces_permission_gate() -> None:
    """F1: default mode (skip_permissions=False) surfaces a structured gate.

    A ``perm:`` directive fires a PermissionRequest hook. The tmux bridge emits a
    ``claude_permission_request`` frame AND surfaces the gate as a structured
    ``ask_user_question`` (so a remote client renders an answer card); the broker
    flips the session to awaiting_input (a pending attention gate).
    """
    _require_tmux()

    async with BrokerHarness(
        hooks=True,
        skip_permissions=False,
        idle_timeout_s=0.3,
        ask_user_question_enabled=True,
        boot="perm:Bash|rm -rf build",
    ) as h:
        client = await h.connect()

        # The raw permission-request frame reaches the client.
        perm = await client.wait_for_type("claude_permission_request", timeout=8.0)
        assert perm.get("tool_name") == "Bash"

        # AND the bridged structured question reaches the client (answerable card).
        ask = await client.wait_for_type("ask_user_question", timeout=8.0)
        request_id = str(ask.get("request_id") or "")
        assert request_id, f"bridged ask_user_question carried no request_id: {ask}"
        assert ask.get("questions"), "bridged gate lost its questions"

        # The broker is awaiting input: the gate is a tracked attention gate.
        assert request_id in h.broker._pending_ask_user_questions
        await client.wait_for(
            lambda _frames: request_id in h.broker._pending_attention,
            timeout=4.0,
        )


# --------------------------------------------------------------------------- F2


async def _drive_permission_choice(
    boot: str,
    answer: str,
    *,
    expect_keys: list[str],
) -> None:
    """Boot a single ``perm:`` gate, answer it via ask_user_answer, and assert the
    keystroke(s) sent to the live menu + that ask_user_resolved is broadcast."""
    async with BrokerHarness(
        hooks=True,
        skip_permissions=False,
        idle_timeout_s=0.3,
        ask_user_question_enabled=True,
        boot=boot,
    ) as h:
        client = await h.connect()
        ask = await client.wait_for_type("ask_user_question", timeout=8.0)
        request_id = str(ask.get("request_id") or "")
        assert request_id

        # Count keystrokes already sent (menu open may not have sent any) so we
        # assert on the NEW keys produced by answering.
        keys_before = [f.get("key") for f in client.frames if f.get("type") == "terminal_key_sent"]

        client.ask_user_answer(request_id, [{"answer": answer}])

        # The bridge resolves the gate.
        await client.wait_for(
            lambda frames: any(
                f.get("type") == "ask_user_resolved"
                and str(f.get("request_id") or "") == request_id
                for f in frames
            ),
            timeout=8.0,
        )

        keys_after = [f.get("key") for f in client.frames if f.get("type") == "terminal_key_sent"]
        new_keys = keys_after[len(keys_before) :]
        for expected in expect_keys:
            assert expected in new_keys, (
                f"answering {answer!r} must send key {expected!r}; new keys were {new_keys}"
            )

        resolved = [
            f
            for f in client.frames_of_type("ask_user_resolved")
            if str(f.get("request_id") or "") == request_id
        ]
        assert resolved, "the gate must broadcast an ask_user_resolved frame"
        return resolved[-1]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_f2_allow_sends_first_row_digit() -> None:
    """F2(allow): an Allow answer presses the first menu row's digit."""
    _require_tmux()
    await _drive_permission_choice(
        "perm:Bash|echo hi",
        "Allow",
        expect_keys=["1"],
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_f2_allow_dont_ask_sends_second_row_digit() -> None:
    """F2(allow & don't ask again): selects the persistent-allow row (digit 2).

    Regression guard for the _match_menu_digit precedence bug: against rows
    [(1,'Allow'),(2,"Allow & don't ask again"),(3,'Deny')] the verbatim choice
    "Allow & don't ask again" previously matched row 1 'Allow' via substring,
    silently downgrading a persistent allow to a one-time allow. The matcher now
    runs an exact-label pass over ALL rows first, so it returns digit 2
    (src/skuld/transports/tmux_interactive.py:_match_menu_digit).
    """
    _require_tmux()
    await _drive_permission_choice(
        "perm:Bash|echo hi",
        "Allow & don't ask again",
        expect_keys=["2"],
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_f2_deny_sends_escape_and_reflects_not_allowed() -> None:
    """F2(deny): a Deny answer presses Escape (cancels the menu) and the
    resolution reflects not-allowed (decision == 'deny')."""
    _require_tmux()
    resolved = await _drive_permission_choice(
        "perm:Bash|rm -rf build",
        "Deny",
        expect_keys=["Escape"],
    )
    assert resolved.get("decision") == "deny", (
        f"deny must resolve as not-allowed; got decision={resolved.get('decision')!r}"
    )


# --------------------------------------------------------------------------- F3


@pytest.mark.asyncio
async def test_f3_sdk_advertises_and_switches_to_plan_mode(monkeypatch, tmp_path) -> None:
    """F3: the SDK transport advertises set_permission_mode and switches live to plan.

    The deep semantic (in plan mode the agent's edit/write tools are gated) is real
    ``claude`` behavior we cannot exercise with a fake client — see the xfail TODO
    test_f3_plan_mode_gates_edits_live below.
    """
    factory = _ClientFactory()
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", factory)

    transport = SDKTransport(workspace_dir=str(tmp_path))
    assert transport.capabilities.set_permission_mode is True

    await transport.start()
    await transport.send_control("set_permission_mode", permissionMode="plan")

    assert factory.client is not None
    factory.client.set_permission_mode.assert_awaited_once_with("plan")

    await transport.stop()


@pytest.mark.xfail(
    reason=(
        "F3 deep semantic: that plan mode actually GATES edit/write tools is real "
        "claude-CLI behavior — it needs a live model + credentials, not a fake SDK "
        "client. The transport CONTRACT (advertises set_permission_mode + forwards "
        "the control to client.set_permission_mode) is covered by "
        "test_f3_sdk_advertises_and_switches_to_plan_mode. TODO: cover the live "
        "edits-gate semantic in a credentialed live-tier suite."
    ),
    strict=False,
    run=False,
)
def test_f3_plan_mode_gates_edits_live() -> None:  # pragma: no cover
    raise AssertionError("not implemented — see xfail reason (live-tier TODO)")


# --------------------------------------------------------------------------- F4


@pytest.mark.asyncio
async def test_f4_sdk_switches_to_accept_edits_mode(monkeypatch, tmp_path) -> None:
    """F4: the same control sets acceptEdits live (mode matrix entry).

    Permission-mode matrix (SDK transport, via set_permission_mode):
      * 'default'            — gate every tool (can_use_tool callback runs)
      * 'plan'               — plan mode; edits/writes gated (live semantic)
      * 'acceptEdits'        — auto-accept edit/write tools
      * 'bypassPermissions'  — bypass all gates (also set by skip_permissions=True)
    The transport faithfully forwards whatever mode string it is given to the live
    client; it does not validate the string itself (the SDK/CLI owns that).
    """
    factory = _ClientFactory()
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", factory)

    transport = SDKTransport(workspace_dir=str(tmp_path))
    await transport.start()

    await transport.send_control("set_permission_mode", permissionMode="acceptEdits")
    assert factory.client is not None
    factory.client.set_permission_mode.assert_awaited_once_with("acceptEdits")

    # The control is idempotent/repeatable — a second mode switch forwards too.
    await transport.send_control("set_permission_mode", permissionMode="default")
    assert factory.client.set_permission_mode.await_args_list[-1].args == ("default",)

    await transport.stop()


# --------------------------------------------------------------------------- F5


@pytest.mark.asyncio
async def test_f5_sdk_bypass_wins_over_enabled_ask_question(monkeypatch, tmp_path) -> None:
    """F5: skip_permissions=True AND ask_user_question_enabled=True is NOT
    contradictory — bypass wins, deterministically.

    Documented precedence (src/skuld/transports/sdk.py:679-688): when
    skip_permissions sets permission_mode=bypassPermissions, the CLI bypasses the
    can_use_tool callback entirely, so AskUserQuestion never surfaces. We assert
    BOTH facts hold on the built ClaudeAgentOptions: bypassPermissions is set, and
    even though a can_use_tool handler is wired (because ask is enabled), the
    bypass mode neutralizes it — the precedence is explicit, not silent.
    """
    factory = _ClientFactory()
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", factory)

    transport = SDKTransport(
        workspace_dir=str(tmp_path),
        skip_permissions=True,
        ask_user_question_enabled=True,
    )
    await transport.start()

    options = factory.options
    assert options is not None, "the SDK client was not constructed with options"

    # Bypass wins: the permission mode is bypassPermissions.
    assert options.permission_mode == "bypassPermissions", (
        f"skip_permissions must set bypassPermissions; got {options.permission_mode!r}"
    )

    # The can_use_tool handler IS wired (ask enabled), but the SDK comment is
    # explicit that bypassPermissions makes the CLI skip it — so the combination
    # is handled by documented precedence, not by silently dropping the callback.
    # (Bound methods don't compare by identity, so match on the underlying func.)
    assert options.can_use_tool is not None, (
        "ask_user_question_enabled should still wire the handler; bypass neutralizes "
        "it at the CLI, not at wiring time"
    )
    assert getattr(options.can_use_tool, "__func__", None) is (SDKTransport._on_can_use_tool), (
        "the wired handler must be the transport's _on_can_use_tool"
    )

    await transport.stop()


@pytest.mark.asyncio
async def test_f5_sdk_ask_without_bypass_uses_default_gate(monkeypatch, tmp_path) -> None:
    """F5(control): with ask enabled and skip_permissions=False there is NO bypass
    mode set, so the can_use_tool callback is live (AskUserQuestion can surface)."""
    factory = _ClientFactory()
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", factory)

    transport = SDKTransport(
        workspace_dir=str(tmp_path),
        skip_permissions=False,
        ask_user_question_enabled=True,
    )
    await transport.start()

    options = factory.options
    assert options is not None
    # No skip_permissions → permission_mode is left unset (default gating path).
    assert getattr(options, "permission_mode", None) in (None, "default"), (
        f"non-bypass default path must not force a bypass mode; got "
        f"{getattr(options, 'permission_mode', None)!r}"
    )
    assert options.can_use_tool is not None
    assert getattr(options.can_use_tool, "__func__", None) is SDKTransport._on_can_use_tool

    await transport.stop()


# --------------------------------------------------------------------------- F6


@pytest.mark.asyncio
async def test_f6_auto_approval_allows_after_delay(tmp_path) -> None:
    """F6(allow): a policy-ALLOWED gate is auto-resolved after delay_seconds.

    The broker POSTs the real auto-approval evaluate endpoint (respx-stubbed),
    schedules the approval, waits delay_seconds, re-checks, then sends an allow
    control-response and broadcasts permission_resolved(auto_approved=True).
    """
    from unittest.mock import AsyncMock

    broker = _broker(tmp_path, volundr_api_url="http://volundr.test")
    channel = _RecordingChannel()
    broker._channels.add(channel)
    transport = AsyncMock()
    broker._transport = transport

    path = FORGE_PERMISSION_AUTO_APPROVAL_EVALUATE_PATH.format(session_id=broker.session_id)

    with respx.mock(assert_all_called=False) as router:
        router.post(f"http://volundr.test{path}").mock(
            return_value=httpx.Response(
                200,
                json={"can_auto_approve": True, "delay_seconds": 0.1, "reason": "allowed"},
            )
        )
        await broker._handle_cli_event(
            {
                "type": "control_request",
                "request_id": "perm-allow",
                "tool": "Bash",
                "input": {"command": "./start-dev"},
            }
        )
        # Scheduled immediately; auto-resolved after the (small) delay.
        await asyncio.wait_for(
            _wait_for(lambda: channel.of_type("permission_resolved")),
            timeout=4.0,
        )

    resolved = channel.of_type("permission_resolved")
    assert resolved, "an allowed gate must broadcast permission_resolved"
    assert resolved[-1]["request_id"] == "perm-allow"
    assert resolved[-1]["behavior"] == "allow"
    assert resolved[-1]["auto_approved"] is True
    transport.send_control_response.assert_awaited_once_with(
        "perm-allow", {"behavior": "allow", "updatedInput": {}}
    )
    # No longer pending — it was auto-resolved.
    assert "perm-allow" not in broker._pending_permission_requests
    assert channel.of_type("permission_auto_approval_scheduled"), (
        "the allow path must announce the scheduled approval"
    )

    await broker._channels.close_all()


@pytest.mark.asyncio
async def test_f6_auto_approval_leaves_denied_gate_pending(tmp_path) -> None:
    """F6(deny): a gate the policy does NOT allow stays pending for the human — no
    control-response is sent and the request remains tracked."""
    from unittest.mock import AsyncMock

    broker = _broker(tmp_path, volundr_api_url="http://volundr.test")
    channel = _RecordingChannel()
    broker._channels.add(channel)
    transport = AsyncMock()
    broker._transport = transport

    path = FORGE_PERMISSION_AUTO_APPROVAL_EVALUATE_PATH.format(session_id=broker.session_id)

    with respx.mock(assert_all_called=False) as router:
        evaluate = router.post(f"http://volundr.test{path}").mock(
            return_value=httpx.Response(
                200,
                json={"can_auto_approve": False, "delay_seconds": 0, "reason": "denylist"},
            )
        )
        await broker._handle_cli_event(
            {
                "type": "control_request",
                "request_id": "perm-deny",
                "tool": "Bash",
                "input": {"command": "rm -rf build"},
            }
        )
        # Let the auto-approval task run to completion (it should NOT resolve).
        await _wait_for(lambda: "perm-deny" in broker._pending_attention, timeout=4.0)
        # The policy WAS consulted (the gate stays pending because the policy said
        # no — not because the HTTP call silently failed).
        assert evaluate.called, "the auto-approval policy endpoint must be consulted"

    transport.send_control_response.assert_not_awaited()
    assert broker._channels and not channel.of_type("permission_resolved"), (
        "a policy-denied gate must NOT auto-resolve"
    )
    assert "perm-deny" in broker._pending_permission_requests, (
        "a non-auto-approvable gate must stay pending for the human"
    )
    # And it is surfaced as a needs-attention gate.
    assert "perm-deny" in broker._pending_attention

    await broker._channels.close_all()


async def _wait_for(predicate, timeout: float = 4.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met before timeout")


# ----------------------------------------------------------------------- F-LIMIT


def test_flimit_tmux_transport_has_no_plan_or_accept_edits_param() -> None:
    """F-LIMIT (unit): the tmux transport is a BINARY bypass-on/off gate.

    It exposes skip_permissions only — there is NO plan/acceptEdits parameter, NO
    set_permission_mode control, and its capabilities do not advertise
    set_permission_mode. This is a real limitation worth recording, not a feature
    to invent. Followup: if Forge ever needs plan/acceptEdits over tmux, the
    transport would need a richer --permission-mode passthrough (the CLI supports
    the modes; the tmux wrapper currently only toggles bypassPermissions).
    """
    import inspect

    from skuld.transports.tmux_interactive import TmuxInteractiveTransport

    sig = inspect.signature(TmuxInteractiveTransport.__init__)
    params = set(sig.parameters)
    assert "skip_permissions" in params
    assert "permission_mode" not in params, (
        "tmux transport unexpectedly grew a permission_mode param — update F-LIMIT"
    )
    assert not any("accept" in p.lower() or "plan" in p.lower() for p in params), (
        f"tmux transport unexpectedly grew a plan/acceptEdits param: {params}"
    )

    # The capabilities object does not advertise set_permission_mode for tmux.
    caps = TmuxInteractiveTransport(
        workspace_dir=".",
        session_id="cap-probe",
    ).capabilities
    assert caps.set_permission_mode is False, (
        "the tmux transport must not advertise set_permission_mode (binary bypass only)"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_flimit_skip_permissions_toggles_bypass_argv() -> None:
    """F-LIMIT (real spawn-argv): skip_permissions=True puts ``--permission-mode
    bypassPermissions`` on the spawned claude argv; False omits it. There is no
    other permission-mode knob — proving the binary nature on the live command."""
    _require_tmux()

    from skuld.transports.tmux_interactive import (
        _DEFAULT_PERMISSION_MODE,
        TmuxInteractiveTransport,
    )

    def _argv(*, skip_permissions: bool) -> list[str]:
        transport = TmuxInteractiveTransport(
            workspace_dir=".",
            session_id="argv-probe",
            skip_permissions=skip_permissions,
        )
        builder = getattr(transport, "_interactive_argv", None)
        if builder is None:  # pragma: no cover - guards a private-name drift
            raise AssertionError(
                "tmux transport _interactive_argv seam not found — update F-LIMIT probe"
            )
        return list(builder())

    on = _argv(skip_permissions=True)
    off = _argv(skip_permissions=False)

    assert "--permission-mode" in on
    assert _DEFAULT_PERMISSION_MODE in on
    assert on[on.index("--permission-mode") + 1] == _DEFAULT_PERMISSION_MODE
    assert "--permission-mode" not in off
