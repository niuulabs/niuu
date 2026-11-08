from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from tests.test_guest_agent.conftest import NOW
from tests.test_guest_agent.test_agent import fetched_with
from volundr.guest_agent.agent import CredentialState
from volundr.guest_agent.config import Listen
from volundr.guest_agent.server import MetadataServer

BODY = json.dumps({"service_account_tokens": [], "forge_session": None}).encode()


async def request(
    server: MetadataServer, raw: bytes | str, *, read_until_close: bool = True
) -> tuple[int, dict[str, str], bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    writer.write(raw.encode() if isinstance(raw, str) else raw)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    head, _, body = data.partition(b"\r\n\r\n")
    status_line, *header_lines = head.decode().split("\r\n")
    headers = {k.lower(): v.strip() for k, _, v in (line.partition(":") for line in header_lines)}
    return int(status_line.split()[1]), headers, body


def get(path: str, *headers: str) -> str:
    lines = [f"GET {path} HTTP/1.1", "Host: 169.254.169.254", *headers, "", ""]
    return "\r\n".join(lines)


@pytest.fixture
def state() -> CredentialState:
    return state_at([NOW])


def state_at(now: list[datetime], raw: bytes = BODY) -> CredentialState:
    state = CredentialState(clock=lambda: now[0])
    state.record_success(replace(fetched_with(100), raw=raw))
    return state


@pytest.fixture
async def server(state: CredentialState) -> AsyncIterator[MetadataServer]:
    server = MetadataServer(state, Listen(address="127.0.0.1", port=0), request_timeout_seconds=2)
    await server.start()
    yield server
    await server.stop()


async def test_credentials_without_the_header_is_forbidden(server: MetadataServer) -> None:
    status, _, body = await request(server, get("/niuu/v1/credentials"))

    assert status == 403
    assert b"Niuu-Metadata" in body
    assert BODY not in body


@pytest.mark.parametrize("header", ["Niuu-Metadata: false", "Niuu-Metadata:", "X-Niuu: true"])
async def test_credentials_needs_exactly_true(server: MetadataServer, header: str) -> None:
    status, _, body = await request(server, get("/niuu/v1/credentials", header))

    assert status == 403
    assert BODY not in body


@pytest.mark.parametrize(
    "header", ["Niuu-Metadata: true", "niuu-metadata: true", "NIUU-METADATA:true"]
)
async def test_credentials_with_the_header_returns_the_last_response(
    server: MetadataServer, header: str
) -> None:
    status, headers, body = await request(server, get("/niuu/v1/credentials", header))

    assert status == 200
    assert body == BODY
    assert headers["content-type"] == "application/json"
    assert headers["content-length"] == str(len(BODY))
    assert headers["cache-control"] == "no-store"


async def test_credentials_ignores_a_query_string(server: MetadataServer) -> None:
    status, _, _ = await request(server, get("/niuu/v1/credentials?x=1", "Niuu-Metadata: true"))

    assert status == 200


async def test_credentials_serve_the_newest_successful_response(
    server: MetadataServer, state: CredentialState
) -> None:
    state.record_success(replace(fetched_with(100), raw=b'{"newer": true}'))

    _, _, body = await request(server, get("/niuu/v1/credentials", "Niuu-Metadata: true"))

    assert body == b'{"newer": true}'


async def test_credentials_before_any_fetch_is_unavailable() -> None:
    server = MetadataServer(
        CredentialState(), Listen(address="127.0.0.1", port=0), request_timeout_seconds=2
    )
    await server.start()
    try:
        status, _, _ = await request(server, get("/niuu/v1/credentials", "Niuu-Metadata: true"))
    finally:
        await server.stop()

    assert status == 503


async def test_health_needs_no_header_and_is_200_while_healthy(server: MetadataServer) -> None:
    status, _, body = await request(server, get("/niuu/v1/health"))

    assert status == 200
    assert json.loads(body) == {"status": "ok"}


async def test_health_is_503_after_a_failed_refresh(
    server: MetadataServer, state: CredentialState
) -> None:
    state.record_failure("boom")

    status, _, body = await request(server, get("/niuu/v1/health"))

    assert status == 503
    assert json.loads(body) == {"status": "unavailable", "reason": "the last refresh failed"}


async def test_health_is_503_once_a_token_has_expired() -> None:
    now = [NOW]
    server = MetadataServer(
        state_at(now), Listen(address="127.0.0.1", port=0), request_timeout_seconds=2
    )
    await server.start()
    try:
        now[0] = NOW + timedelta(seconds=101)
        status, _, body = await request(server, get("/niuu/v1/health"))
    finally:
        await server.stop()

    assert status == 503
    assert json.loads(body)["reason"] == "a token has expired"


async def test_unknown_path_is_404(server: MetadataServer) -> None:
    status, _, _ = await request(server, get("/niuu/v1/other", "Niuu-Metadata: true"))

    assert status == 404


async def test_post_is_405(server: MetadataServer) -> None:
    raw = "POST /niuu/v1/credentials HTTP/1.1\r\nNiuu-Metadata: true\r\n\r\n"

    status, _, _ = await request(server, raw)

    assert status == 405


async def test_malformed_request_line_is_400(server: MetadataServer) -> None:
    status, _, _ = await request(server, "GARBAGE\r\n\r\n")

    assert status == 400


async def test_truncated_request_is_431(server: MetadataServer) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    writer.write(b"GET /niuu/v1/health HTTP/1.1\r\nHost: x")
    writer.write_eof()
    data = await asyncio.wait_for(reader.read(), 5)
    writer.close()

    assert data.startswith(b"HTTP/1.1 431")


async def test_a_silent_client_is_dropped_after_the_timeout(state: CredentialState) -> None:
    server = MetadataServer(state, Listen(address="127.0.0.1", port=0), request_timeout_seconds=0.1)
    await server.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        data = await asyncio.wait_for(reader.read(), 2)
        writer.close()
    finally:
        await server.stop()

    assert data == b""


async def test_port_requires_a_started_server(state: CredentialState) -> None:
    server = MetadataServer(state, Listen(address="127.0.0.1", port=0), request_timeout_seconds=1)

    with pytest.raises(RuntimeError, match="not started"):
        _ = server.port
    await server.stop()
