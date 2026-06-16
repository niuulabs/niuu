"""Tests for launch-spec provider and domain service."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from volundr.adapters.outbound.config_launch_specs import ConfigLaunchSpecProvider
from volundr.config import LaunchSpecConfig
from volundr.domain.models import LaunchScope, LaunchSpec
from volundr.domain.services.launch_spec import (
    LaunchSpecDuplicateNameError,
    LaunchSpecNotFoundError,
    LaunchSpecService,
)


class InMemoryLaunchSpecRepository:
    """Tiny repository fake for user-scope launch specs."""

    def __init__(self, specs: list[LaunchSpec] | None = None) -> None:
        self.specs: dict[UUID, LaunchSpec] = {}
        for spec in specs or []:
            spec.id = spec.id or uuid4()
            self.specs[spec.id] = spec

    async def create(self, spec: LaunchSpec) -> LaunchSpec:
        spec.id = spec.id or uuid4()
        self.specs[spec.id] = spec
        return spec

    async def get(self, spec_id: UUID) -> LaunchSpec | None:
        return self.specs.get(spec_id)

    async def get_by_name(self, name: str) -> LaunchSpec | None:
        return next((spec for spec in self.specs.values() if spec.name == name), None)

    async def list(
        self,
        cli_tool: str | None = None,
        is_default: bool | None = None,
    ) -> list[LaunchSpec]:
        specs = list(self.specs.values())
        if cli_tool is not None:
            specs = [spec for spec in specs if spec.cli_tool == cli_tool]
        if is_default is not None:
            specs = [spec for spec in specs if spec.is_default is is_default]
        return specs

    async def update(self, spec: LaunchSpec) -> LaunchSpec:
        assert spec.id is not None
        self.specs[spec.id] = spec
        return spec

    async def delete(self, spec_id: UUID) -> bool:
        return self.specs.pop(spec_id, None) is not None

    async def clear_default(self, cli_tool: str) -> None:
        for spec in self.specs.values():
            if spec.cli_tool == cli_tool:
                spec.is_default = False


def _provider() -> ConfigLaunchSpecProvider:
    return ConfigLaunchSpecProvider(
        [
            LaunchSpecConfig(
                name="standard",
                description="Default session",
                is_default=True,
                workload_type="session",
                model="claude-sonnet-4-6",
                cli_tool="skuld",
            ),
            LaunchSpecConfig(
                name="flock",
                workload_type="ravn_flock",
                workload_config={"nodes": [{"id": "coordinator"}]},
            ),
        ]
    )


def test_config_provider_maps_filters_and_defaults() -> None:
    provider = _provider()

    assert provider.get("standard").scope is LaunchScope.SYSTEM
    assert [spec.name for spec in provider.list()] == ["flock", "standard"]
    assert [spec.name for spec in provider.list(workload_type="session")] == ["standard"]
    assert provider.get_default("session").name == "standard"
    assert provider.get_default("missing") is None


async def test_service_reads_system_and_handles_missing_user_store() -> None:
    service = LaunchSpecService(_provider())

    assert service.get_system("standard").model == "claude-sonnet-4-6"
    assert service.get_default().name == "standard"
    assert [spec.name for spec in service.list_system("ravn_flock")] == ["flock"]
    assert await service.list_user() == []
    assert [spec.name for spec in await service.list_all(scope=LaunchScope.SYSTEM)] == [
        "flock",
        "standard",
    ]

    with pytest.raises(LaunchSpecNotFoundError, match="no store"):
        await service.get_user(uuid4())


async def test_service_crud_enforces_uniqueness_and_default_clearing() -> None:
    existing_default = LaunchSpec(
        id=uuid4(),
        name="personal-default",
        scope=LaunchScope.USER,
        cli_tool="skuld",
        is_default=True,
    )
    repo = InMemoryLaunchSpecRepository([existing_default])
    service = LaunchSpecService(_provider(), repository=repo)

    created = await service.create(
        LaunchSpec(name="new-default", cli_tool="skuld", is_default=True)
    )
    assert created.scope is LaunchScope.USER
    assert created.id is not None
    assert existing_default.is_default is False

    with pytest.raises(LaunchSpecDuplicateNameError):
        await service.create(LaunchSpec(name="new-default"))

    fetched = await service.get_user(created.id)
    assert fetched.name == "new-default"
    assert [spec.name for spec in await service.list_user(cli_tool="skuld", is_default=True)] == [
        "new-default"
    ]

    other = await service.create(LaunchSpec(name="other", cli_tool="skuld"))
    with pytest.raises(LaunchSpecDuplicateNameError):
        await service.update(other.id, {"name": "new-default"})

    updated = await service.update(other.id, {"name": "renamed", "is_default": True})
    assert updated.name == "renamed"
    assert updated.is_default is True
    assert created.is_default is False

    with pytest.raises(LaunchSpecNotFoundError):
        await service.update(uuid4(), {"name": "missing"})

    deleted = await service.delete(updated.id)
    assert deleted is True
    with pytest.raises(LaunchSpecNotFoundError):
        await service.delete(updated.id)
