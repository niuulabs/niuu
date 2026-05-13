# Adding a New LLM Provider

This guide walks through adding a new LLM provider adapter to Ravn.

## Step 1: Implement LLMPort

Create a new adapter in `src/ravn/adapters/llm/`:

```python
# src/ravn/adapters/llm/my_provider.py
from collections.abc import AsyncIterator

from ravn.domain.models import (
    LLMResponse,
    Message,
    StreamEvent,
    StreamEventType,
    TokenUsage,
    ToolCall,
)
from ravn.ports.llm import LLMPort


class MyProviderAdapter(LLMPort):
    """Adapter for MyProvider LLM API."""

    def __init__(
        self,
        api_key: str = "",
        model: str = "my-model-v1",
        base_url: str = "https://api.myprovider.com",
        max_tokens: int = 8192,
        timeout: float = 120.0,
    ):
        self._api_key = api_key
        self._model = model
        self._base_url = base_url
        self._max_tokens = max_tokens
        self._timeout = timeout

    async def generate(
        self,
        messages: list[dict],
        *,
        tools: list[dict],
        system: str | list[dict],
        model: str,
        max_tokens: int,
        thinking: dict | None = None,
    ) -> LLMResponse:
        """Non-streaming completion."""
        # Convert messages to provider format
        provider_messages = self._convert_messages(messages)
        provider_tools = self._convert_tools(tools)

        # Make API call
        response = await self._call_api(
            provider_messages, provider_tools, system, model, max_tokens
        )

        # Convert response back to Ravn format
        return self._parse_response(response)

    def stream(
        self,
        messages: list[dict],
        *,
        tools: list[dict],
        system: str | list[dict],
        model: str,
        max_tokens: int,
        thinking: dict | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Streaming completion."""
        provider_messages = self._convert_messages(messages)
        provider_tools = self._convert_tools(tools)

        async for chunk in self._stream_api(
            provider_messages, provider_tools, system, model, max_tokens
        ):
            yield self._parse_stream_event(chunk)
```

### LLMPort Interface

| Method | Returns | Description |
|--------|---------|-------------|
| `stream(messages, *, tools, system, model, max_tokens, thinking)` | `AsyncIterator[StreamEvent]` | Stream response events as they arrive. |
| `generate(messages, *, tools, system, model, max_tokens, thinking)` | `LLMResponse` | Non-streaming completion. |
| `supports_thinking` (property) | `bool` | Whether adapter supports extended thinking (default: `False`). |

All parameters after `messages` are **keyword-only** (`*`). The `system`
parameter can be a plain string or a list of Anthropic-format text blocks.

## Step 2: Stream Event Types

Your adapter must emit the correct `StreamEvent` types:

| Event Type | When | Data |
|-----------|------|------|
| `TEXT_DELTA` | Text chunk received | `content: str` |
| `TOOL_CALL` | Tool call detected | `tool_call: ToolCall` |
| `THINKING` | Thinking block (if supported) | `content: str` |
| `MESSAGE_DONE` | Response complete | `usage: TokenUsage, stop_reason: StopReason` |

Example stream emission:

```python
async def stream(self, ...):
    # Text chunks
    yield StreamEvent(type=StreamEventType.TEXT_DELTA, content="Hello")
    yield StreamEvent(type=StreamEventType.TEXT_DELTA, content=" world")

    # Tool calls
    yield StreamEvent(
        type=StreamEventType.TOOL_CALL,
        tool_call=ToolCall(
            id="call_123",
            name="read_file",
            input={"path": "/tmp/file.txt"},
        ),
    )

    # Done
    yield StreamEvent(
        type=StreamEventType.MESSAGE_DONE,
        usage=TokenUsage(input_tokens=100, output_tokens=50),
        stop_reason=StopReason.TOOL_USE,
    )
```

## Step 3: Tool Format Conversion

Different LLM providers use different tool schemas. Convert between Ravn's
format and the provider's format:

```python
def _convert_tools(self, tools: list[dict]) -> list[dict]:
    """Convert Ravn tool definitions to provider format."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
            },
        }
        for tool in tools
    ]
```

## Step 4: Register as Provider

Configure in `ravn.yaml`:

```yaml
llm:
  provider:
    adapter: "ravn.adapters.llm.my_provider.MyProviderAdapter"
    kwargs:
      model: "my-model-v1"
      base_url: "https://api.myprovider.com"
    secret_kwargs_env:
      api_key: "MY_PROVIDER_API_KEY"
```

## Step 5: Fallback Chain Integration

Add as a fallback provider:

```yaml
llm:
  provider:
    adapter: ravn.adapters.llm.anthropic.AnthropicAdapter
  fallbacks:
    - adapter: ravn.adapters.llm.my_provider.MyProviderAdapter
      kwargs:
        model: "my-model-v1"
      secret_kwargs_env:
        api_key: "MY_PROVIDER_API_KEY"
```

The `FallbackLLMAdapter` tries the primary provider first, then falls back
to each provider in order on 429/5xx errors.

**Important:** Extended thinking is Anthropic-only. The fallback adapter
automatically skips thinking parameters for non-Anthropic providers.

## Step 6: Write Tests

Test against the `LLMPort` interface:

```python
import pytest
from ravn.adapters.llm.my_provider import MyProviderAdapter
from ravn.domain.models import Message


@pytest.fixture
def adapter():
    return MyProviderAdapter(api_key="test", model="test-model")


class TestMyProviderAdapter:
    @pytest.mark.asyncio
    async def test_complete(self, adapter):
        messages = [Message(role="user", content="hello")]
        response = await adapter.complete(messages)
        assert response.content
        assert response.usage.input_tokens > 0

    @pytest.mark.asyncio
    async def test_stream(self, adapter):
        messages = [Message(role="user", content="hello")]
        events = []
        async for event in adapter.stream(messages):
            events.append(event)
        assert any(e.type == StreamEventType.MESSAGE_DONE for e in events)

    @pytest.mark.asyncio
    async def test_tool_calls(self, adapter):
        messages = [Message(role="user", content="read /tmp/test")]
        tools = [{"name": "read_file", "description": "Read", "input_schema": {...}}]
        response = await adapter.complete(messages, tools=tools)
        # Verify tool calls are properly formatted
```
