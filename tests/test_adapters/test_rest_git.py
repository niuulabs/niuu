"""Tests for git workflow REST endpoints."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from volundr.adapters.inbound.rest_git import create_git_router
from volundr.domain.models import (
    CIStatus,
    GitProviderType,
    MergeConfidence,
    PullRequest,
    PullRequestStatus,
)
from volundr.domain.services.git_workflow import (
    GitWorkflowService,
    SessionNotFoundError,
)


def _make_pr(
    number: int = 42,
    repo_url: str = "https://github.com/user/repo",
    status: PullRequestStatus = PullRequestStatus.OPEN,
) -> PullRequest:
    return PullRequest(
        number=number,
        title=f"PR #{number}",
        url=f"{repo_url}/pull/{number}",
        repo_url=repo_url,
        provider=GitProviderType.GITHUB,
        source_branch="feature/test",
        target_branch="main",
        status=status,
        description="Test PR",
        created_at=datetime(2025, 1, 1),
        updated_at=datetime(2025, 1, 1),
    )


@pytest.fixture
def mock_service() -> MagicMock:
    """Create a mock GitWorkflowService."""
    service = MagicMock(spec=GitWorkflowService)
    service.create_pr_from_session = AsyncMock()
    service.get_pr = AsyncMock()
    service.list_prs = AsyncMock()
    service.merge_pr = AsyncMock()
    service.get_ci_status = AsyncMock()
    service.calculate_confidence = MagicMock()
    return service


@pytest.fixture
def client(mock_service: MagicMock) -> TestClient:
    """Create a test client with git workflow routes."""
    app = FastAPI()
    router = create_git_router(mock_service)
    app.include_router(router)
    return TestClient(app)


class TestCreatePR:
    """Tests for POST /api/v1/forge/repos/prs."""

    def test_create_pr_success(self, client: TestClient, mock_service: MagicMock):
        """Creates a PR and returns 201."""
        pr = _make_pr()
        mock_service.create_pr_from_session.return_value = pr

        resp = client.post(
            "/api/v1/forge/repos/prs",
            json={"session_id": str(uuid4()), "target_branch": "main"},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["number"] == 42
        assert data["status"] == "open"
        assert data["provider"] == "github"

    def test_create_pr_session_not_found(self, client: TestClient, mock_service: MagicMock):
        """Returns 404 when session not found."""
        mock_service.create_pr_from_session.side_effect = SessionNotFoundError("not found")

        resp = client.post(
            "/api/v1/forge/repos/prs",
            json={"session_id": str(uuid4())},
        )

        assert resp.status_code == 404

    def test_create_pr_no_repo(self, client: TestClient, mock_service: MagicMock):
        """Returns 400 when session has no repo."""
        mock_service.create_pr_from_session.side_effect = ValueError("no repository")

        resp = client.post(
            "/api/v1/forge/repos/prs",
            json={"session_id": str(uuid4())},
        )

        assert resp.status_code == 400

    def test_create_pr_provider_error(self, client: TestClient, mock_service: MagicMock):
        """Returns 502 when provider fails."""
        mock_service.create_pr_from_session.side_effect = RuntimeError("API error")

        resp = client.post(
            "/api/v1/forge/repos/prs",
            json={"session_id": str(uuid4())},
        )

        assert resp.status_code == 502


class TestListPRs:
    """Tests for GET /api/v1/forge/repos/prs."""

    def test_list_prs(self, client: TestClient, mock_service: MagicMock):
        """Lists PRs for a repo."""
        mock_service.list_prs.return_value = [_make_pr(1), _make_pr(2)]

        resp = client.get(
            "/api/v1/forge/repos/prs",
            params={"repo_url": "https://github.com/user/repo"},
        )

        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_list_prs_with_status_filter(self, client: TestClient, mock_service: MagicMock):
        """Passes status filter to service."""
        mock_service.list_prs.return_value = []

        resp = client.get(
            "/api/v1/forge/repos/prs",
            params={
                "repo_url": "https://github.com/user/repo",
                "status": "closed",
            },
        )

        assert resp.status_code == 200
        mock_service.list_prs.assert_called_once_with("https://github.com/user/repo", "closed")

    def test_list_prs_missing_repo_url(self, client: TestClient, mock_service: MagicMock):
        """Returns 422 when repo_url is missing."""
        resp = client.get("/api/v1/forge/repos/prs")
        assert resp.status_code == 422


class TestGetPR:
    """Tests for GET /api/v1/forge/repos/prs/{pr_number}."""

    def test_get_pr(self, client: TestClient, mock_service: MagicMock):
        """Gets a PR by number."""
        mock_service.get_pr.return_value = _make_pr(42)

        resp = client.get(
            "/api/v1/forge/repos/prs/42",
            params={"repo_url": "https://github.com/user/repo"},
        )

        assert resp.status_code == 200
        assert resp.json()["number"] == 42

    def test_get_pr_not_found(self, client: TestClient, mock_service: MagicMock):
        """Returns 404 when PR not found."""
        mock_service.get_pr.return_value = None

        resp = client.get(
            "/api/v1/forge/repos/prs/999",
            params={"repo_url": "https://github.com/user/repo"},
        )

        assert resp.status_code == 404


class TestMergePR:
    """Tests for POST /api/v1/forge/repos/prs/{pr_number}/merge."""

    def test_merge_pr_success(self, client: TestClient, mock_service: MagicMock):
        """Merges a PR successfully."""
        mock_service.merge_pr.return_value = True

        resp = client.post(
            "/api/v1/forge/repos/prs/42/merge",
            params={"repo_url": "https://github.com/user/repo"},
            json={"merge_method": "squash"},
        )

        assert resp.status_code == 200
        assert resp.json()["merged"] is True

    def test_merge_pr_conflict(self, client: TestClient, mock_service: MagicMock):
        """Returns 409 when merge fails."""
        mock_service.merge_pr.return_value = False

        resp = client.post(
            "/api/v1/forge/repos/prs/42/merge",
            params={"repo_url": "https://github.com/user/repo"},
            json={"merge_method": "squash"},
        )

        assert resp.status_code == 409


class TestCIStatus:
    """Tests for GET /api/v1/forge/repos/prs/{pr_number}/ci."""

    def test_get_ci_status(self, client: TestClient, mock_service: MagicMock):
        """Gets CI status for a branch."""
        mock_service.get_ci_status.return_value = CIStatus.PASSING

        resp = client.get(
            "/api/v1/forge/repos/prs/42/ci",
            params={
                "repo_url": "https://github.com/user/repo",
                "branch": "feature/test",
            },
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "passing"


class TestConfidence:
    """Tests for POST /api/v1/forge/repos/confidence."""

    def test_calculate_confidence(self, client: TestClient, mock_service: MagicMock):
        """Calculates merge confidence."""
        mock_service.calculate_confidence.return_value = MergeConfidence(
            score=0.95,
            factors={"tests": 1.0, "size": 1.0},
            action="auto_merge",
            reason="Low-risk change",
        )

        resp = client.post(
            "/api/v1/forge/repos/confidence",
            json={
                "tests_pass": True,
                "coverage_delta": 0.0,
                "lines_changed": 10,
                "files_changed": 1,
                "has_dependency_changes": False,
                "change_categories": ["docs"],
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["score"] == 0.95
        assert data["action"] == "auto_merge"
