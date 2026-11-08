"""Shared fixtures for the persona tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_home(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Keep the developer's own ``~/.ravn/personas`` out of the default persona search path.

    The filesystem loader's default mode reads the user-global persona directory, so without
    this the tests depend on whatever happens to be in the home directory of the machine that
    runs them.
    """
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    return home
