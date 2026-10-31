"""``role: system`` messages stay where the client put them instead of failing the turn.

Only system messages that open the conversation join the system prompt; the
rest stay in the turn they belong to, so every request of a growing
conversation starts with the previous one and a provider's prefix cache can
reuse it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import ValidationError

from bifrost.app import create_app
from bifrost.config import BifrostConfig, ProviderConfig
from bifrost.translation.models import (
    AnthropicRequest,
    CacheControl,
    Message,
    TextBlock,
    ToolResultBlock,
)
from bifrost.translation.to_openai import anthropic_to_openai


def _validate(messages: list[Any], system: Any = None) -> AnthropicRequest:
    body: dict[str, Any] = {"model": "m", "messages": messages}
    if system is not None:
        body["system"] = system
    return AnthropicRequest.model_validate(body)


def _system(content: Any) -> dict[str, Any]:
    return {"role": "system", "content": content}


_TOOL_USE = {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "ls"}}
_TOOL_RESULT = {"type": "tool_result", "tool_use_id": "toolu_1", "content": "README.md"}


# ---------------------------------------------------------------------------
# The fold
# ---------------------------------------------------------------------------


def test_leading_system_messages_join_the_system_prompt() -> None:
    request = _validate(
        [
            _system("Tools changed: none available."),
            _system([{"type": "text", "text": "Answer in English."}]),
            {"role": "user", "content": "hi"},
        ],
        system="Be brief.",
    )
    assert request.messages == [Message(role="user", content="hi")]
    assert request.system == [
        TextBlock(text="Be brief."),
        TextBlock(text="Tools changed: none available."),
        TextBlock(text="Answer in English."),
    ]


def test_leading_system_messages_extend_block_system_prompts() -> None:
    request = _validate(
        [_system("B"), {"role": "user", "content": "q"}],
        system=[{"type": "text", "text": "A", "cache_control": {"type": "ephemeral"}}],
    )
    assert [block.text for block in request.system] == ["A", "B"]
    assert request.system[0].cache_control is not None


def test_leading_system_messages_become_the_system_prompt_when_there_is_none() -> None:
    request = _validate([_system("B"), {"role": "user", "content": "q"}])
    assert request.system == [TextBlock(text="B")]
    assert request.messages == [Message(role="user", content="q")]


def test_a_system_message_after_a_user_turn_is_appended_to_it() -> None:
    request = _validate(
        [{"role": "user", "content": "hi"}, _system("<env>cwd: /repo</env>")],
        system="Be brief.",
    )
    assert request.system == "Be brief."
    assert request.messages == [
        Message(
            role="user", content=[TextBlock(text="hi"), TextBlock(text="<env>cwd: /repo</env>")]
        )
    ]


def test_a_system_message_after_a_tool_result_follows_the_result() -> None:
    request = _validate(
        [
            {"role": "user", "content": "list the files"},
            {"role": "assistant", "content": [_TOOL_USE]},
            {"role": "user", "content": [_TOOL_RESULT]},
            _system("<total_tokens>9000 tokens left</total_tokens>"),
        ]
    )
    assert request.system is None
    assert [m.role for m in request.messages] == ["user", "assistant", "user"]
    assert request.messages[-1].content == [
        ToolResultBlock(tool_use_id="toolu_1", content="README.md"),
        TextBlock(text="<total_tokens>9000 tokens left</total_tokens>"),
    ]


def test_consecutive_system_messages_keep_their_order() -> None:
    request = _validate(
        [
            {"role": "user", "content": "q"},
            _system("one"),
            _system([{"type": "text", "text": "two"}, {"type": "text", "text": "three"}]),
        ]
    )
    assert request.messages[0].content == [
        TextBlock(text="q"),
        TextBlock(text="one"),
        TextBlock(text="two"),
        TextBlock(text="three"),
    ]


def test_a_system_message_after_an_assistant_turn_leads_the_next_user_turn() -> None:
    request = _validate(
        [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a"},
            _system("Tools changed."),
            {"role": "user", "content": "next"},
        ]
    )
    assert request.messages[-1] == Message(
        role="user", content=[TextBlock(text="Tools changed."), TextBlock(text="next")]
    )


def test_a_system_message_between_a_tool_call_and_its_result_goes_behind_the_result() -> None:
    request = _validate(
        [
            {"role": "user", "content": "list the files"},
            {"role": "assistant", "content": [_TOOL_USE]},
            _system("Tools changed."),
            {"role": "user", "content": [_TOOL_RESULT, {"type": "text", "text": "and then?"}]},
        ]
    )
    # A tool call's results must open the user turn that follows it.
    assert [m.role for m in request.messages] == ["user", "assistant", "user"]
    assert request.messages[-1].content == [
        ToolResultBlock(tool_use_id="toolu_1", content="README.md"),
        TextBlock(text="Tools changed."),
        TextBlock(text="and then?"),
    ]


def test_tool_result_blocks_given_as_models_still_lead_the_turn() -> None:
    result = ToolResultBlock(tool_use_id="toolu_1", content="README.md")
    request = _validate(
        [
            {"role": "user", "content": "list the files"},
            {"role": "assistant", "content": [_TOOL_USE]},
            _system("Tools changed."),
            {"role": "user", "content": [result]},
        ]
    )
    assert request.messages[-1].content == [result, TextBlock(text="Tools changed.")]


def test_a_system_message_between_assistant_turns_becomes_a_user_turn() -> None:
    request = _validate(
        [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a1"},
            _system("Continue."),
            {"role": "assistant", "content": "a2"},
        ]
    )
    assert request.messages == [
        Message(role="user", content="q"),
        Message(role="assistant", content="a1"),
        Message(role="user", content=[TextBlock(text="Continue.")]),
        Message(role="assistant", content="a2"),
    ]


def test_a_system_message_ending_the_conversation_after_an_assistant_turn_is_a_user_turn() -> None:
    request = _validate(
        [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a1"},
            _system("Continue."),
            _system("Be brief."),
        ]
    )
    assert request.system is None
    assert request.messages[-1] == Message(
        role="user", content=[TextBlock(text="Continue."), TextBlock(text="Be brief.")]
    )


@pytest.mark.parametrize("content", ["", [], [{"type": "text", "text": ""}]])
def test_system_messages_with_empty_text_are_dropped(content: Any) -> None:
    request = _validate(
        [
            _system(content),
            {"role": "user", "content": "q"},
            _system(content),
            {"role": "assistant", "content": "a"},
            _system(content),
        ]
    )
    assert request.system is None
    assert request.messages == [
        Message(role="user", content="q"),
        Message(role="assistant", content="a"),
    ]


_IMAGE = {"type": "image", "source": {"type": "url", "url": "https://x/y.png"}}


@pytest.mark.parametrize(
    "system_message",
    [
        {"role": "system"},
        _system(None),
        _system(5),
        _system({"type": "text", "text": "note"}),
        _system(["raw string"]),
        _system([_IMAGE]),
        _system([{"type": "text", "text": "note"}, _IMAGE]),
        _system([{"type": "text"}]),
        _system([{"type": "text", "text": None}]),
        _system([{"type": "text", "text": 5}]),
    ],
)
@pytest.mark.parametrize("position", [0, 1, 2])
def test_system_messages_that_are_not_text_fail_validation(
    system_message: dict[str, Any], position: int
) -> None:
    messages: list[Any] = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a"},
    ]
    messages.insert(position, system_message)
    with pytest.raises(ValidationError, match="role: system"):
        _validate(messages)


def test_a_malformed_system_message_is_rejected_with_422(client: TestClient) -> None:
    response = client.post(
        "/v1/messages",
        json={
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "q"}, _system([_IMAGE])],
        },
    )
    assert response.status_code == 422
    assert "role: system" in response.json()["detail"]


def test_only_the_text_of_a_system_message_moves() -> None:
    request = _validate(
        [
            {
                "role": "user",
                "content": [{"type": "text", "text": "q", "cache_control": {"type": "ephemeral"}}],
            },
            _system([{"type": "text", "text": "note", "cache_control": {"type": "ephemeral"}}]),
        ]
    )
    # The client's own breakpoint stays; the moved text brings none of its own.
    assert request.messages[0].content == [
        TextBlock(text="q", cache_control=CacheControl()),
        TextBlock(text="note"),
    ]


def test_moved_text_reaches_an_openai_engine_a_line_after_the_users_text() -> None:
    request = _validate([{"role": "user", "content": "hi"}, _system("Answer in English.")])
    payload = anthropic_to_openai(request, "m")
    assert payload["messages"] == [{"role": "user", "content": "hi\nAnswer in English."}]


def test_requests_without_system_messages_are_untouched() -> None:
    request = _validate([{"role": "user", "content": "q"}])
    assert request.system is None
    assert request.messages == [Message(role="user", content="q")]


# ---------------------------------------------------------------------------
# What the upstream receives across the turns of a Claude Code session
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = [
    {
        "type": "text",
        "text": "You are an interactive coding agent.",
        "cache_control": {"type": "ephemeral"},
    }
]
_TOOLS = [
    {
        "name": "Bash",
        "description": "Run a shell command.",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    }
]
_PROMPT = "Make the failing test pass."
_ENVIRONMENT = "<env>\nWorking directory: /work/repo\nPlatform: linux\n</env>"
_TOKEN_BUDGET = 150_000
_TOKENS_PER_ROUND = 1_000


def _tokens_left(tool_round: int) -> str:
    return (
        f"<total_tokens>{_TOKEN_BUDGET - tool_round * _TOKENS_PER_ROUND} tokens left</total_tokens>"
    )


def _claude_code_turn(tool_rounds: int, *, model: str, stream: bool = False) -> dict[str, Any]:
    """A request body shaped like Claude Code's against a custom base URL.

    The environment arrives as a system message after the first user turn, and
    the remaining token budget as one after every tool result.
    """
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": _PROMPT}]},
        _system(_ENVIRONMENT),
    ]
    for n in range(tool_rounds):
        call_id = f"toolu_{n}"
        messages += [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Running the tests."},
                    {
                        "type": "tool_use",
                        "id": call_id,
                        "name": "Bash",
                        "input": {"command": "pytest"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": call_id, "content": f"{n} failed"}
                ],
            },
            _system(_tokens_left(n)),
        ]
    return {
        "model": model,
        "max_tokens": 4096,
        "stream": stream,
        "system": _SYSTEM_PROMPT,
        "tools": _TOOLS,
        "messages": messages,
    }


def _upstream_bodies(
    config: BifrostConfig,
    url: str,
    reply: Callable[[], httpx.Response],
    *bodies: dict[str, Any],
) -> list[dict[str, Any]]:
    """POST each body to ``/v1/messages`` and return what the provider received."""
    received: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        received.append(json.loads(request.content))
        return reply()

    with respx.mock:
        respx.post(url).mock(side_effect=respond)
        with TestClient(create_app(config)) as client:
            for body in bodies:
                response = client.post("/v1/messages", json=body)
                assert response.status_code == 200, response.text
    return received


_VLLM_MODEL = "qwen3-coder"
_VLLM_URL = "https://vllm.test"


def _vllm_reply(stream: bool) -> httpx.Response:
    if not stream:
        return httpx.Response(
            200,
            json={
                "id": "c1",
                "choices": [
                    {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 1},
            },
        )
    chunks = [
        {"id": "c1", "choices": [{"delta": {"role": "assistant", "content": "ok"}}]},
        {
            "id": "c1",
            "choices": [{"delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 1},
        },
    ]
    events = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
    return httpx.Response(200, text=events + "data: [DONE]\n\n")


@pytest.mark.parametrize("stream", [False, True])
def test_inline_system_messages_keep_a_self_hosted_engines_prefix_stable(stream: bool) -> None:
    turn = _claude_code_turn(1, model=_VLLM_MODEL, stream=stream)
    next_turn = _claude_code_turn(2, model=_VLLM_MODEL, stream=stream)
    # The client only appends: tool call, tool result, token budget.
    assert next_turn["messages"][: len(turn["messages"])] == turn["messages"]
    assert len(next_turn["messages"]) == len(turn["messages"]) + 3

    config = BifrostConfig(
        providers={"vllm": ProviderConfig(base_url=_VLLM_URL, models=[_VLLM_MODEL])}
    )
    first, second = _upstream_bodies(
        config,
        f"{_VLLM_URL}/v1/chat/completions",
        lambda: _vllm_reply(stream),
        turn,
        next_turn,
    )

    # So must the engine's view of it: the next request starts with this one.
    assert second["messages"][: len(first["messages"])] == first["messages"]
    assert second["tools"] == first["tools"]
    assert first["messages"][0] == {"role": "system", "content": _SYSTEM_PROMPT[0]["text"]}
    assert [m["role"] for m in second["messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
        "user",
        "assistant",
        "tool",
        "user",
    ]
    # Every inline system text arrives, where the client put it, and each tool
    # result still directly follows the call it answers.
    assert second["messages"][1]["content"] == f"{_PROMPT}\n{_ENVIRONMENT}"
    for call, result, note, n in ((2, 3, 4, 0), (5, 6, 7, 1)):
        assert second["messages"][call]["tool_calls"][0]["id"] == f"toolu_{n}"
        assert second["messages"][result]["tool_call_id"] == f"toolu_{n}"
        assert second["messages"][note]["content"] == _tokens_left(n)


def _assert_valid_anthropic_conversation(messages: list[dict[str, Any]]) -> None:
    """Roles alternate from a user turn; each tool call's results open the next turn."""
    assert [m["role"] for m in messages] == [
        "user" if i % 2 == 0 else "assistant" for i in range(len(messages))
    ]
    for i, message in enumerate(messages):
        if message["role"] != "assistant" or isinstance(message["content"], str):
            continue
        call_ids = [b["id"] for b in message["content"] if b["type"] == "tool_use"]
        if not call_ids:
            continue
        answer = messages[i + 1]["content"]
        assert [b.get("tool_use_id") for b in answer[: len(call_ids)]] == call_ids
        assert all(b["type"] != "tool_result" for b in answer[len(call_ids) :])


def test_inline_system_messages_reach_anthropic_as_a_valid_stable_request() -> None:
    model = "claude-sonnet-4-6"
    url = "https://anthropic.test"
    config = BifrostConfig(providers={"anthropic": ProviderConfig(base_url=url, models=[model])})
    reply = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "ok"}],
        "model": model,
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 1},
    }
    first, second = _upstream_bodies(
        config,
        f"{url}/v1/messages",
        lambda: httpx.Response(200, json=reply),
        _claude_code_turn(1, model=model),
        _claude_code_turn(2, model=model),
    )

    assert second["system"] == first["system"] == _SYSTEM_PROMPT
    assert second["messages"][: len(first["messages"])] == first["messages"]
    _assert_valid_anthropic_conversation(second["messages"])
    assert second["messages"][0]["content"] == [
        {"type": "text", "text": _PROMPT},
        {"type": "text", "text": _ENVIRONMENT},
    ]
    assert second["messages"][-1]["content"][-1] == {"type": "text", "text": _tokens_left(1)}
