"""The SDK transport reads big messages, and says so when it loses Claude's output.

stokk-apple (21 Nov 2026): Claude read a screenshot, the tool result (the image, base64) was one
stdout line over the SDK's 1 MiB default, and the SDK's reader stopped for good. Claude kept
working for hours, unseen; the broker kept the turn it was building as in progress, and served it
as the trailing ``in_progress`` row, so every client showed that night's half turn below the
morning's messages.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk._errors import CLIJSONDecodeError
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

from skuld.broker import Broker
from skuld.config import SkuldSettings
from skuld.transports.sdk import SDKTransport

#: One stream-json line the size of a Read of a large screenshot.
_IMAGE_LINE = json.dumps(
    {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "A" * 3_000_000}],
        },
    }
)


class _Stdout:
    def __init__(self, *chunks: str) -> None:
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


async def _read_with(options: ClaudeAgentOptions) -> list[dict]:
    transport = SubprocessCLITransport(prompt="", options=options)
    transport._process = SimpleNamespace(returncode=0, wait=AsyncMock(return_value=0))
    transport._stdout_stream = _Stdout(_IMAGE_LINE + "\n")
    return [message async for message in transport.read_messages()]


async def test_the_sdks_own_limit_stops_the_reader_on_one_screenshot() -> None:
    with pytest.raises(CLIJSONDecodeError, match="exceeded maximum buffer size"):
        await _read_with(ClaudeAgentOptions())


class _Client:
    """A Claude SDK client whose reader died on the first turn, as the SDK's does."""

    instances: list[_Client] = []

    def __init__(self, options: ClaudeAgentOptions) -> None:
        self.options = options
        self.query = AsyncMock()
        self.interrupt = AsyncMock()
        self.get_mcp_status = AsyncMock(return_value={"mcpServers": []})
        self.closed = False
        _Client.instances.append(self)

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_exc) -> bool:
        self.closed = True
        return False

    async def receive_response(self):
        yield AssistantMessage(
            content=[TextBlock(text="Now unit tests")],
            model="claude-opus-5-5",
            session_id="native-1",
        )
        if len(_Client.instances) == 1:
            raise CLIJSONDecodeError(
                "JSON message exceeded maximum buffer size of 1048576 bytes", ValueError()
            )
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="native-1",
            total_cost_usd=0.0,
            usage={"input_tokens": 1, "output_tokens": 1},
            result="done",
        )


@pytest.fixture
def clients(monkeypatch) -> list[_Client]:
    _Client.instances = []
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", _Client)
    return _Client.instances


async def test_the_node_setting_reaches_the_sdk_and_reads_the_screenshot(clients, tmp_path) -> None:
    settings = SkuldSettings(session={"id": "s", "workspace_dir": str(tmp_path)})
    transport = SDKTransport(
        workspace_dir=str(tmp_path),
        claude_sdk_max_message_bytes=settings.claude_sdk_max_message_bytes,
    )
    await transport._connect_client()

    options = clients[0].options
    assert options.max_buffer_size == settings.claude_sdk_max_message_bytes
    [message] = await _read_with(options)
    assert message["type"] == "user"


async def test_a_lost_stream_is_an_error_and_the_next_message_resumes(clients, tmp_path) -> None:
    transport = SDKTransport(workspace_dir=str(tmp_path))
    events: list[dict] = []

    async def collect(event: dict) -> None:
        events.append(event)

    transport.on_event(collect)

    await transport.send_message("build the app")

    errors = [e for e in events if e.get("type") == "error"]
    assert len(errors) == 1
    assert "Lost Claude's output" in errors[0]["error"]["message"]
    assert clients[0].closed is True
    assert transport.is_alive is False

    await transport.send_message("done?")

    assert len(clients) == 2
    assert clients[1].options.resume == "native-1"
    assert events[-1]["type"] == "result"
    await transport.stop()


async def test_the_broker_closes_the_half_built_turn_instead_of_trailing_it(tmp_path) -> None:
    broker = Broker(
        SkuldSettings(
            session={"id": "lost-stream", "workspace_dir": str(tmp_path)},
            volundr_api_url="http://forge.test",
            transport="subprocess",
            chronicle_watcher_enabled=False,
        )
    )
    broker._http_client = SimpleNamespace(post=AsyncMock())
    broker._http_client_jwt = None
    broker._report_activity_state = AsyncMock()
    broker._report_timeline_event = AsyncMock()
    broker._pending_assistant_content = "Now unit tests"
    broker._pending_assistant_parts = [{"type": "text", "text": "Now unit tests"}]

    await broker._handle_cli_event(
        {"type": "error", "error": {"message": "Lost Claude's output (too big)."}}
    )

    assert broker._serialize_in_progress_turn() is None
    assert [t.content for t in broker._conversation_turns] == [
        "Now unit tests",
        "Lost Claude's output (too big).",
    ]
