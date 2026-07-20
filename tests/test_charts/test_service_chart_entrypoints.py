"""Tests for service chart entrypoint defaults."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

CHARTS_DIR = Path(__file__).parent.parent.parent / "charts"
CHARTS = (
    "agent",
    "bifrost",
    "guild",
    "mimir",
    "niuu",
    "niuu-shared",
    "observatory",
    "ravn",
    "skuld",
    "skuld-planner",
    "ting",
    "volundr",
)


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
        "volundr.main:create_app",
        "--factory",
        "--host",
        "$(HOST)",
        "--port",
        "$(PORT)",
        "--workers",
        "$(WORKERS)",
    ]


@pytest.mark.parametrize("chart", ["guild", "observatory"])
def test_agent_directory_chart_config_exposes_bounded_runtime_settings(chart: str) -> None:
    values = _load_values(chart)
    template = (CHARTS_DIR / chart / "templates" / "configmap.yaml").read_text()

    assert values["directory"]["cardTimeoutSeconds"] > 0
    assert values["directory"]["localMaxConcurrency"] >= 1
    assert values["directory"]["guildMaxConcurrency"] >= 1
    assert "observatory:" in template
    assert ".Values.directory.cardTimeoutSeconds" in template
    assert ".Values.directory.guildMaxConcurrency" in template
    assert ".Values.directory.signatureAlgorithms" in template
    assert ".Values.directory.authenticatedCardOrigins" in template


def test_mimir_probes_use_constant_time_health_endpoint() -> None:
    values = _load_values("mimir")
    deployment = (CHARTS_DIR / "mimir" / "templates" / "deployment.yaml").read_text()

    assert values["startupProbe"]["path"] == "/health"
    assert "/mimir/stats" not in deployment


def test_agent_chart_can_install_verified_learned_tool_job_contract() -> None:
    values = _load_values("agent")
    network_policy = (
        CHARTS_DIR / "agent" / "templates" / "learned-tool-networkpolicy.yaml"
    ).read_text()
    rbac = (CHARTS_DIR / "agent" / "templates" / "rbac.yaml").read_text()
    config = (CHARTS_DIR / "agent" / "values.yaml").read_text()

    assert values["learnedToolRunner"]["enabled"] is False
    assert "@sha256:" in values["learnedToolRunner"]["image"]
    assert "ingress: []" in network_policy
    assert "egress: []" in network_policy
    assert "networkpolicies" in rbac
    assert "learned_tool_execution_backend: k8s_job" in config


@pytest.mark.parametrize("chart", CHARTS)
def test_charts_default_niuu_deployment_cluster_value(chart: str) -> None:
    values = _load_values(chart)

    assert values["global"]["niuu"]["cluster"] == "unknown"


@pytest.mark.parametrize("chart", CHARTS)
def test_charts_include_niuu_deployment_labels(chart: str) -> None:
    helpers = (CHARTS_DIR / chart / "templates" / "_helpers.tpl").read_text()

    assert "niuu.world/cluster" in helpers
    assert "niuu.world/namespace" in helpers


def _load_values(chart: str) -> dict:
    return yaml.safe_load((CHARTS_DIR / chart / "values.yaml").read_text())
