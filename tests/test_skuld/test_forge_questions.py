"""Scenario Group E — ask_user_question in tmux (the user-reported breakage).

Every tmux scenario here drives a REAL ``skuld.broker.Broker`` wired to a REAL
``TmuxInteractiveTransport`` running ``fakeagent`` inside tmux, with an in-process
fake browser client. They lock the CLI-mode questions bridge end to end — the
path the user reported as broken ("the question is not sent to the frontend in
tmux"):

  * E1 — an ``ask:`` surfaces a structured ``ask_user_question`` frame to the
    client (request_id + questions[].options) AND the broker tracks it in
    ``_pending_ask_user_questions`` for reconnect replay. Proves the question DOES
    reach the frontend. E1b asserts the ``awaiting_input`` state flip separately —
    this exposed a real bug (the tool_use frame's ``active`` report clobbered
    ``awaiting_input``); now fixed in the broker and guarded against regression.
  * E2 — answering the NON-default option translates to the right digit: the
    bridge reads the live menu, ``_match_menu_digit`` picks the matching row, the
    digit is typed, the agent receives that choice, and ``ask_user_resolved``
    carries the chosen label after the correlated native result.
  * E3 — menu-not-yet-rendered RACE (was a real product bug): with the menu
    rendered ~0.4s AFTER the hook, answering immediately used to capture an EMPTY
    menu and blindly press "1"+Enter (the default). The bridge now bound-polls the
    live menu before pressing the chosen digit; this test guards that fix.
  * E4 — multiSelect: digits toggle each requested checkbox, arrows focus the
    unnumbered Submit, Enter opens final review, and digit 1 commits. Both
    consumed selections must appear in the matching native tool result.
  * E5 — the Deny path sends ``Escape`` (not a digit) and resolves ``deny``.
  * E6 — SDK<->tmux parity (credential-free): the SAME logical question through the
    SDK transport (fake client) and through tmux emits an ``ask_user_question``
    frame with the same structural shape and the same ``ask_user_resolved`` /
    resolve contract. Drift is documented.
  * E7 — turn end clears a stale prompt: an unanswered question, then the turn
    ends → the pending tty prompt is resolved+cleared and not replayed later.

The tmux scenarios are @pytest.mark.integration (default addopts deselect them)
and skip when tmux is unavailable. Run them on the real-tmux tier:

    SKULD__TMUX_REMOTE_CONTROL=0 uv run pytest -m integration \
        tests/test_skuld/test_forge_questions.py -p no:cacheprovider -q -rs

E6's SDK half and E1/E7 unit-style assertions run on the default tier.
"""

from __future__ import annotations

import asyncio
import shutil

import pytest

from tests.support.forge import BrokerHarness
from tests.support.forge.tmux_page import TmuxPage


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


def _page(h: BrokerHarness) -> TmuxPage:
    transport = h.transport
    return TmuxPage(
        str(transport._socket_path),  # noqa: SLF001 - test seam
        transport._session_name,
    )


def _ask_frames(frames: list[dict]) -> list[dict]:
    return [f for f in frames if f.get("type") == "ask_user_question"]


def _resolved_frames(frames: list[dict]) -> list[dict]:
    return [f for f in frames if f.get("type") == "ask_user_resolved"]


# --------------------------------------------------------------------------- E1


