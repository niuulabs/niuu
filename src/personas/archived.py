"""Built-in personas that were archived out of the shipped catalogue.

An archived persona is kept in the repository under ``archive/personas`` (see
``archive/README.md``) but is neither packaged, seeded nor served. Anything that is
kept and still names one is a configuration error that stops the service at
startup, with the remedy in the message; it is never resolved to "not found" and
carried on without.

The list is ``archived-personas.txt`` next to this module, not a scan of the archive,
because the archive is not shipped: the wheel and the container images have no
``archive/`` to read. ``tests/test_catalog_archive.py`` keeps it equal to the files in
``archive/personas``.
"""

from __future__ import annotations

from pathlib import Path

ARCHIVED_PERSONAS_FILE = Path(__file__).parent / "archived-personas.txt"


def _read_archived_personas() -> frozenset[str]:
    # A data file, not a literal: it is a list of persona names, which engine code must not carry.
    lines = ARCHIVED_PERSONAS_FILE.read_text(encoding="utf-8").splitlines()
    return frozenset(line.strip() for line in lines if line.strip() and not line.startswith("#"))


ARCHIVED_BUILTIN_PERSONAS: frozenset[str] = _read_archived_personas()


class ArchivedCatalogError(ValueError):
    """A kept component names a built-in persona or workflow that was archived."""


def archived_persona_message(name: str, *, where: str) -> str:
    """Say which archived persona *where* names and how to get it back."""
    return (
        f"{where} names the built-in persona {name!r}, which was archived and is no longer "
        f"shipped. Name a persona that ships, define your own {name!r} persona, or restore "
        f"it with `git mv archive/personas/{name}.yaml src/personas/builtin/` and remove it "
        "from src/personas/archived-personas.txt (see archive/README.md)."
    )


def reject_archived_persona(name: str, *, where: str) -> str:
    """Return *name* unchanged, or raise when it is an archived built-in persona."""
    if name in ARCHIVED_BUILTIN_PERSONAS:
        raise ArchivedCatalogError(archived_persona_message(name, where=where))
    return name
