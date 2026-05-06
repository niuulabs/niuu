"""Serialisation helpers for SleipnirEvent.

Wire formats:
- **msgpack** — compact binary format; preferred for nng transport.
- **JSON** — human-readable fallback for debugging and HTTP SSE delivery.

Both formats round-trip through :meth:`SleipnirEvent.to_dict` /
:meth:`SleipnirEvent.from_dict`, keeping datetime values as ISO 8601 strings.
"""

from __future__ import annotations

import json
from typing import Literal

from sleipnir.domain.events import SleipnirEvent

try:
    import msgpack as _msgpack

    _MSGPACK_AVAILABLE = True
except ImportError:  # pragma: no cover
    _MSGPACK_AVAILABLE = False

SerializationFormat = Literal["msgpack", "json"]


def serialize(event: SleipnirEvent, fmt: SerializationFormat = "msgpack") -> bytes:
    """Serialise *event* to bytes using *fmt*.

    :param event: The event to serialise.
    :param fmt: ``"msgpack"`` (default) or ``"json"``.
    :raises ImportError: If msgpack is requested but not installed.
    :raises ValueError: If *fmt* is not a recognised format.
    """
    if fmt == "msgpack":
        if not _MSGPACK_AVAILABLE:
            raise ImportError("msgpack is not installed. Install it with: pip install msgpack")
        return _msgpack.packb(event.to_dict(), use_bin_type=True)

    if fmt == "json":
        return json.dumps(event.to_dict()).encode("utf-8")

    raise ValueError(f"Unknown serialisation format: {fmt!r}. Use 'msgpack' or 'json'.")


def deserialize(data: bytes, fmt: SerializationFormat = "msgpack") -> SleipnirEvent:
    """Deserialise *data* produced by :func:`serialize`.

    :param data: Raw bytes from :func:`serialize`.
    :param fmt: ``"msgpack"`` (default) or ``"json"``.
    :raises ImportError: If msgpack is requested but not installed.
    :raises ValueError: If *fmt* is not a recognised format.
    """
    if fmt == "msgpack":
        if not _MSGPACK_AVAILABLE:
            raise ImportError("msgpack is not installed. Install it with: pip install msgpack")
        raw = _msgpack.unpackb(data, raw=False)
        return SleipnirEvent.from_dict(raw)

    if fmt == "json":
        raw = json.loads(data.decode("utf-8"))
        return SleipnirEvent.from_dict(raw)

    raise ValueError(f"Unknown serialisation format: {fmt!r}. Use 'msgpack' or 'json'.")


def msgpack_available() -> bool:
    """Return True if msgpack is installed and can be used."""
    return _MSGPACK_AVAILABLE
