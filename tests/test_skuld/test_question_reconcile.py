"""LEX-1979: a question the CLI is not asking is closed in all three places.

A stuck question lived in three places: the transport's native-control card, the
broker's pending and restored ``ask_user_question`` lists (replayed to every client),
and the conversation record (an AskUserQuestion tool_use with no tool_result). The
CLI's session file is the truth; these replay the three field incidents:

* 7 Nov (ios-release, mini-m6): a card kept since 18:32Z after the turn ended; the
  broker replayed the question to every client that connected.
* 8 Nov 02:21Z (ios-coordinator, Thor): an auto-compaction left a question whose call
  is not in the session file at all.
* 8 Nov 08:48Z (Damien's app): the card was dismissed at 08:25Z, but the record kept
  the open tool_use, so the app still showed "The agent needs your input".
"""

from __future__ import annotations

import importlib
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from niuu.domain.transcript_reducer import QUESTION_CLOSURE_SOURCE, apply_question_closures
from skuld.broker import Broker
from skuld.broker_api import reconcile_native_questions
from skuld.config import SkuldSettings
from skuld.conversation_models import ConversationTurn
from skuld.native_question_index import (
    ANSWERED,
    NEVER_SHOWN,
    UNANSWERED,
    NativeQuestionIndex,
    NativeTranscriptUnavailableError,
    claude_project_dir,
)
from skuld.question_reconcile import (
    RECONCILE_OWNER,
    RECONCILE_START,
    RECONCILE_SWEEP,
    NativeQuestionsUnsupportedError,
)
from skuld.transports import TransportCapabilities
from skuld.transports.tmux_interactive import MENU_GONE, MENU_LIVE

NATIVE = "5a62e8b6-9a93-4fed-afaa-43c98b1796a7"
PHANTOM = "toolu_01FNSi1eEKu7LVLCvQ9qL51y"  # 8 Nov: not in the session file
STRANDED = "toolu_01AFM9ZBGMHxj5TKR3drhgq8"  # 7 Nov: asked, never answered
ANSWERED_ID = "toolu_01FgErGfJngeKvXVYjSaWbCu"  # answered in the terminal


@pytest.fixture(autouse=True)
def quiet_http(monkeypatch):
    client = AsyncMock()
    client.post.return_value = MagicMock(status_code=204)
    monkeypatch.setattr(Broker, "_get_http_client", AsyncMock(return_value=client))
    importlib.import_module("skuld.broker")


class SessionFile:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")

    def write(self, record: dict) -> None:
        with self.path.open("a") as stream:
            stream.write(json.dumps({"sessionId": NATIVE, **record}) + "\n")

    def question(self, tool_use_id: str) -> None:
        self.write(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": tool_use_id,
                            "name": "AskUserQuestion",
                            "input": {"questions": [{"question": "Ship it?"}]},
                        }
                    ]
                },
            }
        )

    def answer(self, tool_use_id: str, content: str = "User answered: Ship it? -> Yes") -> None:
        self.write(
            {
                "type": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
                    ]
                },
            }
        )


def _question_turn(*tool_use_ids: str) -> ConversationTurn:
    return ConversationTurn(
        id="turn-3f701fff",
        role="assistant",
        content="",
        parts=[
            {
                "type": "tool_use",
                "id": tool_use_id,
                "name": "AskUserQuestion",
                "input": {"questions": [{"question": "Ship it?"}]},
                "started_at": "2026-11-08T02:21:56+00:00",
            }
            for tool_use_id in tool_use_ids
        ],
    )


@pytest.fixture
def session(tmp_path):
    settings = SkuldSettings(
        session={"id": "5d62a3a5-d697-435e-90c9-2dad8b9453fb", "workspace_dir": str(tmp_path)},
        transport="sdk",
    )
    broker = Broker(settings=settings)
    transport = MagicMock()
    transport.capabilities = TransportCapabilities(native_controls=True)
    transport.session_id = NATIVE
    transport.workspace_dir = str(tmp_path)
    transport.is_alive = True
    transport.open_question_tool_use_ids = MagicMock(return_value=set())
    transport.observe_native_menu_state = AsyncMock(return_value=MENU_GONE)
    file = SessionFile(tmp_path / "claude" / f"{NATIVE}.jsonl")
    transport.native_transcript_path = MagicMock(return_value=str(file.path))
    broker._transport = transport
    broker._save_conversation_history = MagicMock()
    frames: list[dict] = []
    original = broker._handle_cli_event

    async def record(frame: dict) -> None:
        frames.append(frame)
        await original(frame)

    broker._handle_cli_event = record
    return broker, file, frames


