#!/usr/bin/env python
"""Capture a live Ravn ``POST /chat`` turn as a JSONL fixture.

This is a *capture tool*, not a test — pytest ignores it (no ``test_`` prefix).

Why it exists
-------------
The OpenClaw adapter's ``TurnTranslator`` has to map Ravn's event frames onto
OpenClaw chat blocks. Those frame shapes were originally read out of
``ravn.domain.events`` rather than observed, which means the tool and thinking
paths were written against a guess. This tool records what the gateway
*actually* emits so the translator can be tested against real bytes.

Every frame is written verbatim, one JSON object per line, exactly as it
arrived on the wire — no normalisation, no field filtering. A ``__meta__``
header line records how the capture was produced.

Usage
-----
    python tests/test_ravn/capture_turn.py \
        --out tests/test_ravn/fixtures/ravn_turn_plain.jsonl \
        --session capture-plain \
        --message "Reply with exactly: pong. Do not use any tools."

Point it at the DEV gateway (7478). Never capture against 7477 — that is the
live Travis wired to a real Telegram thread.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

DEV_GATEWAY = "http://127.0.0.1:7478"


def capture(
    *,
    base_url: str,
    session_id: str,
    message: str,
    out_path: Path,
    timeout: float,
) -> int:
    """Stream one ``/chat`` turn and write each SSE frame to *out_path*.

    Returns the number of event frames captured (excluding the meta header).
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    frames = 0

    with out_path.open("w", encoding="utf-8") as fh:
        meta = {
            "__meta__": {
                "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "base_url": base_url,
                "session_id": session_id,
                "message": message,
                "note": "verbatim SSE frames from POST /chat; one per line",
            }
        }
        fh.write(json.dumps(meta) + "\n")
        fh.flush()

        with httpx.Client(timeout=httpx.Timeout(timeout, read=timeout)) as client:
            with client.stream(
                "POST",
                f"{base_url}/chat",
                json={"message": message, "session_id": session_id},
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data: "):
                        # Ravn emits bare `data: {json}` frames with no event:,
                        # id: or retry: lines. Record anything else verbatim so
                        # a format change is visible rather than swallowed.
                        if line.strip():
                            fh.write(json.dumps({"__raw__": line}) + "\n")
                            fh.flush()
                        continue
                    payload = line[len("data: ") :]
                    try:
                        frame = json.loads(payload)
                    except json.JSONDecodeError:
                        fh.write(json.dumps({"__unparsed__": payload}) + "\n")
                        fh.flush()
                        continue
                    frames += 1
                    # Stamp arrival offset — Ravn's own `timestamp` is emission
                    # time, which cannot show streaming cadence to a client.
                    frame["__offset_ms__"] = round((time.time() - started) * 1000)
                    fh.write(json.dumps(frame) + "\n")
                    fh.flush()
                    kind = frame.get("type", "?")
                    print(f"  [{frames:3d}] {kind}", file=sys.stderr)

    return frames


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEV_GATEWAY)
    parser.add_argument("--session", required=True)
    parser.add_argument("--message", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args()

    if ":7477" in args.base_url:
        print(
            "refusing to capture against :7477 — that is the live Travis "
            "gateway wired to a real Telegram thread. Use the dev gateway "
            "on :7478.",
            file=sys.stderr,
        )
        return 2

    print(f"capturing -> {args.out}", file=sys.stderr)
    frames = capture(
        base_url=args.base_url,
        session_id=args.session,
        message=args.message,
        out_path=args.out,
        timeout=args.timeout,
    )
    print(f"captured {frames} frames -> {args.out}", file=sys.stderr)
    return 0 if frames else 1


if __name__ == "__main__":
    raise SystemExit(main())
