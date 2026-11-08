"""``ask``: send to another session and wait for its answer (NIU-1183).

The recipient is faked on both sides: its answer is a :class:`PeerMessage` handed to the asker's
inbox the way Forge's WebSocket frame hands it, and Forge's view of it is a scripted
``get_session``. The asker's broker, inbox and durable log are real.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from niuu import cap_telemetry
from niuu.domain.peer_message import render_for_harness
from niuu.forge_mcp.models import AskOutcome, ForgeApiError
from skuld import peer_inbox
from skuld.config import AskConfig, SkuldSettings
from skuld.forge_mcp import host as host_module
from skuld.forge_mcp.host import ASK_OUTCOMES_COUNTER, BrokerForgeMcpHost
from skuld.peer_inbox import PeerInboxEntry, PeerMessageRefusedError
from tests.test_skuld.test_peer_inbox import (
    SESSION_ID,
    _inbox_broker,
    _message,
    _peer_frames,
    _restart_frames,
    _sent_texts,
    _settle,
)

RECIPIENT = "5d4c3b2a-1e0f-4a9b-8c7d-6e5f4a3b2c1d"
QUESTION = "question-1"


def _answer(message_id: str = "answer-1", **changes: Any):
    return _message(
        message_id,
        sender_id=RECIPIENT,
        recipient_id=SESSION_ID,
        in_reply_to=QUESTION,
        content="Yes, ship it.",
        **changes,
    )


class ScriptedForge:
    """Forge's view of the recipient: one session row, or an error, per ``get_session``."""

    def __init__(self, *rows: dict[str, Any] | ForgeApiError) -> None:
        self.rows = list(rows) or [_row()]
        self.reads = 0

    async def get_session(self, session_id, *, instance_id):
        self.reads += 1
        row = self.rows.pop(0) if len(self.rows) > 1 else self.rows[0]
        if isinstance(row, ForgeApiError):
            raise row
        return row

    def __getattr__(self, name):  # the rest of the port is not part of waiting
        raise AssertionError(f"ask waiting called Forge's {name}")


def _row(status: str = "running", activity: str = "active", **metadata: Any) -> dict[str, Any]:
    return {
        "id": RECIPIENT,
        "status": status,
        "activity_state": activity,
        "activity_metadata": metadata,
    }


@pytest.fixture
def outcomes(monkeypatch):
    """Every ``skuld.ask.outcomes`` increment as its attributes."""
    calls: list[dict] = []
    telemetry = SimpleNamespace(
        count=lambda name, value=1, *, attributes=None, description="": calls.append(
            {"name": name, **(attributes or {})}
        )
    )
    monkeypatch.setattr(host_module, "get_observability", lambda: telemetry)
    monkeypatch.setattr(peer_inbox, "get_observability", lambda: telemetry)
    cap_telemetry.reset_cap_hits()
    return calls


def _asks(calls: list[dict]) -> list[str]:
    return [c["outcome"] for c in calls if c["name"] == ASK_OUTCOMES_COUNTER]


def _host(tmp_path, client: ScriptedForge, *, activity: str = "active", **ask: Any):
    broker = _inbox_broker(tmp_path, activity=activity)
    broker._settings.ask = broker._settings.ask.model_copy(
        update={"poll_interval_s": 0.01, "wait_max_s": 5.0, **ask}
    )
    return BrokerForgeMcpHost(broker, client), broker


async def _ask(host: BrokerForgeMcpHost, *, deadline_s: float = 0.2, confirmed: bool = True):
    return await host.ask_for_answer(
        recipient_id=RECIPIENT,
        instance_id=None,
        message_id=QUESTION,
        request_id="req-1",
        delivery_confirmed=confirmed,
        deadline_s=deadline_s,
    )


