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