@pytest.mark.integration
@pytest.mark.tmux
@pytest.mark.asyncio
async def test_e1_question_surfaces_to_frontend_and_is_tracked() -> None:
    """E1: an ask: surfaces a structured ask_user_question to the client + is tracked.

    This is the user-reported breakage ("the question is not sent to the frontend
    in tmux"). We assert it IS sent: a structured frame with request_id +
    questions[].options reaches the browser, AND the broker tracks the question in
    _pending_ask_user_questions for reconnect replay (so a client joining while the
    agent is blocked still gets the answerable card).

    NOTE: the awaiting_input STATE flip is asserted separately in
    test_e1b_* — for the AskUserQuestion tool it is a known product bug (the
    state is clobbered back to active by the tool_use 'assistant' frame).
    """
    _require_tmux()

    async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
        client = await h.connect()

        client.send(
            {
                "type": "message",
                "content": "ask:Database|Which DB?|Postgres;SQLite",
            }
        )

        # The structured question reaches the browser client.
        ask = await client.wait_for_type("ask_user_question", timeout=8.0)

        assert ask.get("event_type") == "ask_user_question", (
            f"the frame must carry event_type=ask_user_question (the awaiting_input "
            f"branch + iOS arm switch on it); got {ask}"
        )
        request_id = ask.get("request_id")
        assert request_id, f"the surfaced question must carry a request_id; got {ask}"
        questions = ask.get("questions")
        assert isinstance(questions, list) and questions, (
            f"the frame must carry questions[]; got {ask}"
        )
        opts = [o["label"] for o in questions[0]["options"]]
        assert opts == ["Postgres", "SQLite"], (
            f"the agent's options must pass through verbatim; got {opts}"
        )

        # The blocked question is tracked for reconnect replay (the server-side
        # proof a reconnecting client still gets the answerable question).
        await _wait_until(
            lambda: request_id in h.broker._pending_ask_user_questions,  # noqa: SLF001
            timeout=5.0,
        )

    # Confirm it was a single surfaced question (no duplicate fan-out).
    assert len(_ask_frames(client.frames)) == 1, (
        f"exactly one ask_user_question must surface; got {_ask_frames(client.frames)}"
    )


@pytest.mark.integration
@pytest.mark.tmux
@pytest.mark.asyncio
async def test_e1b_ask_user_question_flips_session_to_awaiting_input() -> None:
    """E1b: an AskUserQuestion gate must pin the session at awaiting_input.

    The correct contract: while the agent is blocked on a human answer the session
    reports awaiting_input (so the platform knows it needs the user).

    Regression guard for the awaiting_input-clobber bug: the tmux bridge surfaces
    the structured ask_user_question (broker -> _enter_attention -> awaiting_input)
    and then emits the AskUserQuestion tool_use as an assistant frame. Previously
    Broker._handle_cli_event scheduled an 'active' report on that assistant frame
    that ran AFTER and CLOBBERED awaiting_input, pinning the session at 'active'
    while genuinely blocked on the human. Fixed by marking _pending_attention
    synchronously when the question arrives and suppressing the assistant-frame
    'active' report while a gate is pending (src/skuld/broker.py).
    """
    _require_tmux()

    async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
        client = await h.connect()
        client.send(
            {
                "type": "message",
                "content": "ask:Database|Which DB?|Postgres;SQLite",
            }
        )
        await client.wait_for_type("ask_user_question", timeout=8.0)

        # The session must reflect that it is blocked on the human.
        await _wait_until(
            lambda: h.broker._activity_state == "awaiting_input",  # noqa: SLF001
            timeout=5.0,
        )


# --------------------------------------------------------------------------- E2


