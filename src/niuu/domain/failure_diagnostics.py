"""Safe text for failure diagnostics that cross service boundaries."""

from __future__ import annotations

import re

_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_SECRET_VALUE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?P<quote>[\"']?)(?P<key>(?:authorization|api[_-]?key|token|password|secret|credential)"
    r"|[A-Za-z][A-Za-z0-9_-]*(?:authorization|api[_-]?key|token|password|secret|credential)"
    r"[A-Za-z0-9_-]*)"
    r"(?P=quote)\s*(?P<separator>[:=])\s*(?!Bearer\s+\[REDACTED\])"
    r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|\S+)"
)


def sanitize_failure_reason(value: object) -> str:
    """Return a diagnostic reason with credential values removed."""
    reason = str(value or "").strip()
    reason = _BEARER_RE.sub("Bearer [REDACTED]", reason)
    reason = _JWT_RE.sub("[REDACTED_JWT]", reason)
    reason = _SECRET_VALUE_RE.sub(r"\g<quote>\g<key>\g<quote>\g<separator>[REDACTED]", reason)
    return reason
