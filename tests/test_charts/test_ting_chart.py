"""Tests for Ting Helm chart templates."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from ting.config import Settings

CHART_DIR = Path(__file__).parent.parent.parent / "charts" / "ting"
TING_MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "migrations" / "ting"


def _render_ting_chart(tmp_path: Path, values: dict) -> str:
    helm = shutil.which("helm")
    if not helm:
        pytest.skip("helm is not installed")

    values_file = tmp_path / "values.yaml"
    values_file.write_text(yaml.safe_dump(values), encoding="utf-8")
    result = subprocess.run(
        [helm, "template", "ting-test", str(CHART_DIR), "-f", str(values_file)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"helm template failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return result.stdout


def _config_from_rendered(rendered_yaml: str) -> dict:
    for document in yaml.safe_load_all(rendered_yaml):
        if not isinstance(document, dict) or document.get("kind") != "ConfigMap":
            continue
        body = (document.get("data") or {}).get("config.yaml")
        if body and "workflow_execution:" in body:
            return yaml.safe_load(body)
    pytest.fail("Ting config.yaml was not rendered")
    raise AssertionError("Ting config.yaml was not rendered")


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
        generic_observer_adapters = {
            entry["condition_type"]: entry["adapter"] for entry in developer["waitObservers"]
        }
        delivery_observer_adapters = {
            entry["condition_type"]: entry["adapter"]
            for entry in developer["delivery"]["waitObservers"]
        }
        assert generic_observer_adapters["timer"].endswith("TimerWaitObserver")
        assert "forge.checks" not in generic_observer_adapters
        assert "forge.merge" not in generic_observer_adapters
        assert delivery_observer_adapters["forge.checks"].endswith("ForgeChecksWaitObserver")
        assert delivery_observer_adapters["forge.merge"].endswith("ForgeMergeWaitObserver")
        assert developer["admissionRoles"] == ["volundr:developer"]
        assert developer["delivery"]["enabled"] is False
        assert "wait_repository_adapter:" in template_yaml
        assert ".Values.workflowExecution.waitRepositoryAdapter" in template_yaml
        assert "wait_repository_kwargs:" in template_yaml
        assert "wait_observers:" in template_yaml
        assert ".Values.workflowExecution.waitObservers" in template_yaml
        assert ".Values.workflowExecution.delivery.waitObservers" in template_yaml
        assert "admission_roles:" in template_yaml

    def test_default_values_render_a_config_ting_accepts(self, tmp_path):
        """A default install must start: both packs are off and the config loads."""
        config = _config_from_rendered(_render_ting_chart(tmp_path, {}))

        settings = Settings(**config)

        assert settings.workflow_execution.enabled is False
        assert settings.workflow_execution.delivery.enabled is False

    def test_ci_values_render_a_config_ting_accepts(self, tmp_path):
        """The values the Helm smoke test installs with must load too."""
        ci_values = yaml.safe_load((CHART_DIR / "ci-values.yaml").read_text(encoding="utf-8"))

        Settings(**_config_from_rendered(_render_ting_chart(tmp_path, ci_values)))

    def test_delivery_is_enabled_explicitly_on_top_of_the_generic_blocks(self, tmp_path):
        rendered = _render_ting_chart(
            tmp_path, {"workflowExecution": {"enabled": True, "delivery": {"enabled": True}}}
        )
        workflow_execution = _config_from_rendered(rendered)["workflow_execution"]

        assert workflow_execution["enabled"] is True
        assert workflow_execution["delivery"]["enabled"] is True
        condition_types = {
            entry["condition_type"] for entry in workflow_execution["wait_observers"]
        }
        assert condition_types == {"timer", "forge.checks", "forge.merge"}
        assert workflow_execution["delivery"]["evidence_policy_id"] == "developer-workstream"

    def test_delivery_disabled_renders_generic_execution_with_no_forge_observers(self, tmp_path):
        """A deployment can run only the generic fan-out/join/wait pack.

        With workflowExecution.enabled on and delivery.enabled off, the
        rendered config keeps only the generic wait_observers list (timer)
        and emits no forge.* observer — whatever Forge/review values a chart
        still carries under delivery.* are inert once delivery.enabled is
        false, since ting.main only reads them when it is true.
        """
        rendered = _render_ting_chart(
            tmp_path,
            {"workflowExecution": {"enabled": True, "delivery": {"enabled": False}}},
        )
        workflow_execution = _config_from_rendered(rendered)["workflow_execution"]

        assert workflow_execution["enabled"] is True
        assert workflow_execution["delivery"]["enabled"] is False
        condition_types = {
            entry["condition_type"] for entry in workflow_execution["wait_observers"]
        }
        assert condition_types == {"timer"}


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
