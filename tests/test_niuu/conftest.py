"""Test isolation for niuu.observability's process-wide state.

configure_observability/instrument_httpx_client mutate module-level globals
and monkeypatch the httpx transport classes process-wide — state that must
not leak between tests, regardless of which test failed or raised.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from niuu import observability as obs_module
from niuu.cap_telemetry import reset_cap_hits


@pytest.fixture(autouse=True)
def _reset_observability_state() -> Iterator[None]:
    obs_module.shutdown_observability()
    try:
        yield
    finally:
        obs_module.shutdown_observability()
        obs_module.uninstrument_httpx_client()


@pytest.fixture(autouse=True)
def _fresh_cap_hits() -> None:
    """Cap-hit counts are process-wide; each test starts from none."""
    reset_cap_hits()
