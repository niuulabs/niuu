"""The ``[SURFACE]`` marker: an agent's request to escalate, never text for a person."""

from __future__ import annotations

import re

SURFACE_PREFIX = "[SURFACE]"

_SURFACE_MARKER = re.compile(r"^[ \t]*\[SURFACE\][ \t]*", re.MULTILINE)


def strip_surface_marker(text: str) -> str:
    """Remove the marker from the start of any line of *text*."""
    return _SURFACE_MARKER.sub("", text)
