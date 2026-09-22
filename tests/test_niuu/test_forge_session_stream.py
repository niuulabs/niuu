import asyncio

import httpx
import pytest
import respx

from niuu.adapters.inbound.forge_session_stream import merge_events, remote_events


@pytest.mark.asyncio
async def test_slow_or_failed_hosts_do_not_gate_healthy_events_and_disconnect_cleans_up():
    started = asyncio.Event()
    closed = asyncio.Event()

    async def slow():
        started.set()
        try:
            await asyncio.Event().wait()
            yield "unused", {}
        finally:
            closed.set()

    async def failed():
        raise ConnectionError("offline")
        yield  # pragma: no cover

    async def healthy():
        await started.wait()
        yield "session_activity", {"instance_id": "build-bro", "state": "idle"}
        await asyncio.Event().wait()

    stream = merge_events({"slow": slow, "failed": failed, "build-bro": healthy})
    frames = [await asyncio.wait_for(anext(stream), 1) for _ in range(2)]
    assert any(
        b'"instance_id": "build-bro"' in frame and b'"state": "idle"' in frame for frame in frames
    )
    assert any(b"forge_stream_status" in frame for frame in frames)
    await stream.aclose()
    assert closed.is_set()


@pytest.mark.asyncio
async def test_idle_stream_sends_keepalive_and_reconnects_failed_source():
    attempts = 0

    async def source():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("offline")
        yield "session_activity", {"state": "active"}
        await asyncio.Event().wait()

    stream = merge_events({"host": source}, retry_seconds=0, keepalive_seconds=0.01)
    assert b"reconnecting" in await anext(stream)
    assert b"active" in await anext(stream)
    assert await anext(stream) == b": keepalive\n\n"
    await stream.aclose()
    assert attempts == 2


@pytest.mark.asyncio
@respx.mock
async def test_remote_decodes_multiline_sse_and_ignores_malformed_records():
    route = respx.get("http://build/api/v1/forge/sessions/stream").mock(
        return_value=httpx.Response(
            200,
            text=': heartbeat\n\nevent: session_activity\ndata: {"state":\ndata: "idle"}\n\n'
            "event: junk\ndata: invalid\n\ndata: []\n\n",
        )
    )
    events = [
        event
        async for event in remote_events(
            "http://build/api/v1/forge/sessions/stream", {"x-auth-user-id": "reviewer"}
        )
    ]
    assert events == [("session_activity", {"state": "idle"})]
    assert route.calls[0].request.headers["x-auth-user-id"] == "reviewer"


@pytest.mark.asyncio
@respx.mock
async def test_remote_rejects_http_errors_instead_of_hanging_on_an_empty_stream():
    respx.get("http://build/stream").mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await anext(remote_events("http://build/stream", {}))
