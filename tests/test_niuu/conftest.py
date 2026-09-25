"""Test isolation for niuu.observability's process-wide state.

configure_observability/instrument_httpx_client mutate module-level globals
and monkeypatch the httpx transport classes process-wide — state that must
not leak between tests, regardless of which test failed or raised.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from niuu import observability as obs_module


@pytest.fixture(autouse=True)
def _reset_observability_state() -> Iterator[None]:
    obs_module.shutdown_observability()
    try:
        yield
    finally:
        obs_module.shutdown_observability()
        obs_module.uninstrument_httpx_client()
