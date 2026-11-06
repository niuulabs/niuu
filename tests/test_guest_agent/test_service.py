from __future__ import annotations

import asyncio
import os
import signal
import socket
from pathlib import Path

import httpx
import pytest

from tests.test_guest_agent.conftest import NOW, credentials_body
from tests.test_guest_agent.test_agent import StaticSigner, make_agent
from volundr.guest_agent.agent import GuestAgent
from volundr.guest_agent.config import parse_config
from volundr.guest_agent.errors import FetchError, GuestAgentError
from volundr.guest_agent.service import serve, stop_on_signals


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def get(port: int, path: str, *headers: str) -> tuple[int, bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    lines = [f"GET {path} HTTP/1.1", *headers, "", ""]
    writer.write("\r\n".join(lines).encode())
    data = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    head, _, body = data.partition(b"\r\n\r\n")
    return int(head.split()[1]), body


async def wait_until_serving(task: asyncio.Task, port: int) -> tuple[int, bytes]:
    """Poll health until the listener answers; surface serve's error if it exits."""
    async with asyncio.timeout(10):
        while True:
            if task.done():
                task.result()
                raise AssertionError("serve returned before it listened")
            try:
                return await get(port, "/niuu/v1/health")
            except OSError:
                await asyncio.sleep(0.02)


async def test_serve_writes_serves_and_stops_cleanly(make_config, tmp_path: Path) -> None:
    port = free_port()
    config = parse_config(make_config(listen={"address": "127.0.0.1", "port": port}))
    body = credentials_body(token="served-token", now=NOW)
    agent = GuestAgent(
        config,
        StaticSigner(),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)),
        clock=lambda: NOW,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(serve(agent, stop))
    status, _ = await wait_until_serving(task, port)

    assert status == 200
    assert (tmp_path / "tokens" / "identity" / "token").read_text() == "served-token"
    forbidden, _ = await get(port, "/niuu/v1/credentials")
    allowed, credentials = await get(port, "/niuu/v1/credentials", "Niuu-Metadata: true")
    assert forbidden == 403
    assert allowed == 200
    assert b"served-token" in credentials
    stop.set()
    await asyncio.wait_for(task, 5)
    with pytest.raises(OSError):
        await get(port, "/niuu/v1/health")
    await agent.close()


async def test_serve_fails_when_the_first_fetch_fails(config_dict: dict) -> None:
    agent = make_agent(
        parse_config(config_dict),
        lambda request: httpx.Response(401, json={"error": "unauthorized"}),
    )

    with pytest.raises(FetchError, match="HTTP 401"):
        await serve(agent, asyncio.Event())


async def test_serve_fails_loudly_when_it_cannot_listen(make_config) -> None:
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        config = parse_config(make_config(listen={"address": "127.0.0.1", "port": port}))
        agent = make_agent(config, lambda request: httpx.Response(200, json=credentials_body()))

        with pytest.raises(GuestAgentError, match=f"cannot listen on 127.0.0.1:{port}"):
            await serve(agent, asyncio.Event())


async def test_sigterm_sets_the_stop_event() -> None:
    stop = asyncio.Event()
    stop_on_signals(stop)
    loop = asyncio.get_running_loop()
    try:
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(stop.wait(), 2)
    finally:
        loop.remove_signal_handler(signal.SIGTERM)
        loop.remove_signal_handler(signal.SIGINT)

    assert stop.is_set()
