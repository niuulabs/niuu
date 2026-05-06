"""Helpers for resolving known persona names in Tyr API wiring."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from ravn.ports.persona import PersonaPort

logger = logging.getLogger(__name__)


def build_persona_names_dependency(
    persona_source: PersonaPort | None,
) -> Callable[[], Awaitable[set[str]]]:
    """Return a dependency that resolves known persona names from a real source."""

    async def _resolve_persona_names() -> set[str]:
        if persona_source is None:
            return set()

        try:
            return set(persona_source.list_names())
        except Exception:
            logger.warning(
                "Failed to list persona names for flock flow validation",
                exc_info=True,
            )
            return set()

    return _resolve_persona_names
