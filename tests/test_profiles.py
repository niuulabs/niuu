"""Tests for forge profile CRUD API and service."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import InMemorySessionRepository
from volundr.adapters.inbound.rest_profiles import create_profiles_router
from volundr.adapters.outbound.config_profiles import ConfigProfileProvider
from volundr.config import ProfileConfig
from volundr.domain.models import ForgeProfile, Session, SessionStatus, WorkspaceTemplate
from volundr.domain.ports import MutableProfileProvider, ProfileProvider, TemplateProvider
from volundr.domain.services.profile import (
    ForgeProfileService,
    ProfileNotFoundError,
    ProfileReadOnlyError,
    ProfileValidationError,
    validate_profile,
)
from volundr.domain.services.template import WorkspaceTemplateService

# --- Read-only provider stub (does NOT implement MutableProfileProvider) ---


class ReadOnlyProfileProvider(ProfileProvider):
    """A read-only profile provider for testing."""

    def __init__(self, profiles: list[ForgeProfile] | None = None):
        self._profiles = {p.name: p for p in (profiles or [])}

    def get(self, name: str) -> ForgeProfile | None:
        return self._profiles.get(name)

    def list(self, workload_type: str | None = None) -> list[ForgeProfile]:
        profiles = list(self._profiles.values())
        if workload_type is not None:
            profiles = [p for p in profiles if p.workload_type == workload_type]
        return sorted(profiles, key=lambda p: p.name)

    def get_default(self, workload_type: str) -> ForgeProfile | None:
        for p in self._profiles.values():
            if p.workload_type == workload_type and p.is_default:
                return p
        return None


class StubTemplateProvider(TemplateProvider):
    """Stub template provider for testing."""

    def get(self, name: str) -> WorkspaceTemplate | None:
        return None

    def list(self, profile_name: str | None = None) -> list[WorkspaceTemplate]:
        return []


# --- Fixtures ---


def _make_profile(**overrides) -> ForgeProfile:
    """Helper to create a ForgeProfile with defaults."""
    defaults = {
        "name": "test-profile",
        "description": "A test profile",
        "workload_type": "session",
        "model": "claude-sonnet-4-20250514",
        "resource_config": {"cpu": 2, "memory": "4Gi", "gpu": 0},
    }
    defaults.update(overrides)
    return ForgeProfile(**defaults)


@pytest.fixture
def config_provider() -> ConfigProfileProvider:
    """ConfigProfileProvider with two seed profiles."""
    return ConfigProfileProvider(
        [
            ProfileConfig(
                name="default",
                description="Default profile",
                is_default=True,
                model="claude-sonnet-4-20250514",
                resource_config={"cpu": 2, "memory": "4Gi"},
            ),
            ProfileConfig(
                name="heavy",
                description="Heavy workload profile",
                model="claude-opus-4-20250514",
                resource_config={"cpu": 8, "memory": "16Gi", "gpu": 1},
            ),
        ]
    )


@pytest.fixture
def session_repository() -> InMemorySessionRepository:
    return InMemorySessionRepository()


@pytest.fixture
def profile_service(
    config_provider: ConfigProfileProvider,
    session_repository: InMemorySessionRepository,
) -> ForgeProfileService:
    return ForgeProfileService(config_provider, session_repository=session_repository)


@pytest.fixture
def readonly_service() -> ForgeProfileService:
    """Service backed by a read-only provider."""
    provider = ReadOnlyProfileProvider([_make_profile(name="readonly-prof")])
    return ForgeProfileService(provider)


@pytest.fixture
def client(profile_service: ForgeProfileService) -> TestClient:
    """TestClient wired to the profiles router."""
    template_service = WorkspaceTemplateService(StubTemplateProvider())
    router = create_profiles_router(profile_service, template_service)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ============================================================
# ProfileService unit tests
# ============================================================


class TestProfileServiceList:
    def test_list_all(self, profile_service: ForgeProfileService):
        profiles = profile_service.list_profiles()
        assert len(profiles) == 2
        names = [p.name for p in profiles]
        assert "default" in names
        assert "heavy" in names

    def test_list_filtered(self, profile_service: ForgeProfileService):
        profiles = profile_service.list_profiles(workload_type="session")
        assert len(profiles) == 2

    def test_list_filtered_empty(self, profile_service: ForgeProfileService):
        profiles = profile_service.list_profiles(workload_type="nonexistent")
        assert profiles == []


class TestProfileServiceGet:
    def test_get_existing(self, profile_service: ForgeProfileService):
        profile = profile_service.get_profile("default")
        assert profile is not None
        assert profile.name == "default"

    def test_get_missing(self, profile_service: ForgeProfileService):
        assert profile_service.get_profile("nope") is None

    def test_get_default(self, profile_service: ForgeProfileService):
        default = profile_service.get_default("session")
        assert default is not None
        assert default.name == "default"


class TestProfileServiceCreate:
    async def test_create_success(self, profile_service: ForgeProfileService):
        profile = _make_profile(name="new-profile")
        created = await profile_service.create_profile(profile)
        assert created.name == "new-profile"
        assert profile_service.get_profile("new-profile") is not None

    async def test_create_duplicate(self, profile_service: ForgeProfileService):
        profile = _make_profile(name="default")
        with pytest.raises(ValueError, match="already exists"):
            await profile_service.create_profile(profile)

    async def test_create_invalid_cpu(self, profile_service: ForgeProfileService):
        profile = _make_profile(
            name="bad-cpu",
            resource_config={"cpu": 100},
        )
        with pytest.raises(ProfileValidationError, match="cpu"):
            await profile_service.create_profile(profile)

    async def test_create_invalid_memory(self, profile_service: ForgeProfileService):
        profile = _make_profile(
            name="bad-mem",
            resource_config={"memory": "invalid"},
        )
        with pytest.raises(ProfileValidationError, match="memory"):
            await profile_service.create_profile(profile)

    async def test_create_invalid_gpu(self, profile_service: ForgeProfileService):
        profile = _make_profile(
            name="bad-gpu",
            resource_config={"gpu": 10},
        )
        with pytest.raises(ProfileValidationError, match="gpu"):
            await profile_service.create_profile(profile)

    async def test_create_invalid_model_empty(self, profile_service: ForgeProfileService):
        profile = _make_profile(name="bad-model", model="  ")
        with pytest.raises(ProfileValidationError, match="model"):
            await profile_service.create_profile(profile)

    async def test_create_invalid_mcp_no_type(self, profile_service: ForgeProfileService):
        profile = _make_profile(
            name="bad-mcp",
            mcp_servers=[{"name": "foo"}],
        )
        with pytest.raises(ProfileValidationError, match="mcp_servers"):
            await profile_service.create_profile(profile)

    async def test_create_invalid_image_empty(self, profile_service: ForgeProfileService):
        profile = _make_profile(
            name="bad-image",
            workload_config={"image": "  "},
        )
        with pytest.raises(ProfileValidationError, match="image"):
            await profile_service.create_profile(profile)

    async def test_create_readonly_provider(self, readonly_service: ForgeProfileService):
        profile = _make_profile(name="will-fail")
        with pytest.raises(ProfileReadOnlyError):
            await readonly_service.create_profile(profile)


class TestProfileServiceUpdate:
    async def test_update_success(self, profile_service: ForgeProfileService):
        updated_profile = _make_profile(
            name="default",
            description="Updated description",
        )
        result = await profile_service.update_profile("default", updated_profile)
        assert result.description == "Updated description"

    async def test_update_not_found(self, profile_service: ForgeProfileService):
        profile = _make_profile(name="nope")
        with pytest.raises(ProfileNotFoundError):
            await profile_service.update_profile("nope", profile)

    async def test_update_invalid(self, profile_service: ForgeProfileService):
        profile = _make_profile(
            name="default",
            resource_config={"cpu": -1},
        )
        with pytest.raises(ProfileValidationError):
            await profile_service.update_profile("default", profile)

    async def test_update_readonly(self, readonly_service: ForgeProfileService):
        profile = _make_profile(name="readonly-prof")
        with pytest.raises(ProfileReadOnlyError):
            await readonly_service.update_profile("readonly-prof", profile)


class TestProfileServiceDelete:
    async def test_delete_success(self, profile_service: ForgeProfileService):
        result = await profile_service.delete_profile("heavy")
        assert result is True
        assert profile_service.get_profile("heavy") is None

    async def test_delete_not_found(self, profile_service: ForgeProfileService):
        with pytest.raises(ProfileNotFoundError):
            await profile_service.delete_profile("nope")

    async def test_delete_readonly(self, readonly_service: ForgeProfileService):
        with pytest.raises(ProfileReadOnlyError):
            await readonly_service.delete_profile("readonly-prof")

    async def test_delete_in_use(
        self,
        profile_service: ForgeProfileService,
        session_repository: InMemorySessionRepository,
    ):
        """Reject delete when a running session uses the profile name."""
        session = Session(
            name="heavy",
            status=SessionStatus.RUNNING,
        )
        await session_repository.create(session)
        with pytest.raises(ValueError, match="in use"):
            await profile_service.delete_profile("heavy")


# ============================================================
# Validation function tests
# ============================================================


class TestValidateProfile:
    def test_valid_profile(self):
        profile = _make_profile()
        assert validate_profile(profile) == []

    def test_cpu_too_low(self):
        profile = _make_profile(resource_config={"cpu": 0.1})
        errors = validate_profile(profile)
        assert any("cpu" in e for e in errors)

    def test_cpu_too_high(self):
        profile = _make_profile(resource_config={"cpu": 20})
        errors = validate_profile(profile)
        assert any("cpu" in e for e in errors)

    def test_cpu_non_numeric(self):
        profile = _make_profile(resource_config={"cpu": "abc"})
        errors = validate_profile(profile)
        assert any("cpu" in e for e in errors)

    def test_memory_valid_mi(self):
        profile = _make_profile(resource_config={"memory": "512Mi"})
        assert validate_profile(profile) == []

    def test_memory_valid_gi(self):
        profile = _make_profile(resource_config={"memory": "2Gi"})
        assert validate_profile(profile) == []

    def test_memory_invalid_format(self):
        profile = _make_profile(resource_config={"memory": "2GB"})
        errors = validate_profile(profile)
        assert any("memory" in e for e in errors)

    def test_memory_out_of_range(self):
        profile = _make_profile(resource_config={"memory": "1Ki"})
        errors = validate_profile(profile)
        assert any("memory" in e for e in errors)

    def test_gpu_valid(self):
        profile = _make_profile(resource_config={"gpu": 2})
        assert validate_profile(profile) == []

    def test_gpu_too_high(self):
        profile = _make_profile(resource_config={"gpu": 5})
        errors = validate_profile(profile)
        assert any("gpu" in e for e in errors)

    def test_gpu_non_integer(self):
        profile = _make_profile(resource_config={"gpu": "abc"})
        errors = validate_profile(profile)
        assert any("gpu" in e for e in errors)

    def test_empty_model(self):
        profile = _make_profile(model="")
        errors = validate_profile(profile)
        assert any("model" in e for e in errors)

    def test_none_model_ok(self):
        profile = _make_profile(model=None)
        assert validate_profile(profile) == []

    def test_mcp_missing_type(self):
        profile = _make_profile(mcp_servers=[{"name": "x"}])
        errors = validate_profile(profile)
        assert any("mcp_servers" in e for e in errors)

    def test_mcp_with_type_ok(self):
        profile = _make_profile(mcp_servers=[{"type": "stdio", "name": "x"}])
        assert validate_profile(profile) == []

    def test_empty_image(self):
        profile = _make_profile(workload_config={"image": ""})
        errors = validate_profile(profile)
        assert any("image" in e for e in errors)

    def test_no_resource_config(self):
        profile = _make_profile(resource_config={})
        assert validate_profile(profile) == []


# ============================================================
# ConfigProfileProvider (MutableProfileProvider) tests
# ============================================================


class TestConfigProfileProvider:
    def test_implements_mutable(self, config_provider: ConfigProfileProvider):
        assert isinstance(config_provider, MutableProfileProvider)

    async def test_create(self, config_provider: ConfigProfileProvider):
        profile = _make_profile(name="new")
        result = await config_provider.create(profile)
        assert result.name == "new"
        assert config_provider.get("new") is not None

    async def test_create_duplicate(self, config_provider: ConfigProfileProvider):
        profile = _make_profile(name="default")
        with pytest.raises(ValueError, match="already exists"):
            await config_provider.create(profile)

    async def test_update(self, config_provider: ConfigProfileProvider):
        profile = _make_profile(name="default", description="updated")
        result = await config_provider.update("default", profile)
        assert result.description == "updated"

    async def test_update_rename(self, config_provider: ConfigProfileProvider):
        profile = _make_profile(name="renamed")
        await config_provider.update("default", profile)
        assert config_provider.get("default") is None
        assert config_provider.get("renamed") is not None

    async def test_update_not_found(self, config_provider: ConfigProfileProvider):
        profile = _make_profile(name="nope")
        with pytest.raises(ValueError, match="not found"):
            await config_provider.update("nope", profile)

    async def test_delete(self, config_provider: ConfigProfileProvider):
        assert await config_provider.delete("heavy") is True
        assert config_provider.get("heavy") is None

    async def test_delete_missing(self, config_provider: ConfigProfileProvider):
        assert await config_provider.delete("nope") is False

    def test_list(self, config_provider: ConfigProfileProvider):
        profiles = config_provider.list()
        assert len(profiles) == 2

    def test_get_default(self, config_provider: ConfigProfileProvider):
        default = config_provider.get_default("session")
        assert default is not None
        assert default.name == "default"


# ============================================================
# REST endpoint tests
# ============================================================


class TestProfileEndpoints:
    def test_list_profiles(self, client: TestClient):
        resp = client.get("/api/v1/forge/profiles")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        names = [p["name"] for p in data]
        assert "default" in names

    def test_list_profiles_filter(self, client: TestClient):
        resp = client.get("/api/v1/forge/profiles", params={"workload_type": "session"})
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_get_profile(self, client: TestClient):
        resp = client.get("/api/v1/forge/profiles/default")
        assert resp.status_code == 200
        assert resp.json()["name"] == "default"

    def test_get_profile_not_found(self, client: TestClient):
        resp = client.get("/api/v1/forge/profiles/nonexistent")
        assert resp.status_code == 404

    def test_create_profile(self, client: TestClient):
        resp = client.post(
            "/api/v1/forge/profiles",
            json={
                "name": "new-api-profile",
                "description": "Created via API",
                "model": "claude-sonnet-4-20250514",
                "resource_config": {"cpu": 4, "memory": "8Gi"},
            },
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "new-api-profile"

        # Verify it's now listable
        resp = client.get("/api/v1/forge/profiles")
        names = [p["name"] for p in resp.json()]
        assert "new-api-profile" in names

    def test_create_profile_duplicate(self, client: TestClient):
        resp = client.post(
            "/api/v1/forge/profiles",
            json={"name": "default"},
        )
        assert resp.status_code == 409

    def test_create_profile_invalid(self, client: TestClient):
        resp = client.post(
            "/api/v1/forge/profiles",
            json={
                "name": "bad",
                "resource_config": {"cpu": 999},
            },
        )
        assert resp.status_code == 400

    def test_update_profile(self, client: TestClient):
        resp = client.put(
            "/api/v1/forge/profiles/default",
            json={"description": "Updated via API"},
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "Updated via API"

    def test_update_profile_not_found(self, client: TestClient):
        resp = client.put(
            "/api/v1/forge/profiles/nonexistent",
            json={"description": "nope"},
        )
        assert resp.status_code == 404

    def test_update_profile_invalid(self, client: TestClient):
        resp = client.put(
            "/api/v1/forge/profiles/default",
            json={"resource_config": {"gpu": 99}},
        )
        assert resp.status_code == 400

    def test_delete_profile(self, client: TestClient):
        resp = client.delete("/api/v1/forge/profiles/heavy")
        assert resp.status_code == 204

        # Verify it's gone
        resp = client.get("/api/v1/forge/profiles/heavy")
        assert resp.status_code == 404

    def test_delete_profile_not_found(self, client: TestClient):
        resp = client.delete("/api/v1/forge/profiles/nonexistent")
        assert resp.status_code == 404


class TestReadOnlyEndpoints:
    """Test that endpoints return appropriate errors for read-only providers."""

    @pytest.fixture
    def readonly_client(self) -> TestClient:
        provider = ReadOnlyProfileProvider([_make_profile(name="ro-profile")])
        service = ForgeProfileService(provider)
        template_service = WorkspaceTemplateService(StubTemplateProvider())
        router = create_profiles_router(service, template_service)
        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_create_readonly(self, readonly_client: TestClient):
        resp = readonly_client.post(
            "/api/v1/forge/profiles",
            json={"name": "new"},
        )
        assert resp.status_code == 400
        assert "read-only" in resp.json()["detail"].lower()

    def test_update_readonly(self, readonly_client: TestClient):
        resp = readonly_client.put(
            "/api/v1/forge/profiles/ro-profile",
            json={"description": "nope"},
        )
        assert resp.status_code == 400

    def test_delete_readonly(self, readonly_client: TestClient):
        resp = readonly_client.delete("/api/v1/forge/profiles/ro-profile")
        assert resp.status_code == 400