class TestAnswered:
    async def test_an_answer_already_in_the_inbox_is_returned_with_its_sender(
        self, tmp_path, outcomes
    ) -> None:
        host, broker = _host(tmp_path, ScriptedForge(), activity="active")
        await broker.accept_peer_message(_answer())

        result = await _ask(host)

        assert result["outcome"] == "answered" and result["answer"] == "Yes, ship it."
        assert result["from"] == "planner (session " + RECIPIENT + ")"
        assert result["answer_message_id"] == "answer-1"
        assert _asks(outcomes) == ["answered"]

    async def test_an_answer_that_arrives_during_the_wait_wakes_it_at_once(
        self, tmp_path, outcomes
    ) -> None:
        host, broker = _host(tmp_path, ScriptedForge(), poll_interval_s=60)
        asking = asyncio.create_task(_ask(host, deadline_s=30))
        await asyncio.sleep(0.05)
        assert not asking.done()

        await broker.accept_peer_message(_answer())

        assert (await asyncio.wait_for(asking, 2))["outcome"] == "answered"

    async def test_the_answer_is_consumed_and_never_handed_to_the_harness(
        self, tmp_path, outcomes
    ) -> None:
        host, broker = _host(tmp_path, ScriptedForge(), activity="idle")
        asking = asyncio.create_task(_ask(host, deadline_s=30))
        await asyncio.sleep(0.05)

        # The harness is idle, so any other message would be injected at once.
        await broker.accept_peer_message(_answer())
        result = await asyncio.wait_for(asking, 2)
        await _settle()

        assert result["outcome"] == "answered"
        assert _sent_texts(broker) == []
        assert _peer_frames(broker)[-1]["consumed_by"] == f"ask:{QUESTION}"
        assert broker._peer_messages_waiting() is False
        await broker._inject_at_turn_boundary()
        await _settle()
        assert _sent_texts(broker) == []

    async def test_the_answer_is_held_back_even_when_the_boundary_opens_before_the_ask_looks(
        self, tmp_path, outcomes
    ) -> None:
        gate = asyncio.Event()

        class Gated(ScriptedForge):
            async def get_session(self, session_id, *, instance_id):
                await gate.wait()
                return await super().get_session(session_id, instance_id=instance_id)

        host, broker = _host(tmp_path, Gated(), activity="idle")
        asking = asyncio.create_task(_ask(host, deadline_s=30))
        await asyncio.sleep(0.05)  # the ask is inside its Forge check, not at the inbox

        await broker.accept_peer_message(_answer())
        await _settle_without(asking)
        assert _sent_texts(broker) == []  # the idle boundary found it held for the ask

        gate.set()
        result = await asyncio.wait_for(asking, 2)
        await _settle()

        assert result["outcome"] == "answered" and _sent_texts(broker) == []

    async def test_other_messages_still_reach_the_harness_while_an_ask_waits(
        self, tmp_path, outcomes
    ) -> None:
        host, broker = _host(tmp_path, ScriptedForge(), activity="idle")
        asking = asyncio.create_task(_ask(host, deadline_s=30))
        await asyncio.sleep(0.05)

        unrelated = _message("other-1", content="a different matter")
        await broker.accept_peer_message(unrelated)
        await _settle_without(asking)

        assert _sent_texts(broker) == [render_for_harness(unrelated)]
        asking.cancel()
        await asyncio.gather(asking, return_exceptions=True)

    async def test_a_reply_from_someone_else_is_not_the_answer(self, tmp_path, outcomes) -> None:
        host, broker = _host(tmp_path, ScriptedForge(), activity="active")
        impostor = _message(
            "answer-x",
            sender_id="aaaaaaaa-0000-4000-8000-000000000001",
            recipient_id=SESSION_ID,
            in_reply_to=QUESTION,
            content="I am the recipient",
        )
        await broker.accept_peer_message(impostor)

        result = await _ask(host, deadline_s=0.05)

        assert result["outcome"] == "deadline"
        assert broker._peer_inbox["answer-x"].consumed_by == ""

    async def test_a_log_that_will_not_store_the_take_is_an_error_and_loses_nothing(
        self, tmp_path, outcomes
    ) -> None:
        host, broker = _host(tmp_path, ScriptedForge(), activity="active")
        await broker.accept_peer_message(_answer())
        broker._await_event_log_receipt = _refusing

        with pytest.raises(PeerMessageRefusedError, match="was not stored"):
            await _ask(host)

        entry = broker._peer_inbox["answer-1"]
        assert entry.pending is True and entry.consumed_by == ""

    async def test_asking_again_returns_an_answer_the_first_call_took(
        self, tmp_path, outcomes
    ) -> None:
        host, broker = _host(tmp_path, ScriptedForge(), activity="active")
        await broker.accept_peer_message(_answer())
        first = await _ask(host)

        again = await _ask(host)

        assert again == first

    async def test_an_answer_already_handed_to_the_harness_is_reported_as_such(
        self, tmp_path, outcomes
    ) -> None:
        host, broker = _host(tmp_path, ScriptedForge(), activity="idle")
        await broker.accept_peer_message(_answer())
        await _settle()
        assert _sent_texts(broker) == [render_for_harness(_answer())]

        result = await _ask(host)

        assert result["outcome"] == "answered" and "already been delivered" in result["note"]
        assert len(_sent_texts(broker)) == 1


