#!/usr/bin/env python3
"""Minimal webhook receiver that appends every POST body to a JSONL file.

Usage:
    python3 scripts/webhook-sink.py            # listens on :8090, writes ./webhook-events.jsonl
    PORT=9000 OUT=/tmp/foo.jsonl SECRET=topsecret python3 scripts/webhook-sink.py

Stdlib-only. Each successful event becomes one line in OUT, of the form:

    {"received_at": "...", "headers": {...}, "body": {...|"<raw>"}}

Tail with: tail -f ./webhook-events.jsonl | jq .

If SECRET is set, the script verifies the X-Niuu-Signature header (HMAC-SHA256
hex, prefixed 'sha256=') and rejects mismatches with 401 — the same scheme
WebhookNotificationAdapter signs with.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("PORT", "8090"))
OUT_PATH = Path(os.environ.get("OUT", "webhook-events.jsonl")).expanduser().resolve()
SECRET = os.environ.get("SECRET", "")
SIGNATURE_HEADER = "X-Niuu-Signature"


def _verify_signature(body: bytes, header: str | None) -> bool:
    if not SECRET:
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[len("sha256=") :], expected)


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 — required by stdlib API
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""

        if not _verify_signature(body, self.headers.get(SIGNATURE_HEADER)):
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"bad signature\n")
            return

        try:
            parsed: object = json.loads(body.decode("utf-8")) if body else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = body.decode("latin-1", errors="replace")

        record = {
            "received_at": datetime.now(UTC).isoformat(),
            "path": self.path,
            "headers": {k: v for k, v in self.headers.items()},
            "body": parsed,
        }
        with OUT_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}\n')

    def do_GET(self) -> None:  # noqa: N802 — required by stdlib API
        # Health endpoint so you can curl the sink
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "out": str(OUT_PATH)}).encode())

    def log_message(self, format: str, *args: object) -> None:
        # Cleaner one-line access log to stderr instead of stdlib's default.
        sys.stderr.write(
            f"[{datetime.now(UTC).isoformat()}] {self.address_string()} {format % args}\n"
        )


def main() -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), _Handler)
    print(f"webhook-sink listening on http://0.0.0.0:{PORT}")
    print(f"writing to {OUT_PATH}")
    if SECRET:
        print("HMAC verification: ON")
    else:
        print("HMAC verification: OFF (set SECRET=... to enable)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        server.server_close()


if __name__ == "__main__":
    main()
