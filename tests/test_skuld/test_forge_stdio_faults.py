"""Startup cancellation, resume and broken pipes across the stdio engines."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from skuld.transports.grok import GrokACPTransport
from skuld.transports.muse import MuseMSPTransport
from tests.test_skuld.test_muse_transport import SID, _FakeHost, _started_transport


@pytest.mark.parametrize("engine", ["grok", "muse"])
@pytest.mark.parametrize("failure", [FileNotFoundError("missing CLI"), asyncio.CancelledError()])
async def test_failed_spawn_clears_starting_state(tmp_path, engine, failure):
    cls = GrokACPTransport if engine == "grok" else MuseMSPTransport
    transport = cls(str(tmp_path))
    if engine == "grok":
        transport._preflight_auth = AsyncMock()
    with patch(f"skuld.transports.{engine}.asyncio.create_subprocess_exec", side_effect=failure):
        with pytest.raises(type(failure)):
            await transport.start()
    assert transport._starting is False
    assert transport._process is None
    assert not transport.is_alive


async def test_muse_resume_does_not_resend_the_original_initial_prompt(tmp_path):
    with patch.object(MuseMSPTransport, "send_message", new_callable=AsyncMock) as send:
        transport, host, _ = await _started_transport(
            tmp_path, resume_session_id=SID, initial_prompt="already executed task"
        )
        try:
            await asyncio.sleep(0)
            send.assert_not_awaited()
        finally:
            await transport.stop()


async def test_grok_eof_releases_handshake_rpc_immediately(tmp_path):
    transport = GrokACPTransport(str(tmp_path))
    host = _FakeHost()
    transport._process = host.process
    transport._stdout_reader = host.process.stdout
    request = asyncio.create_task(transport._acp_send("initialize", {}))
    await asyncio.sleep(0)
    await host.push(b"")
    try:
        await transport._reader_loop()
        with pytest.raises(RuntimeError, match="closed"):
            await asyncio.wait_for(request, 1)
        assert transport._pending == {}
    finally:
        request.cancel()
        await asyncio.gather(request, return_exceptions=True)


async def test_grok_broken_prompt_pipe_closes_turn_and_releases_request(tmp_path):
    transport = GrokACPTransport(str(tmp_path))
    host = _FakeHost()
    host.process.stdin.drain = AsyncMock(side_effect=BrokenPipeError("host exited"))
    transport._process = host.process
    events = []
    transport.on_event(AsyncMock(side_effect=events.append))
    await transport.send_message("work")
    assert not transport.is_turn_active
    assert transport._pending == {}
    results = [e for e in events if e["type"] == "result"]
    assert len(results) == 1
    assert results[0]["stop_reason"] == "error"


async def test_grok_handshake_write_failure_does_not_leak_rpc(tmp_path):
    transport = GrokACPTransport(str(tmp_path))
    host = _FakeHost()
    host.process.stdin.drain = AsyncMock(side_effect=BrokenPipeError("host exited"))
    transport._process = host.process
    with pytest.raises(BrokenPipeError):
        await transport._acp_send("initialize", {})
    assert transport._pending == {}