async def _refusing(_receipt, _timeout):
    return False


async def _settle_without(task: asyncio.Task) -> None:
    for _ in range(4):
        pending = [
            t for t in asyncio.all_tasks() if t is not asyncio.current_task() and t is not task
        ]
        await asyncio.gather(*pending, return_exceptions=True)


class TestRestart:
    async def test_a_taken_answer_is_not_delivered_after_a_restart(
        self, tmp_path, outcomes
    ) -> None:
        host, before = _host(tmp_path, ScriptedForge(), activity="active")
        await before.accept_peer_message(_answer())
        await _ask(host)

        after = _inbox_broker(tmp_path, activity="idle")
        after._restore_durable_runtime_state(_restart_frames(before))
        assert after._peer_inbox["answer-1"].consumed_by == f"ask:{QUESTION}"
        assert after._peer_messages_waiting() is False
        await after._inject_at_turn_boundary()
        await _settle()

        assert _sent_texts(after) == []

    async def test_the_taken_state_survives_the_durable_frame(self) -> None:
        entry = PeerInboxEntry(_answer(), accepted_at="2026-11-08T10:00:00+00:00")
        entry.consumed_by = "ask:q"

        restored = PeerInboxEntry.from_frame(entry.frame())

        assert restored.consumed_by == "ask:q" and restored.pending is False

    async def test_a_frame_written_before_asks_existed_reads_as_not_taken(self) -> None:
        frame = PeerInboxEntry(_answer(), accepted_at="2026-11-08T10:00:00+00:00").frame()
        del frame["consumed_by"]

        assert PeerInboxEntry.from_frame(frame).pending is True

    async def test_a_malformed_taken_marker_is_refused(self) -> None:
        frame = PeerInboxEntry(_answer(), accepted_at="2026-11-08T10:00:00+00:00").frame()
        frame["consumed_by"] = 7

        with pytest.raises(ValueError, match="consumed_by"):
            PeerInboxEntry.from_frame(frame)


class TestLateAnswer:
    async def test_an_answer_after_the_deadline_is_delivered_at_the_turn_boundary(
        self, tmp_path, outcomes
    ) -> None:
        host, broker = _host(tmp_path, ScriptedForge(), activity="active")
        result = await _ask(host, deadline_s=0.05)
        assert result["outcome"] == "deadline"

        await broker.accept_peer_message(_answer())
        await _settle()
        assert _sent_texts(broker) == []  # the turn is still running

        await broker._report_activity_state("idle")
        await _settle()

        assert _sent_texts(broker) == [render_for_harness(_answer())]

    async def test_an_answer_held_back_during_the_wait_is_released_when_the_call_is_cancelled(
        self, tmp_path, outcomes
    ) -> None:
        host, broker = _host(tmp_path, ScriptedForge(), activity="idle")
        asking = asyncio.create_task(_ask(host, deadline_s=30))
        await asyncio.sleep(0.05)
        # A reply is in the inbox but nobody took it: the ask is killed by the engine first.
        broker._peer_inbox["answer-1"] = PeerInboxEntry(_answer(), accepted_at="t")
        asking.cancel()
        await asyncio.gather(asking, return_exceptions=True)
        await _settle()

        assert _sent_texts(broker) == [render_for_harness(_answer())]

    async def test_an_ask_that_fails_leaves_a_later_answer_to_the_normal_path(
        self, tmp_path, outcomes
    ) -> None:
        host, broker = _host(
            tmp_path, ScriptedForge(ForgeApiError("forge down", status=503)), activity="idle"
        )

        with pytest.raises(ForgeApiError, match="forge down"):
            await _ask(host)
        await broker.accept_peer_message(_answer())
        await _settle()

        assert _sent_texts(broker) == [render_for_harness(_answer())]


