"""Domain service for preset management."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from volundr.domain.models import Preset
from volundr.domain.ports import PresetRepository

logger = logging.getLogger(__name__)


class PresetNotFoundError(Exception):
    """Raised when a preset is not found."""


class PresetDuplicateNameError(Exception):
    """Raised when a preset name already exists."""


class PresetService:
    """Service for managing presets."""

    def __init__(self, repository: PresetRepository):
        self._repository = repository

    async def create_preset(
        self,
        name: str,
        description: str = "",
        is_default: bool = False,
        cli_tool: str = "",
        workload_type: str = "session",
        model: str | None = None,
        system_prompt: str | None = None,
        resource_config: dict | None = None,
        mcp_servers: list[dict] | None = None,
        terminal_sidecar: dict | None = None,
        skills: list[dict] | None = None,
        rules: list[dict] | None = None,
        env_vars: dict[str, str] | None = None,
        env_secret_refs: list[str] | None = None,
        source: dict | None = None,
        integration_ids: list[str] | None = None,
        setup_scripts: list[str] | None = None,
        workload_config: dict | None = None,
    ) -> Preset:
        """Create a new preset."""
        existing = await self._repository.get_by_name(name)
        if existing is not None:
            raise PresetDuplicateNameError(f"Preset with name already exists: {name}")

        if is_default:
            await self._repository.clear_default(cli_tool)

        preset = Preset(
            name=name,
            description=description,
            is_default=is_default,
            cli_tool=cli_tool,
            workload_type=workload_type,
            model=model,
            system_prompt=system_prompt,
            resource_config=resource_config or {},
            mcp_servers=mcp_servers or [],
            terminal_sidecar=terminal_sidecar or {},
            skills=skills or [],
            rules=rules or [],
            env_vars=env_vars or {},
            env_secret_refs=env_secret_refs or [],
            source=source,
            integration_ids=integration_ids or [],
            setup_scripts=setup_scripts or [],
            workload_config=workload_config or {},
        )
        created = await self._repository.create(preset)
        logger.info("Created preset: id=%s, name=%s", created.id, created.name)
        return created

    async def get_preset(self, preset_id: UUID) -> Preset:
        """Get a preset by ID."""
        preset = await self._repository.get(preset_id)
        if preset is None:
            raise PresetNotFoundError(f"Preset not found: {preset_id}")
        return preset

    async def list_presets(
        self,
        cli_tool: str | None = None,
        is_default: bool | None = None,
    ) -> list[Preset]:
        """List presets with optional filters."""
        return await self._repository.list(cli_tool=cli_tool, is_default=is_default)

    async def update_preset(
        self,
        preset_id: UUID,
        updates: dict,
    ) -> Preset:
        """Update an existing preset with partial data."""
        preset = await self._repository.get(preset_id)
        if preset is None:
            raise PresetNotFoundError(f"Preset not found: {preset_id}")

        # Check name uniqueness if name is changing
        new_name = updates.get("name")
        if new_name is not None and new_name != preset.name:
            existing = await self._repository.get_by_name(new_name)
            if existing is not None:
                raise PresetDuplicateNameError(f"Preset with name already exists: {new_name}")

        # Handle default clearing
        new_is_default = updates.get("is_default")
        if new_is_default is True and not preset.is_default:
            cli_tool = updates.get("cli_tool", preset.cli_tool)
            await self._repository.clear_default(cli_tool)

        for key, value in updates.items():
            setattr(preset, key, value)
        preset.updated_at = datetime.now(UTC)

        updated = await self._repository.update(preset)
        logger.info("Updated preset: id=%s", updated.id)
        return updated

    async def delete_preset(self, preset_id: UUID) -> bool:
        """Delete a preset."""
        deleted = await self._repository.delete(preset_id)
        if not deleted:
            raise PresetNotFoundError(f"Preset not found: {preset_id}")
        logger.info("Deleted preset: id=%s", preset_id)
        return True
