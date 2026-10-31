"""Volundr and Ting migrations have exactly one source: ``migrations/``.

Every consumer reads that directory instead of a copy of it:

- the volundr and ting charts reach it through a ``migrations`` symlink and
  render it with ``.Files.Glob`` (``helm package`` follows the link, so the
  published chart carries the files);
- the wheel force-includes it as ``cli/migrations``, which is where an
  installed service (every container image) finds it at startup;
- the Nuitka binary maps it to the same ``cli/migrations`` layout.

These tests fail if any of those paths starts reading something else.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"

CHARTS = {
    "volundr": MIGRATIONS,
    "ting": MIGRATIONS / "ting",
}

# Images that build and install the project wheel; the wheel cannot build
# without migrations/ in the image.
WHEEL_IMAGES = ("agent", "niuu", "openshell", "skuld")


def _source_files(directory: Path) -> dict[str, str]:
    paths = [*directory.glob("*.sql"), *directory.glob("*.json")]
    # A YAML literal block clips trailing newlines to exactly one.
    return {path.name: path.read_text().rstrip("\n") + "\n" for path in paths}


@pytest.mark.parametrize("chart", sorted(CHARTS))
def test_chart_migrations_dir_is_a_link_to_the_source(chart: str) -> None:
    link = ROOT / "charts" / chart / "migrations"
    assert link.is_symlink(), f"charts/{chart}/migrations must be a symlink, not a copy"
    assert link.resolve() == CHARTS[chart].resolve()


@pytest.mark.parametrize("chart", sorted(CHARTS))
def test_chart_template_holds_no_migration_sql(chart: str) -> None:
    template = (ROOT / "charts" / chart / "templates" / "migrations-configmap.yaml").read_text()
    assert ".Files.Glob" in template
    assert not re.search(r"^\s+\d{6}_\S+\.sql: \|", template, re.MULTILINE)


@pytest.mark.parametrize("chart", sorted(CHARTS))
def test_rendered_chart_migrations_equal_the_source(chart: str) -> None:
    helm = shutil.which("helm")
    if not helm:
        pytest.skip("helm is not installed")
    rendered = subprocess.run(
        [
            helm,
            "template",
            "test",
            str(ROOT / "charts" / chart),
            "--set",
            "migrations.enabled=true",
            "--show-only",
            "templates/migrations-configmap.yaml",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    data = yaml.safe_load(rendered)["data"]

    assert data == _source_files(CHARTS[chart])


def test_volundr_chart_does_not_package_ting_migrations_twice() -> None:
    ignored = (ROOT / "charts" / "volundr" / ".helmignore").read_text().splitlines()
    assert "migrations/ting/" in ignored


def test_wheel_force_includes_the_source_as_cli_migrations() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    force_include = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert force_include["migrations"] == "cli/migrations"


def test_no_committed_copy_under_the_cli_package() -> None:
    copies = [
        path.relative_to(ROOT)
        for path in (ROOT / "src" / "cli" / "migrations").rglob("*")
        if path.suffix in {".sql", ".json"}
    ]
    assert copies == []


def test_nuitka_binary_maps_the_source_to_the_wheel_layout() -> None:
    from cli.build import DATA_FILE_MAPPINGS

    mapped = {str(source.relative_to(ROOT)): dest for source, dest in DATA_FILE_MAPPINGS}
    assert mapped["migrations/*.sql"] == "cli/migrations/"
    assert mapped["migrations/lineage-aliases.json"] == "cli/migrations/"
    assert mapped["migrations/ting/*.sql"] == "cli/migrations/ting/"


@pytest.mark.parametrize("image", WHEEL_IMAGES)
def test_wheel_images_copy_the_source_into_the_build(image: str) -> None:
    dockerfile = (ROOT / "containers" / image / "Dockerfile").read_text()
    assert "COPY migrations/ /app/migrations/" in dockerfile


def test_docker_context_keeps_the_source() -> None:
    ignored = (ROOT / ".dockerignore").read_text().splitlines()
    assert "migrations" not in ignored