def _closures(broker: Broker) -> dict[str, dict]:
    return {
        part["tool_use_id"]: part
        for turn in broker._conversation_turns
        for part in turn.parts
        if part.get("type") == "tool_result"
    }


# -- The session-file index ------------------------------------------------------------


def test_the_index_reads_once_then_only_what_was_appended(tmp_path):
    file = SessionFile(tmp_path / f"{NATIVE}.jsonl")
    file.question(STRANDED)
    index = NativeQuestionIndex(file.path, NATIVE)
    index.refresh()
    assert index.state(STRANDED) == UNANSWERED
    assert index.state(PHANTOM) == NEVER_SHOWN
    file.answer(STRANDED)
    index.refresh()
    assert index.state(STRANDED) == ANSWERED
    assert index.result(STRANDED).content == "User answered: Ship it? -> Yes"


def test_the_index_ignores_subagents_other_sessions_and_a_half_written_line(tmp_path):
    file = SessionFile(tmp_path / f"{NATIVE}.jsonl")
    file.write(
        {
            "isSidechain": True,
            "message": {"content": [{"type": "tool_use", "id": "s", "name": "AskUserQuestion"}]},
        }
    )
    with file.path.open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "sessionId": "other",
                    "message": {
                        "content": [{"type": "tool_use", "id": "o", "name": "AskUserQuestion"}]
                    },
                }
            )
            + "\n"
        )
        stream.write('{"sessionId": "' + NATIVE + '", "message": {"content": [{"type": "tool_use"')
    index = NativeQuestionIndex(file.path, NATIVE)
    index.refresh()
    assert index.state("s") == index.state("o") == NEVER_SHOWN
    with file.path.open("a") as stream:
        stream.write(', "id": "late", "name": "AskUserQuestion"}]}}\n')
    index.refresh()
    assert index.state("late") == UNANSWERED


def test_a_replaced_session_file_is_read_again_from_the_start(tmp_path):
    file = SessionFile(tmp_path / f"{NATIVE}.jsonl")
    file.question(STRANDED)
    file.answer(STRANDED)
    index = NativeQuestionIndex(file.path, NATIVE)
    index.refresh()
    replacement = tmp_path / "new.jsonl"
    replacement.write_text("")
    replacement.replace(file.path)
    index.refresh()
    assert index.state(STRANDED) == NEVER_SHOWN


def test_a_missing_session_file_is_loud(tmp_path):
    with pytest.raises(NativeTranscriptUnavailableError, match="cannot be opened"):
        NativeQuestionIndex(tmp_path / "gone.jsonl", NATIVE).refresh()


def test_the_project_dir_follows_claudes_naming():
    assert claude_project_dir("/home/thor/repos/lexi-ios", Path("/h")) == Path(
        "/h/.claude/projects/-home-thor-repos-lexi-ios"
    )
    assert claude_project_dir("/Users/x/.local/w", Path("/h")).name == "-Users-x--local-w"


# -- The three incidents ------------------------------------------------------------------


async def test_8nov_a_question_not_in_the_session_file_closes_as_never_shown(session, caplog):
    broker, file, frames = session
    broker._conversation_turns = [_question_turn(PHANTOM)]
    caplog.set_level(logging.WARNING)

    outcome = await broker.reconcile_open_questions(trigger=RECONCILE_START)

    assert outcome["closed"] == [
        {"tool_use_id": PHANTOM, "request_id": f"reconcile:{PHANTOM}", "reason": "never_shown"}
    ]
    [frame] = [f for f in frames if f["type"] == "ask_user_resolved"]
    assert frame["dismissed"] is True and frame["control_kind"] == "question"
    assert frame["dismiss_trigger"] == RECONCILE_START
    closure = _closures(broker)[PHANTOM]
    assert closure["is_error"] is True
    assert closure["content"].startswith("Dismissed: the CLI never showed it")
    assert closure["metadata"]["source"] == QUESTION_CLOSURE_SOURCE
    assert closure["metadata"]["dismiss_reason"] == "never_shown"
    assert f"question {PHANTOM}" in caplog.text and "AUDIT" in caplog.text
    assert broker.question_reconcile_counts[f"closed.{RECONCILE_START}.never_shown"] == 1
    # Nothing is left open: a second pass checks nothing.
    assert (await broker.reconcile_open_questions(trigger=RECONCILE_SWEEP))["checked"] == 0


