import asyncio

import pytest

from niuu.adapters.cli.runtime import CliTurnRunner, filter_cli_event
from niuu.ports.cli import CLITransport


class StubTransport(CLITransport):
    def __init__(self, events_by_prompt: dict[str, list[dict]]) -> None:
        super().__init__()
        self._events_by_prompt = events_by_prompt
        self._alive = True
        self.sent_prompts: list[str] = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        self._alive = False

    async def send_message(self, content: str) -> None:
        self.sent_prompts.append(content)
        for event in self._events_by_prompt[content]:
            await self._emit(event)

    @property
    def session_id(self) -> str | None:
        return None

    @property
    def last_result(self) -> dict | None:
        return None

    @property
    def is_alive(self) -> bool:
        return self._alive


@pytest.mark.asyncio
async def test_cli_turn_runner_prefers_result_text_and_restores_callback() -> None:
    transport = StubTransport(
        {
            "first": [
                {"type": "assistant", "message": {"content": "draft"}},
                {"type": "result", "result": "final"},
            ]
        }
    )
    forwarded: list[dict] = []

    async def original_callback(data: dict) -> None:
        forwarded.append(data)

    transport.on_event(original_callback)
    runner = CliTurnRunner(transport)

    result = await runner.run_prompt("first", "req-1")

    assert result == "final"
    assert forwarded == [
        {"type": "assistant", "message": {"content": "draft"}},
        {"type": "result", "result": "final"},
    ]
    assert transport.event_callback is original_callback
    assert runner.pending_responses == {}


@pytest.mark.asyncio
async def test_cli_turn_runner_falls_back_to_assistant_content() -> None:
    transport = StubTransport(
        {
            "first": [
                {"type": "assistant", "message": {"content": "partial response"}},
                {"type": "result", "result": ""},
            ]
        }
    )
    runner = CliTurnRunner(transport)

    result = await runner.run_prompt("first", "req-1")

    assert result == "partial response"


@pytest.mark.asyncio
async def test_cli_turn_runner_raises_when_transport_reports_error() -> None:
    transport = StubTransport(
        {
            "first": [
                {"type": "error", "error": "authentication expired"},
                {"type": "result", "result": ""},
            ]
        }
    )
    runner = CliTurnRunner(transport)

    with pytest.raises(RuntimeError, match="authentication expired"):
        await runner.run_prompt("first", "req-1")

    assert runner.pending_responses == {}


@pytest.mark.asyncio
async def test_cli_turn_runner_falls_back_to_streamed_delta_text() -> None:
    transport = StubTransport(
        {
            "first": [
                {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "delta response"},
                },
                {"type": "result", "result": ""},
            ]
        }
    )
    runner = CliTurnRunner(transport)

    result = await runner.run_prompt("first", "req-1")

    assert result == "delta response"


@pytest.mark.asyncio
async def test_cli_turn_runner_concatenates_streamed_token_chunks_without_newlines() -> None:
    transport = StubTransport(
        {
            "first": [
                {"type": "content_block_delta", "delta": {"text": "STRUCT"}},
                {"type": "content_block_delta", "delta": {"text": "URED"}},
                {"type": "content_block_delta", "delta": {"text": " OUT"}},
                {"type": "content_block_delta", "delta": {"text": "COME"}},
                {"type": "result", "result": ""},
            ]
        }
    )
    runner = CliTurnRunner(transport)

    result = await runner.run_prompt("first", "req-1")

    assert result == "STRUCTURED OUTCOME"


@pytest.mark.asyncio
async def test_cli_turn_runner_separates_distinct_text_blocks() -> None:
    transport = StubTransport(
        {
            "first": [
                {"type": "content_block_start", "content_block": {"type": "text"}},
                {"type": "content_block_delta", "delta": {"text": "First"}},
                {"type": "content_block_delta", "delta": {"text": " block."}},
                {"type": "content_block_stop"},
                {"type": "content_block_start", "content_block": {"type": "text"}},
                {"type": "content_block_delta", "delta": {"text": "Second"}},
                {"type": "content_block_delta", "delta": {"text": " block."}},
                {"type": "content_block_stop"},
                {"type": "result", "result": ""},
            ]
        }
    )
    runner = CliTurnRunner(transport)

    result = await runner.run_prompt("first", "req-1")

    assert result == "First block.\n\nSecond block."


@pytest.mark.asyncio
async def test_cli_turn_runner_serializes_overlapping_prompts() -> None:
    emitted: list[str] = []

    class SlowTransport(StubTransport):
        async def send_message(self, content: str) -> None:
            self.sent_prompts.append(content)
            await asyncio.sleep(0.01)
            emitted.append(content)
            await self._emit({"type": "result", "result": f"done:{content}"})

    transport = SlowTransport({})
    runner = CliTurnRunner(transport)

    first = asyncio.create_task(runner.run_prompt("first", "req-1"))
    second = asyncio.create_task(runner.run_prompt("second", "req-2"))
    results = await asyncio.gather(first, second)

    assert results == ["done:first", "done:second"]
    assert emitted == ["first", "second"]


@pytest.mark.asyncio
async def test_cancel_pending_cancels_registered_futures() -> None:
    transport = StubTransport({})
    runner = CliTurnRunner(transport)
    future = asyncio.get_running_loop().create_future()
    runner.pending_responses["req-1"] = future

    await runner.cancel_pending()

    assert future.cancelled()
    assert runner.pending_responses == {}


def test_filter_cli_event_drops_keepalive_and_empty_deltas() -> None:
    assert filter_cli_event({"type": "keep_alive"}) is None
    assert filter_cli_event({"type": "content_block_delta", "delta": {}}) is None
    assert filter_cli_event({"type": "content_block_delta", "delta": {"text": "hi"}}) == {
        "type": "content_block_delta",
        "delta": {"text": "hi"},
    }


async def test_runner_keeps_unidentified_stream_chunk_bytes_exact() -> None:
    text = "Café 東京\n\n```py\nprint('α')\n```"
    events = [
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": char}}
        for char in text
    ]
    events.append({"type": "result", "result": ""})
    assert (
        await CliTurnRunner(StubTransport({"prompt": events})).run_prompt("prompt", "request")
        == text
    )


async def test_runner_reconciles_each_whole_text_item_after_partial_deltas() -> None:
    events = [
        {
            "type": "content_block_start",
            "item_id": "a",
            "index": 0,
            "content_block": {"type": "text", "id": "a", "text": "", "complete": False},
        },
        {
            "type": "content_block_delta",
            "item_id": "a",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Check"},
        },
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "id": "a", "text": "Checking.", "complete": True}]
            },
        },
        {"type": "content_block_stop", "item_id": "a", "index": 0},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "id": "b",
                        "text": "Final answer.",
                        "phase": "final_answer",
                        "complete": True,
                    }
                ]
            },
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "id": "b",
                        "text": "Final answer.",
                        "phase": "final_answer",
                        "complete": True,
                    }
                ]
            },
        },
        {"type": "result", "result": ""},
    ]
    result = await CliTurnRunner(StubTransport({"prompt": events})).run_prompt("prompt", "request")
    assert result == "Checking.\n\nFinal answer."


async def test_runner_captures_completion_only_block_lists() -> None:
    transport = StubTransport(
        {
            "prompt": [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "text",
                                "id": "native-a",
                                "text": "Complete.",
                                "complete": True,
                            }
                        ]
                    },
                },
                {"type": "result", "result": ""},
            ]
        }
    )
    assert await CliTurnRunner(transport).run_prompt("prompt", "request") == "Complete."