class TestEndings:
    async def test_blocked_on_a_person_names_what_it_waits_on(self, tmp_path, outcomes) -> None:
        forge = ScriptedForge(_row(activity="awaiting_input", kind="permission"))
        host, _ = _host(tmp_path, forge)

        result = await _ask(host)

        assert result["outcome"] == "blocked"
        assert "permission" in result["detail"] and "waiting on a person" in result["detail"]
        assert _asks(outcomes) == ["blocked"]

    @pytest.mark.parametrize("status", ["stopped", "failed", "archived"])
    async def test_a_session_that_stopped_or_failed_is_gone(
        self, tmp_path, outcomes, status
    ) -> None:
        host, _ = _host(tmp_path, ScriptedForge(_row(status=status, activity="idle")))

        result = await _ask(host)

        assert result["outcome"] == "gone" and status in result["detail"]
        assert _asks(outcomes) == ["gone"]

    async def test_a_transport_that_died_is_gone(self, tmp_path, outcomes) -> None:
        host, _ = _host(tmp_path, ScriptedForge(_row(activity="stopped")))

        assert (await _ask(host))["outcome"] == "gone"

    async def test_a_deleted_session_is_gone(self, tmp_path, outcomes) -> None:
        host, _ = _host(tmp_path, ScriptedForge(ForgeApiError("Session not found", status=404)))

        result = await _ask(host)

        assert result["outcome"] == "gone" and "deleted" in result["detail"]

    async def test_a_forge_failure_is_an_error_not_an_ending(self, tmp_path, outcomes) -> None:
        host, _ = _host(tmp_path, ScriptedForge(ForgeApiError("forge down", status=503)))

        with pytest.raises(ForgeApiError, match="forge down"):
            await _ask(host)
        assert _asks(outcomes) == []

    async def test_a_message_the_recipient_has_not_read_is_not_started(
        self, tmp_path, outcomes
    ) -> None:
        row = _row(peer_messages={"pairs": [], "waiting": [QUESTION]})
        host, _ = _host(tmp_path, ScriptedForge(row), wait_max_s=0.05)

        result = await _ask(host, deadline_s=0.05)

        assert result["outcome"] == "not_started"
        assert "ask again" in result["detail"].lower() and "req-1" in result["detail"]
        assert _asks(outcomes) == ["not_started"]
        assert cap_telemetry.cap_hit_counts()["ask.wait_max_s"] == 1

    async def test_an_unconfirmed_send_is_delivery_unknown_not_failed(
        self, tmp_path, outcomes
    ) -> None:
        host, _ = _host(tmp_path, ScriptedForge())

        result = await _ask(host, deadline_s=0.05, confirmed=False)

        assert result["outcome"] == "delivery_unknown"
        assert "unknown, not failed" in result["detail"]
        assert _asks(outcomes) == ["delivery_unknown"]

    async def test_the_deadline_is_an_error_that_names_the_knob(self, tmp_path, outcomes) -> None:
        host, _ = _host(tmp_path, ScriptedForge(), wait_max_s=5.0)

        result = await _ask(host, deadline_s=0.05)

        assert result["outcome"] == "deadline"
        assert "ask.wait_max_s=5" in result["detail"] and "deadline_s=0.05" in result["detail"]
        assert "req-1" in result["detail"]
        assert _asks(outcomes) == ["deadline"]
        assert cap_telemetry.cap_hit_counts().get("ask.wait_max_s", 0) == 0

    async def test_running_into_the_ceiling_is_a_counted_cap_hit(self, tmp_path, outcomes) -> None:
        host, _ = _host(tmp_path, ScriptedForge(), wait_max_s=0.05)

        result = await _ask(host, deadline_s=0.05)

        assert result["outcome"] == "deadline"
        assert _asks(outcomes) == ["deadline"]
        assert cap_telemetry.cap_hit_counts()["ask.wait_max_s"] == 1

    async def test_the_recipient_is_asked_about_repeatedly_not_once(
        self, tmp_path, outcomes
    ) -> None:
        forge = ScriptedForge()
        host, _ = _host(tmp_path, forge, poll_interval_s=0.01)

        await _ask(host, deadline_s=0.15)

        assert forge.reads >= 3

    async def test_an_answer_wins_over_a_blocked_recipient_seen_in_the_same_moment(
        self, tmp_path, outcomes
    ) -> None:
        host, broker = _host(
            tmp_path, ScriptedForge(_row(activity="awaiting_input", kind="question"))
        )
        await broker.accept_peer_message(_answer())

        assert (await _ask(host))["outcome"] == "answered"

    async def test_a_recipient_that_stops_or_blocks_during_the_wait_is_seen_at_the_deadline(
        self, tmp_path, outcomes
    ) -> None:
        for row, expected in (
            (_row(status="stopped", activity="idle"), "gone"),
            (_row(activity="awaiting_input", kind="question"), "blocked"),
        ):
            host, _ = _host(tmp_path, ScriptedForge(_row(), row), poll_interval_s=60)

            assert (await _ask(host, deadline_s=0.05))["outcome"] == expected

    async def test_an_answer_that_lands_in_the_last_moment_still_counts(
        self, tmp_path, outcomes
    ) -> None:
        host, broker = _host(tmp_path, ScriptedForge(), activity="active")
        real_take = broker.take_peer_answer
        calls = 0

        async def late(wait):
            nonlocal calls
            calls += 1
            if calls == 1:
                return None
            await broker.accept_peer_message(_answer())
            return await real_take(wait)

        broker.take_peer_answer = late
        result = await _ask(host, deadline_s=0.02)
        await asyncio.sleep(0)  # the wait's last pause is over before the final look

        assert result["outcome"] == "answered" and calls >= 2

    async def test_every_ending_is_a_named_outcome(self) -> None:
        assert {o.value for o in AskOutcome} == {
            "answered",
            "blocked",
            "gone",
            "not_started",
            "delivery_unknown",
            "deadline",
        }


