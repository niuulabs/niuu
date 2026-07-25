"""Tests for Niuu umbrella Helm chart templates."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).parent.parent.parent / "charts" / "niuu"
GUILD_CHART_DIR = Path(__file__).parent.parent.parent / "charts" / "guild"


class TestValuesDefaults:
    """Tests for values.yaml defaults."""

    @pytest.fixture
    def values_yaml(self) -> dict:
        """Load values.yaml."""
        return yaml.safe_load((CHART_DIR / "values.yaml").read_text())

    def test_forge_api_route_uses_logical_backend(self, values_yaml: dict) -> None:
        """The umbrella ingress should not hard-code Guild as the only Forge backend."""
        forge_route = next(
            route
            for route in values_yaml["ingress"]["routeSets"]["api"]
            if route["path"] == "/api/v1/forge"
        )

        assert forge_route["service"] == "forge-api"

    def test_tokens_route_uses_target_volundr_backend(self, values_yaml: dict) -> None:
        """PATs must be created against the target Volundr database."""
        tokens_route = next(
            route
            for route in values_yaml["ingress"]["routeSets"]["api"]
            if route["path"] == "/api/v1/tokens"
        )

        assert tokens_route["service"] == "volundr"

    def test_session_proxy_route_uses_target_volundr_backend(self, values_yaml: dict) -> None:
        session_route = next(
            route for route in values_yaml["ingress"]["routeSets"]["api"] if route["path"] == "/s"
        )

        assert session_route["service"] == "volundr"


class TestIngressTemplate:
    """Tests for ingress backend resolution."""

    @pytest.fixture
    def helpers_tpl(self) -> str:
        """Load _helpers.tpl."""
        return (CHART_DIR / "templates" / "_helpers.tpl").read_text()

    def test_forge_api_backend_resolves_from_enabled_services(self, helpers_tpl: str) -> None:
        """Forge API routing should prefer Guild but still work with Volundr alone."""
        assert 'eq $service "forge-api"' in helpers_tpl
        assert "$root.Values.guild.enabled" in helpers_tpl
        assert "$root.Values.volundr.enabled" in helpers_tpl

    def test_renders_forge_route_to_guild_when_guild_enabled(self) -> None:
        """Render proof for the default aggregate deployment."""
        rendered = _render_niuu_chart()

        assert _service_for_path(rendered, "/api/v1/forge") == "niuu-test-guild"

    def test_renders_forge_route_to_volundr_when_guild_disabled(self) -> None:
        """Render proof for a Forge-only umbrella deployment."""
        rendered = _render_niuu_chart("--set", "guild.enabled=false")

        assert _service_for_path(rendered, "/api/v1/forge") == "niuu-test-volundr"

    def test_renders_tokens_route_to_volundr(self) -> None:
        """Render proof that target-local PAT management reaches Volundr."""
        rendered = _render_niuu_chart()

        assert _service_for_path(rendered, "/api/v1/tokens") == "niuu-test-volundr"

    def test_renders_session_proxy_route_to_volundr(self) -> None:
        rendered = _render_niuu_chart()

        assert _service_for_path(rendered, "/s") == "niuu-test-volundr"


def test_observatory_image_tag_overrides_global_default() -> None:
    rendered = _render_niuu_chart(
        "--set",
        "global.image.tag=global-tag",
        "--set",
        "observatory.image.tag=observatory-tag",
    )

    assert _deployment_image(rendered, "niuu-test-observatory") == (
        "ghcr.io/niuulabs/niuu:observatory-tag"
    )


def test_guild_envoy_accepts_websocket_upgrades() -> None:
    envoy_template = (GUILD_CHART_DIR / "templates" / "envoy-configmap.yaml").read_text()

    assert "upgrade_configs:" in envoy_template
    assert "- upgrade_type: websocket" in envoy_template


def _render_niuu_chart(*extra_args: str) -> str:
    helm = shutil.which("helm")
    if not helm:
        pytest.skip("helm is not installed")

    dependency_result = subprocess.run(
        [helm, "dependency", "build", str(CHART_DIR)],
        capture_output=True,
        text=True,
    )
    if dependency_result.returncode != 0:
        pytest.fail(
            "helm dependency build failed\n"
            f"stdout:\n{dependency_result.stdout}\n"
            f"stderr:\n{dependency_result.stderr}"
        )

    result = subprocess.run(
        [
            helm,
            "template",
            "niuu-test",
            str(CHART_DIR),
            "--set",
            "ingress.enabled=true",
            "--set",
            "ingress.hosts[0].host=api.test.local",
            "--set",
            "ingress.hosts[0].routeSets[0]=api",
            *extra_args,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"helm template failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return result.stdout


def _service_for_path(rendered_yaml: str, path: str) -> str:
    for document in yaml.safe_load_all(rendered_yaml):
        if not isinstance(document, dict) or document.get("kind") != "Ingress":
            continue
        for rule in document["spec"]["rules"]:
            for route in rule["http"]["paths"]:
                if route["path"] == path:
                    return route["backend"]["service"]["name"]
    raise AssertionError(f"route path not found: {path}")


def _deployment_image(rendered_yaml: str, name: str) -> str:
    for document in yaml.safe_load_all(rendered_yaml):
        if (
            isinstance(document, dict)
            and document.get("kind") == "Deployment"
            and document.get("metadata", {}).get("name") == name
        ):
            return document["spec"]["template"]["spec"]["containers"][0]["image"]
    raise AssertionError(f"deployment not found: {name}")
