"""CI guard: a bundled workflow's content must not change without a version bump.

`seed_system_workflows` (src/ting/system_workflows.py) raises at startup if a
bundled workflow's `id@version` is reused for different content, and
`migrate_workflow_catalog` (src/ting/domain/services/workflow_migration.py)
reports it as divergent. Both symptoms have already shipped once, when PR
#1012 restructured `research-campaign.yaml`'s graph without bumping its
`version`. This lock records every released bundled `id@version`'s document
revision (the same content hash `workflow_document_revision` computes at
seed/migration time) so that regression is caught here in CI instead of at
an operator's startup or migration dry run.

Lock entries are append-only: bumping a workflow's `version` and changing its
content is a new entry, never an edit to an existing one.
"""

from __future__ import annotations

import json
from pathlib import Path

from ting.system_workflows import load_system_workflows

LOCK_PATH = Path(__file__).parent / "data" / "bundled_workflow_versions.lock.json"


def _current_lock() -> dict[str, str]:
    """id@version -> document revision for every currently bundled workflow.

    Uses the `document_revision` `load_bundled_workflow` already computed
    with `workflow_document_revision` at load time — the exact value
    `seed_system_workflows` compares against a database row's own recorded
    revision.
    """
    return {
        f"{workflow.id}@{workflow.version}": workflow.document_revision
        for workflow in load_system_workflows()
    }


def _load_lock() -> dict[str, str]:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


def test_lock_file_is_valid_json() -> None:
    assert LOCK_PATH.is_file(), f"Missing bundled workflow version lock at {LOCK_PATH}"
    locked = _load_lock()
    assert locked, "Bundled workflow version lock must not be empty"


def test_bundled_workflow_content_matches_its_locked_version() -> None:
    """A released id@version's content must never change; bump the version instead."""
    locked = _load_lock()
    current = _current_lock()

    changed = sorted(key for key in locked if key in current and locked[key] != current[key])
    assert not changed, (
        "Bundled workflow content changed for an already-released version: "
        f"{changed}. Bump `version:` in the workflow YAML instead of editing "
        f"released content, then append the new id@version to {LOCK_PATH.name} "
        "(existing entries are append-only)."
    )


def test_every_locked_version_still_loads() -> None:
    """Released entries are append-only: a locked bundled workflow must not vanish."""
    locked = _load_lock()
    current = _current_lock()

    missing = sorted(set(locked) - set(current))
    assert not missing, f"Locked bundled workflow version(s) no longer load: {missing}"


def test_every_bundled_workflow_version_is_locked() -> None:
    """A new bundled workflow or a new version of one must be added to the lock."""
    locked = _load_lock()
    current = _current_lock()

    unlocked = sorted(set(current) - set(locked))
    assert not unlocked, (
        f"Bundled workflow version(s) missing from {LOCK_PATH.name}: {unlocked}. "
        "Add their id@version -> document_revision entries (append-only; see "
        "this file's module docstring)."
    )
