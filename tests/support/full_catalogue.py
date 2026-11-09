"""The archived bundled catalogue as test fixtures.

Most workflow engine, board and API tests were written against real bundled
workflows (Ticket Planning Flow, Research Campaign, Developer Delivery, ...). Those
now live in ``archive/`` and are not shipped, but their graphs are still the best
fixtures for those tests. ``load_full_catalogue`` loads the shipped workflows and the
archived ones together, with the archived personas, exactly as the package used to
load them.

The archived personas and workflows were released while a persona could carry
``allowed_tools`` / ``forbidden_tools``, and the archive must stay as released, so the
workflows pin personas that still list tools. The persona model no longer has those fields
and a persona document refuses them (``personas.domain.persona_document``), so this module
serves the personas as released: ``full_catalogue_personas`` adds the released tool lists back
to the portable documents it returns (their digests then match the archived workflows' pins),
and ``archived_personas_readable`` lets the documents it returns be read back in.

This is test support only. Nothing that ships may load the archive.
"""

from __future__ import annotations

import atexit
import copy
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from unittest.mock import patch

import yaml

from personas.adapters.loader import FilesystemPersonaAdapter
from personas.domain import persona_document
from personas.domain.persona_document import (
    PortablePersonaDefinition,
    persona_revision_for_definition,
)
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


#: The tool lists the shipped personas had when the archived workflows pinned them. The
#: shipped files no longer carry them.
RELEASED_BUILTIN_TOOL_LISTS: dict[str, dict[str, list[str]]] = {
    "coder": {
        "allowed_tools": [
            "mimir:search",
            "mimir:read",
            "mimir:write",
            "file_read",
            "file_write",
            "git",
            "shell",
            "web_fetch",
            "web_search",
            "plan",
            "tracker",
        ]
    },
    "reviewer": {
        "allowed_tools": [
            "mimir:read",
            "mimir:write",
            "file_read",
            "file_write",
            "git",
            "shell",
            "web_fetch",
            "web_search",
            "tracker",
        ]
    },
    "security-auditor": {
        "allowed_tools": ["file_read", "git_read", "web_fetch", "web_search"],
        "forbidden_tools": ["shell"],
    },
}
_TOOL_LISTS = ("allowed_tools", "forbidden_tools")


class _ReleasedPersonas(FilesystemPersonaAdapter):
    """The archived personas, and the shipped ones as the archived workflows pinned them."""

    def _archived_tool_lists(self, persona_id: str) -> dict[str, list[str]]:
        path = ARCHIVE / "personas" / f"{persona_id}.yaml"
        if not path.is_file():
            return {}
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return {key: list(raw[key]) for key in _TOOL_LISTS if raw.get(key)}

    def _released(
        self, document: PortablePersonaDefinition, tool_lists: dict[str, list[str]]
    ) -> PortablePersonaDefinition:
        if not tool_lists:
            return document
        definition = {**document.definition, **tool_lists}
        return PortablePersonaDefinition(
            id=document.id,
            revision=persona_revision_for_definition(definition),
            definition=definition,
        )

    def load_current_portable(self, persona_id: str) -> PortablePersonaDefinition | None:
        document = super().load_current_portable(persona_id)
        if document is None:
            return None
        return self._released(document, self._archived_tool_lists(persona_id))

    def load_portable(self, persona_id: str, revision: str) -> PortablePersonaDefinition | None:
        current = self.load_current_portable(persona_id)
        if current is not None and current.revision == revision:
            return current
        shipped = super().load_current_portable(persona_id)
        if shipped is None:
            return None
        released = self._released(shipped, RELEASED_BUILTIN_TOOL_LISTS.get(persona_id, {}))
        return released if released.revision == revision else None


def full_catalogue_personas() -> FilesystemPersonaAdapter:
    return _ReleasedPersonas(
        persona_dirs=[str(ARCHIVE / "personas")],
        include_builtin=True,
    )


@contextmanager
def archived_personas_readable() -> Iterator[None]:
    """Let a persona document carry the tool lists the archived personas were released with."""
    removed = persona_document.REMOVED_TOOL_RESTRICTION_FIELDS
    with (
        patch.object(persona_document, "REMOVED_TOOL_RESTRICTION_FIELDS", ()),
        patch.object(
            persona_document,
            "_PORTABLE_DEFINITION_FIELDS",
            persona_document._PORTABLE_DEFINITION_FIELDS | frozenset(removed),
        ),
    ):
        yield


@lru_cache(maxsize=1)
def _loaded() -> tuple[WorkflowDefinition, ...]:
    with archived_personas_readable():
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
        with archived_personas_readable():
            return load_system_workflows(
                path, persona_source=full_catalogue_personas(), allow_archived=True
            )
    # Tests edit the graphs they get, so each caller gets its own copy.
    return copy.deepcopy(list(_loaded()))
