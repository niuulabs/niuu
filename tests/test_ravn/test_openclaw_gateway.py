"""End-to-end wire tests for the OpenClaw shim.

These boot the real server on a real socket and drive it with
:class:`FakeLexiChatClient`, which behaves the way the shipped iOS client
behaves. That is the whole validation loop for this feature: it runs in under a
second on Thor, with no Mac, no archive and no TestFlight build.

A stub agent stands in for Ravn so the tests are deterministic and spend no
tokens. The stub's event vocabulary is the real one — the shapes came off a
live gateway into ``fixtures/``.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
import uvicorn

from ravn.adapters.channels.gateway_openclaw import OpenClawGateway
from ravn.adapters.channels.openclaw_store import OpenClawStore
from tests.test_ravn.fake_lexichat_client import (
    FakeLexiChatClient,
    HandshakeError,
)
from tests.test_ravn.openclaw_handshake import FakeDevice

TOKEN = "test-token-4a1b2c3d4e5f60718293a4b5c6d7e8f9"


@dataclass
class _Event:
    """Duck-types ravn.domain.events.RavnEvent for the shim's consumption."""

    type: str
    payload: dict


@dataclass
class StubRavnGateway:
    """Stands in for RavnGateway.handle_message_stream."""

    script: list[_Event] = field(default_factory=list)
    delay: float = 0.0
    raise_after: int | None = None
    seen: list[tuple[str, str]] = field(default_factory=list)

    async def handle_message_stream(self, session_id: str, text: str) -> AsyncIterator[_Event]:
        self.seen.append((session_id, text))
        for i, event in enumerate(self.script):
            if self.raise_after is not None and i == self.raise_after:
                raise RuntimeError("ravn blew up mid-turn")
            if self.delay:
                await asyncio.sleep(self.delay)
            yield event


def plain_turn() -> list[_Event]:
    """The captured plain turn: incremental deltas + a repeating terminal."""
    return [
        _Event("thought", {"text": "p"}),
        _Event("thought", {"text": "ong"}),
        _Event("response", {"text": "pong"}),
    ]


@dataclass
class RunningShim:
    gateway: OpenClawGateway
    url: str
    stub: StubRavnGateway


