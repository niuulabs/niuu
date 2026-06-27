"""Shared sanitization helpers for the outbound Postgres event-log adapters.

PostgreSQL ``text``/``JSONB`` cannot store the Unicode NUL code point (U+0000)
or lone UTF-16 surrogates (U+D800–U+DFFF). When such a character reaches a column
asyncpg raises ``UntranslatableCharacterError`` ("unsupported Unicode escape
sequence"). Agent output (crash dumps, hang-detector listings, tmux logs, raw
terminal bytes) regularly contains these. Both event-log write paths use
``executemany``, so a single poisoned frame fails the WHOLE batch and every frame
in it is dropped — the session's output stream is black-holed (UI shows nothing)
while ``/activity`` still reports 204/Active.

Two layers, used by both write paths:

1. **Primary** — scrub the offending characters (replacing each with U+FFFD, so
   the frame is preserved lossless-ish rather than dropped) from every string
   value AND dict key in the JSON payload, and from the plain text columns
   (``kind``/``role``/``request_id``/``event_type``/``model``) too. ``dumps_jsonb``
   is the single serialization choke point.
2. **Defensive** — ``force_scrub_json`` strips the escaped NUL/surrogate sequences
   from an already-serialized JSON string, so a write path can catch a
   still-thrown ``UntranslatableCharacterError`` and retry the batch instead of
   500-ing and dropping the frame.

Valid content — including astral characters (emoji, CJK) — is preserved: those are
single codepoints outside the surrogate range, so the scrub never touches them.
"""

import json
import re

REPLACEMENT = "�"
# NUL and lone UTF-16 surrogates: the characters Postgres text/JSONB rejects.
_INVALID_CHARS = re.compile("[\x00\ud800-\udfff]")
# The same, as they appear ESCAPED in json.dumps(ensure_ascii=True) output.
_INVALID_ESCAPES = re.compile(r"\\u(?:0000|[dD][89aAbBcCdDeEfF][0-9a-fA-F]{2})")


def scrub_text(value: object) -> object:
    """Replace NUL + lone surrogates with U+FFFD in a string. A non-str (incl.
    ``None``) passes through unchanged."""
    if not isinstance(value, str):
        return value
    if not _INVALID_CHARS.search(value):
        return value
    return _INVALID_CHARS.sub(REPLACEMENT, value)


def _scrub(value: object) -> object:
    """Recursively scrub string values and dict keys."""
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return {scrub_text(k): _scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    return value


def dumps_jsonb(value: object) -> str:
    """Serialize ``value`` to a JSON string safe for a Postgres JSONB column: NUL
    and lone surrogates are replaced with U+FFFD in every string value and dict
    key; all other content (including valid non-ASCII) is preserved."""
    return json.dumps(_scrub(value))


def force_scrub_json(serialized: str) -> str:
    """Defensive last resort: replace the escaped NUL/surrogate sequences in an
    already-serialized JSON string with an escaped U+FFFD, for retrying an insert
    that still raised ``UntranslatableCharacterError``."""
    return _INVALID_ESCAPES.sub(lambda _m: "\\ufffd", serialized)
