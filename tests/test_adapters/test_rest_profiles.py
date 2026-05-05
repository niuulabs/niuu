"""Tests for the REST adapter for profiles and templates."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from volundr.adapters.inbound.rest_profiles import (
    ProfileResponse,
    TemplateResponse,
    create_profiles_router,
)
from volundr.domain.models import ForgeProfile, WorkspaceTemplate
from volundr.domain.ports import ProfileProvider, TemplateProvider
from volundr.domain.services.profile import ForgeProfileService
from volundr.domain.services.template import WorkspaceTemplateService

# ---- In-memory test doubles ----


class InMemoryProfileProvider(ProfileProvider):
    """In-memory profile provider for testing."""

    def __init__(self, profiles: list[ForgeProfile] | None = None):
        self._profiles: dict[str, ForgeProfile] = {}
        for p in profiles or []:
            self._profiles[p.name] = p

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


class InMemoryTemplateProvider(TemplateProvider):
    """In-memory template provider for testing."""

    def __init__(self, templates: list[WorkspaceTemplate] | None = None):
        self._templates: dict[str, WorkspaceTemplate] = {}
        for t in templates or []:
            self._templates[t.name] = t

    def get(self, name: str) -> WorkspaceTemplate | None:
        return self._templates.get(name)

    def list(self, workload_type: str | None = None) -> list[WorkspaceTemplate]:
        templates = list(self._templates.values())
        if workload_type is not None:
            templates = [t for t in templates if t.workload_type == workload_type]
        return sorted(templates, key=lambda t: t.name)


# ---- Fixtures ----


@pytest.fixture
def sample_profiles() -> list[ForgeProfile]:
    """Create sample forge profiles."""
    return [
        ForgeProfile(
            name="standard",
            description="Standard coding session",
            workload_type="session",
            model="claude-sonnet-4",
            system_prompt="You are helpful.",
            resource_config={"cpu": "500m", "memory": "1Gi"},
            mcp_servers=[{"name": "fs", "command": "mcp-fs"}],
            env_vars={"MY_VAR": "value"},
            env_secret_refs=["secret-1"],
            workload_config={"timeout": 300},
            is_default=True,
        ),
        ForgeProfile(
            name="gpu-heavy",
            description="GPU workspace",
            workload_type="session",
            model="claude-opus-4",
            resource_config={"cpu": "2", "memory": "8Gi"},
            is_default=False,
        ),
    ]


@pytest.fixture
def sample_templates() -> list[WorkspaceTemplate]:
    """Create sample workspace templates."""
    return [
        WorkspaceTemplate(
            name="default-session",
            description="Default coding session",
            workload_type="session",
            model="claude-sonnet-4",
            system_prompt="You are helpful.",
            resource_config={"cpu": "500m", "memory": "1Gi"},
            mcp_servers=[{"name": "fs", "command": "mcp-fs"}],
            env_vars={"MY_VAR": "value"},
            env_secret_refs=["secret-1"],
            repos=[{"url": "https://github.com/org/repo"}],
            setup_scripts=["pip install -r requirements.txt"],
            workspace_layout={"editor": "vscode"},
            is_default=True,
        ),
        WorkspaceTemplate(
            name="data-science",
            description="Data science workspace",
            workload_type="session",
            model="claude-opus-4",
            resource_config={"cpu": "2", "memory": "8Gi"},
        ),
    ]


@pytest.fixture
def profile_provider(sample_profiles) -> InMemoryProfileProvider:
    """Create an in-memory profile provider."""
    return InMemoryProfileProvider(sample_profiles)


@pytest.fixture
def template_provider(sample_templates) -> InMemoryTemplateProvider:
    """Create an in-memory template provider."""
    return InMemoryTemplateProvider(sample_templates)


@pytest.fixture
def profile_service(profile_provider: InMemoryProfileProvider) -> ForgeProfileService:
    """Create a profile service with test doubles."""
    return ForgeProfileService(provider=profile_provider)


@pytest.fixture
def template_service(template_provider: InMemoryTemplateProvider) -> WorkspaceTemplateService:
    """Create a template service with test doubles."""
    return WorkspaceTemplateService(provider=template_provider)


@pytest.fixture
def app(
    profile_service: ForgeProfileService,
    template_service: WorkspaceTemplateService,
) -> FastAPI:
    """Create a test FastAPI app with profile/template routes."""
    app = FastAPI()
    router = create_profiles_router(profile_service, template_service)
    app.include_router(router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Create a test client."""
    return TestClient(app)