@contextlib.asynccontextmanager
async def running_shim(
    tmp_path,
    *,
    stub: StubRavnGateway | None = None,
    tick_seconds: float | None = None,
):
    """Boot the shim on an ephemeral port and yield its URL."""
    import ravn.adapters.channels.gateway_openclaw as mod

    stub = stub or StubRavnGateway(script=plain_turn())

    class Cfg:
        enabled = True
        host = "127.0.0.1"
        port = 0
        token_env = "RAVN_OPENCLAW_TEST_TOKEN_ENV"
        agent_id = "travis"
        session_prefix = ""
        max_live_sessions = 8
        store_path = str(tmp_path / "state.db")

    import os

    os.environ["RAVN_OPENCLAW_TEST_TOKEN_ENV"] = TOKEN

    original_tick = mod.TICK_EMIT_SECONDS
    if tick_seconds is not None:
        mod.TICK_EMIT_SECONDS = tick_seconds

    store = OpenClawStore(Cfg.store_path)
    gw = OpenClawGateway(Cfg(), stub, store=store)
    config = uvicorn.Config(
        gw._app,
        host="127.0.0.1",
        port=0,
        log_level="warning",
        access_log=False,
        ws_ping_interval=None,
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(200):
            if server.started and server.servers:
                break
            await asyncio.sleep(0.02)
        port = server.servers[0].sockets[0].getsockname()[1]
        yield RunningShim(gateway=gw, url=f"ws://127.0.0.1:{port}", stub=stub)
    finally:
        mod.TICK_EMIT_SECONDS = original_tick
        server.should_exit = True
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(task, timeout=5)
        store.close()


class TestHandshake:
    async def test_a_real_client_can_connect(self, tmp_path) -> None:
        async with running_shim(tmp_path) as shim:
            async with FakeLexiChatClient(url=shim.url, token=TOKEN) as client:
                assert client.hello["type"] == "hello-ok"
                assert client.hello["protocol"] == 3
                assert client.hello["auth"]["role"] == "operator"
                assert client.hello["policy"]["tickIntervalMs"] > 0

    async def test_challenge_is_pushed_unprompted_and_first(self, tmp_path) -> None:
        """No challenge = the shipped client wedges forever, silently."""
        import json

        import websockets

        async with running_shim(tmp_path) as shim:
            async with websockets.connect(shim.url) as ws:
                frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                assert frame["event"] == "connect.challenge"
                assert frame["payload"]["nonce"]
                assert frame.get("seq") is None, "seq starts after hello-ok"

    async def test_a_bad_token_is_refused(self, tmp_path) -> None:
        async with running_shim(tmp_path) as shim:
            with pytest.raises(HandshakeError, match="connect rejected"):
                async with FakeLexiChatClient(url=shim.url, token="wrong"):
                    pass

    async def test_a_forged_device_id_is_refused(self, tmp_path) -> None:
        """deviceId must equal sha256(publicKey) — upstream's rule."""
        import json

        import websockets

        from tests.test_ravn.openclaw_handshake import build_connect_request

        async with running_shim(tmp_path) as shim:
            async with websockets.connect(shim.url) as ws:
                challenge = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                nonce = challenge["payload"]["nonce"]
                req = build_connect_request(device=FakeDevice(), token=TOKEN, nonce=nonce)
                req["params"]["device"]["id"] = "an-id-i-just-made-up"
                await ws.send(json.dumps(req))
                resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                assert resp["ok"] is False
                assert resp["error"]["details"]["reason"] == "device-id-mismatch"

    async def test_a_replayed_nonce_is_refused(self, tmp_path) -> None:
        import json

        import websockets

        from tests.test_ravn.openclaw_handshake import build_connect_request

        async with running_shim(tmp_path) as shim:
            async with websockets.connect(shim.url) as ws:
                await asyncio.wait_for(ws.recv(), timeout=5)
                req = build_connect_request(
                    device=FakeDevice(), token=TOKEN, nonce="a-nonce-we-never-issued"
                )
                await ws.send(json.dumps(req))
                resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                assert resp["ok"] is False
                assert resp["error"]["details"]["reason"] == "device-nonce-mismatch"

    async def test_push_token_is_tolerated(self, tmp_path) -> None:
        """The shipped app sends auth.pushToken once APNs registration ran."""
        async with running_shim(tmp_path) as shim:
            async with FakeLexiChatClient(
                url=shim.url, token=TOKEN, push_token="apns-abc"
            ) as client:
                assert client.hello["type"] == "hello-ok"

    async def test_rpcs_before_connect_are_refused(self, tmp_path) -> None:
        import json

        import websockets

        async with running_shim(tmp_path) as shim:
            async with websockets.connect(shim.url) as ws:
                await asyncio.wait_for(ws.recv(), timeout=5)
                await ws.send(
                    json.dumps({"type": "req", "id": "x1", "method": "sessions.list", "params": {}})
                )
                resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                assert resp["ok"] is False
                assert resp["error"]["code"] == "UNAUTHORIZED"


class TestChannelVisibility:
    """What has to be true for Travis to APPEAR in the app."""

    async def test_sessions_list_yields_a_channel_row(self, tmp_path) -> None:
        async with running_shim(tmp_path) as shim:
            async with FakeLexiChatClient(url=shim.url, token=TOKEN) as client:
                resp = await client.request("sessions.list")
                sessions = resp["payload"]["sessions"]
                assert sessions, "no row = no channel in the app"
                row = sessions[0]
                # `key` alone renders a channel; agentId comes from segment 1.
                assert row["key"] == "agent:travis:main"
                assert row["key"].split(":")[1] == "travis"
                assert isinstance(row["updatedAt"], int), "epoch ms — client divides by 1000"

    async def test_agents_list_advertises_travis(self, tmp_path) -> None:
        async with running_shim(tmp_path) as shim:
            async with FakeLexiChatClient(url=shim.url, token=TOKEN) as client:
                payload = (await client.request("agents.list"))["payload"]
                assert payload["defaultId"] == "travis"
                assert payload["agents"][0]["id"] == "travis"

    async def test_unimplemented_rpc_errors_without_killing_the_socket(self, tmp_path) -> None:
        """This is what lets a partial shim ship at all."""
        async with running_shim(tmp_path) as shim:
            async with FakeLexiChatClient(url=shim.url, token=TOKEN) as client:
                resp = await client.request("tools.catalog")
                assert resp["ok"] is False
                assert resp["error"]["code"] and resp["error"]["message"]
                # Socket still usable.
                assert (await client.request("sessions.list"))["ok"] is True


class TestChatRoundTrip:
    async def test_a_turn_streams_and_never_doubles_the_answer(self, tmp_path) -> None:
        async with running_shim(tmp_path) as shim:
            async with FakeLexiChatClient(url=shim.url, token=TOKEN) as client:
                await client.request("sessions.subscribe", {"sessionKey": "agent:travis:main"})
                sent = await client.request(
                    "chat.send", {"sessionKey": "agent:travis:main", "message": "ping"}
                )
                assert sent["ok"] is True and sent["payload"]["runId"]

                events = await client.collect_events(until_event="chat", timeout=10)
                chats = [e for e in events if e["event"] == "chat"]
                while chats[-1]["payload"]["state"] != "final":
                    more = await client.collect_events(until_event="chat", timeout=10)
                    chats += [e for e in more if e["event"] == "chat"]

                final = chats[-1]["payload"]
                text = "".join(
                    b.get("text", "") for b in final["message"]["blocks"] if b["type"] == "text"
                )
                assert text == "pong", "naive concatenation would give 'pongpong'"
                assert final["sessionKey"] == "agent:travis:main"

    async def test_the_turn_is_persisted_and_replayable(self, tmp_path) -> None:
        """chat.history is what all three of the app's healing rails call."""
        async with running_shim(tmp_path) as shim:
            async with FakeLexiChatClient(url=shim.url, token=TOKEN) as client:
                await client.request("sessions.subscribe", {"sessionKey": "agent:travis:main"})
                await client.request(
                    "chat.send", {"sessionKey": "agent:travis:main", "message": "ping"}
                )
                for _ in range(60):
                    await asyncio.sleep(0.05)
                    hist = (
                        await client.request(
                            "chat.history", {"sessionKey": "agent:travis:main", "limit": 30}
                        )
                    )["payload"]["messages"]
                    if len(hist) >= 2:
                        break
                assert [m["role"] for m in hist] == ["user", "assistant"], "oldest-first"
                assert hist[0]["content"] == "ping"
                assert hist[1]["content"] == "pong"
                assert isinstance(hist[0]["createdAt"], int)

    async def test_history_is_durable_the_instant_final_arrives(self, tmp_path) -> None:
        """The terminal-reconcile race, pinned.

        ChatScreenModel refetches chat.history the moment a turn ends. If the
        assistant row is written after the `final` event is broadcast, that
        refetch finds only the user message and drops the answer the user just
        watched stream in. So the write must happen first.
        """
        async with running_shim(tmp_path) as shim:
            async with FakeLexiChatClient(url=shim.url, token=TOKEN) as client:
                await client.request("sessions.subscribe", {"sessionKey": "agent:travis:main"})
                await client.request(
                    "chat.send", {"sessionKey": "agent:travis:main", "message": "ping"}
                )
                for _ in range(80):
                    frame = await client.next_event(timeout=10)
                    if frame["event"] == "chat" and frame["payload"]["state"] == "final":
                        break
                else:
                    pytest.fail("never saw a final")

                # No sleep: query immediately, exactly as the app's rail does.
                hist = (await client.request("chat.history", {"sessionKey": "agent:travis:main"}))[
                    "payload"
                ]["messages"]
                roles = [m["role"] for m in hist]
                assert "assistant" in roles, (
                    "the answer was not durable when final was announced — "
                    "a terminal reconcile would drop it"
                )
                assert hist[-1]["content"] == "pong"

    async def test_history_survives_a_reconnect(self, tmp_path) -> None:
        async with running_shim(tmp_path) as shim:
            async with FakeLexiChatClient(url=shim.url, token=TOKEN) as client:
                await client.request("sessions.subscribe", {"sessionKey": "agent:travis:main"})
                await client.request(
                    "chat.send", {"sessionKey": "agent:travis:main", "message": "ping"}
                )
                for _ in range(60):
                    await asyncio.sleep(0.05)
                    if shim.gateway.store.message_count("agent:travis:main") >= 2:
                        break
            # New connection, new device — the transcript is the server's, not the phone's.
            async with FakeLexiChatClient(url=shim.url, token=TOKEN) as client2:
                hist = (await client2.request("chat.history", {"sessionKey": "agent:travis:main"}))[
                    "payload"
                ]["messages"]
                assert len(hist) >= 2

    async def test_a_foreign_session_key_is_refused(self, tmp_path) -> None:
        """A typo'd key must not reach Damien's live Telegram thread.

        Ravn performs no ownership check and Telegram shares the same
        RavnGateway, so the allowlist is the only thing standing between a
        phone and that conversation.
        """
        async with running_shim(tmp_path) as shim:
            async with FakeLexiChatClient(url=shim.url, token=TOKEN) as client:
                resp = await client.request(
                    "chat.send",
                    {"sessionKey": "telegram:8572736034", "message": "hello"},
                )
                assert resp["ok"] is False
                assert resp["error"]["code"] == "NOT_FOUND"
                assert shim.stub.seen == [], "the turn must never have started"

    async def test_idempotency_key_does_not_start_two_turns(self, tmp_path) -> None:
        async with running_shim(tmp_path) as shim:
            async with FakeLexiChatClient(url=shim.url, token=TOKEN) as client:
                params = {
                    "sessionKey": "agent:travis:main",
                    "message": "ping",
                    "idempotencyKey": "idem-1",
                }
                first = await client.request("chat.send", params)
                for _ in range(60):
                    await asyncio.sleep(0.05)
                    if shim.gateway.store.get_session("agent:travis:main")["status"] == "idle":
                        break
                second = await client.request("chat.send", params)
                assert second["payload"]["runId"] == first["payload"]["runId"]
                assert len(shim.stub.seen) == 1


class TestFailureModes:
    async def test_a_turn_that_dies_becomes_an_error_not_a_final(self, tmp_path) -> None:
        """RavnAgent emits no error events; without synthesis this is silence."""
        stub = StubRavnGateway(
            script=[_Event("thought", {"text": "half an ans"}), _Event("response", {"text": "x"})],
            raise_after=1,
        )
        async with running_shim(tmp_path, stub=stub) as shim:
            async with FakeLexiChatClient(url=shim.url, token=TOKEN) as client:
                await client.request("sessions.subscribe", {"sessionKey": "agent:travis:main"})
                await client.request(
                    "chat.send", {"sessionKey": "agent:travis:main", "message": "go"}
                )
                states: list[str] = []
                for _ in range(40):
                    frame = await client.next_event(timeout=10)
                    if frame["event"] == "chat":
                        states.append(frame["payload"]["state"])
                        if states[-1] in {"final", "error", "aborted"}:
                            break
                assert states[-1] == "error"
                assert "final" not in states, "a dead turn must never look complete"

    async def test_a_second_send_while_busy_is_refused_not_queued(self, tmp_path) -> None:
        """Ravn's per-session lock would silently queue it with no feedback."""
        stub = StubRavnGateway(script=plain_turn(), delay=0.4)
        async with running_shim(tmp_path, stub=stub) as shim:
            async with FakeLexiChatClient(url=shim.url, token=TOKEN) as client:
                await client.request(
                    "chat.send", {"sessionKey": "agent:travis:main", "message": "first"}
                )
                await asyncio.sleep(0.1)
                second = await client.request(
                    "chat.send", {"sessionKey": "agent:travis:main", "message": "second"}
                )
                assert second["ok"] is False
                assert second["error"]["code"] == "RATE_LIMITED"

    async def test_every_error_frame_is_decodable_by_the_swift_client(self, tmp_path) -> None:
        """A shorthand error is dropped silently and hangs the caller 15s.

        FakeLexiChatClient.request raises MalformedErrorFrameError rather than
        returning, so any shorthand error fails this test loudly.
        """
        async with running_shim(tmp_path) as shim:
            async with FakeLexiChatClient(url=shim.url, token=TOKEN) as client:
                for method, params in [
                    ("nope.not.a.method", {}),
                    ("chat.history", {"sessionKey": "telegram:123"}),
                    ("chat.send", {"sessionKey": "agent:travis:main"}),
                ]:
                    resp = await client.request(method, params)
                    assert resp["ok"] is False
                    assert isinstance(resp["error"]["code"], str)
                    assert isinstance(resp["error"]["message"], str)
                    assert resp["error"]["details"]["code"]


class TestHeartbeat:
    async def test_ticks_arrive_and_carry_monotonic_seq(self, tmp_path) -> None:
        async with running_shim(tmp_path, tick_seconds=0.2) as shim:
            async with FakeLexiChatClient(url=shim.url, token=TOKEN) as client:
                ticks = 0
                for _ in range(20):
                    frame = await client.next_event(timeout=5)
                    if frame["event"] == "tick":
                        ticks += 1
                        if ticks >= 3:
                            break
                assert ticks >= 3
                client.assert_seq_monotonic()
                client.assert_tick_alive()

    async def test_ticks_continue_during_a_long_turn(self, tmp_path) -> None:
        """Chat events do NOT reset the client's watchdog — only ticks do.

        A turn longer than 2.5x the tick interval would otherwise have the
        transport killed underneath it.
        """
        stub = StubRavnGateway(script=plain_turn(), delay=0.35)
        async with running_shim(tmp_path, stub=stub, tick_seconds=0.15) as shim:
            async with FakeLexiChatClient(url=shim.url, token=TOKEN) as client:
                await client.request("sessions.subscribe", {"sessionKey": "agent:travis:main"})
                await client.request(
                    "chat.send", {"sessionKey": "agent:travis:main", "message": "slow"}
                )
                ticks_during_turn = 0
                saw_delta = False
                for _ in range(60):
                    frame = await client.next_event(timeout=5)
                    if frame["event"] == "tick":
                        ticks_during_turn += 1
                    elif frame["event"] == "chat":
                        state = frame["payload"]["state"]
                        saw_delta = saw_delta or state == "delta"
                        if state == "final":
                            break
                assert saw_delta
                assert ticks_during_turn >= 2, "the transport would have been killed"
