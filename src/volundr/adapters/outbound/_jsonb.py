"""Shared JSONB serialization helpers for the outbound Postgres adapters.

PostgreSQL ``JSONB`` cannot store the Unicode NUL code point (U+0000). Even
though ``json.dumps`` happily escapes it to the valid JSON sequence ``\\u0000``,
asyncpg raises ``UntranslatableCharacterError`` ("unsupported Unicode escape
sequence") when that value reaches a JSONB column. Agent output (crash dumps,
hang-detector listings, raw terminal bytes) regularly contains NUL, so an
unsanitized payload fails the INSERT. Because both write paths use
``executemany``, a single poisoned frame fails the WHOLE batch and every frame
in it is dropped — sessions then look frozen while the agent is still working.

The fix strips the NUL code point from every string leaf AND every dict key
*before* serialization, so a NUL can never reach JSONB regardless of nesting.
Valid non-ASCII Unicode is preserved unchanged.
"""

import json

_NUL = "\x00"


def _strip_nul(value: object) -> object:
    """Recursively remove the NUL code point from string values and dict keys."""
    if isinstance(value, str):
        if _NUL not in value:
            return value
        return value.replace(_NUL, "")
    if isinstance(value, dict):
        return {_strip_key(k): _strip_nul(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_strip_nul(v) for v in value]
    return value


def _strip_key(key: object) -> object:
    """JSON object keys are strings; strip NUL but leave non-str keys intact."""
    if isinstance(key, str) and _NUL in key:
        return key.replace(_NUL, "")
    return key


def dumps_jsonb(value: object) -> str:
    """Serialize ``value`` to a JSON string safe for a Postgres JSONB column.

    Guarantees the result contains no ``\\u0000`` escape (the sequence asyncpg
    rejects), while preserving all other content — including valid non-ASCII
    Unicode — byte-for-byte with a plain :func:`json.dumps`.
    """
    return json.dumps(_strip_nul(value))
