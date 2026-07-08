from __future__ import annotations

from mimir import __main__ as mimir_main


def test_mcp_adapter_prefers_configured_mimir(monkeypatch) -> None:
    configured = object()
    monkeypatch.setattr(mimir_main, "_configured_mimir_adapter", lambda: configured)

    assert mimir_main._mcp_adapter("~/.ravn/mimir") is configured
