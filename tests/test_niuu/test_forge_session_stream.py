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
    client = httpx.AsyncClient()
    events = [
        event
        async for event in remote_events(
            client,
            "http://build/api/v1/forge/sessions/stream",
            {"x-auth-user-id": "reviewer"},
        )
    ]
    assert events == [("session_activity", {"state": "idle"})]
    assert route.calls[0].request.headers["x-auth-user-id"] == "reviewer"


@pytest.mark.asyncio
@respx.mock
async def test_remote_rejects_http_errors_instead_of_hanging_on_an_empty_stream():
    respx.get("http://build/stream").mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await anext(remote_events(httpx.AsyncClient(), "http://build/stream", {}))


@pytest.mark.asyncio
@respx.mock
async def test_remote_events_uses_the_callers_client_as_given():
    """Timeout/verify/trust_env construction is build_guild_httpx_client's
    job (see test_guild_transport.py) — remote_events only ever reads from
    whatever client the caller built and handed it, and closes it after."""
    respx.get("http://build/stream").mock(return_value=httpx.Response(200, text=""))
    client = httpx.AsyncClient(timeout=httpx.Timeout(1.0, connect=0.25))

    async for _ in remote_events(client, "http://build/stream", {}):
        pass  # pragma: no cover - empty body, loop never iterates

    assert client.timeout.connect == 0.25
    assert client.timeout.read == 1.0
    assert client.is_closed


@pytest.mark.asyncio
async def test_merge_events_honors_configured_queue_maxsize(monkeypatch):
    """The queue_maxsize kwarg (Settings-backed) reaches the merge queue."""
    captured: dict[str, int] = {}
    original_queue_init = asyncio.Queue.__init__

    def capture_init(self, maxsize=0, **kwargs):
        captured["maxsize"] = maxsize
        return original_queue_init(self, maxsize=maxsize, **kwargs)

    monkeypatch.setattr(asyncio.Queue, "__init__", capture_init)

    async def source():
        yield "session_activity", {"state": "active"}
        await asyncio.Event().wait()

    stream = merge_events({"host": source}, queue_maxsize=7)
    assert b"active" in await anext(stream)
    await stream.aclose()

    assert captured["maxsize"] == 7