# ---- ProfileResponse model tests ----


class TestProfileResponse:
    """Tests for ProfileResponse model."""

    def test_from_profile(self):
        """ProfileResponse.from_profile converts domain model."""
        profile = ForgeProfile(
            name="Test",
            workload_type="session",
            model="claude-sonnet-4",
            description="A profile",
            resource_config={"cpu": "2"},
            mcp_servers=[{"name": "fs"}],
            env_vars={"KEY": "VAL"},
            env_secret_refs=["s1"],
            workload_config={"k": "v"},
            is_default=True,
        )
        response = ProfileResponse.from_profile(profile)

        assert response.name == profile.name
        assert response.workload_type == "session"
        assert response.model == "claude-sonnet-4"
        assert response.resource_config == {"cpu": "2"}
        assert response.mcp_servers == [{"name": "fs"}]
        assert response.is_default is True


class TestTemplateResponse:
    """Tests for TemplateResponse model."""

    def test_from_template(self):
        """TemplateResponse.from_template converts domain model."""
        template = WorkspaceTemplate(
            name="Test",
            description="A template",
            workload_type="session",
            model="claude-sonnet-4",
            resource_config={"cpu": "500m"},
            repos=[{"url": "https://github.com/org/repo"}],
            setup_scripts=["make build"],
            workspace_layout={"editor": "vscode"},
            is_default=False,
        )
        response = TemplateResponse.from_template(template)

        assert response.name == template.name
        assert response.workload_type == "session"
        assert response.model == "claude-sonnet-4"
        assert response.resource_config == {"cpu": "500m"}
        assert response.repos == [{"url": "https://github.com/org/repo"}]
        assert response.setup_scripts == ["make build"]
        assert response.is_default is False


# ---- Profile endpoint tests (read-only) ----


class TestListProfiles:
    """Tests for GET /api/v1/volundr/profiles."""

    def test_list_profiles(self, client: TestClient):
        """Returns all profiles from config."""
        response = client.get("/api/v1/volundr/profiles")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        names = {p["name"] for p in data}
        assert names == {"standard", "gpu-heavy"}

    def test_list_profiles_filter_by_workload_type(self, client: TestClient):
        """Returns profiles filtered by workload_type."""
        response = client.get("/api/v1/volundr/profiles?workload_type=session")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert all(p["workload_type"] == "session" for p in data)

    def test_list_profiles_filter_no_match(self, client: TestClient):
        """Returns empty when workload_type filter doesn't match."""
        response = client.get("/api/v1/volundr/profiles?workload_type=nonexistent")
        assert response.status_code == 200
        assert response.json() == []


class TestGetProfile:
    """Tests for GET /api/v1/volundr/profiles/{profile_name}."""

    def test_get_profile_success(self, client: TestClient):
        """Returns profile by name."""
        response = client.get("/api/v1/volundr/profiles/standard")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "standard"
        assert data["workload_type"] == "session"
        assert data["model"] == "claude-sonnet-4"
        assert data["resource_config"] == {"cpu": "500m", "memory": "1Gi"}
        assert data["is_default"] is True

    def test_get_profile_not_found(self, client: TestClient):
        """Returns 404 for non-existent profile."""
        response = client.get("/api/v1/volundr/profiles/nonexistent")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


# ---- Template endpoint tests ----