async def test_an_answered_question_closes_with_the_clis_own_answer(session):
    broker, file, _ = session
    file.question(ANSWERED_ID)
    file.answer(ANSWERED_ID, "User answered: keep waiting")
    broker._conversation_turns = [_question_turn(ANSWERED_ID)]

    outcome = await broker.reconcile_open_questions(trigger=RECONCILE_START)

    assert outcome["closed"][0]["reason"] == "resolved_in_terminal"
    closure = _closures(broker)[ANSWERED_ID]
    assert closure["content"] == "User answered: keep waiting"
    assert closure["is_error"] is False


async def test_7nov_a_restored_question_whose_menu_is_gone_closes_at_the_sweep(session):
    """Its call is in the session file without a result: kept at start, closed once the
    sweep sees no menu; the broker stops replaying it to clients."""
    broker, file, frames = session
    file.question(STRANDED)
    broker._unrestored_questions = {
        "tty-1-c0653dcb": {"request_id": "tty-1-c0653dcb", "tool_use_id": STRANDED}
    }
    broker._conversation_turns = [_question_turn(STRANDED)]

    start = await broker.reconcile_open_questions(trigger=RECONCILE_START)
    assert start["closed"] == []
    assert start["kept"] == [
        {"tool_use_id": STRANDED, "why": "unanswered; checked again by the sweep"}
    ]

    sweep = await broker.reconcile_open_questions(trigger=RECONCILE_SWEEP)
    assert sweep["closed"][0] == {
        "tool_use_id": STRANDED,
        "request_id": "tty-1-c0653dcb",
        "reason": "menu_not_shown",
    }
    assert broker._unrestored_questions == {}
    assert _closures(broker)[STRANDED]["metadata"]["request_id"] == "tty-1-c0653dcb"


async def test_an_unanswered_question_whose_menu_is_live_is_kept(session):
    broker, file, frames = session
    file.question(STRANDED)
    broker._conversation_turns = [_question_turn(STRANDED)]
    broker._transport.observe_native_menu_state = AsyncMock(return_value=MENU_LIVE)

    outcome = await broker.reconcile_open_questions(trigger=RECONCILE_SWEEP)

    assert outcome["closed"] == []
    assert outcome["kept"] == [{"tool_use_id": STRANDED, "why": "unanswered, menu live"}]
    assert not frames


async def test_a_question_with_an_open_card_is_left_to_the_cards_checks(session):
    broker, file, frames = session
    file.question(STRANDED)  # in the session file: the card's own checks apply
    broker._conversation_turns = [_question_turn(STRANDED)]
    broker._transport.open_question_tool_use_ids = MagicMock(return_value={STRANDED})
    broker._transport.dismiss_never_shown_question = AsyncMock()
    outcome = await broker.reconcile_open_questions(trigger=RECONCILE_SWEEP)
    assert outcome["closed"] == [] and not frames
    broker._transport.dismiss_never_shown_question.assert_not_awaited()


async def test_10nov_an_open_card_whose_call_is_not_in_the_file_closes_everywhere(session):
    """LEX-2025: a side query's question held ios-release 2 h with its menu "unknown"."""
    broker, file, frames = session
    broker._conversation_turns = [_question_turn(PHANTOM)]
    broker._transport.open_question_tool_use_ids = MagicMock(return_value={PHANTOM})
    broker._transport.dismiss_never_shown_question = AsyncMock(return_value="tty-1-9c8e24a8")

    outcome = await broker.reconcile_open_questions(trigger=RECONCILE_SWEEP)

    broker._transport.dismiss_never_shown_question.assert_awaited_once_with(
        PHANTOM, trigger=RECONCILE_SWEEP
    )
    assert outcome["closed"] == [
        {"tool_use_id": PHANTOM, "request_id": "tty-1-9c8e24a8", "reason": "never_shown"}
    ]
    assert _closures(broker)[PHANTOM]  # the record holds the closure too


