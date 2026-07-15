"""Hermetic environment for the Skuld test suite.

A developer machine may be running a live Forge/Skuld session that exports
``SKULD__*`` / ``VOLUNDR*`` environment variables (transport adapter, session id,
``skip_permissions``, volundr URL, ...). Pydantic ``BaseSettings`` ingests those,
so a test constructing ``SkuldSettings(...)`` would silently pick up the ambient
*production* config and diverge from CI, where the environment is clean. For
example an exported ``SKULD__TRANSPORT_ADAPTER=...PersistentSubprocessTransport``
overrides a test's explicit ``transport="sdk"`` and breaks the transport-selection
assertions.

This autouse fixture strips those ambient variables before each Skuld test so
local runs match CI. A test that needs a specific value sets it explicitly via
``monkeypatch.setenv`` (which applies after this fixture and is restored before
it), so per-test configuration keeps working.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

_AMBIENT_PREFIXES = ("SKULD__", "VOLUNDR__", "VOLUNDR_")


@pytest.fixture(autouse=True)
def _hermetic_skuld_env() -> Iterator[None]:
    saved = {k: v for k, v in os.environ.items() if k.startswith(_AMBIENT_PREFIXES)}
    for key in saved:
        del os.environ[key]
    try:
        yield
    finally:
        for key, value in saved.items():
            os.environ[key] = value
