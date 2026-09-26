"""Helm chart rendering for LiveActivityConfig (GET /mimir/activity/live).

Mirrors the pattern in test_mimir_embedded_warden.py: render the real chart
with `helm template` against a tmp values file and inspect the ConfigMap.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    shutil.which("helm") is None, reason="Helm is required to render charts"
)

_CHART = Path(__file__).resolve().parents[2] / "charts/mimir"


def _render(values_path: Path) -> list[dict]:
    rendered = subprocess.run(
        ["helm", "template", "release", str(_CHART), "-f", str(values_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return list(yaml.safe_load_all(rendered.stdout))


def test_live_activity_config_renders_into_configmap(tmp_path: Path) -> None:
    values = tmp_path / "values.yaml"
    values.write_text(
        yaml.safe_dump({"config": {"liveActivity": {"buffer_size": 500, "window_seconds": 120}}})
    )
    docs = _render(values)
    cm = next(d for d in docs if d["kind"] == "ConfigMap" and "config.yaml" in d.get("data", {}))
    config = yaml.safe_load(cm["data"]["config.yaml"])
    assert config["live_activity"] == {"buffer_size": 500, "window_seconds": 120}


def test_live_activity_omitted_when_not_configured(tmp_path: Path) -> None:
    values = tmp_path / "values.yaml"
    values.write_text(yaml.safe_dump({}))
    docs = _render(values)
    cm = next(d for d in docs if d["kind"] == "ConfigMap")
    # Nothing in the default values.yaml (empty liveActivity/observability,
    # no tenantId, no knowledgeDeployment) opts into the config.yaml block —
    # the service falls back to LiveActivityConfig's own code defaults.
    assert "config.yaml" not in cm.get("data", {})
