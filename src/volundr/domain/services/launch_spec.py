"""Domain service for launch specs.

Unifies the former profile / template / preset services. System-scope specs are
config-seeded and read-only (served from a LaunchSpecProvider); user-scope specs
are DB-stored and fully CRUD-managed (via a LaunchSpecRepository).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from volundr.domain.models import LaunchScope, LaunchSpec
from volundr.domain.ports import LaunchSpecProvider, LaunchSpecRepository

logger = logging.getLogger(__name__)


class LaunchSpecNotFoundError(Exception):
    """Raised when a launch spec is not found."""


class LaunchSpecDuplicateNameError(Exception):
    """Raised when a user launch spec name already exists."""


class LaunchSpecService:
    """Service for reading system launch specs and managing user launch specs."""

    def __init__(
        self,
        provider: LaunchSpecProvider,
        repository: LaunchSpecRepository | None = None,
    ):
        self._provider = provider
        self._repository = repository

    def _require_repo(self) -> LaunchSpecRepository:
        if self._repository is None:
            raise LaunchSpecNotFoundError("User launch specs are not available (no store)")
        return self._repository

    # --- system (config) reads ---

    def get_system(self, name: str) -> LaunchSpec | None:
        return self._provider.get(name)

    def get_default(self, workload_type: str = "session") -> LaunchSpec | None:
        return self._provider.get_default(workload_type)

    def list_system(self, workload_type: str | None = None) -> list[LaunchSpec]:
        return self._provider.list(workload_type=workload_type)

    # --- user (DB) reads ---

    async def get_user(self, spec_id: UUID) -> LaunchSpec:
        spec = await self._require_repo().get(spec_id)
        if spec is None:
            raise LaunchSpecNotFoundError(f"Launch spec not found: {spec_id}")
        return spec

    async def list_user(
        self,
        cli_tool: str | None = None,
        is_default: bool | None = None,
    ) -> list[LaunchSpec]:
        if self._repository is None:
            return []
        return await self._repository.list(cli_tool=cli_tool, is_default=is_default)

    # --- combined listing ---

    async def list_all(
        self,
        scope: LaunchScope | None = None,
        workload_type: str | None = None,
        cli_tool: str | None = None,
        is_default: bool | None = None,
    ) -> list[LaunchSpec]:
        specs: list[LaunchSpec] = []
        if scope in (None, LaunchScope.SYSTEM):
            specs.extend(self._provider.list(workload_type=workload_type))
        if scope in (None, LaunchScope.USER) and self._repository is not None:
            specs.extend(await self._repository.list(cli_tool=cli_tool, is_default=is_default))
        return specs

    # --- user CRUD ---

    async def create(self, spec: LaunchSpec) -> LaunchSpec:
        repo = self._require_repo()
        spec.scope = LaunchScope.USER
        existing = await repo.get_by_name(spec.name)
        if existing is not None:
            raise LaunchSpecDuplicateNameError(f"Launch spec already exists: {spec.name}")
        if spec.is_default:
            await repo.clear_default(spec.cli_tool)
        created = await repo.create(spec)
        logger.info("Created launch spec: id=%s, name=%s", created.id, created.name)
        return created

    async def update(self, spec_id: UUID, updates: dict) -> LaunchSpec:
        repo = self._require_repo()
        spec = await repo.get(spec_id)
        if spec is None:
            raise LaunchSpecNotFoundError(f"Launch spec not found: {spec_id}")

        new_name = updates.get("name")
        if new_name is not None and new_name != spec.name:
            if await repo.get_by_name(new_name) is not None:
                raise LaunchSpecDuplicateNameError(f"Launch spec already exists: {new_name}")

        if updates.get("is_default") is True and not spec.is_default:
            await repo.clear_default(updates.get("cli_tool", spec.cli_tool))

        for key, value in updates.items():
            setattr(spec, key, value)
        spec.updated_at = datetime.now(UTC)
        updated = await repo.update(spec)
        logger.info("Updated launch spec: id=%s", updated.id)
        return updated

    async def delete(self, spec_id: UUID) -> bool:
        deleted = await self._require_repo().delete(spec_id)
        if not deleted:
            raise LaunchSpecNotFoundError(f"Launch spec not found: {spec_id}")
        logger.info("Deleted launch spec: id=%s", spec_id)
        return True
