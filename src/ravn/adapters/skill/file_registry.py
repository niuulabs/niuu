"""File-based skill registry.

Discovers user-defined and built-in skills from Markdown files on the filesystem.

Discovery order (highest priority first):
1. Project-local:  ``.ravn/skills/`` in the current working directory.
2. User-global:    ``~/.ravn/skills/``.
3. Built-in:       skills shipped with the ``ravn`` package (``src/ravn/skills/``).

When the same skill name appears in multiple locations, the highest-priority
source wins.

Skill file format
-----------------
A skill is a ``.md`` file.  The name is taken from the first line if it
matches the ``# skill: <name>`` pattern; otherwise the filename stem is used.
The description is the first non-empty, non-header line of content.

Example::

    # skill: fix-tests

    Run the test suite, identify all failing tests, and fix them one by one.

Configuration
-------------
Pass *skill_dirs* to override the default search paths entirely, or leave it
as ``None`` to use the default three-layer discovery.  Set
*include_builtin=False* to suppress the built-in skills.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ravn.domain.models import Episode, Skill
from ravn.ports.skill import SkillPort

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SKILL_HEADER_RE = re.compile(r"^#\s*skill:\s*(.+)$", re.IGNORECASE)
_BUILTIN_SKILLS_DIR = Path(__file__).parent.parent.parent / "skills"


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_skill_file(path: Path) -> Skill | None:
    """Parse a single skill Markdown file and return a :class:`Skill`.

    Returns ``None`` if the file cannot be read or contains no usable content.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("file_skill_registry: cannot read %s: %s", path, exc)
        return None

    if not text.strip():
        return None

    lines = text.splitlines()
    name: str = path.stem  # fallback: filename without extension
    description: str = ""
    content_start = 0

    # Extract name from `# skill: <name>` header on the first non-empty line.
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        m = _SKILL_HEADER_RE.match(stripped)
        if m:
            name = m.group(1).strip()
            content_start = i + 1
        break

    # Extract description from the first non-empty line after the header.
    for line in lines[content_start:]:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            description = stripped
            break

    if not description:
        description = f"Skill: {name}"

    return Skill(
        skill_id=str(uuid4()),
        name=name,
        description=description,
        content=text,
        requires_tools=[],
        fallback_for_tools=[],
        source_episodes=[],
        created_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
        success_count=0,
    )


def _discover_from_dir(directory: Path) -> dict[str, Skill]:
    """Return a ``{name: Skill}`` mapping of skills found in *directory*.

    Silently returns an empty dict if the directory does not exist.
    """
    if not directory.is_dir():
        return {}

    skills: dict[str, Skill] = {}
    for md_file in sorted(directory.glob("*.md")):
        skill = _parse_skill_file(md_file)
        if skill is None:
            continue
        # Later entries overwrite earlier ones for the same name, so sort order
        # determines tie-breaking within a single directory (alphabetical).
        skills[skill.name.lower()] = skill

    return skills


def _filter_by_query(skills: list[Skill], query: str) -> list[Skill]:
    q = query.lower()
    return [
        s
        for s in skills
        if q in s.name.lower() or q in s.description.lower() or q in s.content.lower()
    ]


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class FileSkillRegistry(SkillPort):
    """Skill registry backed by Markdown files on the filesystem.

    Args:
        skill_dirs: Explicit list of directories to search (highest priority
            first).  When ``None``, uses the default three-layer discovery:
            ``.ravn/skills/`` → ``~/.ravn/skills/`` → built-in skills.
        include_builtin: Whether to include the skills shipped with the
            ``ravn`` package.  Controls whether built-in skills are appended
            regardless of how *skill_dirs* is configured.
        cwd: Working directory used to resolve ``.ravn/skills/``.  Defaults to
            the process working directory at construction time.
    """

    def __init__(
        self,
        *,
        skill_dirs: list[str] | None = None,
        write_dir: str | Path | None = None,
        include_builtin: bool = True,
        cwd: Path | None = None,
    ) -> None:
        self._include_builtin = include_builtin
        self._cwd = cwd or Path.cwd()
        self._write_dir = Path(write_dir).expanduser() if write_dir is not None else None

        if skill_dirs is not None:
            self._skill_dirs: list[Path] | None = [Path(d).expanduser() for d in skill_dirs]
        else:
            self._skill_dirs = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_dirs(self) -> list[Path]:
        """Return the ordered list of directories to search.

        When *skill_dirs* was supplied explicitly it forms the base list;
        otherwise the default two-layer (project-local → user-global) paths
        are used.  Built-in skills are appended last when *include_builtin*
        is True, regardless of how *skill_dirs* was set.
        """
        if self._skill_dirs is not None:
            dirs = list(self._skill_dirs)
        else:
            dirs = [
                self._cwd / ".ravn" / "skills",
                Path.home() / ".ravn" / "skills",
            ]
        if self._include_builtin:
            dirs.append(_BUILTIN_SKILLS_DIR)
        return dirs

    def _load_all_sync(self) -> list[Skill]:
        """Discover all skills from the filesystem (sync, for use with asyncio.to_thread)."""
        # Merge priority layers: later (lower-priority) dirs fill in gaps for
        # names not yet seen; earlier (higher-priority) dirs win conflicts.
        merged: dict[str, Skill] = {}

        for directory in reversed(self._resolve_dirs()):
            layer = _discover_from_dir(directory)
            merged.update(layer)

        return list(merged.values())

    # ------------------------------------------------------------------
    # SkillPort
    # ------------------------------------------------------------------

    async def record_episode(self, episode: Episode) -> Skill | None:
        """File-based skills are user-managed; episodes are not processed."""
        return None

    async def list_skills(self, query: str | None = None) -> list[Skill]:
        """List all discovered skills, optionally filtered by *query*."""
        skills = await asyncio.to_thread(self._load_all_sync)
        if query:
            skills = _filter_by_query(skills, query)
        return sorted(skills, key=lambda s: s.name.lower())

    async def record_skill(self, skill: Skill) -> None:
        """Persist a skill to the user-global skills directory.

        Creates ``~/.ravn/skills/<name>.md`` with the skill content.
        Silently skips if the content is empty.
        """
        if not skill.content.strip():
            return

        dest_dir = self._write_dir or Path.home() / ".ravn" / "skills"
        await asyncio.to_thread(self._write_skill_file, dest_dir, skill)

    def _write_skill_file(self, dest_dir: Path, skill: Skill) -> None:
        dest_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^\w\-]", "-", skill.name.lower())
        dest = dest_dir / f"{safe_name}.md"
        try:
            dest.write_text(skill.content, encoding="utf-8")
        except OSError as exc:
            logger.warning("file_skill_registry: cannot write skill %s: %s", dest, exc)

    async def get_skill(self, name: str) -> Skill | None:
        """Return the skill with the given *name*, or ``None`` if not found.

        Searches the filesystem in priority order.  The first match wins.
        """
        name_lower = name.lower()

        def _find_sync() -> Skill | None:
            for directory in self._resolve_dirs():
                layer = _discover_from_dir(directory)
                if name_lower in layer:
                    return layer[name_lower]
            return None

        return await asyncio.to_thread(_find_sync)
