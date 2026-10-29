"""Safe text for failure diagnostics that cross service boundaries."""

from __future__ import annotations

import re

MAX_FAILURE_REASON_CHARS = 1_000
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_SECRET_VALUE_RE = re.compile(
    r"(?i)\b(authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"token|password|secret|credential)\b\s*([:=])\s*(?:\"[^\"]*\"|'[^']*'|\S+)"
)


def sanitize_failure_reason(value: object) -> str:
    """Return a bounded diagnostic reason with credential values removed."""
    reason = str(value or "").strip()
    reason = _BEARER_RE.sub("Bearer [REDACTED]", reason)
    reason = _JWT_RE.sub("[REDACTED_JWT]", reason)
    reason = _SECRET_VALUE_RE.sub(r"\1\2[REDACTED]", reason)
    return reason[:MAX_FAILURE_REASON_CHARS]
