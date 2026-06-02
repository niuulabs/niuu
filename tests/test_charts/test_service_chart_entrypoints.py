"""Tests for service chart entrypoint defaults."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

CHARTS_DIR = Path(__file__).parent.parent.parent / "charts"


@pytest.mark.parametrize(
    ("chart", "expected_command"),
    [
        ("ting", ["python", "-m", "ting"]),
        ("guild", ["python", "-m", "guild.main"]),
        ("observatory", ["python", "-m", "observatory.main"]),
        ("bifrost", ["python", "-m", "bifrost"]),
        ("ravn", ["python", "-m", "ravn.main"]),
    ],
)
def test_niuu_image_charts_use_packaged_module_entrypoints(
    chart: str,
    expected_command: list[str],
) -> None:
    """Charts for the unified niuu image should not depend on removed scripts."""
    values = _load_values(chart)

    assert values["image"]["repository"] in {"niuu", "niuulabs/niuu"}
    assert values["command"] == expected_command
    assert values.get("args", []) == []


def test_volundr_chart_uses_uvicorn_import_path() -> None:
    """Standalone Volundr/Forge should launch from an import path in k8s."""
    values = _load_values("volundr")

    assert values["image"]["repository"] == "niuulabs/niuu"
    assert values["command"] == ["uvicorn"]
    assert values["args"] == [
        "volundr.main:app",
        "--host",
        "$(HOST)",
        "--port",
        "$(PORT)",
        "--workers",
        "$(WORKERS)",
    ]


def _load_values(chart: str) -> dict:
    return yaml.safe_load((CHARTS_DIR / chart / "values.yaml").read_text())
