"""Tests for Ting Helm chart templates."""

import re
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).parent.parent.parent / "charts" / "ting"
TING_MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "migrations" / "ting"


class TestChartMetadata:
    """Tests for Chart.yaml."""

    @pytest.fixture
    def chart_yaml(self) -> dict:
        return yaml.safe_load((CHART_DIR / "Chart.yaml").read_text())

    def test_chart_name(self, chart_yaml):
        assert chart_yaml["name"] == "ting"

    def test_chart_version_present(self, chart_yaml):
        assert chart_yaml["version"]


class TestDeploymentTemplate:
    """Tests for deployment.yaml."""

    @pytest.fixture
    def template_yaml(self) -> str:
        return (CHART_DIR / "templates" / "deployment.yaml").read_text()

    def test_uses_unified_niuu_image_command(self, template_yaml):
        assert ".Values.command" in template_yaml
        assert "name: {{ .Chart.Name }}" in template_yaml

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
        assert "guild_registry:" in template_yaml
        assert ".Values.guildRegistry.baseUrl" in template_yaml

    def test_workflow_wait_adapters_are_rendered_from_values(self, template_yaml):
        values = yaml.safe_load((CHART_DIR / "values.yaml").read_text())
        developer = values["workflowExecution"]
        assert developer["waitRepositoryAdapter"].endswith("PostgresWorkflowWaitRepository")
        observer_adapters = {
            entry["condition_type"]: entry["adapter"] for entry in developer["waitObservers"]
        }
        assert observer_adapters["forge.checks"].endswith("ForgeChecksWaitObserver")
        assert observer_adapters["forge.merge"].endswith("ForgeMergeWaitObserver")
        assert observer_adapters["timer"].endswith("TimerWaitObserver")
        assert developer["admissionRoles"] == ["volundr:developer"]
        assert "wait_repository_adapter:" in template_yaml
        assert ".Values.workflowExecution.waitRepositoryAdapter" in template_yaml
        assert "wait_repository_kwargs:" in template_yaml
        assert "wait_observers:" in template_yaml
        assert ".Values.workflowExecution.waitObservers" in template_yaml
        assert "admission_roles:" in template_yaml


class TestMigrationConfigMap:
    """Tests for embedded Ting SQL migrations."""

    @pytest.fixture
    def template_yaml(self) -> str:
        return (CHART_DIR / "templates" / "migrations-configmap.yaml").read_text()

    def test_embedded_migrations_match_source_files(self, template_yaml: str) -> None:
        pattern = re.compile(r"^  (\d+_[^:]+\.(?:up|down)\.sql): \|\n", re.MULTILINE)
        blocks: dict[str, str] = {}
        matches = list(pattern.finditer(template_yaml))
        template_end = template_yaml.index("{{- end }}")

        for index, match in enumerate(matches):
            name = match.group(1)
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else template_end
            lines = template_yaml[start:end].splitlines()
            content = (
                "\n".join(line[4:] if line.startswith("    ") else "" for line in lines).rstrip()
                + "\n"
            )
            blocks[name] = content

        source_files = sorted(path.name for path in TING_MIGRATIONS_DIR.glob("*.sql"))
        assert sorted(blocks) == source_files

        for filename in source_files:
            expected = (TING_MIGRATIONS_DIR / filename).read_text().rstrip() + "\n"
            assert blocks[filename] == expected
