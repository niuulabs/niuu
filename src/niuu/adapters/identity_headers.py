"""Decode identity headers supplied by the trusted Envoy gateway."""

import base64
import json


def parse_roles_header(raw: str) -> list[str]:
    """Parse roles from an Envoy header value.

    Envoy base64-encodes non-string JWT claims (e.g. arrays).
    This handles both plain comma-separated strings and base64-encoded
    JSON arrays.
    """
    if not raw:
        return []

    # Try base64 decode → JSON array (Envoy encodes array claims this way)
    try:
        decoded = base64.b64decode(raw).decode("utf-8")
        parsed = json.loads(decoded)
        if isinstance(parsed, list):
            return [str(r) for r in parsed]
    except Exception:
        pass  # Expected: not base64/JSON, fall back to comma-separated parsing

    # Fall back to comma-separated plain text
    return [r.strip() for r in raw.split(",") if r.strip()]
