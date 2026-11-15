from __future__ import annotations

import pytest
import typer

from mimir import __main__ as mimir_main


def test_mcp_without_a_configured_mimir_is_an_error(monkeypatch) -> None:
    monkeypatch.setattr(mimir_main, "_configured_mimir_adapter", lambda: None)

    with pytest.raises(typer.BadParameter, match="--adapter-config"):
        mimir_main.mcp(name="local", adapter_config=None, read_only=False, write_prefix=[])


def test_backend_option_parses_a_json_object() -> None:
    assert mimir_main._backend_option('{"adapter": "a.B", "x": 1}') == {"adapter": "a.B", "x": 1}


@pytest.mark.parametrize("raw", ["not json", "[1, 2]"])
def test_backend_option_rejects_anything_else(raw: str) -> None:
    with pytest.raises(typer.BadParameter, match="--backend"):
        mimir_main._backend_option(raw)
