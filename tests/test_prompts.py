"""Tests for saved prompts API."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from volundr.adapters.inbound.rest_prompts import create_prompts_router
from volundr.domain.models import PromptScope, SavedPrompt
from volundr.domain.ports import SavedPromptRepository
from volundr.domain.services.prompt import PromptNotFoundError, PromptService

# --- In-memory repository for testing ---


class InMemoryPromptRepository(SavedPromptRepository):
    """In-memory saved prompt repository for testing."""

    def __init__(self):
        self._prompts: dict[UUID, SavedPrompt] = {}

    async def create(self, prompt: SavedPrompt) -> SavedPrompt:
        self._prompts[prompt.id] = prompt
        return prompt

    async def get(self, prompt_id: UUID) -> SavedPrompt | None:
        return self._prompts.get(prompt_id)

    async def list(
        self,
        scope: PromptScope | None = None,
        repo: str | None = None,
    ) -> list[SavedPrompt]:
        results = list(self._prompts.values())
        if scope is not None:
            results = [p for p in results if p.scope == scope]
        if repo is not None:
            results = [
                p for p in results if p.scope == PromptScope.GLOBAL or p.project_repo == repo
            ]
        results.sort(key=lambda p: p.updated_at, reverse=True)
        return results

    async def update(self, prompt: SavedPrompt) -> SavedPrompt:
        self._prompts[prompt.id] = prompt
        return prompt

    async def delete(self, prompt_id: UUID) -> bool:
        if prompt_id in self._prompts:
            del self._prompts[prompt_id]
            return True
        return False

    async def search(self, query: str) -> list[SavedPrompt]:
        query_lower = query.lower()
        results = []
        for p in self._prompts.values():
            name_match = query_lower in p.name.lower()
            content_match = query_lower in p.content.lower()
            if name_match or content_match:
                results.append((0 if name_match else 1, p))
        results.sort(key=lambda x: x[0])
        return [p for _, p in results]


@pytest.fixture
def prompt_repository() -> InMemoryPromptRepository:
    return InMemoryPromptRepository()


@pytest.fixture
def prompt_service(prompt_repository: InMemoryPromptRepository) -> PromptService:
    return PromptService(prompt_repository)


@pytest.fixture
def prompt_client(prompt_service: PromptService) -> TestClient:
    app = FastAPI()
    router = create_prompts_router(prompt_service)
    app.include_router(router)
    return TestClient(app)


# --- Service tests ---


class TestPromptService:
    """Tests for PromptService."""

    async def test_create_prompt(self, prompt_service: PromptService):
        prompt = await prompt_service.create_prompt(
            name="Security Review",
            content="Review this code for vulnerabilities",
        )
        assert prompt.name == "Security Review"
        assert prompt.scope == PromptScope.GLOBAL
        assert prompt.project_repo is None

    async def test_create_project_scoped_prompt(self, prompt_service: PromptService):
        prompt = await prompt_service.create_prompt(
            name="Test",
            content="Run tests",
            scope=PromptScope.PROJECT,
            project_repo="github.com/org/repo",
            tags=["testing"],
        )
        assert prompt.scope == PromptScope.PROJECT
        assert prompt.project_repo == "github.com/org/repo"
        assert prompt.tags == ["testing"]

    async def test_get_prompt(self, prompt_service: PromptService):
        created = await prompt_service.create_prompt(name="Test", content="Content")
        fetched = await prompt_service.get_prompt(created.id)
        assert fetched.id == created.id
        assert fetched.name == "Test"

    async def test_get_prompt_not_found(self, prompt_service: PromptService):
        with pytest.raises(PromptNotFoundError):
            await prompt_service.get_prompt(uuid4())

    async def test_list_prompts(self, prompt_service: PromptService):
        await prompt_service.create_prompt(name="A", content="Content A")
        await prompt_service.create_prompt(
            name="B",
            content="Content B",
            scope=PromptScope.PROJECT,
            project_repo="repo1",
        )
        all_prompts = await prompt_service.list_prompts()
        assert len(all_prompts) == 2

    async def test_list_prompts_filtered_by_scope(self, prompt_service: PromptService):
        await prompt_service.create_prompt(name="Global", content="G")
        await prompt_service.create_prompt(
            name="Project",
            content="P",
            scope=PromptScope.PROJECT,
            project_repo="repo1",
        )
        global_only = await prompt_service.list_prompts(scope=PromptScope.GLOBAL)
        assert len(global_only) == 1
        assert global_only[0].name == "Global"

    async def test_list_prompts_filtered_by_repo(self, prompt_service: PromptService):
        await prompt_service.create_prompt(name="Global", content="G")
        await prompt_service.create_prompt(
            name="Repo1",
            content="R1",
            scope=PromptScope.PROJECT,
            project_repo="repo1",
        )
        await prompt_service.create_prompt(
            name="Repo2",
            content="R2",
            scope=PromptScope.PROJECT,
            project_repo="repo2",
        )
        results = await prompt_service.list_prompts(repo="repo1")
        assert len(results) == 2  # global + repo1
        names = {p.name for p in results}
        assert "Global" in names
        assert "Repo1" in names

    async def test_update_prompt(self, prompt_service: PromptService):
        created = await prompt_service.create_prompt(name="Old", content="Old content")
        updated = await prompt_service.update_prompt(created.id, name="New", content="New content")
        assert updated.name == "New"
        assert updated.content == "New content"

    async def test_update_prompt_all_fields(self, prompt_service: PromptService):
        created = await prompt_service.create_prompt(name="Base", content="Base content")
        updated = await prompt_service.update_prompt(
            created.id,
            scope=PromptScope.PROJECT,
            project_repo="org/repo",
            tags=["python", "test"],
        )
        assert updated.scope == PromptScope.PROJECT
        assert updated.project_repo == "org/repo"
        assert updated.tags == ["python", "test"]

    async def test_update_prompt_not_found(self, prompt_service: PromptService):
        with pytest.raises(PromptNotFoundError):
            await prompt_service.update_prompt(uuid4(), name="X")

    async def test_delete_prompt(self, prompt_service: PromptService):
        created = await prompt_service.create_prompt(name="Del", content="C")
        result = await prompt_service.delete_prompt(created.id)
        assert result is True

    async def test_delete_prompt_not_found(self, prompt_service: PromptService):
        with pytest.raises(PromptNotFoundError):
            await prompt_service.delete_prompt(uuid4())

    async def test_search_prompts(self, prompt_service: PromptService):
        await prompt_service.create_prompt(
            name="Security Review", content="Check for vulnerabilities"
        )
        await prompt_service.create_prompt(name="Code Style", content="Review code style")
        await prompt_service.create_prompt(name="Unrelated", content="Something else")
        results = await prompt_service.search_prompts("review")
        assert len(results) == 2

    async def test_search_case_insensitive(self, prompt_service: PromptService):
        await prompt_service.create_prompt(name="TEST Prompt", content="content")
        results = await prompt_service.search_prompts("test")
        assert len(results) == 1

    async def test_search_name_ranked_first(self, prompt_service: PromptService):
        await prompt_service.create_prompt(name="Other", content="security analysis")
        await prompt_service.create_prompt(name="Security Review", content="review code")
        results = await prompt_service.search_prompts("security")
        assert results[0].name == "Security Review"


# --- REST endpoint tests ---


class TestPromptEndpoints:
    """Tests for saved prompt REST endpoints."""

    def test_create_prompt(self, prompt_client: TestClient):
        resp = prompt_client.post(
            "/api/v1/volundr/prompts",
            json={"name": "Test", "content": "Test content"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Test"
        assert data["scope"] == "global"

    def test_list_prompts(self, prompt_client: TestClient):
        prompt_client.post(
            "/api/v1/volundr/prompts",
            json={"name": "A", "content": "A content"},
        )
        prompt_client.post(
            "/api/v1/volundr/prompts",
            json={"name": "B", "content": "B content"},
        )
        resp = prompt_client.get("/api/v1/volundr/prompts")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_list_prompts_with_scope_filter(self, prompt_client: TestClient):
        prompt_client.post(
            "/api/v1/volundr/prompts",
            json={"name": "Global", "content": "G"},
        )
        prompt_client.post(
            "/api/v1/volundr/prompts",
            json={
                "name": "Project",
                "content": "P",
                "scope": "project",
                "project_repo": "repo1",
            },
        )
        resp = prompt_client.get("/api/v1/volundr/prompts?scope=global")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_update_prompt(self, prompt_client: TestClient):
        create_resp = prompt_client.post(
            "/api/v1/volundr/prompts",
            json={"name": "Old", "content": "Old content"},
        )
        prompt_id = create_resp.json()["id"]
        resp = prompt_client.put(
            f"/api/v1/volundr/prompts/{prompt_id}",
            json={"name": "New"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "New"

    def test_update_prompt_not_found(self, prompt_client: TestClient):
        resp = prompt_client.put(
            f"/api/v1/volundr/prompts/{uuid4()}",
            json={"name": "X"},
        )
        assert resp.status_code == 404

    def test_delete_prompt(self, prompt_client: TestClient):
        create_resp = prompt_client.post(
            "/api/v1/volundr/prompts",
            json={"name": "Del", "content": "C"},
        )
        prompt_id = create_resp.json()["id"]
        resp = prompt_client.delete(f"/api/v1/volundr/prompts/{prompt_id}")
        assert resp.status_code == 204

    def test_delete_prompt_not_found(self, prompt_client: TestClient):
        resp = prompt_client.delete(f"/api/v1/volundr/prompts/{uuid4()}")
        assert resp.status_code == 404

    def test_search_prompts(self, prompt_client: TestClient):
        prompt_client.post(
            "/api/v1/volundr/prompts",
            json={"name": "Security Review", "content": "Check vulnerabilities"},
        )
        prompt_client.post(
            "/api/v1/volundr/prompts",
            json={"name": "Unrelated", "content": "Nothing"},
        )
        resp = prompt_client.get("/api/v1/volundr/prompts/search?q=security")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_search_requires_query(self, prompt_client: TestClient):
        resp = prompt_client.get("/api/v1/volundr/prompts/search")
        assert resp.status_code == 422

    def test_create_with_tags(self, prompt_client: TestClient):
        resp = prompt_client.post(
            "/api/v1/volundr/prompts",
            json={
                "name": "Tagged",
                "content": "Content",
                "tags": ["review", "security"],
            },
        )
        assert resp.status_code == 201
        assert resp.json()["tags"] == ["review", "security"]
