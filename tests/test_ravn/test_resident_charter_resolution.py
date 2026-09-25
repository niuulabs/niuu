"""Tests for resolving a resident's charter from its realm's Mímir page.

``environment.charter_mimir_page`` is a decision the operator made — the
resident should fail to start rather than silently keep using the static
``environment.charter`` string when that page is missing (no-fallbacks.md).
"""

from __future__ import annotations

import pytest

# Importing ravn.cli.commands runs the compatibility-facade patching that
# injects names (Settings, etc.) into resident_runtime_wiring's globals; the
# wiring module is not meant to be imported standalone.
import ravn.cli.commands  # noqa: F401
from ravn.cli import resident_runtime_wiring as wiring
from ravn.config import Settings


class _FakeMimir:
    def __init__(self, pages: dict[str, str]) -> None:
        self._pages = pages

    async def read_page(self, path: str) -> str:
        try:
            return self._pages[path]
        except KeyError:
            raise FileNotFoundError(path) from None


async def test_static_charter_is_unchanged_when_no_mimir_page_configured():
    settings = Settings()
    settings.environment.charter = "Steward the workshop."
    assert await wiring._resolve_environment_charter(settings, None) == "Steward the workshop."


async def test_resolves_charter_from_mimir_when_page_exists():
    settings = Settings()
    settings.environment.charter = "static fallback text"
    settings.environment.charter_mimir_page = "realms/workshop/charter.md"
    mimir = _FakeMimir({"realms/workshop/charter.md": "Steward the workshop from its signals."})

    charter = await wiring._resolve_environment_charter(settings, mimir)
    assert charter == "Steward the workshop from its signals."


async def test_raises_when_charter_page_configured_but_missing():
    settings = Settings()
    settings.environment.charter_mimir_page = "realms/workshop/charter.md"
    mimir = _FakeMimir({})

    with pytest.raises(RuntimeError, match="does not exist yet"):
        await wiring._resolve_environment_charter(settings, mimir)


async def test_raises_when_charter_page_configured_but_mimir_disabled():
    settings = Settings()
    settings.environment.charter_mimir_page = "realms/workshop/charter.md"

    with pytest.raises(RuntimeError, match="Mímir is disabled"):
        await wiring._resolve_environment_charter(settings, None)