class TestConfig:
    def test_the_defaults_fit_inside_the_engines_timeouts(self) -> None:
        settings = SkuldSettings()

        assert settings.ask.wait_max_s == 50.0 and settings.ask.poll_interval_s == 2.0
        assert settings.ask.wait_max_s < settings.forge_mcp.broker_request_timeout_s
        assert settings.ask.wait_max_s < settings.forge_mcp.tool_timeout_s

    @pytest.mark.parametrize(
        ("forge_mcp", "knob"),
        [
            ({"broker_request_timeout_s": 60.0, "tool_timeout_s": 600.0}, "broker_request"),
            ({"broker_request_timeout_s": 600.0, "tool_timeout_s": 120.0}, "tool_timeout"),
        ],
    )
    def test_a_wait_the_engine_would_cut_off_is_a_startup_error(self, forge_mcp, knob) -> None:
        with pytest.raises(ValidationError) as raised:
            SkuldSettings(ask={"wait_max_s": 300.0}, forge_mcp=forge_mcp)

        assert "ask.wait_max_s (300.0)" in str(raised.value)
        assert f"forge_mcp.{knob}" in str(raised.value)

    def test_a_longer_wait_is_fine_once_the_timeouts_allow_it(self) -> None:
        settings = SkuldSettings(
            ask={"wait_max_s": 300.0},
            forge_mcp={"broker_request_timeout_s": 400.0, "tool_timeout_s": 500.0},
        )

        assert settings.ask.wait_max_s == 300.0

    def test_the_check_is_skipped_when_the_forge_mcp_is_off(self) -> None:
        SkuldSettings(ask={"wait_max_s": 900.0}, forge_mcp={"enabled": False})

    @pytest.mark.parametrize("field", ["poll_interval_s", "wait_max_s"])
    def test_a_non_positive_value_is_refused(self, field) -> None:
        with pytest.raises(ValidationError):
            AskConfig(**{field: 0})
