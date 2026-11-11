"""Charts must carry ``database.name`` to the per-service database variable.

``niuu.service_databases.apply_service_database_settings`` replaces
``DATABASE__NAME`` with ``database_name_for_service(<service>)``, which reads
``NIUU_DATABASE_NAME_<SERVICE>`` and otherwise falls back to the service's own
default name (``guild``, ``observatory``, ``niuu_shared``). A chart that only
sets ``DATABASE__NAME`` therefore ignores ``database.name`` and connects to a
database that may not exist, as Guild did in the umbrella chart where it
shares Volundr's database.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from niuu.service_databases import service_database_env_var
from tests.test_charts.test_niuu_chart import _render_niuu_chart

CHARTS_DIR = Path(__file__).parent.parent.parent / "charts"

# chart directory -> (service name passed to apply_service_database_settings
# by the app the chart deploys, chart default database name)
SERVICE_CHARTS = {
    "guild": ("guild", "guild"),
    "observatory": ("observatory", "observatory"),
    "niuu-shared": ("niuu-shared", "niuu_shared"),
}

OVERRIDE_NAME = "custom_db"
INIT_DATABASE_ENV = "DATABASE_NAME"


def _helm_template(chart_dir: Path, *extra_args: str) -> str:
    helm = shutil.which("helm")
    if not helm:
        pytest.skip("helm is not installed")
    result = subprocess.run(
        [helm, "template", "test", str(chart_dir), *extra_args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"helm template failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return result.stdout


def _deployment_pods(rendered: str, name_suffix: str) -> dict:
    for document in yaml.safe_load_all(rendered):
        if not isinstance(document, dict) or document.get("kind") != "Deployment":
            continue
        if document["metadata"]["name"].endswith(name_suffix):
            return document["spec"]["template"]["spec"]
    raise AssertionError(f"deployment ending in {name_suffix!r} not found")


def _env(container: dict) -> dict[str, str]:
    return {item["name"]: item.get("value") for item in container.get("env", [])}


def _service_env(pod: dict) -> dict[str, str]:
    return _env(next(c for c in pod["containers"] if c["name"] != "migrate"))


def _migrate_env(pod: dict) -> dict[str, str]:
    return _env(next(c for c in pod["initContainers"] if "migrate" in c["name"]))


@pytest.mark.parametrize("chart", sorted(SERVICE_CHARTS))
def test_default_database_name_reaches_service_variable(chart: str) -> None:
    service, default_name = SERVICE_CHARTS[chart]
    pod = _deployment_pods(_helm_template(CHARTS_DIR / chart), chart)
    env = _service_env(pod)

    assert env["DATABASE__NAME"] == default_name
    assert env[service_database_env_var(service)] == default_name


@pytest.mark.parametrize("chart", sorted(SERVICE_CHARTS))
def test_overridden_database_name_reaches_service_variable(chart: str) -> None:
    service, _ = SERVICE_CHARTS[chart]
    rendered = _helm_template(CHARTS_DIR / chart, "--set", f"database.name={OVERRIDE_NAME}")
    pod = _deployment_pods(rendered, chart)
    env = _service_env(pod)

    assert env["DATABASE__NAME"] == OVERRIDE_NAME
    assert env[service_database_env_var(service)] == OVERRIDE_NAME
    assert _migrate_env(pod)[INIT_DATABASE_ENV] == OVERRIDE_NAME


@pytest.mark.parametrize("chart", ["guild", "observatory"])
def test_umbrella_shared_database_reaches_service_variable(chart: str) -> None:
    """The umbrella points Guild and Observatory at Volundr's database."""
    service, _ = SERVICE_CHARTS[chart]
    pod = _deployment_pods(_render_niuu_chart(), f"-{chart}")
    env = _service_env(pod)

    assert env["DATABASE__NAME"] == "volundr"
    assert env[service_database_env_var(service)] == "volundr"
    assert _migrate_env(pod)[INIT_DATABASE_ENV] == "volundr"


def test_umbrella_override_reaches_service_variable() -> None:
    rendered = _render_niuu_chart("--set", f"guild.database.name={OVERRIDE_NAME}")
    pod = _deployment_pods(rendered, "-guild")

    assert _service_env(pod)[service_database_env_var("guild")] == OVERRIDE_NAME