@pytest.mark.integration
@pytest.mark.tmux
@pytest.mark.asyncio
async def test_e2_answer_translates_to_right_digit_and_resolves() -> None:
    """E2: answering the non-default option presses the matched digit only.

    The agent renders [1. Postgres, 2. SQLite]; the human chooses SQLite (row 2).
    Assert _match_menu_digit picks 2, the digit keystroke lands, the agent
    receives that choice ("chose: SQLite" on the pane), and ask_user_resolved
    carries the chosen label.
    """
    _require_tmux()

    async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
        client = await h.connect()

        client.send(
            {
                "type": "message",
                "content": "ask:Database|Which DB?|Postgres;SQLite",
            }
        )
        ask = await client.wait_for_type("ask_user_question", timeout=8.0)
        request_id = ask["request_id"]

        # The on-screen menu must be live before we answer (E2 is the non-race
        # path: the menu is rendered first at delay 0).
        page = _page(h)
        await page.wait_for_text("2. SQLite", timeout=8.0)
        rows = await page.menu_rows()
        assert TmuxPage.match_digit("SQLite", rows) == 2, (
            f"the matcher must map 'SQLite' to row 2; rows={rows}"
        )

        # Answer the NON-default option through the broker inbound path.
        client.ask_user_answer(request_id, [{"answer": "SQLite"}])

        # A native single-choice digit advances immediately without Enter.
        await page.wait_for_text("chose: SQLite", timeout=8.0)

        # ask_user_resolved carries the chosen label, correlated by request_id.
        await client.wait_for(
            lambda frames: any(
                f.get("type") == "ask_user_resolved" and f.get("request_id") == request_id
                for f in frames
            ),
            timeout=5.0,
        )
        resolved = [f for f in _resolved_frames(client.frames) if f.get("request_id") == request_id]
        assert resolved, "an ask_user_resolved must be emitted for the answered question"
        assert resolved[-1].get("decision") == "SQLite", (
            f"the resolved decision must be the chosen label; got {resolved[-1]}"
        )
        assert resolved[-1].get("accepted") is True
        assert _native_answers(client.frames, "Which DB?") == ["SQLite"]

        # The answered question is dropped from the reconnect-replay tracking so a
        # later client does not re-surface it.
        await _wait_until(
            lambda: request_id not in h.broker._pending_ask_user_questions,  # noqa: SLF001
            timeout=5.0,
        )


# --------------------------------------------------------------------------- E3


@pytest.mark.integration
@pytest.mark.tmux
@pytest.mark.asyncio
async def test_e3_menu_not_yet_rendered_race_waits_for_menu() -> None:
    """E3: answering before the menu renders must still press the CHOSEN digit.

    With a menu-render delay the hook fires first and the numbered menu appears
    ~0.4s later. The browser answers the NON-default option immediately. The
    correct contract: the bridge waits for the menu to render (bound-polls
    _capture_menu_rows_wait up to SKULD__TMUX_MENU_RENDER_WAIT_SECONDS), then
    presses the chosen row's digit — so the agent receives the chosen option, not
    the default. Regression guard for the menu-render race: previously the bridge
    captured an empty menu once and pressed the default '1'
    (src/skuld/transports/tmux_interactive.py).
    """
    _require_tmux()

    async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
        client = await h.connect()

        # menudelay:0.4 -> the hook fires first, the menu renders 0.4s later.
        client.send(
            {
                "type": "message",
                "content": "menudelay:0.4 ;; ask:Database|Which DB?|Postgres;SQLite",
            }
        )
        ask = await client.wait_for_type("ask_user_question", timeout=8.0)
        request_id = ask["request_id"]

        # Answer IMMEDIATELY — the on-screen menu is not rendered yet (race).
        page = _page(h)
        rows_at_answer = await page.menu_rows()
        assert rows_at_answer == [], (
            f"precondition: the menu must NOT be on screen yet for the race; got {rows_at_answer}"
        )
        client.ask_user_answer(request_id, [{"answer": "SQLite"}])

        # CORRECT behavior: the agent ends up choosing SQLite (the bridge waited
        # for the menu and pressed digit 2). The buggy behavior chooses Postgres.
        await page.wait_for_text("chose: SQLite", timeout=8.0)


# --------------------------------------------------------------------------- E4


