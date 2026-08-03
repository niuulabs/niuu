"""A resident says who it is, whichever wire protocol it routes over.

Bifröst speaks OpenAI as well as Anthropic, so a resident can point at the
gateway without changing adapters — but only the Anthropic-shaped adapter sent
attribution, so one that switched arrived anonymous.
"""

from __future__ import annotations

import pytest

from ravn.adapters.llm.bifrost import (
    HEADER_AGENT_ID,
    HEADER_LEGACY_AGENT_ID,
    BifrostAdapter,
)
from ravn.adapters.llm.openai import OpenAICompatibleAdapter


def test_the_bifrost_adapter_sends_the_name_bifrost_reads() -> None:
    headers = BifrostAdapter(agent_id="muninn", session_id="sess-1")._headers()

    assert headers[HEADER_AGENT_ID] == "muninn"
    # And the old spelling, so a gateway on an older image still attributes.
    assert headers[HEADER_LEGACY_AGENT_ID] == "muninn"
    assert HEADER_AGENT_ID.lower() == "x-agent-id"


def test_the_openai_adapter_can_say_who_is_calling() -> None:
    headers = OpenAICompatibleAdapter(
        base_url="https://bifrost.test",
        agent_id="gondul",
        session_id="sess-2",
    )._headers()

    assert headers["x-agent-id"] == "gondul"
    assert headers["x-session-id"] == "sess-2"


def test_an_unattributed_openai_adapter_sends_no_identity() -> None:
    """Pointed at a plain vLLM there is nobody to tell, and nothing is added."""
    headers = OpenAICompatibleAdapter(base_url="https://vllm.test")._headers()

    assert "x-agent-id" not in headers
    assert "x-session-id" not in headers


@pytest.mark.asyncio
async def test_a_gateway_behind_auth_gets_a_minted_credential() -> None:
    """Bifröst sits behind an Envoy that validates a workload JWT.

    Without a credential a resident can only ever talk straight to a model
    server, which is why none of them routed through the gateway that meters
    and attributes their calls.
    """
    adapter = OpenAICompatibleAdapter(
        base_url="https://bifrost.test",
        agent_id="ivaldi",
        auth_adapter="niuu.adapters.outbound.http_auth.StaticBearerTokenAuthAdapter",
        auth_kwargs={"token": "minted"},
    )

    headers = await adapter._request_headers()

    assert headers["Authorization"] == "Bearer minted"
    assert headers["x-agent-id"] == "ivaldi"


@pytest.mark.asyncio
async def test_an_adapter_with_no_auth_sends_none() -> None:
    adapter = OpenAICompatibleAdapter(base_url="https://vllm.test")

    assert "Authorization" not in await adapter._request_headers()
