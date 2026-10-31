"""Shared fixtures for the tests/ravn test suite."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from ravn.adapters.permission.allow_deny import AllowAllPermission, DenyAllPermission
from ravn.agent import RavnAgent
from ravn.cap_telemetry import reset_cap_hits
from ravn.domain.models import (
    LLMResponse,
    StopReason,
    StreamEvent,
    StreamEventType,
    TokenUsage,
)
from ravn.ports.llm import LLMPort
from ravn.ports.permission import PermissionPort
from tests.ravn.fixtures.fakes import EchoTool, FailingTool, InMemoryChannel

# Re-export fakes so tests can import from a single conftest location.
__all__ = [
    "AllowAllPermission",
    "DenyAllPermission",
    "EchoTool",
    "FailingTool",
    "InMemoryChannel",
    "MockLLM",
    "make_agent",
    "make_text_response",
]


# ---------------------------------------------------------------------------
# MockLLM — scripted, deterministic LLM for testing
# ---------------------------------------------------------------------------


class MockLLM(LLMPort):
    """Scripted LLM that replays a fixed list of responses deterministically.

    Each call to ``generate`` or ``stream`` consumes the next response from the
    list.  When the list is exhausted, ``StopIteration`` is raised so tests
    fail clearly rather than silently repeating the last response.
    """

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = iter(responses)

    async def generate(
        self,
        messages: list[dict],
        *,
        tools: list[dict],
        system: str,
        model: str,
        max_tokens: int,
        thinking: dict | None = None,
    ) -> LLMResponse:
        return next(self._responses)

    async def stream(
        self,
        messages: list[dict],
        *,
        tools: list[dict],
        system: str,
        model: str,
        max_tokens: int,
        thinking: dict | None = None,
    ) -> AsyncIterator[StreamEvent]:
        response = next(self._responses)

        if response.content:
            yield StreamEvent(type=StreamEventType.TEXT_DELTA, text=response.content)

        for tool_call in response.tool_calls:
            yield StreamEvent(type=StreamEventType.TOOL_CALL, tool_call=tool_call)

        yield StreamEvent(
            type=StreamEventType.MESSAGE_DONE,
            usage=response.usage,
            stop_reason=(
                StopReason.MAX_TOKENS if response.stop_reason == StopReason.MAX_TOKENS else None
            ),
        )


# ---------------------------------------------------------------------------
# Shared agent factory
# ---------------------------------------------------------------------------


def make_agent(
    llm: LLMPort,
    tools=None,
    *,
    channel: InMemoryChannel | None = None,
    permission: PermissionPort | None = None,
    system_prompt: str = "You are a test assistant.",
    max_iterations: int = 10,
    user_input_fn=None,
) -> tuple[RavnAgent, InMemoryChannel]:
    """Construct a RavnAgent wired to an InMemoryChannel for test assertions."""
    ch = channel or InMemoryChannel()
    perm = permission or AllowAllPermission()
    agent = RavnAgent(
        llm=llm,
        tools=tools or [],
        channel=ch,
        permission=perm,
        system_prompt=system_prompt,
        model="claude-sonnet-4-6",
        max_tokens=1024,
        max_iterations=max_iterations,
        user_input_fn=user_input_fn,
    )
    return agent, ch


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------


def make_text_response(
    text: str = "Hello!",
    *,
    input_tokens: int = 10,
    output_tokens: int = 5,
) -> LLMResponse:
    """Build a simple text-only LLMResponse for scripting the MockLLM."""
    return LLMResponse(
        content=text,
        tool_calls=[],
        stop_reason=StopReason.END_TURN,
        usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens),
    )


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_channel() -> InMemoryChannel:
    return InMemoryChannel()


@pytest.fixture
def mock_permission_enforcer() -> AllowAllPermission:
    return AllowAllPermission()


@pytest.fixture
def deny_permission() -> DenyAllPermission:
    return DenyAllPermission()


@pytest.fixture
def echo_tool() -> EchoTool:
    return EchoTool()


@pytest.fixture
def failing_tool() -> FailingTool:
    return FailingTool()


@pytest.fixture
def mock_llm() -> MockLLM:
    """A MockLLM pre-loaded with a single text response."""
    return MockLLM([make_text_response("Hello!")])


@pytest.fixture(autouse=True)
def _fresh_cap_hits() -> None:
    """Cap-hit counts are process-wide; each test starts from none."""
    reset_cap_hits()
