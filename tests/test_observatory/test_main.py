"""Tests for the standalone Observatory entrypoint."""

from __future__ import annotations

from fastapi import FastAPI

import observatory.main as observatory_main


def test_observatory_main_exposes_app() -> None:
    assert isinstance(observatory_main.app, FastAPI)


def test_observatory_main_runs_service_app(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(import_path: str, default_port: int) -> None:
        captured["import_path"] = import_path
        captured["default_port"] = default_port

    monkeypatch.setattr(observatory_main, "run_service_app", fake_run)

    observatory_main.main()

    assert captured == {
        "import_path": "observatory.main:app",
        "default_port": 8083,
    }
