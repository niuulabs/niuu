"""Tests for issue tracker integration."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from volundr.adapters.inbound.rest_tracker import (
    create_canonical_tracker_router,
    create_tracker_router,
)
from volundr.domain.models import ProjectMapping, TrackerConnectionStatus, TrackerIssue
from volundr.domain.ports import IssueTrackerProvider, ProjectMappingRepository
from volundr.domain.services.tracker import (
    TrackerIssueNotFoundError,
    TrackerMappingNotFoundError,
    TrackerService,
)

# --- In-memory implementations for testing ---


class InMemoryIssueTracker(IssueTrackerProvider):
    """In-memory issue tracker for testing."""

    def __init__(self):
        self._issues: dict[str, TrackerIssue] = {}
        self._connected: bool = True

    @property
    def provider_name(self) -> str:
        return "test"

    def add_issue(self, issue: TrackerIssue) -> None:
        """Add an issue to the in-memory store."""
        self._issues[issue.id] = issue

    async def check_connection(self) -> TrackerConnectionStatus:
        return TrackerConnectionStatus(
            connected=self._connected,
            provider="test",
            workspace="Test Workspace",
            user="Test User",
        )

    async def search_issues(
        self,
        query: str,
        project_id: str | None = None,
    ) -> list[TrackerIssue]:
        results = []
        query_lower = query.lower()
        for issue in self._issues.values():
            if query_lower in issue.title.lower():
                results.append(issue)
            elif query_lower in issue.identifier.lower():
                results.append(issue)
        return results

    async def get_recent_issues(
        self,
        project_id: str,
        limit: int = 10,
    ) -> list[TrackerIssue]:
        return list(self._issues.values())[:limit]

    async def get_issue(self, issue_id: str) -> TrackerIssue | None:
        return self._issues.get(issue_id)

    async def update_issue_status(
        self,
        issue_id: str,
        status: str,
    ) -> TrackerIssue:
        issue = self._issues.get(issue_id)
        if issue is None:
            raise TrackerIssueNotFoundError(f"Issue not found: {issue_id}")
        updated = issue.model_copy(update={"status": status})
        self._issues[issue_id] = updated
        return updated


class InMemoryMappingRepository(ProjectMappingRepository):
    """In-memory project mapping repository for testing."""

    def __init__(self):
        self._mappings: dict[UUID, ProjectMapping] = {}

    async def create(self, mapping: ProjectMapping) -> ProjectMapping:
        self._mappings[mapping.id] = mapping
        return mapping

    async def list(self) -> list[ProjectMapping]:
        return sorted(
            self._mappings.values(),
            key=lambda m: m.created_at,
            reverse=True,
        )

    async def get_by_repo(self, repo_url: str) -> ProjectMapping | None:
        for m in self._mappings.values():
            if m.repo_url == repo_url:
                return m
        return None

    async def delete(self, mapping_id: UUID) -> bool:
        if mapping_id in self._mappings:
            del self._mappings[mapping_id]
            return True
        return False


# --- Fixtures ---

SAMPLE_ISSUE = TrackerIssue(
    id="issue-1",
    identifier="NIU-57",
    title="Add Linear integration",
    status="In Progress",
    assignee="Test User",
    labels=["feature", "backend"],
    priority=2,
    url="https://linear.app/niuu/issue/NIU-57",
)

SAMPLE_ISSUE_2 = TrackerIssue(
    id="issue-2",
    identifier="NIU-58",
    title="Fix dashboard bug",
    status="Todo",
    assignee=None,
    labels=["bug"],
    priority=1,
    url="https://linear.app/niuu/issue/NIU-58",
)


@pytest.fixture
def tracker() -> InMemoryIssueTracker:
    t = InMemoryIssueTracker()
    t.add_issue(SAMPLE_ISSUE)
    t.add_issue(SAMPLE_ISSUE_2)
    return t


@pytest.fixture
def mapping_repo() -> InMemoryMappingRepository:
    return InMemoryMappingRepository()


@pytest.fixture
def tracker_service(
    tracker: InMemoryIssueTracker,
    mapping_repo: InMemoryMappingRepository,
) -> TrackerService:
    return TrackerService(tracker, mapping_repo)


@pytest.fixture
def tracker_client(tracker_service: TrackerService) -> TestClient:
    app = FastAPI()
    app.state.legacy_route_hits = {}
    app.include_router(create_canonical_tracker_router(tracker_service))
    router = create_tracker_router(tracker_service)
    app.include_router(router)
    return TestClient(app)


# --- TrackerService tests ---


class TestTrackerService:
    """Tests for TrackerService."""

    async def test_check_connection(self, tracker_service: TrackerService):
        status = await tracker_service.check_connection()
        assert status.connected is True
        assert status.provider == "test"
        assert status.workspace == "Test Workspace"
        assert status.user == "Test User"

    async def test_check_connection_no_tracker(self, mapping_repo):
        service = TrackerService(None, mapping_repo)
        status = await service.check_connection()
        assert status.connected is False
        assert status.provider == "none"

    async def test_search_issues(self, tracker_service: TrackerService):
        results = await tracker_service.search_issues("Linear")
        assert len(results) == 1
        assert results[0].identifier == "NIU-57"

    async def test_search_issues_by_identifier(self, tracker_service: TrackerService):
        results = await tracker_service.search_issues("NIU-58")
        assert len(results) == 1
        assert results[0].title == "Fix dashboard bug"

    async def test_search_issues_no_results(self, tracker_service: TrackerService):
        results = await tracker_service.search_issues("nonexistent")
        assert len(results) == 0

    async def test_search_issues_with_project_id(self, tracker_service: TrackerService):
        results = await tracker_service.search_issues("Linear", project_id="proj-1")
        assert len(results) == 1

    async def test_get_recent_issues(self, tracker_service: TrackerService):
        results = await tracker_service.get_recent_issues("proj-1")
        assert len(results) == 2

    async def test_get_recent_issues_with_limit(self, tracker_service: TrackerService):
        results = await tracker_service.get_recent_issues("proj-1", limit=1)
        assert len(results) == 1

    async def test_get_issue(self, tracker_service: TrackerService):
        issue = await tracker_service.get_issue("issue-1")
        assert issue.identifier == "NIU-57"
        assert issue.title == "Add Linear integration"

    async def test_get_issue_not_found(self, tracker_service: TrackerService):
        with pytest.raises(TrackerIssueNotFoundError):
            await tracker_service.get_issue("nonexistent")

    async def test_update_issue_status(self, tracker_service: TrackerService):
        updated = await tracker_service.update_issue_status("issue-1", "Done")
        assert updated.status == "Done"
        assert updated.identifier == "NIU-57"

    async def test_update_issue_status_not_found(self, tracker_service: TrackerService):
        with pytest.raises(TrackerIssueNotFoundError):
            await tracker_service.update_issue_status("nonexistent", "Done")


class TestTrackerServiceMappings:
    """Tests for TrackerService mapping operations."""

    async def test_create_mapping(self, tracker_service: TrackerService):
        mapping = await tracker_service.create_mapping(
            repo_url="https://github.com/niuulabs/volundr",
            project_id="proj-1",
            project_name="Volundr",
        )
        assert mapping.repo_url == "https://github.com/niuulabs/volundr"
        assert mapping.project_id == "proj-1"
        assert mapping.project_name == "Volundr"

    async def test_list_mappings(self, tracker_service: TrackerService):
        await tracker_service.create_mapping(
            repo_url="https://github.com/niuulabs/volundr",
            project_id="proj-1",
        )
        await tracker_service.create_mapping(
            repo_url="https://github.com/niuu/buri",
            project_id="proj-2",
        )
        mappings = await tracker_service.list_mappings()
        assert len(mappings) == 2

    async def test_get_mapping_by_repo(self, tracker_service: TrackerService):
        await tracker_service.create_mapping(
            repo_url="https://github.com/niuulabs/volundr",
            project_id="proj-1",
        )
        mapping = await tracker_service.get_mapping_by_repo("https://github.com/niuulabs/volundr")
        assert mapping is not None
        assert mapping.project_id == "proj-1"

    async def test_get_mapping_by_repo_not_found(self, tracker_service: TrackerService):
        mapping = await tracker_service.get_mapping_by_repo("nonexistent")
        assert mapping is None

    async def test_delete_mapping(self, tracker_service: TrackerService):
        mapping = await tracker_service.create_mapping(
            repo_url="https://github.com/niuulabs/volundr",
            project_id="proj-1",
        )
        result = await tracker_service.delete_mapping(mapping.id)
        assert result is True
        mappings = await tracker_service.list_mappings()
        assert len(mappings) == 0

    async def test_delete_mapping_not_found(self, tracker_service: TrackerService):
        with pytest.raises(TrackerMappingNotFoundError):
            await tracker_service.delete_mapping(uuid4())


# --- REST endpoint tests ---


class TestTrackerEndpoints:
    """Tests for issue tracker REST endpoints."""

    def test_get_status(self, tracker_client: TestClient):
        response = tracker_client.get("/api/v1/volundr/tracker/status")
        assert response.status_code == 200
        data = response.json()
        assert data["connected"] is True
        assert data["provider"] == "test"
        assert data["workspace"] == "Test Workspace"
        assert response.headers["X-Niuu-Canonical-Route"] == "/api/v1/tracker/status"

    def test_canonical_get_status(self, tracker_client: TestClient):
        response = tracker_client.get("/api/v1/tracker/status")
        assert response.status_code == 200
        assert response.json()["connected"] is True

    def test_search_issues(self, tracker_client: TestClient):
        response = tracker_client.get("/api/v1/volundr/tracker/issues", params={"q": "Linear"})
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["identifier"] == "NIU-57"

    def test_search_issues_with_project_id(self, tracker_client: TestClient):
        response = tracker_client.get(
            "/api/v1/volundr/tracker/issues",
            params={"q": "Linear", "project_id": "proj-1"},
        )
        assert response.status_code == 200

    def test_search_issues_missing_query(self, tracker_client: TestClient):
        response = tracker_client.get("/api/v1/volundr/tracker/issues")
        assert response.status_code == 422

    def test_get_recent_issues(self, tracker_client: TestClient):
        response = tracker_client.get(
            "/api/v1/volundr/tracker/issues/recent",
            params={"project_id": "proj-1"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    def test_get_recent_issues_with_limit(self, tracker_client: TestClient):
        response = tracker_client.get(
            "/api/v1/volundr/tracker/issues/recent",
            params={"project_id": "proj-1", "limit": 1},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1

    def test_get_recent_issues_missing_project_id(self, tracker_client: TestClient):
        response = tracker_client.get("/api/v1/volundr/tracker/issues/recent")
        assert response.status_code == 422

    def test_update_issue_status(self, tracker_client: TestClient):
        response = tracker_client.patch(
            "/api/v1/volundr/tracker/issues/issue-1",
            json={"status": "Done"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "Done"
        assert data["identifier"] == "NIU-57"

    def test_update_issue_not_found(self, tracker_client: TestClient):
        response = tracker_client.patch(
            "/api/v1/volundr/tracker/issues/nonexistent",
            json={"status": "Done"},
        )
        assert response.status_code == 404

    def test_list_mappings_empty(self, tracker_client: TestClient):
        response = tracker_client.get("/api/v1/volundr/tracker/mappings")
        assert response.status_code == 200
        assert response.json() == []
        assert response.headers["X-Niuu-Canonical-Route"] == "/api/v1/tracker/repo-mappings"

    def test_canonical_repo_mappings_empty(self, tracker_client: TestClient):
        response = tracker_client.get("/api/v1/tracker/repo-mappings")
        assert response.status_code == 200
        assert response.json() == []

    def test_create_mapping(self, tracker_client: TestClient):
        response = tracker_client.post(
            "/api/v1/volundr/tracker/mappings",
            json={
                "repo_url": "https://github.com/niuulabs/volundr",
                "project_id": "proj-1",
                "project_name": "Volundr",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["repo_url"] == "https://github.com/niuulabs/volundr"
        assert data["project_id"] == "proj-1"
        assert data["project_name"] == "Volundr"
        assert "id" in data
        assert "created_at" in data

    def test_create_and_list_mappings(self, tracker_client: TestClient):
        tracker_client.post(
            "/api/v1/volundr/tracker/mappings",
            json={
                "repo_url": "https://github.com/niuulabs/volundr",
                "project_id": "proj-1",
            },
        )
        response = tracker_client.get("/api/v1/volundr/tracker/mappings")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1

    def test_canonical_create_and_list_repo_mappings(self, tracker_client: TestClient):
        create_response = tracker_client.post(
            "/api/v1/tracker/repo-mappings",
            json={
                "repo_url": "https://github.com/niuulabs/volundr",
                "project_id": "proj-1",
            },
        )
        assert create_response.status_code == 201

        response = tracker_client.get("/api/v1/tracker/repo-mappings")
        assert response.status_code == 200
        assert len(response.json()) == 1

    def test_delete_mapping(self, tracker_client: TestClient):
        create_response = tracker_client.post(
            "/api/v1/volundr/tracker/mappings",
            json={
                "repo_url": "https://github.com/niuulabs/volundr",
                "project_id": "proj-1",
            },
        )
        mapping_id = create_response.json()["id"]
        response = tracker_client.delete(f"/api/v1/volundr/tracker/mappings/{mapping_id}")
        assert response.status_code == 204

    def test_delete_mapping_not_found(self, tracker_client: TestClient):
        random_id = uuid4()
        response = tracker_client.delete(f"/api/v1/volundr/tracker/mappings/{random_id}")
        assert response.status_code == 404

    def test_create_mapping_validation(self, tracker_client: TestClient):
        response = tracker_client.post(
            "/api/v1/volundr/tracker/mappings",
            json={"repo_url": "", "project_id": "proj-1"},
        )
        assert response.status_code == 422

    def test_issue_response_fields(self, tracker_client: TestClient):
        response = tracker_client.get("/api/v1/volundr/tracker/issues", params={"q": "NIU-57"})
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        issue = data[0]
        assert issue["id"] == "issue-1"
        assert issue["identifier"] == "NIU-57"
        assert issue["title"] == "Add Linear integration"
        assert issue["status"] == "In Progress"
        assert issue["assignee"] == "Test User"
        assert issue["labels"] == ["feature", "backend"]
        assert issue["priority"] == 2
        assert "url" in issue


# --- Model tests ---


class TestTrackerModels:
    """Tests for tracker domain models."""

    def test_tracker_issue_defaults(self):
        issue = TrackerIssue(
            id="1",
            identifier="TEST-1",
            title="Test",
            status="Open",
            url="https://example.com",
        )
        assert issue.assignee is None
        assert issue.labels == []
        assert issue.priority == 0

    def test_project_mapping_defaults(self):
        mapping = ProjectMapping(
            repo_url="https://github.com/org/repo",
            project_id="proj-1",
        )
        assert mapping.project_name == ""
        assert mapping.id is not None
        assert mapping.created_at is not None

    def test_tracker_connection_status(self):
        status = TrackerConnectionStatus(
            connected=False,
            provider="linear",
        )
        assert status.connected is False
        assert status.workspace is None
        assert status.user is None


# --- Linear adapter unit tests (no network) ---


class TestLinearAdapterCache:
    """Tests for LinearAdapter caching (unit-level, no network)."""

    def test_cache_entry_expired(self):
        from volundr.adapters.outbound.linear import _CacheEntry

        entry = _CacheEntry("value", ttl=-1.0)
        assert entry.expired is True

    def test_cache_entry_not_expired(self):
        from volundr.adapters.outbound.linear import _CacheEntry

        entry = _CacheEntry("value", ttl=300.0)
        assert entry.expired is False

    def test_linear_adapter_provider_name(self):
        from volundr.adapters.outbound.linear import LinearAdapter

        adapter = LinearAdapter(api_key="test-key")
        assert adapter.provider_name == "linear"

    def test_node_to_issue(self):
        from volundr.adapters.outbound.linear import LinearAdapter

        node = {
            "id": "abc123",
            "identifier": "NIU-99",
            "title": "Test issue",
            "state": {"name": "In Progress"},
            "assignee": {"name": "John"},
            "labels": {"nodes": [{"name": "bug"}, {"name": "p1"}]},
            "priority": 1,
            "url": "https://linear.app/issue/NIU-99",
        }
        issue = LinearAdapter._node_to_issue(node)
        assert issue.id == "abc123"
        assert issue.identifier == "NIU-99"
        assert issue.title == "Test issue"
        assert issue.status == "In Progress"
        assert issue.assignee == "John"
        assert issue.labels == ["bug", "p1"]
        assert issue.priority == 1

    def test_node_to_issue_missing_fields(self):
        from volundr.adapters.outbound.linear import LinearAdapter

        node = {
            "id": "abc123",
            "identifier": "NIU-99",
            "title": "Test issue",
            "state": {},
            "assignee": None,
            "labels": None,
            "priority": 0,
            "url": "",
        }
        issue = LinearAdapter._node_to_issue(node)
        assert issue.status == "Unknown"
        assert issue.assignee is None
        assert issue.labels == []

    def test_linear_adapter_cache_set_get(self):
        from volundr.adapters.outbound.linear import LinearAdapter

        adapter = LinearAdapter(api_key="test-key")
        adapter._set_cached("test-key", "test-value", ttl=300.0)
        assert adapter._get_cached("test-key") == "test-value"

    def test_linear_adapter_cache_expired(self):
        from volundr.adapters.outbound.linear import LinearAdapter

        adapter = LinearAdapter(api_key="test-key")
        adapter._set_cached("test-key", "test-value", ttl=-1.0)
        assert adapter._get_cached("test-key") is None

    def test_linear_adapter_cache_miss(self):
        from volundr.adapters.outbound.linear import LinearAdapter

        adapter = LinearAdapter(api_key="test-key")
        assert adapter._get_cached("nonexistent") is None
