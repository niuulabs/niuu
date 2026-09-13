"""``role: system`` messages fold into the system prompt instead of failing the turn."""

from __future__ import annotations

from bifrost.translation.models import AnthropicRequest, TextBlock


def test_system_messages_move_into_the_system_prompt() -> None:
    request = AnthropicRequest.model_validate(
        {
            "model": "llama3.2:latest",
            "system": "Be brief.",
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "system", "content": "Tools changed: none available."},
                {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
                {"role": "system", "content": [{"type": "text", "text": "Answer in English."}]},
            ],
        }
    )
    assert [m.role for m in request.messages] == ["user", "assistant"]
    assert request.system == [
        TextBlock(text="Be brief."),
        TextBlock(text="Tools changed: none available."),
        TextBlock(text="Answer in English."),
    ]


def test_system_messages_extend_block_system_prompts() -> None:
    request = AnthropicRequest.model_validate(
        {
            "model": "m",
            "system": [{"type": "text", "text": "A", "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "system", "content": "B"}, {"role": "user", "content": "q"}],
        }
    )
    assert [block.text for block in request.system] == ["A", "B"]
    assert request.system[0].cache_control is not None


def test_requests_without_system_messages_are_untouched() -> None:
    request = AnthropicRequest.model_validate(
        {"model": "m", "messages": [{"role": "user", "content": "q"}]}
    )
    assert request.system is None
    assert [m.role for m in request.messages] == ["user"]
