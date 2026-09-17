"""A fake LexiChat client — the test harness for the Ravn OpenClaw shim.

This is what lets the shim be developed without a Mac, an archive, or a
TestFlight build: it connects over a real WebSocket and behaves the way the
shipped iOS client behaves, so a handshake or streaming regression fails in a
sub-second pytest run on Thor.

It deliberately models the client's *observed* behaviour, including the parts
that make debugging hard if the server gets them wrong:

* It waits for an **unprompted** ``connect.challenge``. The real client has no
  timeout on this — a server that upgrades the socket and never challenges
  leaves it wedged in ``.waitingForChallenge`` forever, with no reconnect and
  no error. :meth:`connect` therefore times out loudly instead.
* It correlates responses **by id**, never by arrival order. Against the real
  gateway a ``health`` event landed between ``agents.list`` and its response.
* It enforces the tick watchdog the way the client does: only ``hello-ok`` and
  ``event:"tick"`` reset it — chat and agent events do not — so a server that
  stops ticking during a long turn is caught here.
* It requires full-shape error frames. The Swift ``WSError`` has non-optional
  ``code`` and ``message``, so a shorthand ``{"ok":false,"error":"oops"}`` is
  silently dropped by the real client and the caller hangs its full 15 s RPC
  timeout. :meth:`request` raises on a malformed error instead of hanging.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import websockets

from tests.test_ravn.openclaw_handshake import (
    MAX_PROTOCOL,
    MIN_PROTOCOL,
    FakeDevice,
    build_connect_request,
)

# GatewayWebSocketClient uses OCConstants.defaultRPCRequestTimeout = 15s.
RPC_TIMEOUT = 15.0
# The client tears the transport down at 2.5x the advertised tickIntervalMs.
TICK_WATCHDOG_MULTIPLIER = 2.5


class HandshakeError(RuntimeError):
    """The server failed the handshake in a way the real client cannot survive."""


class MalformedErrorFrameError(RuntimeError):
    """An error frame the shipped client would silently drop."""


@dataclass
class FakeLexiChatClient:
    """Drives a gateway exactly as the shipped iOS client would."""

    url: str
    token: str
    device: FakeDevice = field(default_factory=FakeDevice)
    push_token: str | None = None

    _ws: Any = None
    _pending: dict[str, asyncio.Future] = field(default_factory=dict)
    _events: asyncio.Queue = field(default_factory=asyncio.Queue)
    _reader: asyncio.Task | None = None
    _req_n: int = 0

    hello: dict[str, Any] | None = None
    seqs: list[int] = field(default_factory=list)
    last_tick_at: float | None = None

    # -- lifecycle ---------------------------------------------------------

    async def connect(self, *, challenge_timeout: float = 10.0) -> dict[str, Any]:
        """Open the socket, complete the handshake, return the hello-ok payload."""
        self._ws = await websockets.connect(self.url, max_size=None)

        raw = await asyncio.wait_for(self._ws.recv(), timeout=challenge_timeout)
        challenge = json.loads(raw)
        if challenge.get("event") != "connect.challenge":
            raise HandshakeError(
                f"first frame must be an unprompted connect.challenge, got {challenge!r}"
            )
        nonce = (challenge.get("payload") or {}).get("nonce")
        if not nonce:
            raise HandshakeError(f"connect.challenge carried no nonce: {challenge!r}")

        req = build_connect_request(
            device=self.device,
            token=self.token,
            nonce=nonce,
            req_id="c1",
            push_token=self.push_token,
        )
        await self._ws.send(json.dumps(req))

        resp = json.loads(await asyncio.wait_for(self._ws.recv(), timeout=RPC_TIMEOUT))
        if not resp.get("ok"):
            raise HandshakeError(f"connect rejected: {resp.get('error')!r}")

        payload = resp.get("payload") or {}
        if payload.get("type") != "hello-ok":
            raise HandshakeError(f"expected hello-ok, got type={payload.get('type')!r}")
        protocol = payload.get("protocol")
        if not isinstance(protocol, int) or not (MIN_PROTOCOL <= protocol <= MAX_PROTOCOL):
            # The real client hard-disconnects here and schedules NO reconnect.
            raise HandshakeError(
                f"protocol {protocol!r} outside the client's accepted range "
                f"{MIN_PROTOCOL}...{MAX_PROTOCOL} — the shipped client would "
                f"disconnect with no user-visible error"
            )

        self.hello = payload
        self.last_tick_at = asyncio.get_running_loop().time()
        self._req_n = 1
        self._reader = asyncio.create_task(self._read_loop())
        return payload

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
        if self._ws:
            await self._ws.close()

    async def __aenter__(self) -> FakeLexiChatClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    # -- plumbing ----------------------------------------------------------

    async def _read_loop(self) -> None:
        loop = asyncio.get_running_loop()
        async for raw in self._ws:
            frame = json.loads(raw)
            if "event" in frame:
                seq = frame.get("seq")
                if seq is not None:
                    self.seqs.append(seq)
                if frame["event"] == "tick":
                    self.last_tick_at = loop.time()
                await self._events.put(frame)
                continue
            fut = self._pending.pop(frame.get("id"), None)
            if fut and not fut.done():
                fut.set_result(frame)

    # -- RPC ---------------------------------------------------------------

    async def request(
        self, method: str, params: dict | None = None, *, timeout: float = RPC_TIMEOUT
    ) -> dict[str, Any]:
        """Send an RPC and await its response, correlated by id."""
        self._req_n += 1
        req_id = f"c{self._req_n}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        await self._ws.send(
            json.dumps({"type": "req", "id": req_id, "method": method, "params": params or {}})
        )
        try:
            frame = await asyncio.wait_for(fut, timeout=timeout)
        except TimeoutError as exc:
            raise TimeoutError(
                f"{method} did not answer within {timeout}s. If the server sent "
                f"a shorthand error frame, the shipped client would drop it "
                f"silently and hang exactly like this."
            ) from exc

        if frame.get("ok") is False:
            err = frame.get("error")
            if not isinstance(err, dict) or "code" not in err or "message" not in err:
                raise MalformedErrorFrameError(
                    f"{method} returned an error the shipped client cannot decode "
                    f"(WSError.code/message are non-optional): {err!r}"
                )
        return frame

    # -- events ------------------------------------------------------------

    async def next_event(self, *, timeout: float = 10.0) -> dict[str, Any]:
        return await asyncio.wait_for(self._events.get(), timeout=timeout)

    async def collect_events(
        self, *, until_event: str, timeout: float = 60.0
    ) -> list[dict[str, Any]]:
        """Drain events until one matching *until_event* arrives."""
        out: list[dict[str, Any]] = []
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"never saw {until_event!r}; got {len(out)} events")
            frame = await self.next_event(timeout=remaining)
            out.append(frame)
            if frame.get("event") == until_event:
                return out

    # -- assertions the shipped client effectively makes --------------------

    def assert_seq_monotonic(self) -> None:
        assert self.seqs == sorted(self.seqs), f"seq went backwards: {self.seqs}"
        if self.seqs:
            expected = list(range(self.seqs[0], self.seqs[0] + len(self.seqs)))
            assert self.seqs == expected, f"seq gap: {self.seqs} != {expected}"

    def tick_deadline_seconds(self) -> float:
        policy = (self.hello or {}).get("policy") or {}
        interval_ms = policy.get("tickIntervalMs") or 30000
        return (interval_ms / 1000.0) * TICK_WATCHDOG_MULTIPLIER

    def assert_tick_alive(self) -> None:
        """Fail if the watchdog the real client runs would have fired."""
        assert self.last_tick_at is not None, "never established a tick baseline"
        elapsed = asyncio.get_running_loop().time() - self.last_tick_at
        deadline = self.tick_deadline_seconds()
        assert elapsed < deadline, (
            f"{elapsed:.1f}s since the last tick, watchdog fires at {deadline:.1f}s — "
            f"the shipped client would have killed this transport. Note that chat "
            f"and agent events do NOT reset it: the server must keep ticking "
            f"during a long turn."
        )
