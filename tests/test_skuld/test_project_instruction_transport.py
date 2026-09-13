"""Project instruction bytes survive supported harness assembly (no provider calls).

These assert adapters' outgoing contracts, not model compliance or Grok acceptance
of its non-standard systemPrompt extension.
"""

from unittest.mock import AsyncMock

from skuld.transports.grok import GrokACPTransport
from skuld.transports.tmux_interactive import TmuxInteractiveTransport
from tests.test_skuld.test_codex_ws_transport import FakeWebSocket, _make_transport

INSTRUCTIONS = "## AGENTS.md\nKeep the original objectives; report material progress.\n"


async def test_codex_base_instructions_include_project_owned_guidance(tmp_path):
    transport = _make_transport(tmp_path, system_prompt=INSTRUCTIONS)
    transport._ws = FakeWebSocket()
    transport._alive = True
    transport._send_rpc = AsyncMock(side_effect=[{"userAgent": "codex"}, {"thread": {"id": "t"}}])
    transport._send_notification = AsyncMock()
    transport.on_event(AsyncMock())
    await transport._handshake()
    call = transport._send_rpc.await_args_list[1]
    assert call.args[0] == "thread/start"
    assert call.args[1]["baseInstructions"] == INSTRUCTIONS


def test_claude_tmux_appended_prompt_retains_project_bytes(tmp_path):
    transport = TmuxInteractiveTransport(str(tmp_path), system_prompt=INSTRUCTIONS)
    prompt = transport._composed_system_prompt()
    assert prompt.endswith(INSTRUCTIONS)
    assert "exactly one in_progress" not in prompt
    assert "Prefer subagents" not in prompt
    assert "Use the TodoWrite tool" not in prompt
    assert "present-file" in prompt


async def test_grok_forwards_project_bytes_in_existing_extension(tmp_path):
    transport = GrokACPTransport(str(tmp_path), system_prompt=INSTRUCTIONS)
    transport._acp_send = AsyncMock(return_value={"sessionId": "synthetic"})
    await transport._acp_new_session()
    assert transport._acp_send.await_args.args[1]["systemPrompt"] == INSTRUCTIONS
