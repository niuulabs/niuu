"""Environment-aware skill management and telemetry."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from ravn.domain.models import Skill
from ravn.ports.skill import SkillPort

if TYPE_CHECKING:
    from ravn.domain.models import Episode

SKILL_SCOPES = frozenset({"private", "environment", "domain", "flock", "shared"})
SKILL_STATUSES = frozenset({"active", "archived", "stale"})


@dataclass
class SkillLifecycle:
    skill_id: str
    name: str
    scope: str = "private"
    environment_id: str = ""
    domain: str = ""
    status: str = "active"
    pinned: bool = False
    version: int = 1
    source: str = "manual"
    source_environment_id: str = ""
    source_valkyrie_id: str = ""
    action_safety_class: str = "read_only"
    created_at: str = ""
    updated_at: str = ""
    archived_at: str = ""
    run_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    consecutive_failures: int = 0
    last_used_at: str = ""


class SkillManagementRegistry:
    """Manage skill lifecycle metadata around an existing ``SkillPort``."""

    def __init__(
        self,
        skill_port: SkillPort,
        *,
        metadata_path: str | Path = "~/.ravn/skill_management.json",
    ) -> None:
        self._skill_port = skill_port
        self._metadata_path = Path(metadata_path).expanduser()
        self._metadata: dict[str, SkillLifecycle] = {}
        self._load()

    @property
    def skill_port(self) -> SkillPort:
        """The backing skill store shared by lifecycle-aware tool adapters."""
        return self._skill_port

    def status(self, name: str) -> str | None:
        """Return managed lifecycle status, or ``None`` for an unmanaged skill."""
        meta = self._metadata.get(self._key(name))
        return meta.status if meta is not None else None

    async def create(
        self,
        *,
        name: str,
        content: str,
        description: str = "",
        scope: str = "private",
        environment_id: str = "",
        domain: str = "",
        source: str = "manual",
        source_environment_id: str = "",
        source_valkyrie_id: str = "",
        action_safety_class: str = "read_only",
    ) -> Skill:
        self._validate_name_content(name, content)
        self._validate_scope(scope)
        existing = await self._skill_port.get_skill(name)
        if existing is not None and not self._is_archived(name):
            raise ValueError(f"Skill already exists: {name}")

        skill = Skill(
            skill_id=str(uuid4()),
            name=name,
            description=description or _description_from_content(name, content),
            content=_ensure_skill_header(name, content),
            requires_tools=_tools_from_content(content),
            fallback_for_tools=[],
            source_episodes=[],
            created_at=datetime.now(UTC),
            success_count=0,
        )
        await self._skill_port.record_skill(skill)
        self._metadata[self._key(name)] = SkillLifecycle(
            skill_id=skill.skill_id,
            name=skill.name,
            scope=scope,
            environment_id=environment_id,
            domain=domain,
            source=source,
            source_environment_id=source_environment_id,
            source_valkyrie_id=source_valkyrie_id,
            action_safety_class=action_safety_class,
            created_at=skill.created_at.isoformat(),
            updated_at=skill.created_at.isoformat(),
        )
        self._save()
        return skill

    async def update(
        self,
        *,
        name: str,
        content: str | None = None,
        description: str | None = None,
        source: str | None = None,
        source_environment_id: str | None = None,
        source_valkyrie_id: str | None = None,
    ) -> Skill:
        skill = await self._require_skill(name, include_archived=True)
        updated = Skill(
            skill_id=skill.skill_id,
            name=skill.name,
            description=description if description is not None else skill.description,
            content=(
                _ensure_skill_header(skill.name, content) if content is not None else skill.content
            ),
            requires_tools=(
                _tools_from_content(content) if content is not None else skill.requires_tools
            ),
            fallback_for_tools=skill.fallback_for_tools,
            source_episodes=skill.source_episodes,
            created_at=skill.created_at,
            success_count=skill.success_count,
        )
        await self._skill_port.record_skill(updated)
        meta = self._metadata_for(updated)
        meta.version += 1
        meta.status = "active"
        meta.archived_at = ""
        if source is not None:
            meta.source = source
        if source_environment_id is not None:
            meta.source_environment_id = source_environment_id
        if source_valkyrie_id is not None:
            meta.source_valkyrie_id = source_valkyrie_id
        meta.updated_at = datetime.now(UTC).isoformat()
        self._save()
        return updated

    async def archive(self, name: str) -> SkillLifecycle:
        await self._require_skill(name, include_archived=True)
        meta = self._metadata_for_name(name)
        meta.status = "archived"
        meta.archived_at = datetime.now(UTC).isoformat()
        meta.updated_at = meta.archived_at
        self._save()
        return meta

    async def restore(self, name: str) -> SkillLifecycle:
        await self._require_skill(name, include_archived=True)
        meta = self._metadata_for_name(name)
        meta.status = "active"
        meta.archived_at = ""
        meta.updated_at = datetime.now(UTC).isoformat()
        self._save()
        return meta

    async def mark_stale(self, name: str) -> SkillLifecycle:
        """Flag a long-unused skill; it stays runnable but is surfaced for review."""
        await self._require_skill(name, include_archived=True)
        meta = self._metadata_for_name(name)
        meta.status = "stale"
        meta.updated_at = datetime.now(UTC).isoformat()
        self._save()
        return meta

    async def pin(self, name: str, *, pinned: bool) -> SkillLifecycle:
        await self._require_skill(name, include_archived=True)
        meta = self._metadata_for_name(name)
        meta.pinned = pinned
        meta.updated_at = datetime.now(UTC).isoformat()
        self._save()
        return meta

    async def promote(
        self,
        name: str,
        *,
        scope: str,
        environment_id: str = "",
        domain: str = "",
    ) -> SkillLifecycle:
        await self._require_skill(name, include_archived=True)
        self._validate_scope(scope)
        meta = self._metadata_for_name(name)
        meta.scope = scope
        meta.environment_id = environment_id
        meta.domain = domain
        meta.updated_at = datetime.now(UTC).isoformat()
        self._save()
        return meta

    async def show(self, name: str, *, include_archived: bool = False) -> dict:
        skill = await self._require_skill(name, include_archived=include_archived)
        return {"skill": asdict(skill), "metadata": asdict(self._metadata_for(skill))}

    async def list_skills(
        self,
        query: str | None = None,
        *,
        include_archived: bool = False,
    ) -> list[dict]:
        skills = await self._skill_port.list_skills(query)
        rows = []
        for skill in skills:
            meta = self._metadata_for(skill)
            if meta.status == "archived" and not include_archived:
                continue
            rows.append({"skill": asdict(skill), "metadata": asdict(meta)})
        rows.sort(key=lambda row: (not row["metadata"]["pinned"], row["skill"]["name"].lower()))
        return rows

    async def list_runnable_skills(self, query: str | None = None) -> list[Skill]:
        """List skills that are available for discovery and execution."""
        skills = await self._skill_port.list_skills(query)
        runnable = [skill for skill in skills if not self._is_archived(skill.name)]
        return sorted(
            runnable,
            key=lambda skill: (
                not self._metadata_for(skill).pinned,
                skill.name.lower(),
            ),
        )

    async def get_runnable_skill(self, name: str) -> Skill | None:
        skill = await self._skill_port.get_skill(name)
        if skill is None or self._is_archived(skill.name):
            return None
        return skill

    async def record_usage(
        self,
        name: str,
        *,
        success: bool,
        environment_id: str = "",
        domain: str = "",
        action_safety_class: str = "",
    ) -> SkillLifecycle:
        await self._require_skill(name, include_archived=True)
        meta = self._metadata_for_name(name)
        meta.run_count += 1
        if success:
            meta.success_count += 1
            meta.consecutive_failures = 0
            if meta.status == "stale":
                # A skill in active use is no longer stale.
                meta.status = "active"
        else:
            meta.failure_count += 1
            meta.consecutive_failures += 1
        if environment_id:
            meta.environment_id = environment_id
        if domain:
            meta.domain = domain
        if action_safety_class:
            meta.action_safety_class = action_safety_class
        meta.last_used_at = datetime.now(UTC).isoformat()
        meta.updated_at = meta.last_used_at
        self._save()
        return meta

    async def record_episode(self, episode: Episode) -> Skill | None:
        skill = await self._skill_port.record_episode(episode)
        if skill is None:
            return None
        structured = episode.structured_outcome or {}
        environment_id = str(structured.get("environment_id") or "")
        domain = str(structured.get("domain_scope") or "")
        meta = self._metadata_for(skill)
        meta.source = "episode"
        meta.scope = "environment" if environment_id else "private"
        meta.environment_id = environment_id
        meta.domain = domain
        meta.updated_at = datetime.now(UTC).isoformat()
        self._save()
        return skill

    async def _require_skill(self, name: str, *, include_archived: bool) -> Skill:
        skill = await self._skill_port.get_skill(name)
        if skill is None:
            raise LookupError(f"Skill not found: {name}")
        if self._is_archived(skill.name) and not include_archived:
            raise LookupError(f"Skill is archived: {name}")
        return skill

    def _metadata_for(self, skill: Skill) -> SkillLifecycle:
        key = self._key(skill.name)
        meta = self._metadata.get(key)
        if meta is None:
            created = skill.created_at.isoformat()
            meta = SkillLifecycle(
                skill_id=skill.skill_id,
                name=skill.name,
                created_at=created,
                updated_at=created,
            )
            self._metadata[key] = meta
            self._save()
        return meta

    def _metadata_for_name(self, name: str) -> SkillLifecycle:
        key = self._key(name)
        meta = self._metadata.get(key)
        if meta is None:
            meta = SkillLifecycle(
                skill_id="",
                name=name,
                created_at=datetime.now(UTC).isoformat(),
                updated_at=datetime.now(UTC).isoformat(),
            )
            self._metadata[key] = meta
        return meta

    def _is_archived(self, name: str) -> bool:
        meta = self._metadata.get(self._key(name))
        return meta is not None and meta.status == "archived"

    @staticmethod
    def _key(name: str) -> str:
        return name.strip().lower()

    @staticmethod
    def _validate_name_content(name: str, content: str) -> None:
        if not name.strip():
            raise ValueError("name is required")
        if not content.strip():
            raise ValueError("content is required")

    @staticmethod
    def _validate_scope(scope: str) -> None:
        if scope not in SKILL_SCOPES:
            raise ValueError(f"Unknown skill scope: {scope}")

    def _load(self) -> None:
        try:
            data = json.loads(self._metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self._metadata = {
            key: SkillLifecycle(**value) for key, value in data.items() if isinstance(value, dict)
        }

    def _save(self) -> None:
        self._metadata_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {key: asdict(value) for key, value in self._metadata.items()}
        self._metadata_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _ensure_skill_header(name: str, content: str | None) -> str:
    text = (content or "").strip()
    if text.lower().startswith("# skill:"):
        return text + "\n"
    return f"# skill: {name}\n\n{text}\n"


def _description_from_content(name: str, content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return f"Managed skill: {name}"


def _tools_from_content(content: str | None) -> list[str]:
    if not content:
        return []
    tools = sorted(set(re.findall(r"`([A-Za-z][\\w.-]*)`", content)))
    return [tool for tool in tools if tool not in {"skill", "markdown"}]