class TestListTemplates:
    """Tests for GET /api/v1/volundr/templates."""

    def test_list_templates(self, client: TestClient):
        """Returns all templates from config."""
        response = client.get("/api/v1/volundr/templates")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        names = {t["name"] for t in data}
        assert names == {"default-session", "data-science"}

    def test_list_templates_filter_by_workload_type(self, client: TestClient):
        """Returns templates filtered by workload_type."""
        response = client.get("/api/v1/volundr/templates?workload_type=session")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert all(t["workload_type"] == "session" for t in data)

    def test_list_templates_filter_no_match(self, client: TestClient):
        """Returns empty when workload_type filter doesn't match."""
        response = client.get("/api/v1/volundr/templates?workload_type=nonexistent")
        assert response.status_code == 200
        assert response.json() == []

    def test_template_response_includes_runtime_fields(self, client: TestClient):
        """Template response includes merged runtime config fields."""
        response = client.get("/api/v1/volundr/templates/default-session")
        assert response.status_code == 200
        data = response.json()
        assert data["workload_type"] == "session"
        assert data["model"] == "claude-sonnet-4"
        assert data["system_prompt"] == "You are helpful."
        assert data["resource_config"] == {"cpu": "500m", "memory": "1Gi"}
        assert data["mcp_servers"] == [{"name": "fs", "command": "mcp-fs"}]
        assert data["env_vars"] == {"MY_VAR": "value"}
        assert data["env_secret_refs"] == ["secret-1"]


class TestGetTemplate:
    """Tests for GET /api/v1/volundr/templates/{template_name}."""

    def test_get_template_success(self, client: TestClient):
        """Returns template by name."""
        response = client.get("/api/v1/volundr/templates/default-session")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "default-session"
        assert data["workload_type"] == "session"
        assert data["repos"] == [{"url": "https://github.com/org/repo"}]

    def test_get_template_not_found(self, client: TestClient):
        """Returns 404 for non-existent template."""
        response = client.get("/api/v1/volundr/templates/nonexistent")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


# -------------------------------------------------------------------
# Session definitions endpoint tests
# -------------------------------------------------------------------


class TestSessionDefinitions:
    """Tests for GET /session-definitions."""

    @pytest.fixture
    def definitions_client(
        self,
        profile_service: ForgeProfileService,
        template_service: WorkspaceTemplateService,
    ) -> TestClient:
        from volundr.config import SessionDefinitionConfig

        definitions = {
            "skuldClaude": SessionDefinitionConfig(
                enabled=True,
                display_name="Claude Code",
                description="Anthropic Claude",
                labels=["session", "claude"],
                default_model="claude-sonnet-4-6",
            ),
            "skuldCodex": SessionDefinitionConfig(
                enabled=True,
                display_name="OpenAI Codex",
                description="Codex with WebSocket",
                labels=["session", "codex"],
                default_model="",
            ),
            "skuldDisabled": SessionDefinitionConfig(
                enabled=False,
                display_name="Disabled",
                description="Should not appear",
            ),
        }
        app = FastAPI()
        router = create_profiles_router(profile_service, template_service, definitions)
        app.include_router(router)
        return TestClient(app)

    def test_list_session_definitions(self, definitions_client: TestClient):
        """Returns enabled session definitions."""
        resp = definitions_client.get("/api/v1/volundr/session-definitions")
        assert resp.status_code == 200
        data = resp.json()
        keys = [d["key"] for d in data]
        assert "skuldClaude" in keys
        assert "skuldCodex" in keys
        assert "skuldDisabled" not in keys

    def test_session_definition_fields(self, definitions_client: TestClient):
        """Response has correct fields."""
        resp = definitions_client.get("/api/v1/volundr/session-definitions")
        claude = next(d for d in resp.json() if d["key"] == "skuldClaude")
        assert claude["display_name"] == "Claude Code"
        assert claude["description"] == "Anthropic Claude"
        assert claude["labels"] == ["session", "claude"]
        assert claude["default_model"] == "claude-sonnet-4-6"

    def test_empty_definitions(
        self,
        profile_service: ForgeProfileService,
        template_service: WorkspaceTemplateService,
    ):
        """Returns empty list when no definitions configured."""
        app = FastAPI()
        router = create_profiles_router(profile_service, template_service)
        app.include_router(router)
        client = TestClient(app)
        resp = client.get("/api/v1/volundr/session-definitions")
        assert resp.status_code == 200
        assert resp.json() == []
