"""Guards for CLI runtime container tool versions."""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CODEX_VERSION = "0.139.0"


def _load_json(path: str) -> dict:
    return json.loads((REPO_ROOT / path).read_text())


def test_skuld_and_devrunner_pin_same_codex_version() -> None:
    """Skuld broker and devrunner shell must expose the same Codex CLI."""
    package_paths = [
        "containers/skuld/npm-tools/package.json",
        "containers/devrunner/npm-tools/package.json",
    ]

    for package_path in package_paths:
        package = _load_json(package_path)
        assert package["dependencies"]["@openai/codex"] == CODEX_VERSION


def test_devrunner_lockfile_resolves_codex_version() -> None:
    """The checked-in npm lockfile must match the devrunner Codex pin."""
    package_lock = _load_json("containers/devrunner/npm-tools/package-lock.json")
    codex_package = package_lock["packages"]["node_modules/@openai/codex"]

    assert codex_package["version"] == CODEX_VERSION
    assert f"codex-{CODEX_VERSION}.tgz" in codex_package["resolved"]


def test_cli_runtime_images_install_vim() -> None:
    """Interactive session containers should include a real vim binary."""
    dockerfile_paths = [
        "containers/skuld/Dockerfile",
        "containers/devrunner/Dockerfile",
    ]

    for dockerfile_path in dockerfile_paths:
        dockerfile = (REPO_ROOT / dockerfile_path).read_text()
        assert "    vim \\\n" in dockerfile
