"""Dependency-direction tests for the shared collaboration capability."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_shared_collaboration_has_no_runtime_or_surface_dependency() -> None:
    forbidden_roots = {"ravn", "skuld", "fastapi"}
    for path in (ROOT / "src/niuu/collaboration").glob("*.py"):
        dependencies = {name.split(".", 1)[0] for name in _imports(path)}
        assert dependencies.isdisjoint(forbidden_roots), path


def test_semantic_projection_and_surface_adapter_do_not_cross_import() -> None:
    ravn_projection = ROOT / "src/ravn/adapters/collaboration/projection.py"
    skuld_surface = ROOT / "src/skuld/collaboration_adapter.py"

    assert all(not name.startswith("skuld") for name in _imports(ravn_projection))
    assert all(not name.startswith("ravn") for name in _imports(skuld_surface))
