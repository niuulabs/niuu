#!/usr/bin/env python
"""Capture a golden handshake vector from the REAL OpenClaw gateway.

Why against the running gateway and not the docs
------------------------------------------------
The shim has to satisfy a server we do not own. Three sources disagree about
what that server does: the upstream docs, the local checkout at
``/home/thor/thirdparty/openclaw`` (2026.5.30 @ 2b5ddf8f2a), and the gateway
actually running on this box (npm dist, per ``openclaw-gateway.service``). Only
the last one is authoritative, because it is the one the phone talks to.

This connects as the iOS client, records every frame verbatim, and writes a
fixture the shim's own tests assert against.

Read-only: it performs a handshake and a few list calls, then disconnects. It
sends no chat and mutates nothing.

    python tests/test_ravn/capture_openclaw_vector.py \
        --url ws://127.0.0.1:18789 --token <bearer> \
        --out tests/test_ravn/fixtures/openclaw_handshake_vector.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent))
from openclaw_handshake import (  # noqa: E402
    FakeDevice,
    build_connect_request,
    build_req,
)

# Read-only RPCs worth probing while we have a live socket. Each is something
# the shim will have to answer, so knowing the real response shape matters.
PROBE_RPCS: list[tuple[str, dict]] = [
    ("agents.list", {}),
    ("sessions.list", {}),
]

# Keys whose values are credentials, not protocol shape. The vector is committed
# so the shim can be tested against it, so it must never carry a live secret:
# the bearer we authenticate with, the deviceToken the gateway mints back, and
# the capability URL in pluginSurfaceUrls are all bearer-equivalent.
SECRET_KEYS = {"token", "devicetoken", "secret", "password", "apikey"}


def redact(obj: object, *, literals: tuple[str, ...]) -> object:
    """Strip credentials while preserving structure, types and key order.

    Values are replaced with a marker rather than removed, so tests can still
    assert that a field was *present* — which is the thing the shim must
    reproduce.
    """
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if key.lower() in SECRET_KEYS and isinstance(value, str):
                out[key] = "<REDACTED>"
            else:
                out[key] = redact(value, literals=literals)
        return out
    if isinstance(obj, list):
        return [redact(item, literals=literals) for item in obj]
    if isinstance(obj, str):
        text = obj
        for literal in literals:
            if literal and literal in text:
                text = text.replace(literal, "<REDACTED>")
        # Capability URLs are bearer-equivalent grants.
        if "__openclaw__/cap/" in text:
            text = text.split("__openclaw__/cap/")[0] + "__openclaw__/cap/<REDACTED>"
        return text
    return obj


async def capture(url: str, token: str, out: Path, idle_seconds: float) -> int:
    device = FakeDevice()
    record: dict = {
        "__meta__": {
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "url": url,
            "note": (
                "Golden vector from the RUNNING OpenClaw gateway — the "
                "authority for the shim's handshake. Not from docs, not from "
                "the local checkout."
            ),
        },
        "frames": [],
    }
    started = time.time()

    def note(direction: str, frame: object) -> None:
        record["frames"].append(
            {
                "dir": direction,
                "offset_ms": round((time.time() - started) * 1000),
                "frame": frame,
            }
        )

    async with websockets.connect(url, max_size=None) as ws:
        # 1. The gateway must push connect.challenge UNPROMPTED. The iOS client
        #    has no timeout on this: no challenge means it sits in
        #    .waitingForChallenge forever, with no reconnect and no error.
        raw = await asyncio.wait_for(ws.recv(), timeout=15)
        challenge = json.loads(raw)
        note("recv", challenge)
        payload = challenge.get("payload") or {}
        nonce = payload.get("nonce")
        if not nonce:
            print(f"no nonce in first frame: {challenge}", file=sys.stderr)
            return 1

        # 2. connect, signed exactly as the iOS client signs it.
        req = build_connect_request(device=device, token=token, nonce=nonce)
        note("send", req)
        await ws.send(json.dumps(req))

        hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
        note("recv", hello)
        ok = hello.get("ok")
        hp = hello.get("payload") or {}
        print(
            f"connect ok={ok} type={hp.get('type')} protocol={hp.get('protocol')}",
            file=sys.stderr,
        )
        if not ok:
            print(f"connect REJECTED: {hello.get('error')}", file=sys.stderr)
            record["__meta__"]["connect_rejected"] = True
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(record, indent=2))
            return 1

        # 3. Probe the read-only RPCs the shim must implement.
        for i, (method, params) in enumerate(PROBE_RPCS, start=2):
            frame = build_req(method, params, f"c{i}")
            note("send", json.loads(frame))
            await ws.send(frame)
            try:
                resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                note("recv", resp)
                print(f"{method} -> ok={resp.get('ok')}", file=sys.stderr)
            except TimeoutError:
                print(f"{method} -> TIMEOUT", file=sys.stderr)

        # 4. Sit idle to observe the heartbeat. The iOS client tears the
        #    transport down at 2.5x the advertised tickIntervalMs, and only
        #    hello-ok and event:"tick" reset that watchdog.
        print(f"idling {idle_seconds}s for tick cadence…", file=sys.stderr)
        deadline = time.time() + idle_seconds
        while time.time() < deadline:
            try:
                remaining = max(0.1, deadline - time.time())
                frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
                note("recv", frame)
                if frame.get("event") == "tick":
                    print(f"  tick @ {record['frames'][-1]['offset_ms']}ms", file=sys.stderr)
            except TimeoutError:
                break

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(redact(record, literals=(token,)), indent=2))
    print(f"captured {len(record['frames'])} frames -> {out}", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="ws://127.0.0.1:18789")
    ap.add_argument("--token", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--idle", type=float, default=75.0)
    args = ap.parse_args()
    return asyncio.run(capture(args.url, args.token, args.out, args.idle))


if __name__ == "__main__":
    raise SystemExit(main())
