"""A terminal provider failure must survive both live folding and durable replay."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from niuu.domain.transcript_reducer import reduce_frames, result_metadata
from niuu.ports.cli.transport import TransportCapabilities
from skuld.broker import Broker
from skuld.config import SkuldSettings

_SID = "codex-error-replay"
_ERROR = "Codex WebSocket closed"
_FAILURE = {
    "type": "result",
    "result": "",
    "stop_reason": "error",
    "is_error": True,
    "error": _ERROR,
    "modelUsage": {"gpt-6-astra": {"inputTokens": 53_650, "outputTokens": 290}},
}


def _frames(payloads):
    return [
        SimpleNamespace(
            session_id=_SID,
            seq=seq,
            kind=payload["type"],
            payload=payload,
            request_id=None,
            ts=datetime(2026, 9, 8, tzinfo=UTC),
        )
        for seq, payload in enumerate(payloads, start=1)
    ]


@pytest.mark.parametrize("streamed_text", ["", "I’m checking the live service next."])
def test_closed_codex_socket_is_a_visible_failed_turn(streamed_text):
    """Regression from Lexi iOS Astra's 2026-09-08 seq 282 failure shape."""
    events = []
    if streamed_text:
        events.append(
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": streamed_text}}
        )
    events.append(_FAILURE)

    result = reduce_frames(_frames(events))

    assert result.partial is True
    assert len(result.turns) == 1
    turn = result.turns[0]
    assert turn["content"] == (streamed_text or _ERROR)
    assert turn["metadata"]["status"] == "error"
    assert turn["metadata"]["messageType"] == "error"
    assert turn["metadata"]["is_error"] is True
    assert turn["metadata"]["error"] == _ERROR
    assert turn["metadata"]["stop_reason"] == "error"
    assert turn["metadata"]["usage"] == _FAILURE["modelUsage"]


@pytest.mark.parametrize(
    ("failure", "expected_error"),
    [
        (
            {"stop_reason": "error", "error": {"message": "Connection lost", "code": 42}},
            "Connection lost",
        ),
        (
            {"is_error": True, "errors": ["Provider overloaded", "Retry later"]},
            "Provider overloaded\nRetry later",
        ),
        (
            {"is_error": True, "result": "Provider refused the request"},
            "Provider refused the request",
        ),
        ({"subtype": "error_during_execution"}, "The agent stopped with an error."),
        ({"stop_reason": "failed", "is_error": False}, "The agent stopped with an error."),
    ],
)
def test_other_provider_failure_shapes_keep_the_error(failure, expected_error):
    result = reduce_frames(_frames([{"type": "result", **failure}]))

    assert len(result.turns) == 1
    assert result.turns[0]["content"] == expected_error
    assert result.turns[0]["metadata"]["error"] == expected_error
    assert result.turns[0]["metadata"]["status"] == "error"
    assert result.partial is True


def test_interruption_does_not_become_a_provider_error():
    payload = {"type": "result", "is_error": True, "stop_reason": "interrupted"}
    metadata = result_metadata(payload)

    assert metadata["status"] == "interrupted"
    assert "error" not in metadata
    assert "messageType" not in metadata
    # An empty intentional interrupt has no invented assistant prose.
    assert reduce_frames(_frames([payload])).turns == []


def test_successful_empty_result_still_does_not_create_a_phantom_turn():
    payload = {"type": "result", "result": "", "is_error": False, "stop_reason": "end_turn"}

    assert reduce_frames(_frames([payload])).turns == []
    assert result_metadata(payload) == {
        "usage": {},
        "cost": None,
        "model": None,
        "stop_reason": "end_turn",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("streamed_text", ["", "I’m checking the live service next."])
async def test_live_broker_and_raw_replay_preserve_identical_failure(tmp_path, streamed_text):
    broker = Broker(
        settings=SkuldSettings(
            session={"id": _SID, "workspace_dir": str(tmp_path)},
            transport="subprocess",
            volundr_api_url="http://volundr.test",
        )
    )
    broker._transport = MagicMock()
    broker._transport.is_alive = False
    broker._transport.capabilities = TransportCapabilities()
    broker._report_activity_state = AsyncMock()
    broker._report_usage = AsyncMock()
    broker._report_timeline_event = AsyncMock()
    broker._on_result_publish_mesh = AsyncMock()
    broker._save_conversation_history = MagicMock()

    if streamed_text:
        await broker._handle_cli_event(
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": streamed_text}}
        )
    await broker._handle_cli_event(dict(_FAILURE))

    rows = [
        SimpleNamespace(
            session_id=_SID,
            seq=row["seq"],
            kind=row["kind"],
            payload=row["payload"],
            request_id=row.get("request_id"),
            ts=datetime.fromisoformat(row["ts"]),
        )
        for row in broker._event_log_buffer
        if row["kind"] != "conversation.turn"
    ]
    rebuilt = reduce_frames(rows)
    assert len(broker._conversation_turns) == len(rebuilt.turns) == 1
    live = broker._conversation_turns[0]
    replayed = rebuilt.turns[0]
    for field in ("id", "content", "parts", "metadata", "visibility"):
        assert getattr(live, field) == replayed[field]
    assert live.content == (streamed_text or _ERROR)
    assert live.metadata["status"] == "error"
    assert live.metadata["error"] == _ERROR
    assert rebuilt.partial is True
