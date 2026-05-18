"""Tests for the standalone Guild entrypoint."""

from __future__ import annotations

from fastapi import FastAPI

import niuu.guild_main as guild_main
from niuu.guild_app import app as guild_app


def test_guild_main_exposes_app() -> None:
    assert isinstance(guild_app, FastAPI)


def test_guild_main_runs_service_app(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(import_path: str, default_port: int) -> None:
        captured["import_path"] = import_path
        captured["default_port"] = default_port

    monkeypatch.setattr(guild_main, "run_service_app", fake_run)

    guild_main.main()

    assert captured == {
        "import_path": "niuu.guild_app:app",
        "default_port": 8084,
    }
