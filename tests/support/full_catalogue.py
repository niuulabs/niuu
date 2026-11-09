"""The archived bundled catalogue as test fixtures.

Most workflow engine, board and API tests were written against real bundled
workflows (Ticket Planning Flow, Research Campaign, Developer Delivery, ...). Those
now live in ``archive/`` and are not shipped, but their graphs are still the best
fixtures for those tests. ``load_full_catalogue`` loads the shipped workflows and the
archived ones together, with the archived personas, exactly as the package used to
load them.

This is test support only. Nothing that ships may load the archive.
"""

from __future__ import annotations

import atexit
import copy
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path

from personas.adapters.loader import FilesystemPersonaAdapter
from ting.domain.models import WorkflowDefinition
from ting.system_workflows import BUNDLED_SYSTEM_WORKFLOWS_PATH, load_system_workflows

REPO_ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = REPO_ROOT / "archive"


@lru_cache(maxsize=1)
def full_catalogue_root() -> Path:
    """A bundle root whose ``workflows/`` holds the shipped and the archived documents."""
    root = Path(tempfile.mkdtemp(prefix="full-catalogue-"))
    atexit.register(shutil.rmtree, root, ignore_errors=True)
    workflows = root / "workflows"
    workflows.mkdir()
    for source in (BUNDLED_SYSTEM_WORKFLOWS_PATH, ARCHIVE / "workflows"):
        for path in source.glob("*.yaml"):
            shutil.copy(path, workflows / path.name)
    return workflows


def full_catalogue_personas() -> FilesystemPersonaAdapter:
    return FilesystemPersonaAdapter(
        persona_dirs=[str(ARCHIVE / "personas")],
        include_builtin=True,
    )


@lru_cache(maxsize=1)
def _loaded() -> tuple[WorkflowDefinition, ...]:
    return tuple(
        load_system_workflows(
            full_catalogue_root(),
            persona_source=full_catalogue_personas(),
            allow_archived=True,
        )
    )


def load_full_catalogue(path: Path | None = None) -> list[WorkflowDefinition]:
    """Every bundled workflow, shipped and archived.

    Takes the same optional path argument as ``load_system_workflows`` so a test can
    swap the import; any path other than the shipped one is passed through unchanged.
    """
    if path is not None and path != BUNDLED_SYSTEM_WORKFLOWS_PATH:
        return load_system_workflows(
            path, persona_source=full_catalogue_personas(), allow_archived=True
        )
    # Tests edit the graphs they get, so each caller gets its own copy.
    return copy.deepcopy(list(_loaded()))
