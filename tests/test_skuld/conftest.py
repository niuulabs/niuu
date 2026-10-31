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


@pytest.fixture(autouse=True)
def _hermetic_forge_runtime(
    _hermetic_skuld_env: None, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[None]:
    """Keep each test broker's loopback secret and generated configs out of the real /tmp."""
    runtime_dir = tmp_path_factory.mktemp("skuld-runtime")
    previous = os.environ.get("SKULD__FORGE_MCP__RUNTIME_DIR")
    os.environ["SKULD__FORGE_MCP__RUNTIME_DIR"] = str(runtime_dir)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("SKULD__FORGE_MCP__RUNTIME_DIR", None)
        else:
            os.environ["SKULD__FORGE_MCP__RUNTIME_DIR"] = previous


@pytest.fixture
def module_broker_runtime(tmp_path_factory: pytest.TempPathFactory) -> Iterator[object]:
    """Give the module-level broker singleton a fresh loopback runtime for one test."""
    from skuld import broker as bmod
    from skuld.session_runtime import SessionRuntime

    singleton = bmod.broker
    saved = (singleton._session_runtime_cache, singleton._session_tools_cache)
    runtime = SessionRuntime.create(str(tmp_path_factory.mktemp("broker-rt")), "test-session")
    singleton._session_runtime_cache = runtime
    singleton._session_tools_cache = None
    try:
        yield runtime
    finally:
        singleton._session_runtime_cache, singleton._session_tools_cache = saved


@pytest.fixture(autouse=True)
def _isolate_codex_daemon_dir(tmp_path, monkeypatch):
    """Keep Codex app-server spawns from touching the real /tmp/codex-daemon-<uid>."""
    monkeypatch.setattr("skuld.transports.codex_ws._CODEX_DAEMON_PARENT", tmp_path)