@pytest.mark.integration
@pytest.mark.tmux
@pytest.mark.asyncio
async def test_e4_multiselect_maps_selection_then_submit() -> None:
    """E4: real PTY input must consume both checkboxes and final review.

    The fake implements the captured native Colors widget: digits toggle without
    moving focus; Enter on a checkbox toggles it, so prematurely pressing Enter
    cannot fabricate successful completion. Proof comes from the actual consumed
    native tool response, correlated to this question's session and tool IDs.
    """
    _require_tmux()

    async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
        client = await h.connect()

        client.send(
            {
                "type": "message",
                "content": "ask_multi:Colors|Which colors?|Blue;Amber;Violet",
            }
        )
        ask = await client.wait_for_type("ask_user_question", timeout=8.0)
        request_id = ask["request_id"]

        page = _page(h)
        await page.wait_for_text("3. [ ] Violet", timeout=8.0)
        assert ask["questions"][0]["multiSelect"] is True
        keys_before = len(_key_sends(client.frames))
        client.ask_user_answer(
            request_id,
            [{"question": "Which colors?", "answer": ["Violet", "Blue"], "option_indexes": [2, 0]}],
        )
        await client.wait_for(
            lambda frames: any(
                f.get("request_id") == request_id and f.get("accepted") is True
                for f in _resolved_frames(frames)
            ),
            timeout=8.0,
        )
        native = _native_answers(client.frames, "Which colors?")
        assert len(native) == 1
        assert set(native[0].split(", ")) == {"Violet", "Blue"}
        assert _key_sends(client.frames)[keys_before:] == [
            "1",
            "3",
            "Down",
            "Down",
            "Down",
            "Down",
            "Enter",
            "1",
        ]
        assert request_id not in h.broker._pending_ask_user_questions


def _native_answers(frames: list[dict], question: str) -> list[str]:
    """Read the CLI's HTTP proof, checking its identity against the actual PreToolUse."""
    from uuid import UUID

    payloads = [f["payload"] for f in frames if f.get("type") == "claude_hook"]
    asks = {
        (p["session_id"], p["tool_use_id"])
        for p in payloads
        if p.get("hook_event_name") == "PreToolUse" and p.get("tool_name") == "AskUserQuestion"
    }
    results = []
    for payload in payloads:
        if (
            payload.get("hook_event_name") != "PostToolUse"
            or payload.get("tool_name") != "AskUserQuestion"
        ):
            continue
        assert (payload["session_id"], payload["tool_use_id"]) in asks
        assert str(UUID(payload["session_id"])) == payload["session_id"]
        answers = payload["tool_response"]["answers"]
        if question in answers:
            results.append(answers[question])
    return results


@pytest.mark.integration
@pytest.mark.tmux
@pytest.mark.asyncio
async def test_e4b_other_text_is_consumed_before_native_acceptance() -> None:
    """The old digit/Enter/paste sequence cancels this native-style Other editor."""
    _require_tmux()
    async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
        client = await h.connect()
        client.send({"type": "message", "content": "ask:Label|Which label?|Blue;Amber"})
        ask = await client.wait_for_type("ask_user_question", timeout=8.0)
        text = "Copper multi café 東京"
        client.ask_user_answer(ask["request_id"], [{"answer": text, "free_text": text}])
        await client.wait_for(
            lambda frames: any(
                f.get("request_id") == ask["request_id"] and f.get("accepted") is True
                for f in _resolved_frames(frames)
            ),
            timeout=8.0,
        )
        assert _native_answers(client.frames, "Which label?") == [text]
        await _page(h).wait_for_text(f"chose: {text}", timeout=5.0)


@pytest.mark.integration
@pytest.mark.tmux
@pytest.mark.asyncio
async def test_e4c_premature_other_enter_does_not_fake_success() -> None:
    """The captured failing digit/Enter sequence rejects the empty Other editor.

    Drive the original buggy keys directly into the real PTY. The fake must
    report the actual cancellation, never derive a successful answer from the
    structured question or receipt it is expected to produce.
    """
    _require_tmux()
    async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
        client = await h.connect()
        client.send({"type": "message", "content": "ask:Label|Which label?|Blue;Amber"})
        ask = await client.wait_for_type("ask_user_question", timeout=8.0)
        page = _page(h)
        await page.wait_for_text("3. Type something.", timeout=5.0)
        await page.press("3")
        await page.wait_for_text("ctrl+g to edit in Vim", timeout=5.0)
        await page.press("Enter")
        await page.wait_for_text("chose: cancelled", timeout=5.0)
        await client.wait_for(
            lambda frames: any(
                frame.get("request_id") == ask["request_id"]
                and frame.get("decision") == "turn_ended"
                for frame in _resolved_frames(frames)
            ),
            timeout=5.0,
        )
        assert _native_answers(client.frames, "Which label?") == ["cancelled"]
        assert not any(
            frame.get("request_id") == ask["request_id"] and frame.get("accepted") is True
            for frame in _resolved_frames(client.frames)
        )


