"""Prove the fake client is faithful by pointing it at the REAL gateway.

The fake client is only useful as a proxy for the shipped iOS app if it can
complete a handshake against the server the app actually talks to. This test is
the calibration: if it passes here, a failure against the Ravn shim is the
shim's fault, not the harness's.

It is opt-in — the gateway is not present in CI, and it is a shared service:

    RAVN_OPENCLAW_TEST_URL=ws://127.0.0.1:18789 \\
    RAVN_OPENCLAW_TEST_TOKEN=<bearer> \\
    pytest tests/test_ravn/test_fake_client_against_openclaw.py

Read-only. It connects, lists, and disconnects. It sends no chat.
"""

from __future__ import annotations

import os

import pytest

from tests.test_ravn.fake_lexichat_client import FakeLexiChatClient
from tests.test_ravn.openclaw_handshake import MAX_PROTOCOL, MIN_PROTOCOL

URL = os.environ.get("RAVN_OPENCLAW_TEST_URL")
TOKEN = os.environ.get("RAVN_OPENCLAW_TEST_TOKEN")

pytestmark = pytest.mark.skipif(
    not (URL and TOKEN),
    reason="set RAVN_OPENCLAW_TEST_URL and RAVN_OPENCLAW_TEST_TOKEN to run",
)


async def test_handshake_completes_against_the_real_gateway() -> None:
    async with FakeLexiChatClient(url=URL, token=TOKEN) as client:
        hello = client.hello
        assert hello is not None
        assert hello["type"] == "hello-ok"
        assert MIN_PROTOCOL <= hello["protocol"] <= MAX_PROTOCOL
        assert hello["server"]["version"]
        assert hello["auth"]["role"] == "operator"


async def test_read_only_rpcs_answer() -> None:
    async with FakeLexiChatClient(url=URL, token=TOKEN) as client:
        agents = await client.request("agents.list")
        assert agents["ok"] is True
        assert "agents" in agents["payload"]

        sessions = await client.request("sessions.list")
        assert sessions["ok"] is True
        assert "sessions" in sessions["payload"]


async def test_responses_are_correlated_by_id_not_arrival_order() -> None:
    """The regression this guards: a health event landing mid-request.

    Against the real gateway a `health` event arrived between `agents.list` and
    its response. A harness that reads the next frame off the socket would
    mis-attribute it and then hang.
    """
    async with FakeLexiChatClient(url=URL, token=TOKEN) as client:
        first = await client.request("agents.list")
        second = await client.request("sessions.list")
        assert first["id"] != second["id"]
        assert first["ok"] and second["ok"]


async def test_unknown_method_returns_a_decodable_error() -> None:
    """An unimplemented RPC must not tear the socket down.

    This is what lets the shim ship partially: Phase 1 answers `chat.send`
    with an error while the rest of the surface works.
    """
    async with FakeLexiChatClient(url=URL, token=TOKEN) as client:
        resp = await client.request("ravn.method.that.does.not.exist")
        assert resp["ok"] is False
        # request() already raised if the error frame were malformed.
        assert resp["error"]["code"]
        # The socket must still be usable afterwards.
        follow_up = await client.request("sessions.list")
        assert follow_up["ok"] is True
