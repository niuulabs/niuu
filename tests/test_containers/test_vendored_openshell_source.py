"""Keep the locally built SDK, release pin and container build inputs in step."""

import tomllib
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SDK_DIR = "vendor/openshell"
SYNCING_DOCKERFILES = (
    "containers/niuu/Dockerfile",
    "containers/openshell/Dockerfile",
    "containers/skuld/Dockerfile",
)


def _toml(path: str) -> dict:
    return tomllib.loads((REPO_ROOT / path).read_text())


def _lock_package(name: str) -> dict:
    (package,) = [p for p in _toml("uv.lock")["package"] if p["name"] == name]
    return package


def test_vendored_source_matches_the_extra_version() -> None:
    (requirement,) = _toml("pyproject.toml")["project"]["optional-dependencies"]["openshell"]
    url = requirement.split(" @ ", 1)[1].split(" ;", 1)[0]
    sdk = _toml(f"{SDK_DIR}/pyproject.toml")["project"]

    assert unquote(urlparse(url).path.rsplit("/", 1)[1]) == (
        f"openshell-{sdk['version']}-py3-none-any.whl"
    )
    assert (REPO_ROOT / SDK_DIR / "openshell/_proto/openshell_pb2.py").is_file()
    assert (REPO_ROOT / SDK_DIR / "LICENSE").is_file()


def test_lock_resolves_openshell_from_source_with_matching_dependencies() -> None:
    source = _toml("pyproject.toml")["tool"]["uv"]["sources"]["openshell"]
    sdk = _toml(f"{SDK_DIR}/pyproject.toml")["project"]
    package = _lock_package("openshell")

    assert source == {"path": SDK_DIR}
    assert package["source"] == {"directory": SDK_DIR}
    assert package["version"] == sdk["version"]
    assert sorted(sdk["dependencies"]) == sorted(
        f"{dep['name']}{dep['specifier']}" for dep in package["metadata"]["requires-dist"]
    )
    assert "wheels" not in package


def test_lock_names_no_github_release_download() -> None:
    assert "github.com/jve/OpenShell" not in (REPO_ROOT / "uv.lock").read_text()


@pytest.mark.parametrize("dockerfile", SYNCING_DOCKERFILES)
def test_images_copy_the_source_beside_the_lockfile(dockerfile: str) -> None:
    """Even a dependency-only sync needs local directory sources present."""
    lines = (REPO_ROOT / dockerfile).read_text().splitlines()
    copy_lockfile = next(
        i for i, line in enumerate(lines) if line.startswith("COPY pyproject.toml uv.lock")
    )
    copy_source = next(
        i for i, line in enumerate(lines) if line == f"COPY {SDK_DIR}/ /app/{SDK_DIR}/"
    )
    first_sync = next(i for i, line in enumerate(lines) if line.startswith("RUN uv sync"))

    assert copy_lockfile < copy_source < first_sync


def test_build_context_keeps_the_source() -> None:
    ignored = {
        line.strip().rstrip("/")
        for line in (REPO_ROOT / ".dockerignore").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }

    assert not ignored & {"vendor", SDK_DIR, f"{SDK_DIR}/openshell"}