# --------------------------------------------------------------------------- E5


@pytest.mark.integration
@pytest.mark.tmux
@pytest.mark.asyncio
async def test_e4d_mixed_checkbox_other_consumes_exact_native_answer() -> None:
    """The actual iOS mixed shape follows the recorded native focus/paste/submit flow."""
    _require_tmux()
    async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
        client = await h.connect()
        client.send(
            {"type": "message", "content": "ask_multi:Features|Which features?|Auth;Search"}
        )
        ask = await client.wait_for_type("ask_user_question", timeout=8.0)
        page = _page(h)
        await page.wait_for_text("3. [ ] Type something", timeout=5.0)
        keys_before = len(_key_sends(client.frames))
        text = "Copper focus café 東京"
        client.ask_user_answer(
            ask["request_id"],
            [
                {
                    "question": "Which features?",
                    "answer": ["Auth", text],
                    "free_text": text,
                    "option_indexes": [0],
                }
            ],
        )
        await client.wait_for(
            lambda frames: any(
                frame.get("request_id") == ask["request_id"] and frame.get("accepted") is True
                for frame in _resolved_frames(frames)
            ),
            timeout=8.0,
        )
        assert _native_answers(client.frames, "Which features?") == [f"Auth, {text}"]
        assert _key_sends(client.frames)[keys_before:] == [
            "1",
            "Down",
            "Down",
            "Down",
            "Enter",
            "1",
        ]
        assert (
            len(
                [
                    frame
                    for frame in _resolved_frames(client.frames)
                    if frame.get("request_id") == ask["request_id"]
                ]
            )
            == 1
        )
        assert ask["request_id"] not in h.broker._pending_ask_user_questions
        await page.wait_for_text(f"chose: Auth, {text}", timeout=5.0)


@pytest.mark.integration
@pytest.mark.tmux
@pytest.mark.asyncio
async def test_e4e_mixed_native_digits_do_not_focus_and_enter_unchecks_text() -> None:
    """Guard the two observed native traps independently of structured answer code."""
    _require_tmux()
    async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
        client = await h.connect()
        client.send(
            {"type": "message", "content": "ask_multi:Features|Which features?|Auth;Search"}
        )
        ask = await client.wait_for_type("ask_user_question", timeout=8.0)
        page = _page(h)
        await page.wait_for_text("3. [ ] Type something", timeout=5.0)
        await page.press("1")
        await page.press("3")
        snapshot = await page.wait_for_text("3. [✔] Type something", timeout=5.0)
        assert "❯ 1. [✔] Auth" in snapshot and "ctrl+g to edit" not in snapshot
        await page.press("Down")
        await page.press("Down")
        await page.wait_for_text("❯ 3. [✔] Type something", timeout=5.0)
        text = "Copper focus café 東京"
        # Literal paste without the page helper's automatic Enter.
        await h.transport.send_control("terminal_input", text=text, enter=False)
        await page.wait_for_text(f"❯ 3. [✔] {text}", timeout=5.0)
        await page.press("Enter")
        snapshot = await page.wait_for_text(f"❯ 3. [ ] {text}", timeout=5.0)
        assert "Review your answers" not in snapshot
        assert _native_answers(client.frames, "Which features?") == []
        assert not any(
            frame.get("request_id") == ask["request_id"] and frame.get("accepted") is True
            for frame in _resolved_frames(client.frames)
        )


def _key_sends(frames: list[dict]) -> list[str]:
    return [f.get("key") for f in frames if f.get("type") == "terminal_key_sent"]