async def test_an_open_never_shown_card_the_transport_keeps_stays_kept(session):
    """The transport's guards (young, being answered, a subagent's) win."""
    broker, file, frames = session
    broker._conversation_turns = [_question_turn(PHANTOM)]
    broker._transport.open_question_tool_use_ids = MagicMock(return_value={PHANTOM})
    broker._transport.dismiss_never_shown_question = AsyncMock(return_value=None)
    outcome = await broker.reconcile_open_questions(trigger=RECONCILE_SWEEP)
    assert outcome["closed"] == [] and not frames


async def test_a_question_asked_moments_ago_is_not_checked_yet(session):
    """Its card may not be open yet, nor its call written to the session file."""
    broker, file, frames = session
    turn = _question_turn(PHANTOM)
    turn.parts[0]["started_at"] = "2999-01-01T00:00:00+00:00"
    broker._conversation_turns = [turn]
    assert (await broker.reconcile_open_questions(trigger=RECONCILE_SWEEP))["checked"] == 0
    assert not frames


async def test_an_unreadable_session_file_closes_nothing_and_is_counted(session):
    broker, file, frames = session
    broker._conversation_turns = [_question_turn(PHANTOM)]
    file.path.unlink()
    with pytest.raises(NativeTranscriptUnavailableError):
        await broker.reconcile_open_questions(trigger=RECONCILE_SWEEP)
    assert not frames
    assert broker.question_reconcile_counts["transcript_unavailable"] == 1


async def test_8nov_0848_a_dismissed_card_also_closes_the_question_in_the_record(session):
    """The 08:25Z dismiss cleared the card and the replay list, not the record."""
    broker, file, frames = session
    broker._conversation_turns = [_question_turn(PHANTOM)]
    broker._pending_ask_user_questions = {
        "tty-2-1920b6d1": {"request_id": "tty-2-1920b6d1", "tool_use_id": PHANTOM}
    }
    await broker._handle_cli_event(
        {
            "type": "ask_user_resolved",
            "request_id": "tty-2-1920b6d1",
            "tool_use_id": PHANTOM,
            "decision": "dismissed_by_operator",
            "accepted": False,
            "dismissed": True,
            "dismiss_reason": "dismissed_by_operator",
            "dismiss_detail": "dismissed by operator dev-user",
            "control_kind": "question",
        }
    )
    assert broker._pending_ask_user_questions == {}
    closure = _closures(broker)[PHANTOM]
    assert closure["content"] == ("Dismissed: dismissed by operator dev-user. No answer was given.")


async def test_a_question_open_only_in_the_record_can_be_dismissed_by_its_owner(session, caplog):
    broker, file, frames = session
    broker._conversation_turns = [_question_turn(PHANTOM)]
    broker._transport.dismiss_native_control = AsyncMock(side_effect=LookupError("no card"))
    caplog.set_level(logging.WARNING)
    dismissed = await broker.dismiss_native_control(PHANTOM, dismissed_by="dfagnou")
    assert dismissed["dismiss_detail"] == "dismissed by operator dfagnou"
    assert _closures(broker)[PHANTOM]["metadata"]["dismiss_reason"] == "dismissed_by_operator"
    assert "AUDIT" in caplog.text
    with pytest.raises(LookupError):
        await broker.dismiss_native_control(PHANTOM, dismissed_by="dfagnou")


async def test_a_reconcile_on_a_transport_without_native_controls_refuses(session):
    broker, _, _ = session
    broker._transport.capabilities = TransportCapabilities(native_controls=False)
    with pytest.raises(NativeQuestionsUnsupportedError):
        await broker.reconcile_open_questions(trigger=RECONCILE_OWNER)


async def test_live_closure_and_the_rebuild_agree(session):
    """INV-4: the record the live broker writes is the record a rebuild produces."""
    broker, file, frames = session
    broker._conversation_turns = [_question_turn(PHANTOM)]
    await broker.reconcile_open_questions(trigger=RECONCILE_START)
    [frame] = [f for f in frames if f["type"] == "ask_user_resolved"]

    class Row:
        kind = "ask_user_resolved"
        seq = 7
        payload = frame
        ts = None
        request_id = None
        session_id = "s"

    rebuilt = apply_question_closures(
        [{"role": "assistant", "parts": _question_turn(PHANTOM).parts}], [Row()]
    )
    live = [p for p in broker._conversation_turns[0].parts if p["type"] == "tool_result"]
    rebuilt_result = [p for p in rebuilt[0]["parts"] if p["type"] == "tool_result"]
    assert [{k: v for k, v in p.items() if k != "ended_at"} for p in live] == [
        {k: v for k, v in p.items() if k != "ended_at"} for p in rebuilt_result
    ]


