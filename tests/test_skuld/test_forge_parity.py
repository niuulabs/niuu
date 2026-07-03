"""Phase 3 — cross-mode TRANSPORT PARITY matrix.

A Forge frontend must behave identically regardless of which CLI transport sits
under the broker. This suite locks that: ONE shared contract (the
``assert_*_shape`` helpers below) is asserted against EVERY mode that legitimately
supports a given capability. A per-mode divergence in a field the frontend
depends on (frame shape, capability semantics, resolve contract) fails that mode's
cell — it is a BUG TO ALIGN, not something to weaken. A deliberate, defensible
per-mode difference is skipped/xfailed with a reason + followup, never faked green.

MODES
-----
  * ``sdk``                  — ``SDKTransport`` with a monkeypatched fake
    ``ClaudeSDKClient`` (the ``_FakeClient``/``_ClientFactory`` pattern from
    ``tests/test_skuld/transports/test_sdk.py``). Default tier.
  * ``persistent_subprocess`` — ``PersistentSubprocessTransport`` driven by stub
    asyncio streams (the ``_StubStream``/``_StubStdin``/``_make_proc`` pattern from
    ``tests/test_skuld/test_persistent_subprocess.py``). Default tier.
  * ``tmux_interactive``     — the REAL ``TmuxInteractiveTransport`` via the forge
    ``BrokerHarness`` + ``fakeagent`` inside tmux. Integration tier; the param is
    marked ``pytest.mark.integration`` and skips when tmux is unavailable.

Each contract is parametrized over the modes that support it; cells a mode
legitimately can't do are skipped/xfailed with a reason (see the per-mode notes in
``_MODE_SUPPORT`` and the inline ``skip``/``xfail`` calls).

Tiers / how to run::

    # default tier (sdk + persistent cells)
    uv run pytest tests/test_skuld/test_forge_parity.py -p no:cacheprovider -q

    # real-tmux tier (adds the tmux_interactive cells)
    SKULD__TMUX_REMOTE_CONTROL=0 uv run pytest -m integration \
        tests/test_skuld/test_forge_parity.py -p no:cacheprovider -q -rs

DRIFT NOTES (documented, NOT papered over)
------------------------------------------
  * ask_user_question resolve channel: tmux resolves a question/permission gate via
    ``ask_user_resolved`` (the keystroke bridge), the SDK via ``resolve_question``
    threading the chosen label into the model's tool_result, and persistent via the
    stdio control protocol (``ask_user_answer`` -> deny-with-message). The OUTBOUND
    ``ask_user_question`` frame shape is identical across all three (asserted by
    ``assert_ask_user_question_shape``); the resolve MECHANISM differs by design and
    is checked per-mode against the documented contract.
  * permission gate: the tmux transport routes a permission gate THROUGH the
    AskUserQuestion bridge (resolution frame ``ask_user_resolved``), whereas the
    SDK/persistent path is a broker ``control_request`` -> ``permission_resolved``.
    This is a deliberate per-mode difference (documented in
    ``test_forge_permissions.py`` F2) — the permission cell is asserted per-mode,
    not as one shared frame type.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from niuu.ports.cli.transport import TransportCapabilities

# ─────────────────────────────────────────────────────────── modes / markers


SDK = "sdk"
PERSISTENT = "persistent_subprocess"
TMUX = "tmux_interactive"

_DEFAULT_TIER_MODES = (SDK, PERSISTENT)


def _tmux_param() -> object:
    """The tmux_interactive param — integration-tier; skips if tmux is missing."""
    marks = [pytest.mark.integration]
    if shutil.which("tmux") is None:
        marks.append(pytest.mark.skip(reason="tmux is not installed"))
    return pytest.param(TMUX, marks=marks)


def _modes(*names: str) -> list[object]:
    """Build a parametrize list: plain default-tier names + the tmux param object."""
    out: list[object] = []
    for name in names:
        if name == TMUX:
            out.append(_tmux_param())
            continue
        out.append(name)
    return out


ALL_MODES = _modes(SDK, PERSISTENT, TMUX)


# ─────────────────────────────────────────────────────────── SHARED CONTRACT


def assert_capabilities_self_consistent(caps: TransportCapabilities) -> None:
    """Every transport must advertise a SELF-CONSISTENT capability set.

    These invariants are what a frontend relies on to decide which UI affordances
    to render, so they must hold identically across modes:
      * ``steer`` implies a concrete ``steering_mode`` (never the "none" sentinel).
      * a transport that does NOT steer must leave ``steering_mode == "none"``.
      * the terminal_* surface group is coherent: any terminal affordance
        (input/keys/resize/panes) implies a terminal exists, i.e.
        ``terminal_output`` is set.
      * ``send_message`` is always true — it is the one universal capability.
    """
    assert caps.send_message is True, "every transport must support send_message"

    if caps.steer:
        assert caps.steering_mode != "none", (
            f"a steering transport must name its steering_mode; got {caps.steering_mode!r}"
        )
    if not caps.steer:
        assert caps.steering_mode == "none", (
            f"a non-steering transport must leave steering_mode 'none'; got {caps.steering_mode!r}"
        )

    terminal_affordances = (
        caps.terminal_input,
        caps.terminal_keys,
        caps.terminal_resize,
        caps.terminal_panes,
    )
    if any(terminal_affordances):
        assert caps.terminal_output is True, (
            "a transport exposing terminal input/keys/resize/panes must also expose "
            "terminal_output (the terminal surface must exist to drive it)"
        )


def assert_result_shape(frame: dict) -> None:
    """A turn-closing ``result`` frame — the shape ``message_count`` advances on.

    The frontend reads exactly these fields off the result frame, so they must be
    present and well-typed for EVERY mode:
      * ``type == "result"``
      * a ``result`` string (the assistant's final text; may be empty but present)
      * non-empty usage accounting under ``usage`` OR ``modelUsage`` — the
        per-message token/cost record the usage path folds into ``message_count``.
    """
    assert frame.get("type") == "result", f"not a result frame: {frame}"
    assert "result" in frame, f"result frame missing 'result' field: {frame}"
    usage = frame.get("usage")
    model_usage = frame.get("modelUsage")
    has_usage = isinstance(usage, dict) and bool(usage)
    has_model_usage = isinstance(model_usage, dict) and bool(model_usage)
    assert has_usage or has_model_usage, (
        "a result frame must carry non-empty usage accounting (usage or modelUsage) "
        f"so message_count can advance; got usage={usage!r} modelUsage={model_usage!r}"
    )


def assert_ask_user_question_shape(frame: dict) -> None:
    """The SHARED ask_user_question schema — identical across every mode.

    This is the single definition the frontend renders an answer card from. Drift
    here (a missing request_id, a question without options, an option without a
    label) breaks the card in exactly one transport — so it must fail that cell.
      * ``type == "ask_user_question"``
      * a non-empty ``request_id`` (the correlation id the answer is keyed by)
      * ``questions`` is a non-empty list; each question carries an ``options``
        list; every option carries a ``label``.
    """
    assert frame.get("type") == "ask_user_question", f"not an ask_user_question frame: {frame}"
    request_id = frame.get("request_id")
    assert isinstance(request_id, str) and request_id, (
        f"ask_user_question must carry a non-empty request_id; got {request_id!r}"
    )
    questions = frame.get("questions")
    assert isinstance(questions, list) and questions, (
        f"ask_user_question must carry a non-empty questions[]; got {questions!r}"
    )
    for q in questions:
        assert isinstance(q, dict), f"each question must be a dict; got {q!r}"
        options = q.get("options")
        assert isinstance(options, list) and options, (
            f"each question must carry a non-empty options[]; got {options!r}"
        )
        for opt in options:
            assert isinstance(opt, dict) and isinstance(opt.get("label"), str) and opt["label"], (
                f"each option must carry a string label; got {opt!r}"
            )


def ask_question_payload() -> dict:
    """The SAME logical question every mode is driven with (Database / Postgres;SQLite)."""
    return {
        "header": "Database",
        "question": "Which DB?",
        "options": [{"label": "Postgres"}, {"label": "SQLite"}],
        "multiSelect": False,
    }


# ─────────────────────────────────────────────────────────── mode drivers


@dataclass
class ModeSession:
    """A started transport + the machinery to drive one logical turn and observe it.

    A driver fixture yields this. Helpers are async so the same call site works for
    every mode (the tmux mode genuinely awaits a live agent; the others resolve
    against stubs)."""

    mode: str
    transport: object
    events: list[dict]
    # Drive one ordinary message turn; resolves when the turn's result is in events.
    run_message: Callable[[str], Awaitable[None]]
    # Capabilities convenience.
    caps: TransportCapabilities = field(init=False)

    def __post_init__(self) -> None:
        self.caps = self.transport.capabilities  # type: ignore[attr-defined]

    def results(self) -> list[dict]:
        return [e for e in self.events if e.get("type") == "result"]


# ---- sdk -------------------------------------------------------------------


class _SdkFakeClient:
    """Fake ClaudeSDKClient — mirrors tests/test_skuld/transports/test_sdk.py."""

    def __init__(self, responses: list[list[object]]) -> None:
        self._responses = [list(batch) for batch in responses]
        self.query = AsyncMock()
        self.interrupt = AsyncMock()
        self.set_model = AsyncMock()
        self.set_permission_mode = AsyncMock()
        self.rewind_files = AsyncMock()

    async def __aenter__(self) -> _SdkFakeClient:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def receive_response(self) -> AsyncIterator[object]:
        batch = self._responses.pop(0) if self._responses else []
        for message in batch:
            yield message


class _SdkClientFactory:
    def __init__(self, responses: list[list[object]]) -> None:
        self._responses = responses
        self.client: _SdkFakeClient | None = None
        self.options: object = None

    def __call__(self, options: object) -> _SdkFakeClient:
        self.options = options
        self.client = _SdkFakeClient(self._responses)
        return self.client


def _sdk_assistant(text: str):
    from claude_agent_sdk.types import AssistantMessage, TextBlock

    return AssistantMessage(
        content=[TextBlock(text=text)],
        model="claude-opus-4-20250514",
        usage={"input_tokens": 10, "output_tokens": 5},
        session_id="sdk-session",
    )


def _sdk_result(text: str):
    """A result message that carries BOTH usage and model_usage (so the shared
    assert_result_shape sees real accounting for the SDK mode)."""
    from claude_agent_sdk.types import ResultMessage

    return ResultMessage(
        subtype="success",
        duration_ms=12,
        duration_api_ms=8,
        is_error=False,
        num_turns=1,
        session_id="sdk-session",
        total_cost_usd=0.01,
        usage={"input_tokens": 10, "output_tokens": 5},
        result=text,
        model_usage={"claude-opus-4-20250514": {"input_tokens": 10, "output_tokens": 5}},
    )


class _AskContext:
    """Minimal can_use_tool context — SDKTransport reads only .tool_use_id."""

    def __init__(self, tool_use_id: str) -> None:
        self.tool_use_id = tool_use_id


# ---- persistent_subprocess -------------------------------------------------


class _StubStream:
    """Async stream stub backed by a queue of byte lines (from test_persistent_subprocess)."""

    def __init__(self, lines: list[bytes]) -> None:
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        for line in lines:
            self._queue.put_nowait(line)

    def push(self, line: bytes) -> None:
        self._queue.put_nowait(line)

    def close(self) -> None:
        self._queue.put_nowait(b"")

    async def readline(self) -> bytes:
        return await self._queue.get()


class _StubStdin:
    def __init__(self) -> None:
        self.buf = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.buf.extend(data)

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def close(self) -> None:
        self.closed = True

    def is_closing(self) -> bool:
        return self.closed


def _make_proc(stdout_lines: list[bytes], pid: int = 4321) -> MagicMock:
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = None
    proc.stdout = _StubStream(stdout_lines)
    proc.stderr = _StubStream([b""])
    proc.stdin = _StubStdin()
    proc.terminate = MagicMock()
    proc.wait = AsyncMock(return_value=0)
    return proc


def _persistent_system_line(session_id: str) -> bytes:
    return (
        json.dumps({"type": "system", "subtype": "init", "session_id": session_id}).encode() + b"\n"
    )


def _persistent_assistant_line(text: str) -> bytes:
    return (
        json.dumps(
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
            }
        ).encode()
        + b"\n"
    )


def _persistent_result_line(text: str) -> bytes:
    """A result line carrying real usage accounting (the live CLI emits these
    fields; the Phase-1/2 fixtures omitted them, but the parity contract requires
    usage/modelUsage to be present — which the real protocol does provide)."""
    return (
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": text,
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "modelUsage": {"claude": {"input_tokens": 10, "output_tokens": 5}},
            }
        ).encode()
        + b"\n"
    )


# ─────────────────────────────────────────────────────────── driver fixtures


async def _make_sdk_session(tmp_path, monkeypatch) -> ModeSession:
    from skuld.transports.sdk import SDKTransport

    factory = _SdkClientFactory(
        [
            [_sdk_assistant("PARITY-OK"), _sdk_result("PARITY-OK")],
            # A spare second batch for a follow-up turn (interrupt-then-resume).
            [_sdk_assistant("PARITY-AGAIN"), _sdk_result("PARITY-AGAIN")],
        ]
    )
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", factory)

    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    transport = SDKTransport(workspace_dir=str(tmp_path), ask_user_question_enabled=True)
    transport.on_event(on_event)
    await transport.start()

    async def run_message(content: str) -> None:
        await transport.send_message(content)

    return ModeSession(SDK, transport, events, run_message)


async def _make_persistent_session(tmp_path) -> tuple[ModeSession, MagicMock]:
    from skuld.transports.persistent_subprocess import PersistentSubprocessTransport

    proc = _make_proc(
        [
            _persistent_system_line("sess-parity"),
            _persistent_assistant_line("PARITY-OK"),
            _persistent_result_line("PARITY-OK"),
        ]
    )
    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    transport = PersistentSubprocessTransport(str(tmp_path), initial_prompt="")
    transport.on_event(on_event)

    spawn = AsyncMock(return_value=proc)
    spawn_patch = patch("asyncio.create_subprocess_exec", spawn)
    spawn_patch.start()
    await transport.start()

    async def run_message(content: str) -> None:
        await transport.send_message(content)

    session = ModeSession(PERSISTENT, transport, events, run_message)
    # Stash the patch + proc so the caller can stop the patch + push more lines.
    session.transport._parity_spawn_patch = spawn_patch  # type: ignore[attr-defined]
    return session, proc


# ─────────────────────────────────────────────────────────── static capability table
# What each mode legitimately supports — drives skip/xfail decisions below.

_MODE_SUPPORT = {
    SDK: {"interrupt": True, "steer": True, "ask": True, "result": True},
    PERSISTENT: {"interrupt": False, "steer": False, "ask": True, "result": True},
    TMUX: {"interrupt": True, "steer": True, "ask": True, "result": True},
}


async def _wait_until(predicate, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met before timeout")


# ═══════════════════════════════════════════════════════════ CONTRACT: capabilities


@pytest.mark.parametrize("mode", ALL_MODES)
def test_capabilities_self_consistent(mode: str) -> None:
    """Every mode advertises a self-consistent TransportCapabilities (one shared check)."""
    if mode == SDK:
        from skuld.transports.sdk import SDKTransport

        caps = SDKTransport("/tmp").capabilities
    elif mode == PERSISTENT:
        from skuld.transports.persistent_subprocess import PersistentSubprocessTransport

        caps = PersistentSubprocessTransport("/tmp", initial_prompt="").capabilities
    else:
        from skuld.transports.tmux_interactive import TmuxInteractiveTransport

        caps = TmuxInteractiveTransport(workspace_dir=".", session_id="cap-probe").capabilities

    assert_capabilities_self_consistent(caps)


@pytest.mark.parametrize("mode", ALL_MODES)
def test_capabilities_match_documented_per_mode(mode: str) -> None:
    """Each mode advertises the DOCUMENTED capability semantics (per-mode expected set).

    This pins the deliberate per-mode differences (only tmux exposes a terminal;
    persistent cannot interrupt/steer; sdk steers via interrupt_resume vs tmux's
    native) so a silent capability drift in any single transport fails its cell.
    """
    if mode == SDK:
        from skuld.transports.sdk import SDKTransport

        caps = SDKTransport("/tmp").capabilities
        assert caps.interrupt is True
        assert caps.steer is True
        assert caps.steering_mode == "interrupt_resume"
        assert caps.set_permission_mode is True
        assert caps.terminal_output is False
    elif mode == PERSISTENT:
        from skuld.transports.persistent_subprocess import PersistentSubprocessTransport

        caps = PersistentSubprocessTransport("/tmp", initial_prompt="").capabilities
        assert caps.interrupt is False
        assert caps.steer is False
        assert caps.steering_mode == "none"
        assert caps.terminal_output is False
    else:
        from skuld.transports.tmux_interactive import TmuxInteractiveTransport

        caps = TmuxInteractiveTransport(workspace_dir=".", session_id="cap-probe").capabilities
        assert caps.interrupt is True
        assert caps.steer is True
        assert caps.steering_mode == "native"
        assert caps.terminal_output is True
        assert caps.terminal_input is True
        # The deliberate tmux limitation (F-LIMIT): binary bypass, no plan/acceptEdits.
        assert caps.set_permission_mode is False


# ═══════════════════════════════════════════════════════════ CONTRACT: message -> result


@pytest.mark.parametrize("mode", ALL_MODES)
@pytest.mark.asyncio
async def test_message_yields_one_result_with_usage(mode: str, tmp_path, monkeypatch) -> None:
    """Sending a message yields exactly one result frame carrying usage accounting.

    The shared ``assert_result_shape`` is the single definition; every mode is held
    to it so ``message_count`` advances identically regardless of transport.
    """
    if mode == SDK:
        session = await _make_sdk_session(tmp_path, monkeypatch)
        try:
            await session.run_message("hello")
            results = session.results()
            assert len(results) == 1, f"sdk: expected exactly one result; got {results}"
            assert_result_shape(results[-1])
        finally:
            await session.transport.stop()
        return

    if mode == PERSISTENT:
        session, _proc = await _make_persistent_session(tmp_path)
        try:
            await session.run_message("hello")
            results = session.results()
            assert len(results) == 1, f"persistent: expected exactly one result; got {results}"
            assert_result_shape(results[-1])
        finally:
            session.transport._parity_spawn_patch.stop()  # type: ignore[attr-defined]
            await session.transport.stop()
        return

    # tmux_interactive — real broker harness + fakeagent.
    from tests.support.forge import BrokerHarness

    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
        h.transport.on_event(on_event)
        client = await h.connect()
        client.send({"type": "message", "content": "say:PARITY-OK"})
        await _wait_until(lambda: any(e.get("type") == "result" for e in events), timeout=8.0)
        # Let the idle watchdog settle so we can assert a single result for the turn.
        await asyncio.sleep(0.6)
        results = [e for e in events if e.get("type") == "result"]

    assert len(results) == 1, f"tmux: expected exactly one result for the turn; got {results}"
    assert_result_shape(results[-1])


# ═══════════════════════════════════════════════════════════ CONTRACT: interrupt


@pytest.mark.parametrize("mode", ALL_MODES)
@pytest.mark.asyncio
async def test_interrupt_cancels_and_leaves_session_reusable(
    mode: str, tmp_path, monkeypatch
) -> None:
    """Interrupt cancels an active turn and a subsequent message still completes.

    Only modes whose capabilities advertise ``interrupt`` are exercised; a mode
    that legitimately cannot interrupt (persistent_subprocess) is skipped with a
    reason rather than asserted against an invented behavior.
    """
    if not _MODE_SUPPORT[mode]["interrupt"]:
        pytest.skip(
            f"{mode} does not advertise interrupt (caps.interrupt is False) — there is "
            "no turn-cancel contract to assert; the no-op default is covered by the "
            "transport's own suite"
        )

    if mode == SDK:
        # The SDK interrupt forwards to client.interrupt(); after it the session is
        # reusable — a fresh send_message completes against the spare response batch.
        session = await _make_sdk_session(tmp_path, monkeypatch)
        try:
            await session.transport.interrupt()
            client = session.transport._client  # noqa: SLF001
            assert client is not None
            client.interrupt.assert_awaited()
            # The session remains usable: a new message completes.
            await session.run_message("again")
            assert session.results(), "sdk: session unusable after interrupt"
            assert_result_shape(session.results()[-1])
        finally:
            await session.transport.stop()
        return

    # tmux_interactive — real interrupt of a long turn, then resume.
    from tests.support.forge import BrokerHarness

    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    async with BrokerHarness(hooks=False, idle_timeout_s=0.25) as h:
        h.transport.on_event(on_event)
        client = await h.connect()

        client.send({"type": "message", "content": "work:10"})
        await _wait_until(lambda: h.transport.is_turn_active, timeout=8.0)
        await _wait_until(lambda: any("...working" in str(e) for e in events), timeout=8.0)

        client.send({"type": "interrupt"})
        await _wait_until(lambda: any(e.get("type") == "result" for e in events), timeout=8.0)
        interrupted = [e for e in events if e.get("type") == "result"][-1]
        assert interrupted.get("stop_reason") == "interrupted", (
            f"tmux: interrupt result must be marked interrupted; got {interrupted}"
        )
        await _wait_until(lambda: not h.transport.is_turn_active, timeout=4.0)
        results_after_interrupt = len([e for e in events if e.get("type") == "result"])

        # Resume: a fresh message starts AND finishes a new turn.
        client.send({"type": "message", "content": "say:resumed"})
        await _wait_until(
            lambda: len([e for e in events if e.get("type") == "result"]) > results_after_interrupt,
            timeout=8.0,
        )

    resumed = [e for e in events if e.get("type") == "result"][-1]
    assert resumed.get("stop_reason") != "interrupted", (
        f"tmux: resumed turn unexpectedly interrupted: {resumed}"
    )
    assert "resumed" in str(resumed.get("result", "")), (
        f"tmux: resumed turn lost its assistant text: {resumed}"
    )


# ═══════════════════════════════════════════════════════════ CONTRACT: steer


@pytest.mark.parametrize("mode", ALL_MODES)
@pytest.mark.asyncio
async def test_steer_does_not_restart_or_duplicate_turn(mode: str, tmp_path, monkeypatch) -> None:
    """A mid-turn steer does not restart/duplicate the turn (modes with caps.steer).

    Each steering mode keeps the SAME logical turn alive:
      * sdk steers via interrupt_resume — one interrupt, the steer text queued as
        the continuation, the original turn NOT re-issued as a fresh prompt.
      * tmux steers natively — the in-flight turn stays active and no new result is
        emitted at steer time (no restart).
    A mode without ``caps.steer`` (persistent_subprocess) is skipped with a reason.
    """
    if not _MODE_SUPPORT[mode]["steer"]:
        pytest.skip(
            f"{mode} does not advertise steer (caps.steer is False) — there is no "
            "mid-turn steer contract to assert"
        )

    if mode == SDK:
        # interrupt_resume: while a turn is active, a steer interrupts ONCE and the
        # steer text becomes the continuation prompt — the original prompt is not
        # re-issued (no duplicate/restart of the same content).
        session = await _make_sdk_session(tmp_path, monkeypatch)
        transport = session.transport
        try:
            client = transport._client  # noqa: SLF001
            assert client is not None

            async def _slow_query(_prompt: str) -> None:
                await asyncio.sleep(0)

            client.query.side_effect = _slow_query

            async def request_steer() -> None:
                while not transport.is_turn_active:
                    await asyncio.sleep(0)
                await transport.send_control("steer", content="Use option B instead")

            steer_task = asyncio.create_task(request_steer())
            await transport.send_message("Start with option A")
            await steer_task

            queries = [c.args[0] for c in client.query.await_args_list]
            assert queries[0] == "Start with option A"
            assert "Use option B instead" in queries[1], (
                f"sdk steer must continue with the steer text, not restart; queries={queries}"
            )
            # Exactly one interrupt for the single steer — not a restart loop.
            client.interrupt.assert_awaited_once()
            # The original prompt is issued ONCE (not duplicated by the steer).
            assert queries.count("Start with option A") == 1, (
                f"sdk steer duplicated the original turn prompt; queries={queries}"
            )
        finally:
            await transport.stop()
        return

    # tmux_interactive — native steer keeps the in-flight turn (no restart).
    from tests.support.forge import BrokerHarness
    from tests.support.forge.tmux_page import TmuxPage

    async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
        client = await h.connect()
        client.send({"type": "message", "content": "work:3"})
        await _wait_until(lambda: h.transport.is_turn_active, timeout=5.0)

        results_before = len(client.frames_of_type("result"))
        assert results_before == 0, "tmux: the work turn must not have closed before steering"

        await asyncio.sleep(1.0)
        assert h.transport.is_turn_active, "tmux: long turn ended before we could steer"

        client.send({"type": "message", "content": "say:STEERED", "request_id": "parity-steer"})
        await client.wait_for(
            lambda frames: any(f.get("type") == "terminal_input_sent" for f in frames),
            timeout=5.0,
        )
        await client.wait_for(
            lambda frames: any(
                f.get("type") == "user_delivered" and f.get("request_id") == "parity-steer"
                for f in frames
            ),
            timeout=5.0,
        )

        # The steer kept the SAME turn alive: still active, no new result, no interrupt.
        assert h.transport.is_turn_active, "tmux: steer must not restart the running turn"
        assert client.frames_of_type("result") == [], (
            "tmux: a mid-turn steer must not close/restart the turn (a result appeared)"
        )
        page = TmuxPage(str(h.transport._socket_path), h.transport.session_id or "")  # noqa: SLF001
        snapshot = await page.snapshot()
        assert "interrupted" not in snapshot, "tmux: steer must not interrupt the work turn"
        await page.wait_for_text("STEERED", timeout=8.0)

        # The original work turn closes exactly once (no duplicate from the steer).
        await client.wait_for(
            lambda frames: any(
                f.get("type") == "result" and f.get("result") == "done" for f in frames
            ),
            timeout=10.0,
        )
        work_results = [r for r in client.frames_of_type("result") if r.get("result") == "done"]

    assert len(work_results) == 1, (
        f"tmux: the original work turn must close exactly once; got {work_results}"
    )


# ═══════════════════════════════════════════════════════════ CONTRACT: ask_user_question


@pytest.mark.parametrize("mode", ALL_MODES)
@pytest.mark.asyncio
async def test_ask_user_question_shared_shape_and_resolve(mode: str, tmp_path, monkeypatch) -> None:
    """The SAME logical question yields a structurally-identical ask_user_question
    frame across modes, and answering it resolves with the chosen label threaded back.

    The OUTBOUND frame is held to ONE shared schema (``assert_ask_user_question_shape``)
    for every mode — this is where tmux<->sdk drift is most likely, so divergence in
    the frame shape fails the cell. The resolve MECHANISM differs by design per mode
    (documented in the module docstring) and is checked against each mode's contract.
    """
    if mode == SDK:
        from skuld.transports.sdk import SDKTransport

        events: list[dict] = []

        async def on_event(event: dict) -> None:
            events.append(event)

        transport = SDKTransport(workspace_dir=str(tmp_path), ask_user_question_enabled=True)
        transport.on_event(on_event)
        assert transport.is_alive is False  # credential-free: no client started

        ask_task = asyncio.create_task(
            transport._handle_ask_user_question(  # noqa: SLF001
                {"questions": [ask_question_payload()]}, _AskContext("sdk-tooluse-1")
            )
        )
        await _wait_until(
            lambda: any(e.get("type") == "ask_user_question" for e in events), timeout=3.0
        )
        ask = next(e for e in events if e.get("type") == "ask_user_question")

        # SHARED SHAPE — the one definition every mode is checked against.
        assert_ask_user_question_shape(ask)
        assert [o["label"] for o in ask["questions"][0]["options"]] == ["Postgres", "SQLite"]

        # Resolve contract (SDK mechanism): resolve_question threads the chosen
        # label into the deny-with-message tool_result the model reads.
        assert transport.resolve_question(ask["request_id"], [{"answer": "SQLite"}]) is True
        result = await asyncio.wait_for(ask_task, timeout=3.0)
        assert "SQLite" in getattr(result, "message", ""), (
            f"sdk: the chosen label must reach the model's tool_result; got {result!r}"
        )
        # Resolving an unknown id is a clean miss (broker attention bookkeeping relies on it).
        assert transport.resolve_question("does-not-exist", [{"answer": "x"}]) is False
        return

    if mode == PERSISTENT:
        from skuld.transports.persistent_subprocess import PersistentSubprocessTransport

        transport = PersistentSubprocessTransport(str(tmp_path), ask_user_question_enabled=True)
        proc = _make_proc([])
        transport._process = proc  # noqa: SLF001
        events: list[dict] = []

        async def on_event(event: dict) -> None:
            events.append(event)

        transport.on_event(on_event)

        # Drive the SAME logical question through the stdio control protocol's
        # can_use_tool(AskUserQuestion) path.
        task = asyncio.create_task(
            transport._handle_control_request(  # noqa: SLF001
                {
                    "request_id": "persistent-req-1",
                    "request": {
                        "subtype": "can_use_tool",
                        "tool_name": "AskUserQuestion",
                        "tool_use_id": "toolu_parity",
                        "input": {"questions": [ask_question_payload()]},
                    },
                }
            )
        )
        await _wait_until(
            lambda: any(e.get("type") == "ask_user_question" for e in events), timeout=3.0
        )
        ask = next(e for e in events if e.get("type") == "ask_user_question")

        # SHARED SHAPE — identical schema to sdk + tmux.
        assert_ask_user_question_shape(ask)
        assert [o["label"] for o in ask["questions"][0]["options"]] == ["Postgres", "SQLite"]

        # Resolve contract (persistent mechanism): ask_user_answer -> deny-with-message.
        await transport.send_control(
            "ask_user_answer",
            request_id=ask["request_id"],
            answers=[{"answer": "SQLite"}],
        )
        await asyncio.wait_for(task, timeout=3.0)
        written = json.loads(bytes(proc.stdin.buf).decode())
        response = written["response"]["response"]
        assert response["behavior"] == "deny", (
            f"persistent: resolve must deny-with-message; {response}"
        )
        assert "SQLite" in response["message"], (
            f"persistent: the chosen label must reach the model's tool_result; got {response}"
        )
        return

    # tmux_interactive — the real bridge surfaces + resolves via ask_user_resolved.
    from tests.support.forge import BrokerHarness

    async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
        client = await h.connect()
        client.send({"type": "message", "content": "ask:Database|Which DB?|Postgres;SQLite"})
        ask = await client.wait_for_type("ask_user_question", timeout=8.0)

        # SHARED SHAPE — identical schema to sdk + persistent.
        assert_ask_user_question_shape(ask)
        assert [o["label"] for o in ask["questions"][0]["options"]] == ["Postgres", "SQLite"]

        request_id = ask["request_id"]
        client.ask_user_answer(request_id, [{"answer": "SQLite"}])

        # Resolve contract (tmux mechanism): ask_user_resolved carries the chosen label.
        await client.wait_for(
            lambda frames: any(
                f.get("type") == "ask_user_resolved" and f.get("request_id") == request_id
                for f in frames
            ),
            timeout=8.0,
        )
        resolved = [
            f
            for f in client.frames_of_type("ask_user_resolved")
            if f.get("request_id") == request_id
        ]
        assert resolved, "tmux: an ask_user_resolved must be emitted for the answered question"
        assert resolved[-1].get("decision") == "SQLite", (
            f"tmux: the resolved decision must be the chosen label; got {resolved[-1]}"
        )


# ═══════════════════════════════════════════════════════════ CONTRACT: permission gate


@pytest.mark.parametrize("mode", ALL_MODES)
@pytest.mark.asyncio
async def test_permission_gate_shape_and_resolution(mode: str, tmp_path, monkeypatch) -> None:
    """A permission gate surfaces a request frame + resolves — per the mode's
    DOCUMENTED equivalent.

    Deliberate per-mode difference (NOT drift — see module docstring + F2):
      * sdk        — advertises set_permission_mode; the gate path is the broker
        ``control_request`` -> ``permission_resolved`` (covered for the broker in
        test_forge_permissions F6). At the transport level the SDK forwards
        set_permission_mode; we assert that contract here (the can_use_tool gate +
        permission_resolved is a broker concern, asserted in F6, not re-derived).
      * persistent — same broker control_request/permission_resolved family; the
        transport-level gate is the stdio can_use_tool protocol (covered in
        test_persistent_subprocess). Skipped here with a pointer to avoid a
        duplicate, weaker assertion.
      * tmux       — routes the gate THROUGH the AskUserQuestion bridge, so the
        resolution frame is ``ask_user_resolved`` (decision='deny' on a Deny), NOT
        ``permission_resolved``. Asserted as the real, current contract.
    """
    if mode == SDK:
        from skuld.transports.sdk import SDKTransport

        # The SDK transport's permission contract is set_permission_mode (the gate
        # decision itself flows through the broker control_request path — covered by
        # test_forge_permissions F6). Assert the transport-level contract: it
        # advertises + forwards the mode switch.
        factory = _SdkClientFactory([[]])
        monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", factory)
        transport = SDKTransport(workspace_dir=str(tmp_path))
        assert transport.capabilities.set_permission_mode is True
        await transport.start()
        await transport.send_control("set_permission_mode", permissionMode="plan")
        assert factory.client is not None
        factory.client.set_permission_mode.assert_awaited_once_with("plan")
        await transport.stop()
        return

    if mode == PERSISTENT:
        pytest.skip(
            "persistent_subprocess: the permission gate is the stdio can_use_tool "
            "control protocol (covered in test_persistent_subprocess) and the broker "
            "control_request -> permission_resolved family (covered in "
            "test_forge_permissions F6). No distinct transport-level permission frame "
            "to re-assert here without duplicating a weaker check. Followup: if a "
            "shared permission_resolved frame is ever surfaced by the persistent "
            "transport directly, fold it into this parity cell."
        )

    # tmux_interactive — the gate is bridged through AskUserQuestion; Deny resolves
    # via ask_user_resolved(decision='deny'). This is the documented per-mode
    # difference vs the SDK/broker permission_resolved family.
    from tests.support.forge import BrokerHarness

    async with BrokerHarness(
        hooks=True,
        skip_permissions=False,
        idle_timeout_s=0.3,
        ask_user_question_enabled=True,
        boot="perm:Bash|rm -rf build",
    ) as h:
        client = await h.connect()

        perm = await client.wait_for_type("claude_permission_request", timeout=8.0)
        assert perm.get("tool_name") == "Bash"

        ask = await client.wait_for_type("ask_user_question", timeout=8.0)
        # The bridged gate is a normal ask_user_question — same shared shape.
        assert_ask_user_question_shape(ask)
        opts = [o["label"] for o in ask["questions"][0]["options"]]
        assert opts == ["Allow", "Allow & don't ask again", "Deny"], (
            f"tmux: the permission gate must offer the fixed 3-option shape; got {opts}"
        )

        request_id = ask["request_id"]
        client.ask_user_answer(request_id, [{"answer": "Deny"}])
        await client.wait_for(
            lambda frames: any(
                f.get("type") == "ask_user_resolved"
                and f.get("request_id") == request_id
                and f.get("decision") == "deny"
                for f in frames
            ),
            timeout=8.0,
        )


# ═══════════════════════════════════════════════════════════ CONTRACT: durable-log rebuild


def _rebuild_from_frames(frames: list[dict]) -> list[dict]:
    """Feed a turn's frames through rebuild_turns and return the reconstructed turns.

    Builds minimal SessionLogEntry rows (seq-ordered) the way the durable log would
    persist them, so every mode's frames are reduced by the SAME pure reducer the
    crash-reload path uses.
    """
    import uuid

    from volundr.domain.models import SessionLogEntry
    from volundr.domain.services.transcript_rebuild import rebuild_turns

    session_uuid = uuid.uuid4()
    entries: list[SessionLogEntry] = []
    for seq, frame in enumerate(frames):
        entries.append(
            SessionLogEntry(
                session_id=session_uuid,
                seq=seq,
                kind=str(frame.get("type", "unknown")),
                payload=frame,
                ts=None,
                role=None,
                request_id=frame.get("request_id"),
            )
        )
    return rebuild_turns(entries).turns


@pytest.mark.parametrize("mode", ALL_MODES)
@pytest.mark.asyncio
async def test_durable_log_rebuild_reproduces_turn(mode: str, tmp_path, monkeypatch) -> None:
    """The frames a turn emits, fed to rebuild_turns, reproduce the turn for every mode.

    The durable-log rebuild is the crash-reload path; it must reconstruct the
    assistant's text identically no matter which transport produced the frames. We
    capture each mode's real per-turn frames and reduce them with the SAME reducer.
    """
    if mode == SDK:
        session = await _make_sdk_session(tmp_path, monkeypatch)
        try:
            await session.run_message("hello")
            frames = list(session.events)
        finally:
            await session.transport.stop()

    elif mode == PERSISTENT:
        session, _proc = await _make_persistent_session(tmp_path)
        try:
            await session.run_message("hello")
            frames = list(session.events)
        finally:
            session.transport._parity_spawn_patch.stop()  # type: ignore[attr-defined]
            await session.transport.stop()

    else:
        from tests.support.forge import BrokerHarness

        events: list[dict] = []

        async def on_event(event: dict) -> None:
            events.append(event)

        async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
            h.transport.on_event(on_event)
            client = await h.connect()
            client.send({"type": "message", "content": "say:PARITY-OK"})
            await _wait_until(lambda: any(e.get("type") == "result" for e in events), timeout=8.0)
            await asyncio.sleep(0.4)
            frames = list(events)

    turns = _rebuild_from_frames(frames)
    assert turns, f"{mode}: rebuild produced no turns from {len(frames)} frames"
    # The assistant's text must be reproduced — locate the assistant turn carrying it.
    assistant_turns = [t for t in turns if t.get("role") == "assistant"]
    assert assistant_turns, f"{mode}: rebuild produced no assistant turn; turns={turns}"
    combined = " ".join(str(t.get("content", "")) for t in assistant_turns)
    assert "PARITY-OK" in combined, (
        f"{mode}: rebuild lost the assistant text 'PARITY-OK'; reconstructed={combined!r}"
    )