@pytest.mark.integration
@pytest.mark.tmux
@pytest.mark.asyncio
async def test_e5_deny_path_sends_escape_not_a_digit() -> None:
    """E5: choosing Deny on a permission gate sends Escape and resolves deny.

    A perm: gate surfaces the fixed Allow / Allow & don't ask again / Deny shape.
    Choosing Deny must press Escape (the universal cancel), NOT a numbered digit,
    and ask_user_resolved must carry decision='deny'.

    We assert the product contract via the broker-forwarded terminal_key_sent
    frames (the real CLI acts on a raw ESC immediately; the harness fakeagent
    reads line-oriented stdin and cannot consume a bare ESC char, so the keystroke
    + resolved frame are the ground truth, not a pane echo).
    """
    _require_tmux()

    async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
        client = await h.connect()

        client.send({"type": "message", "content": "perm:Bash|rm -rf build"})
        ask = await client.wait_for_type("ask_user_question", timeout=8.0)
        request_id = ask["request_id"]
        opts = [o["label"] for o in ask["questions"][0]["options"]]
        assert opts == ["Allow", "Allow & don't ask again", "Deny"], (
            f"the permission gate must offer the fixed 3-option shape; got {opts}"
        )

        # Wait until the on-screen menu is live, then snapshot the keystrokes
        # already sent (the menu render itself sends no keys) so we can prove the
        # Deny answer adds an Escape and never a digit.
        page = _page(h)
        await page.wait_for_text("3. Deny", timeout=8.0)
        keys_before = _key_sends(client.frames)

        # Deny the gate.
        client.ask_user_answer(request_id, [{"answer": "Deny"}])

        # ask_user_resolved carries decision='deny'.
        await client.wait_for(
            lambda frames: any(
                f.get("type") == "ask_user_resolved"
                and f.get("request_id") == request_id
                and f.get("decision") == "deny"
                for f in frames
            ),
            timeout=5.0,
        )

        # The Deny answer pressed Escape (the universal cancel) and NOT a digit.
        new_keys = _key_sends(client.frames)[len(keys_before) :]
        assert "Escape" in new_keys, (
            f"Deny must press Escape; new keys sent for the answer: {new_keys}"
        )
        assert not any(k and k.isdigit() for k in new_keys), (
            f"Deny must NOT press a numbered digit (that would be an allow); got {new_keys}"
        )


# --------------------------------------------------------------------------- E6


class _AskContext:
    """Minimal can_use_tool context — SDKTransport reads only .tool_use_id."""

    def __init__(self, tool_use_id: str) -> None:
        self.tool_use_id = tool_use_id


@pytest.mark.asyncio
async def test_e6_sdk_emits_same_ask_user_question_shape_and_resolves(tmp_path) -> None:
    """E6 (SDK half): the SDK transport emits a structurally-identical question.

    Credential-free: construct SDKTransport (no live client), call the in-product
    seam _handle_ask_user_question with the SAME logical question, capture the
    emitted ask_user_question frame, then resolve it via resolve_question (the
    SDK's analogue of the tmux ask_user_answer keystroke translation).

    Parity contract asserted here (the tmux half is E1/E2):
      * frame type == "ask_user_question"
      * carries a request_id + tool_use_id
      * carries questions[] passed through verbatim (same header/options shape)
      * resolve_question(request_id, answers) unblocks the agent and the resolved
        answers are threaded into the tool_result the model reads.
    """
    from skuld.transports.sdk import SDKTransport

    events: list[dict] = []

    async def _on_event(event: dict) -> None:
        events.append(event)

    transport = SDKTransport(workspace_dir=str(tmp_path), ask_user_question_enabled=True)
    transport.on_event(_on_event)
    # Credential-free guarantee: no live client / process was started.
    assert transport.is_alive is False

    questions = [
        {
            "header": "Database",
            "question": "Which DB?",
            "options": [{"label": "Postgres"}, {"label": "SQLite"}],
            "multiSelect": False,
        }
    ]

    # Drive the question through the in-product permission seam; it blocks on a
    # future until a client answers, so resolve it concurrently.
    ask_task = asyncio.create_task(
        transport._handle_ask_user_question(  # noqa: SLF001
            {"questions": questions}, _AskContext("sdk-tooluse-1")
        )
    )

    # Wait for the structured frame, then answer the NON-default option.
    await _wait_until(
        lambda: any(e.get("type") == "ask_user_question" for e in events),
        timeout=3.0,
    )
    ask = next(e for e in events if e.get("type") == "ask_user_question")

    # --- structural parity with the tmux frame (E1) ---
    assert ask["type"] == "ask_user_question"
    request_id = ask.get("request_id")
    assert request_id, f"the SDK question must carry a request_id; got {ask}"
    assert ask.get("tool_use_id") == "sdk-tooluse-1", (
        f"the SDK question must carry the tool_use_id; got {ask}"
    )
    assert ask["questions"] == questions, (
        f"the SDK must pass questions[] through verbatim (tmux parity); got {ask['questions']}"
    )
    opts = [o["label"] for o in ask["questions"][0]["options"]]
    assert opts == ["Postgres", "SQLite"]

    # --- resolve contract parity: resolve_question unblocks + threads the answer ---
    assert transport.resolve_question(request_id, [{"answer": "SQLite"}]) is True, (
        "resolve_question must find + resolve the pending question"
    )
    result = await asyncio.wait_for(ask_task, timeout=3.0)

    # The chosen option is threaded into the tool_result the model reads (the SDK
    # deny-with-answer mechanism). This is the SDK analogue of the tmux
    # ask_user_resolved decision carrying the chosen label.
    message = getattr(result, "message", "")
    assert "SQLite" in message, (
        f"the resolved answer must reach the model's tool_result; got {message!r}"
    )

    # Resolving an unknown id is a clean miss (no crash) — the broker-side
    # _pending_ask_user_questions / attention bookkeeping relies on this contract.
    assert transport.resolve_question("does-not-exist", [{"answer": "x"}]) is False