# -- The owner route -----------------------------------------------------------------------


def _request(room_role: str):
    from types import SimpleNamespace

    return SimpleNamespace(headers={}, state=SimpleNamespace(room_role=room_role))


async def test_the_reconcile_route_is_owner_only_and_maps_refusals(monkeypatch):
    import skuld.broker_api as api

    fake = MagicMock()
    fake.reconcile_open_questions = AsyncMock(return_value={"checked": 1, "closed": [], "kept": []})
    monkeypatch.setattr(api, "broker", fake)
    monkeypatch.setattr(api, "_room_role_from_state", AsyncMock(return_value="owner"))
    assert await reconcile_native_questions(_request("owner")) == {
        "checked": 1,
        "closed": [],
        "kept": [],
    }
    fake.reconcile_open_questions.assert_awaited_once_with(trigger=RECONCILE_OWNER)
    fake.reconcile_open_questions = AsyncMock(
        side_effect=NativeTranscriptUnavailableError("cannot be opened")
    )
    with pytest.raises(HTTPException) as refused:
        await reconcile_native_questions(_request("owner"))
    assert refused.value.status_code == 409
    monkeypatch.setattr(api, "_room_role_from_state", AsyncMock(return_value="viewer"))
    with pytest.raises(HTTPException) as denied:
        await reconcile_native_questions(_request("viewer"))
    assert denied.value.status_code == 403


# -- Codex review of ba9074354 ------------------------------------------------------------


async def test_concurrent_refreshes_never_see_a_half_indexed_file(tmp_path):
    import asyncio

    file = SessionFile(tmp_path / f"{NATIVE}.jsonl")
    for n in range(2000):
        file.write({"type": "user", "message": {"content": [{"type": "text", "text": f"x{n}"}]}})
    file.question(STRANDED)
    index = NativeQuestionIndex(file.path, NATIVE)
    await asyncio.gather(*(asyncio.to_thread(index.refresh) for _ in range(4)))
    assert index.state(STRANDED) == UNANSWERED


async def test_reconciles_run_one_at_a_time(session):
    import asyncio

    broker, file, frames = session
    broker._conversation_turns = [_question_turn(PHANTOM)]
    outcomes = await asyncio.gather(
        broker.reconcile_open_questions(trigger=RECONCILE_SWEEP),
        broker.reconcile_open_questions(trigger=RECONCILE_OWNER),
    )
    assert sorted(len(o["closed"]) for o in outcomes) == [0, 1]
    assert len([f for f in frames if f["type"] == "ask_user_resolved"]) == 1


@pytest.mark.parametrize("kind", ["permission", None])
async def test_a_restored_permission_gate_is_never_reconciled(session, kind):
    """Permission gates are sent as ask_user_question too; their call is no question."""
    broker, file, frames = session
    bash = ConversationTurn(
        id="t",
        role="assistant",
        content="",
        parts=[{"type": "tool_use", "id": "toolu_bash", "name": "Bash", "input": {}}],
    )
    broker._conversation_turns = [bash]
    broker._unrestored_questions = {
        "tty-1": {
            "request_id": "tty-1",
            "tool_use_id": "toolu_bash",
            "metadata": {"control_kind": kind} if kind else {},
        }
    }
    assert (await broker.reconcile_open_questions(trigger=RECONCILE_SWEEP))["checked"] == 0
    assert broker._unrestored_questions and not frames


async def test_a_hung_screen_check_keeps_every_question(session):
    broker, file, frames = session
    file.question(STRANDED)
    broker._conversation_turns = [_question_turn(STRANDED)]
    broker._transport.observe_native_menu_state = AsyncMock(side_effect=TimeoutError)
    outcome = await broker.reconcile_open_questions(trigger=RECONCILE_SWEEP)
    assert outcome["closed"] == [] and not frames
    assert outcome["kept"] == [{"tool_use_id": STRANDED, "why": "unanswered, menu timed out"}]
    assert broker.question_reconcile_counts["screen_check_timeouts"] == 1


