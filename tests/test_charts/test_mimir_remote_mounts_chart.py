"""Helm chart rendering for operator-declared remote mounts (RemoteMountConfig)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from mimir.config import RemoteMountConfig

pytestmark = pytest.mark.skipif(
    shutil.which("helm") is None, reason="Helm is required to render charts"
)

_CHART = Path(__file__).resolve().parents[2] / "charts/mimir"


def _config_yaml(tmp_path: Path, values: dict) -> dict | None:
    values_path = tmp_path / "values.yaml"
    values_path.write_text(yaml.safe_dump(values))
    rendered = subprocess.run(
        ["helm", "template", "release", str(_CHART), "-f", str(values_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    cm = next(d for d in yaml.safe_load_all(rendered.stdout) if d and d["kind"] == "ConfigMap")
    raw = cm.get("data", {}).get("config.yaml")
    return yaml.safe_load(raw) if raw else None


def test_remote_mounts_render_as_the_service_reads_them(tmp_path: Path) -> None:
    mount = {
        "name": "regin",
        "role": "local",
        "adapter": "ravn.adapters.mimir.http.HttpMimirAdapter",
        "kwargs": {
            "base_url": "https://regin.valhalla.test",
            "auth": {"type": "workload", "audiences": ["regin-mimir"]},
        },
    }

    config = _config_yaml(tmp_path, {"config": {"remoteMounts": [mount]}})

    assert config is not None
    assert config["remote_mounts"] == [mount]
    assert RemoteMountConfig(**config["remote_mounts"][0]).name == "regin"


def test_no_remote_mounts_render_nothing(tmp_path: Path) -> None:
    assert _config_yaml(tmp_path, {}) is None
