"""Tests for Tyr Helm chart templates."""

from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).parent.parent.parent / "charts" / "tyr"


class TestChartMetadata:
    """Tests for Chart.yaml."""

    @pytest.fixture
    def chart_yaml(self) -> dict:
        return yaml.safe_load((CHART_DIR / "Chart.yaml").read_text())

    def test_chart_name(self, chart_yaml):
        assert chart_yaml["name"] == "tyr"

    def test_chart_version_present(self, chart_yaml):
        assert chart_yaml["version"]


class TestDeploymentTemplate:
    """Tests for deployment.yaml."""

    @pytest.fixture
    def template_yaml(self) -> str:
        return (CHART_DIR / "templates" / "deployment.yaml").read_text()

    def test_uses_unified_niuu_image_command(self, template_yaml):
        assert ".Values.command" in template_yaml
        assert 'name: {{ .Chart.Name }}' in template_yaml

    def test_has_process_runtime_env_vars(self, template_yaml):
        assert "HOST" in template_yaml
        assert "PORT" in template_yaml
        assert "WORKERS" in template_yaml

    def test_has_database_env_vars(self, template_yaml):
        assert "DATABASE__HOST" in template_yaml
        assert "DATABASE__PORT" in template_yaml
        assert "DATABASE__NAME" in template_yaml
        assert "DATABASE__USER" in template_yaml
        assert "DATABASE__PASSWORD" in template_yaml


class TestConfigMapTemplate:
    """Tests for configmap.yaml."""

    @pytest.fixture
    def template_yaml(self) -> str:
        return (CHART_DIR / "templates" / "configmap.yaml").read_text()

    def test_has_runtime_config_entries(self, template_yaml):
        assert "HOST:" in template_yaml
        assert "PORT:" in template_yaml
        assert "WORKERS:" in template_yaml

    def test_has_embedded_config_yaml(self, template_yaml):
        assert "config.yaml: |" in template_yaml
        assert "database:" in template_yaml
        assert "volundr:" in template_yaml