async def test_a_restored_card_whose_question_the_record_answered_stops_replaying(session):
    broker, file, frames = session
    turn = _question_turn(STRANDED)
    turn.parts.append({"type": "tool_result", "tool_use_id": STRANDED, "content": "Yes"})
    broker._conversation_turns = [turn]
    broker._unrestored_questions = {
        "tty-1-c0653dcb": {
            "request_id": "tty-1-c0653dcb",
            "tool_use_id": STRANDED,
            "metadata": {"control_kind": "question"},
        }
    }
    outcome = await broker.reconcile_open_questions(trigger=RECONCILE_START)
    assert outcome["closed"][0]["reason"] == "resolved_in_terminal"
    assert broker._unrestored_questions == {}
    # The record's real answer stays the only result.
    assert [p for p in broker._conversation_turns[0].parts if p["type"] == "tool_result"] == [
        {"type": "tool_result", "tool_use_id": STRANDED, "content": "Yes"}
    ]


async def test_a_real_answer_after_a_close_replaces_the_closure_live(session):
    broker, file, frames = session
    broker._conversation_turns = [_question_turn(PHANTOM)]
    await broker.reconcile_open_questions(trigger=RECONCILE_START)
    assert _closures(broker)[PHANTOM]["metadata"]["source"] == QUESTION_CLOSURE_SOURCE
    await broker._handle_cli_event(
        {
            "type": "user",
            "message": {
                "content": [{"type": "tool_result", "tool_use_id": PHANTOM, "content": "Yes"}]
            },
        }
    )
    sources = [
        (p.get("metadata") or {}).get("source")
        for t in broker._conversation_turns
        for p in t.parts
        if p.get("type") == "tool_result" and p["tool_use_id"] == PHANTOM
    ]
    assert QUESTION_CLOSURE_SOURCE not in sources


def _transport_close(request_id: str, *, trigger: str, reason: str, kind: str = "question"):
    """An ask_user_resolved frame as the tmux transport emits it when a card closes."""
    return {
        "type": "ask_user_resolved",
        "request_id": request_id,
        "tool_use_id": f"toolu_{request_id}",
        "decision": reason,
        "accepted": False,
        "dismissed": True,
        "dismiss_reason": reason,
        "dismiss_detail": "the menu is gone",
        "dismiss_trigger": trigger,
        "control_kind": kind,
        "metadata": {"source": "tmux_tty_bridge"},
    }


async def test_every_question_close_is_counted_by_stage_and_reason(session, monkeypatch):
    """LEX-1979 (f): closes by the transport are counted with the reconcile's, once each."""
    broker, _, _ = session
    await broker._handle_cli_event(
        _transport_close("tty-2", trigger="delivery", reason="superseded")
    )
    await broker._handle_cli_event(_transport_close("tty-3", trigger="hook", reason="menu_gone"))
    await broker._handle_cli_event(
        _transport_close("perm-1", trigger="delivery", reason="superseded", kind="permission")
    )

    counts = broker.question_reconcile_counts
    assert counts["closed.delivery.superseded"] == 1  # the permission card is not a question
    assert counts["closed.hook.menu_gone"] == 1

    import skuld.broker_api as api

    monkeypatch.setattr(api, "broker", broker)
    health = await api.health()
    assert health["question_counts"]["closed.delivery.superseded"] == 1
    assert health["question_counts"]["closed.hook.menu_gone"] == 1


async def test_a_question_closed_twice_counts_once(session):
    """A second close frame for the same question (a race at turn end) is not a second close."""
    broker, _, _ = session
    await broker._handle_cli_event(
        _transport_close("tty-9", trigger="operator", reason="dismissed_by_operator")
    )
    await broker._handle_cli_event(
        _transport_close("tty-9", trigger="turn_ended", reason="turn_ended")
    )
    await broker._handle_cli_event(
        _transport_close("tty-9", trigger="operator", reason="dismissed_by_operator")
    )

    counts = broker.question_reconcile_counts
    assert counts["closed.operator.dismissed_by_operator"] == 1
    assert counts["closed.turn_ended.turn_ended"] == 0
