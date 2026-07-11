"""Architecture regression tests for package import direction."""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"


def _external_package_imports(package: str) -> set[tuple[str, str]]:
    package_root = SRC_ROOT / package
    imports: set[tuple[str, str]] = set()

    for path in package_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative_path = path.relative_to(package_root).as_posix()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.partition(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots = {node.module.partition(".")[0]}
            else:
                continue

            for root in roots:
                if root != package:
                    imports.add((relative_path, root))

    return imports


def test_ting_and_volundr_do_not_import_each_other() -> None:
    ting_violations = {
        path for path, imported in _external_package_imports("ting") if imported == "volundr"
    }
    volundr_violations = {
        path for path, imported in _external_package_imports("volundr") if imported == "ting"
    }

    assert ting_violations == set()
    assert volundr_violations == set()


def test_niuu_does_not_import_feature_packages() -> None:
    actual = {
        path
        for path, imported in _external_package_imports("niuu")
        if imported in {"ting", "volundr"}
    }

    assert actual == set()


def test_domain_layers_do_not_read_process_environment() -> None:
    violations: set[str] = set()

    for domain_root in SRC_ROOT.glob("*/domain"):
        for path in domain_root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "os.environ" in source or "os.getenv" in source:
                violations.add(path.relative_to(SRC_ROOT).as_posix())

    assert violations == set()
