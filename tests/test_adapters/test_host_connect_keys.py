"""The connect endpoint's key lookups, answered on a pod-local Unix socket."""

from __future__ import annotations

import asyncio
import json
import shutil
import stat
import tempfile
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from volundr.adapters.inbound.host_connect_keys import HostConnectKeyServer
from volundr.domain.compute_hosts import EndpointKey


@pytest.fixture
def short_dir():
    # A Unix socket path is limited to about 100 bytes; pytest's tmp_path can be longer.
    path = Path(tempfile.mkdtemp(prefix="hc", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


async def _ask(path: Path, payload: bytes) -> bytes:
    reader, writer = await asyncio.open_unix_connection(str(path))
    writer.write(payload)
    await writer.drain()
    reply = await reader.readline()
    writer.close()
    await writer.wait_closed()
    return reply


async def test_answers_a_key_with_its_line(short_dir):
    host_id = uuid4()
    asked = []

    async def answer(**request):
        asked.append(request)
        return EndpointKey(host_id=host_id, line="restrict ssh-ed25519 AAAA")

    socket_path = short_dir / "keys.sock"
    server = HostConnectKeyServer(socket_path=str(socket_path), answer=answer)
    await server.start()
    try:
        assert server.socket_path == str(socket_path)
        assert stat.S_IMODE(socket_path.stat().st_mode) == 0o666
        request = {"user": "niuu-connect", "type": "ssh-ed25519", "key": "AAAA"}
        reply = json.loads(await _ask(socket_path, json.dumps(request).encode() + b"\n"))
    finally:
        await server.stop()
    assert reply == {"host_id": str(host_id), "line": "restrict ssh-ed25519 AAAA"}
    assert asked == [{"user": "niuu-connect", "key_type": "ssh-ed25519", "key": "AAAA"}]
    assert not socket_path.exists()


async def test_a_key_forge_does_not_admit_gets_no_line(short_dir):
    async def answer(**_request):
        return None

    socket_path = short_dir / "keys.sock"
    socket_path.touch()  # left by the previous Forge process in the pod
    server = HostConnectKeyServer(socket_path=str(socket_path), answer=answer)
    await server.start()
    try:
        request = b'{"user": "u", "type": "ssh-ed25519", "key": "AAAA"}\n'
        reply = json.loads(await _ask(socket_path, request))
    finally:
        await server.stop()
    assert reply == {"host_id": None, "line": None}


async def test_a_lookup_that_fails_answers_nothing_and_is_counted(short_dir, caplog):
    async def answer(**_request):
        raise RuntimeError("database down")

    socket_path = short_dir / "keys.sock"
    server = HostConnectKeyServer(socket_path=str(socket_path), answer=answer)
    await server.start()
    try:
        with patch("volundr.adapters.inbound.host_connect_keys.get_observability") as observe:
            malformed = await _ask(socket_path, b"not json\n")
            failed = await _ask(socket_path, b'{"user": "u", "type": "t", "key": "k"}\n')
    finally:
        await server.stop()
    assert malformed == failed == b""
    assert observe.return_value.count.call_count == 2
    observe.return_value.count.assert_called_with("volundr.compute.host.connect_key_errors")
    assert "key lookup" in caplog.text


async def test_stop_before_start_does_nothing(short_dir):
    async def answer(**_request):
        return None

    await HostConnectKeyServer(socket_path=str(short_dir / "keys.sock"), answer=answer).stop()
