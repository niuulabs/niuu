#!/usr/bin/env python3
"""Report module size and dependency-direction signals for review.

This command is intentionally informational: architecture tests enforce forbidden
edges, while this report makes large or highly coupled modules visible to reviewers.
"""

from __future__ import annotations

import argparse
import ast
import json
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGETS = (
    "src/skuld/broker.py",
    "src/ravn/cli/commands.py",
    "src/ravn/api/valkyries.py",
    "src/volundr/main.py",
    "src/niuu/app.py",
    "src/niuu/session_proxy.py",
    "src/volundr/composition_builders.py",
    "web-next/packages/plugin-volundr/src/ui/LaunchWizard.tsx",
    "web-next/packages/plugin-volundr/src/ui/useLaunchWizard.ts",
    "web-next/packages/plugin-ting/src/ui/ResearchCampaignPage.tsx",
)


@dataclass(frozen=True)
class ModuleSignal:
    path: str
    lines: int
    python_imports: int | None
    internal_imports: int | None


def _python_import_counts(path: Path) -> tuple[int, int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    package_roots = {
        "audit",
        "credentials",
        "features",
        "identity",
        "niuu",
        "ravn",
        "skuld",
        "ting",
        "tracker",
        "volundr",
    }
    internal = sum(name.split(".", 1)[0] in package_roots for name in imports)
    return len(imports), internal


def analyze(relative_path: str) -> ModuleSignal:
    path = ROOT / relative_path
    text = path.read_text(encoding="utf-8")
    imports: int | None = None
    internal: int | None = None
    if path.suffix == ".py":
        imports, internal = _python_import_counts(path)
    return ModuleSignal(relative_path, len(text.splitlines()), imports, internal)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", default=DEFAULT_TARGETS)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    signals = sorted(
        (analyze(path) for path in args.paths),
        key=lambda item: item.lines,
        reverse=True,
    )
    if args.as_json:
        print(json.dumps([asdict(signal) for signal in signals], indent=2))
        return 0
    print("lines  imports  internal  module")
    for signal in signals:
        imports = "-" if signal.python_imports is None else str(signal.python_imports)
        internal = "-" if signal.internal_imports is None else str(signal.internal_imports)
        print(f"{signal.lines:5}  {imports:>7}  {internal:>8}  {signal.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
