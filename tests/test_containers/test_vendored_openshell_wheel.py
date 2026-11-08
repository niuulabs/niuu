"""The OpenShell SDK is installed from a wheel committed to the repo.

GitHub's release-download servers time out from CI runners, and a single
timeout fails every ``uv sync``. These checks keep the vendored wheel, the
extra's URL (which the built niuu wheel still advertises to pip), the path
source uv follows and the lockfile in step, and keep every image that syncs
from the lockfile copying the wheel first.
"""

import hashlib
import tomllib
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WHEEL_DIR = "vendor/wheels"
SYNCING_DOCKERFILES = (
    "containers/niuu/Dockerfile",
    "containers/openshell/Dockerfile",
    "containers/skuld/Dockerfile",
)


def _pyproject() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())


def _lock_package(name: str) -> dict:
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text())
    (package,) = [p for p in lock["package"] if p["name"] == name]
    return package


def _extra_requirement() -> str:
    (requirement,) = _pyproject()["project"]["optional-dependencies"]["openshell"]
    return requirement


def _extra_url_and_hash() -> tuple[str, str]:
    """The URL and sha256 fragment of the ``openshell`` extra's direct reference."""
    url = _extra_requirement().split(" @ ", 1)[1].split(" ;", 1)[0]
    parsed = urlparse(url)
    assert parsed.fragment.startswith("sha256=")
    return parsed.path, parsed.fragment.removeprefix("sha256=")


def _source_wheel() -> Path:
    source = _pyproject()["tool"]["uv"]["sources"]["openshell"]
    return REPO_ROOT / source["path"]


def test_uv_source_is_a_committed_wheel() -> None:
    wheel = _source_wheel()

    assert wheel.parent == REPO_ROOT / WHEEL_DIR
    assert wheel.is_file()


def test_vendored_wheel_matches_the_extra_pin() -> None:
    """The wheel uv installs is byte-for-byte the one the extra's URL names."""
    url_path, pinned_hash = _extra_url_and_hash()
    wheel = _source_wheel()

    assert wheel.name == unquote(url_path.rsplit("/", 1)[1])
    assert hashlib.sha256(wheel.read_bytes()).hexdigest() == pinned_hash


def test_lock_resolves_openshell_from_the_vendored_wheel() -> None:
    package = _lock_package("openshell")
    wheel = _source_wheel()

    assert package["source"] == {"path": wheel.relative_to(REPO_ROOT).as_posix()}
    assert [(w["filename"], w["hash"]) for w in package["wheels"]] == [
        (wheel.name, f"sha256:{hashlib.sha256(wheel.read_bytes()).hexdigest()}")
    ]
    assert "url" not in package["source"]
    assert all("url" not in w for w in package["wheels"])


def test_lock_names_no_github_release_download() -> None:
    assert "github.com/jve/OpenShell" not in (REPO_ROOT / "uv.lock").read_text()


@pytest.mark.parametrize("dockerfile", SYNCING_DOCKERFILES)
def test_images_copy_the_wheel_beside_the_lockfile(dockerfile: str) -> None:
    """The dependency layer needs the wheel in place before ``uv sync`` runs."""
    lines = (REPO_ROOT / dockerfile).read_text().splitlines()
    copy_lockfile = next(
        i for i, line in enumerate(lines) if line.startswith("COPY pyproject.toml uv.lock")
    )
    copy_wheel = next(
        i for i, line in enumerate(lines) if line == f"COPY {WHEEL_DIR}/ /app/{WHEEL_DIR}/"
    )
    first_sync = next(i for i, line in enumerate(lines) if line.startswith("RUN uv sync"))

    assert copy_lockfile < copy_wheel < first_sync


def test_build_context_keeps_the_wheel() -> None:
    ignored = {
        line.strip().rstrip("/")
        for line in (REPO_ROOT / ".dockerignore").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }

    assert not ignored & {"vendor", "vendor/wheels", "**/*.whl", "*.whl", "**/wheels", "wheels"}
