"""Helm chart rendering for resident memory (mimir.config.ResidentMemoryConfig)."""

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


def test_resident_memory_renders_as_the_service_reads_it(tmp_path: Path) -> None:
    from mimir.config import MimirServiceConfig

    resident_memory = {
        "directory": {
            "adapter": "mimir.adapters.valkyrie_memory.ValkyrieMemoryDirectory",
            "base_url": "http://niuu-ravn.volundr.svc.cluster.local",
            "auth_adapter": (
                "niuu.adapters.outbound.http_auth.WorkloadIdentityBearerTokenAuthAdapter"
            ),
            "timeout_seconds": 10,
        },
        "mount_auth": {"type": "workload", "audiences": ["resident-memory"]},
    }

    config = _config_yaml(tmp_path, {"config": {"residentMemory": resident_memory}})

    assert config is not None
    assert config["resident_memory"] == resident_memory
    parsed = MimirServiceConfig(path=str(tmp_path), resident_memory=config["resident_memory"])
    assert parsed.resident_memory is not None


def test_no_resident_memory_renders_nothing(tmp_path: Path) -> None:
    assert _config_yaml(tmp_path, {}) is None
