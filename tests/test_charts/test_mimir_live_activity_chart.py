"""Helm chart rendering for the Mímir service config: its backend and live activity.

Renders the real chart with `helm template` against a tmp values file and inspects
the ConfigMap and Deployment.
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

_BACKEND = {
    "mcpUrl": "http://gbrain:3131/mcp",
    "apiTokenSecret": {"name": "gbrain-token", "key": "token"},
}


def _values(tmp_path: Path, values: dict) -> Path:
    path = tmp_path / "values.yaml"
    path.write_text(yaml.safe_dump(values))
    return path


def _helm(values_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["helm", "template", "release", str(_CHART), "-f", str(values_path)],
        capture_output=True,
        text=True,
    )


def _render(values_path: Path) -> list[dict]:
    rendered = _helm(values_path)
    assert rendered.returncode == 0, rendered.stderr
    return list(yaml.safe_load_all(rendered.stdout))


def _config(docs: list[dict]) -> dict:
    cm = next(d for d in docs if d["kind"] == "ConfigMap" and "config.yaml" in d.get("data", {}))
    return yaml.safe_load(cm["data"]["config.yaml"])


def test_live_activity_config_renders_into_configmap(tmp_path: Path) -> None:
    values = _values(
        tmp_path,
        {
            "config": {
                "backend": _BACKEND,
                "liveActivity": {"buffer_size": 500, "window_seconds": 120},
            }
        },
    )
    assert _config(_render(values))["live_activity"] == {
        "buffer_size": 500,
        "window_seconds": 120,
    }


def test_live_activity_omitted_when_not_configured(tmp_path: Path) -> None:
    config = _config(_render(_values(tmp_path, {"config": {"backend": _BACKEND}})))

    # The service falls back to LiveActivityConfig's own code defaults.
    assert "live_activity" not in config


def test_backend_renders_the_gbrain_adapter_with_a_token_file(tmp_path: Path) -> None:
    docs = _render(_values(tmp_path, {"config": {"backend": _BACKEND}}))

    assert _config(docs)["backend"] == {
        "adapter": "mimir.adapters.gbrain.GBrainMimirAdapter",
        "mcp_url": "http://gbrain:3131/mcp",
        "api_token_file": "/etc/mimir-backend/token",
    }
    deployment = next(d for d in docs if d["kind"] == "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    assert {
        "name": "backend-token",
        "secret": {"secretName": "gbrain-token", "defaultMode": 0o440},
    } in pod["volumes"]
    container = pod["containers"][0]
    assert {"name": "MIMIR_CONFIG", "value": "/etc/mimir/config.yaml"} in container["env"]
    assert any(
        m["name"] == "backend-token" and m["mountPath"] == "/etc/mimir-backend"
        for m in container["volumeMounts"]
    )


def test_optional_backend_values_render_only_when_set(tmp_path: Path) -> None:
    backend = {
        **_BACKEND,
        "ingestUrl": "http://gbrain:3131/ingest",
        "timeoutSeconds": 45,
        "searchLimit": 20,
        "thinkModel": "anthropic:claude-sonnet",
        "queryExpansion": False,
    }
    config = _config(_render(_values(tmp_path, {"config": {"backend": backend}})))

    assert config["backend"]["ingest_url"] == "http://gbrain:3131/ingest"
    assert config["backend"]["timeout_seconds"] == 45
    assert config["backend"]["search_limit"] == 20
    assert config["backend"]["think_model"] == "anthropic:claude-sonnet"
    assert config["backend"]["query_expansion"] is False


def test_removed_markdown_store_settings_are_not_rendered(tmp_path: Path) -> None:
    config = _config(_render(_values(tmp_path, {"config": {"backend": _BACKEND}})))

    for removed in ("search_db", "embedding_model", "ranking", "evidence"):
        assert removed not in config


def test_the_knowledge_deployment_is_rendered_as_configured(tmp_path: Path) -> None:
    deployment = {
        "adapter": "mimir.adapters.flux.FluxKnowledgeDeploymentAdapter",
        "namespace": "knowledge",
        "source_name": "niuu",
    }
    values = _values(
        tmp_path,
        {
            "config": {"backend": _BACKEND},
            "knowledgeDeployment": deployment,
            "envoy": {"enabled": True},
        },
    )

    assert _config(_render(values))["deployment"] == deployment


def test_chart_refuses_to_render_without_a_backend_url(tmp_path: Path) -> None:
    rendered = _helm(
        _values(tmp_path, {"config": {"backend": {"apiTokenSecret": {"name": "gbrain-token"}}}})
    )

    assert rendered.returncode != 0
    assert "config.backend.mcpUrl is required" in rendered.stderr


def test_chart_refuses_to_render_without_a_backend_token_secret(tmp_path: Path) -> None:
    rendered = _helm(_values(tmp_path, {"config": {"backend": {"mcpUrl": "http://gbrain/mcp"}}}))

    assert rendered.returncode != 0
    assert "config.backend.apiTokenSecret.name is required" in rendered.stderr