# --------------------------------------------------------------------------- E7


@pytest.mark.integration
@pytest.mark.tmux
@pytest.mark.asyncio
async def test_e7_turn_end_clears_stale_prompt_and_does_not_replay() -> None:
    """E7: an unanswered question is cleared on turn end and not replayed later.

    Leave an ask: unanswered; let the turn end (the Stop hook fires after the
    agent's blocked read is released). Assert the pending tty prompt is resolved
    (ask_user_resolved with reason 'turn_ended'), the transport's
    _pending_tty_prompts is empty, and the broker drops its reconnect-replay entry
    so a later client does NOT re-surface the stale question.
    """
    _require_tmux()

    async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
        client = await h.connect()

        client.send(
            {
                "type": "message",
                "content": "ask:Database|Which DB?|Postgres;SQLite",
            }
        )
        ask = await client.wait_for_type("ask_user_question", timeout=8.0)
        request_id = ask["request_id"]
        assert request_id in h.broker._pending_ask_user_questions  # noqa: SLF001

        # End the turn WITHOUT answering through the structured channel: pressing
        # Enter in the pane lets the agent's blocked read return (it resolves to
        # the default in-terminal), the directive completes, and the Stop hook
        # fires — ending the turn with the structured prompt still pending.
        page = _page(h)
        await page.wait_for_text("2. SQLite", timeout=8.0)
        await page.press("Enter")

        # The turn ends -> the stale pending prompt is resolved with 'turn_ended'.
        await client.wait_for(
            lambda frames: any(
                f.get("type") == "ask_user_resolved"
                and f.get("request_id") == request_id
                and f.get("decision") == "turn_ended"
                for f in frames
            ),
            timeout=8.0,
        )

        # The transport cleared its pending tty prompts (nothing stranded).
        await _wait_until(
            lambda: not h.transport._pending_tty_prompts,  # noqa: SLF001
            timeout=5.0,
        )
        # The broker dropped the reconnect-replay entry: a fresh client must NOT
        # re-surface the stale, already-resolved question.
        await _wait_until(
            lambda: request_id not in h.broker._pending_ask_user_questions,  # noqa: SLF001
            timeout=5.0,
        )

        second = await h.connect()
        await asyncio.sleep(0.5)
        replayed = [f for f in _ask_frames(second.frames) if f.get("request_id") == request_id]
        assert replayed == [], (
            f"a resolved/cleared question must NOT be replayed to a new client; got {replayed}"
        )
