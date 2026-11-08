"""Re-pin the bundled workflows to the built-in personas and to each other.

A bundled workflow pins every persona it uses by ``id@revision`` and content digest, and
every child workflow by the child's document revision. Changing a built-in persona changes
its digest, which changes the workflow that pins it, which changes every workflow that
pins that one. This script recomputes all of them and rewrites only the pin lines::

    uv run python scripts/repin_bundled_workflows.py          # rewrite
    uv run python scripts/repin_bundled_workflows.py --check  # exit 1 when a pin is stale

Run it after editing anything under ``src/personas/builtin`` or a bundled workflow.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from personas.adapters.loader import FilesystemPersonaAdapter  # noqa: E402
from ting.domain.workflow_document import (  # noqa: E402
    load_workflow_document,
    workflow_document_revision,
)

WORKFLOWS_DIR = REPO_ROOT / "src" / "ting" / "workflows"
BUNDLE_ROOT = WORKFLOWS_DIR.parent


def _persona_replacements(text: str, source: FilesystemPersonaAdapter) -> dict[str, str]:
    """Old pin text to new pin text for the personas a workflow document pins."""
    replacements: dict[str, str] = {}
    for dependency in load_workflow_document(text).persona_dependencies.values():
        portable = source.load_current_portable(dependency.id)
        if portable is None:
            raise SystemExit(f"persona {dependency.id!r} is not a built-in persona")
        replacements[dependency.digest] = portable.digest
        replacements[dependency.revision] = portable.revision
    return replacements


def _workflow_replacements(text: str) -> dict[str, str]:
    """Old pin text to new pin text for the child workflows a document pins."""
    replacements: dict[str, str] = {}
    for dependency in load_workflow_document(text).workflow_dependencies.values():
        if dependency.path is None:
            raise SystemExit(f"workflow dependency {dependency.id} has no bundle path")
        child = load_workflow_document((BUNDLE_ROOT / dependency.path).read_text(encoding="utf-8"))
        current = workflow_document_revision(child)
        replacements[dependency.digest] = current
        replacements[dependency.revision] = current
    return replacements


def _apply(text: str, replacements: dict[str, str]) -> str:
    for old, new in replacements.items():
        if old != new:
            text = text.replace(old, new)
    return text


def repin(*, check: bool) -> int:
    source = FilesystemPersonaAdapter(persona_dirs=[], include_builtin=True)
    paths = sorted(WORKFLOWS_DIR.glob("*.yaml"))
    changed: set[Path] = set()
    # A child's revision depends on its own pins, so repeat until nothing moves.
    for _ in range(len(paths) + 1):
        moved = False
        for path in paths:
            before = path.read_text(encoding="utf-8")
            after = _apply(before, _persona_replacements(before, source))
            after = _apply(after, _workflow_replacements(after))
            if after == before:
                continue
            moved = True
            changed.add(path)
            if not check:
                path.write_text(after, encoding="utf-8")
        if not moved or check:
            break
    else:
        raise SystemExit("workflow pins did not settle; the dependency graph has a cycle")
    for path in sorted(changed):
        print(("stale: " if check else "repinned: ") + str(path.relative_to(REPO_ROOT)))
    return 1 if (check and changed) else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="report stale pins, change nothing")
    raise SystemExit(repin(check=parser.parse_args().check))


if __name__ == "__main__":
    main()
