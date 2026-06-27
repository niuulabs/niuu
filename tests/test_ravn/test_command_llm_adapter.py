from __future__ import annotations

import pytest

from ravn.adapters.llm.command import CommandLLMAdapter
from ravn.adapters.process_runner import CommandResult


@pytest.mark.asyncio
async def test_command_llm_stdout_becomes_response() -> None:
    calls = []

    async def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return CommandResult(0, '{"ok": true}', "")

    llm = CommandLLMAdapter(command="local-llm", args=["--print"], runner=runner)

    response = await llm.generate(
        [{"role": "user", "content": "hello"}],
        tools=[],
        system="system",
        model="fake-model",
        max_tokens=100,
    )

    assert response.content == '{"ok": true}'
    assert calls[0][0] == ["local-llm", "--print"]
    assert "## System" in calls[0][1]["input_text"]
    assert "hello" in calls[0][1]["input_text"]


@pytest.mark.asyncio
async def test_command_llm_invalid_exit_code_fails_clearly() -> None:
    async def runner(argv, **kwargs):
        return CommandResult(2, "", "bad auth")

    llm = CommandLLMAdapter(command="local-llm", runner=runner)

    with pytest.raises(RuntimeError, match="(?s)command failed \\(2\\).*bad auth"):
        await llm.generate([], tools=[], system="", model="fake-model", max_tokens=100)


@pytest.mark.asyncio
async def test_command_llm_timeout_fails_clearly() -> None:
    async def runner(argv, **kwargs):
        raise TimeoutError

    llm = CommandLLMAdapter(command="local-llm", runner=runner)

    with pytest.raises(RuntimeError, match="command timed out"):
        await llm.generate([], tools=[], system="", model="fake-model", max_tokens=100)


@pytest.mark.asyncio
async def test_command_llm_rejects_tools() -> None:
    llm = CommandLLMAdapter(command="local-llm")

    with pytest.raises(RuntimeError, match="does not support tools"):
        await llm.generate([], tools=[{"name": "x"}], system="", model="fake-model", max_tokens=100)


def test_command_llm_rejects_streaming() -> None:
    llm = CommandLLMAdapter(command="local-llm")

    with pytest.raises(RuntimeError, match="does not support streaming"):
        llm.stream([], tools=[], system="", model="fake-model", max_tokens=100)
