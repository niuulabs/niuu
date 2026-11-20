"""LEX-1847: a provider failure is stored as ONE error turn, live and rebuilt alike.

Codex reports a provider 400 as an ``error`` notification and then a failed result whose
text is the same message (LEX-1842); both used to become assistant turns.
"""

from __future__ import annotations

from tests.test_domain.test_transcript_reducer_parity import (
    _broker,
    _drive,
    _live_turns,
    _rebuilt_turns,
)

MESSAGE = (
    '{"type":"error","status":400,"error":{"type":"invalid_request_error",'
    '"message":"The \'gpt-6-sol\' model is not supported when using Codex with a ChatGPT account"}}'
)


def user(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def failed_result(text: str) -> dict:
    return {
        "type": "result",
        "result": text,
        "is_error": True,
        "stop_reason": "error",
        "modelUsage": {},
    }


def assistant_turns(turns: list[dict]) -> list[dict]:
    return [turn for turn in turns if turn["role"] == "assistant"]


async def test_a_provider_failure_is_one_error_turn(tmp_path):
    broker = _broker(tmp_path)

    await _drive(
        broker, [user("hello"), {"type": "error", "error": MESSAGE}, failed_result(MESSAGE)]
    )

    replies = assistant_turns(_live_turns(broker))
    assert [(turn["content"], turn["metadata"]["status"]) for turn in replies] == [
        (MESSAGE, "error")
    ]
    assert _rebuilt_turns(broker) == _live_turns(broker)


async def test_a_failed_result_with_its_own_message_keeps_its_turn(tmp_path):
    broker = _broker(tmp_path)

    await _drive(
        broker,
        [user("hello"), {"type": "error", "error": "rate limited"}, failed_result("gave up")],
    )

    assert [turn["content"] for turn in assistant_turns(_live_turns(broker))] == [
        "rate limited",
        "gave up",
    ]
    assert _rebuilt_turns(broker) == _live_turns(broker)


async def test_text_streamed_before_the_failure_is_kept(tmp_path):
    broker = _broker(tmp_path)

    await _drive(
        broker,
        [
            user("hello"),
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Working"}]}},
            {"type": "error", "error": MESSAGE},
            failed_result(MESSAGE),
        ],
    )

    contents = [turn["content"] for turn in assistant_turns(_live_turns(broker))]
    assert contents[0] == "Working" and contents.count(MESSAGE) == 1
    assert _rebuilt_turns(broker) == _live_turns(broker)


async def test_an_error_after_reasoning_alone_is_still_one_error_turn(tmp_path):
    broker = _broker(tmp_path)

    await _drive(
        broker,
        [
            user("hello"),
            {
                "type": "content_block_delta",
                "delta": {"type": "thinking_delta", "thinking": "Let me try."},
            },
            {"type": "error", "error": MESSAGE},
            failed_result(MESSAGE),
        ],
    )

    contents = [turn["content"] for turn in assistant_turns(_live_turns(broker))]
    assert contents.count(MESSAGE) == 1
    assert _rebuilt_turns(broker) == _live_turns(broker)


async def test_a_later_failure_with_only_a_result_keeps_its_turn(tmp_path):
    broker = _broker(tmp_path)

    await _drive(
        broker,
        [
            user("hello"),
            {"type": "error", "error": MESSAGE},
            failed_result(MESSAGE),
            # A continuation fails again, this time with a result alone: it must show.
            failed_result(MESSAGE),
        ],
    )

    contents = [turn["content"] for turn in assistant_turns(_live_turns(broker))]
    assert contents.count(MESSAGE) == 2
    assert _rebuilt_turns(broker) == _live_turns(broker)


async def test_an_error_frame_text_is_read_the_same_way_on_both_paths(tmp_path):
    broker = _broker(tmp_path)

    await _drive(
        broker,
        [user("hello"), {"type": "error", "content": "", "error": "boom"}, failed_result("boom")],
    )

    assert [turn["content"] for turn in assistant_turns(_live_turns(broker))] == ["boom"]
    assert _rebuilt_turns(broker) == _live_turns(broker)


async def test_a_new_human_turn_clears_an_error_that_had_no_result(tmp_path):
    broker = _broker(tmp_path)

    await _drive(
        broker,
        [
            user("hello"),
            {"type": "error", "error": "rate limit"},  # no result follows
            user("try again"),
            failed_result("rate limit"),  # the new turn's own failure must show
        ],
    )

    contents = [turn["content"] for turn in assistant_turns(_live_turns(broker))]
    assert contents == ["rate limit", "rate limit"]
    assert _rebuilt_turns(broker) == _live_turns(broker)
