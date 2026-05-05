"""Tests for REST issue endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from volundr.adapters.inbound.rest_issues import (
    create_canonical_issues_router,
    create_issues_router,
)
from volundr.domain.models import (
    IntegrationConnection,
    IntegrationType,
    Principal,
    TrackerIssue,
)


def _mock_identity(principal: Principal | None = None):
    identity = AsyncMock()
    if principal is None:
        principal = Principal(
            user_id="u1",
            email="user@test.com",
            tenant_id="t1",
            roles=["volundr:admin"],
        )
    identity.validate_token.return_value = principal
    return identity


def _make_connection(
    conn_id: str = "conn-1",
    enabled: bool = True,
) -> IntegrationConnection:
    now = datetime.now(UTC)
    return IntegrationConnection(
        id=conn_id,
        owner_id="u1",
        integration_type=IntegrationType.ISSUE_TRACKER,
        adapter="volundr.adapters.linear.LinearProvider",
        credential_name="linear-token",
        config={},
        enabled=enabled,
        created_at=now,
        updated_at=now,
        slug="linear",
    )


def _make_issue(
    issue_id: str = "issue-1",
    title: str = "Fix the bug",
    status: str = "Todo",
) -> TrackerIssue:
    return TrackerIssue(
        id=issue_id,
        identifier="NIU-42",
        title=title,
        status=status,
        assignee="Alice",
        labels=["bug"],
        priority=1,
        url="https://linear.app/team/NIU-42",
    )


def _make_app(identity=None) -> tuple[FastAPI, AsyncMock, AsyncMock]:
    integration_repo = AsyncMock()
    tracker_factory = AsyncMock()

    app = FastAPI()
    app.state.identity = identity or _mock_identity()
    app.state.legacy_route_hits = {}
    app.include_router(create_canonical_issues_router(integration_repo, tracker_factory))
    router = create_issues_router(
        integration_repo=integration_repo,
        tracker_factory=tracker_factory,
    )
    app.include_router(router)
    return app, integration_repo, tracker_factory


AUTH = {"Authorization": "Bearer tok"}
PREFIX = "/api/v1/volundr/issues"
CANONICAL_PREFIX = "/api/v1/tracker"


class TestSearchIssues:
    def test_returns_empty_when_no_connections(self):
        app, integration_repo, _ = _make_app()
        integration_repo.list_connections.return_value = []
        client = TestClient(app)

        resp = client.get(f"{PREFIX}/search?q=bug", headers=AUTH)

        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_issues_from_connected_tracker(self):
        app, integration_repo, tracker_factory = _make_app()
        connection = _make_connection()
        integration_repo.list_connections.return_value = [connection]

        mock_adapter = AsyncMock()
        issue = _make_issue()
        mock_adapter.search_issues.return_value = [issue]
        tracker_factory.create.return_value = mock_adapter
        client = TestClient(app)

        resp = client.get(f"{PREFIX}/search?q=bug", headers=AUTH)

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "issue-1"
        assert data[0]["title"] == "Fix the bug"
        assert data[0]["identifier"] == "NIU-42"

    def test_skips_disabled_connections(self):
        app, integration_repo, tracker_factory = _make_app()
        connection = _make_connection(enabled=False)
        integration_repo.list_connections.return_value = [connection]
        client = TestClient(app)

        resp = client.get(f"{PREFIX}/search?q=test", headers=AUTH)

        assert resp.status_code == 200
        assert resp.json() == []
        tracker_factory.create.assert_not_called()

    def test_aggregates_from_multiple_connections(self):
        app, integration_repo, tracker_factory = _make_app()
        conn1 = _make_connection(conn_id="conn-1")
        conn2 = _make_connection(conn_id="conn-2")
        integration_repo.list_connections.return_value = [conn1, conn2]

        adapter1 = AsyncMock()
        adapter1.search_issues.return_value = [_make_issue(issue_id="i1")]
        adapter2 = AsyncMock()
        adapter2.search_issues.return_value = [_make_issue(issue_id="i2")]
        tracker_factory.create.side_effect = [adapter1, adapter2]
        client = TestClient(app)

        resp = client.get(f"{PREFIX}/search?q=bug", headers=AUTH)

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    def test_search_continues_on_adapter_error(self):
        app, integration_repo, tracker_factory = _make_app()
        conn1 = _make_connection(conn_id="conn-1")
        conn2 = _make_connection(conn_id="conn-2")
        integration_repo.list_connections.return_value = [conn1, conn2]

        adapter1 = AsyncMock()
        adapter1.search_issues.side_effect = Exception("network error")
        adapter2 = AsyncMock()
        adapter2.search_issues.return_value = [_make_issue(issue_id="i2")]
        tracker_factory.create.side_effect = [adapter1, adapter2]
        client = TestClient(app)

        resp = client.get(f"{PREFIX}/search?q=bug", headers=AUTH)

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "i2"

    def test_canonical_search_matches_legacy(self):
        app, integration_repo, tracker_factory = _make_app()
        connection = _make_connection()
        integration_repo.list_connections.return_value = [connection]

        mock_adapter = AsyncMock()
        mock_adapter.search_issues.return_value = [_make_issue()]
        tracker_factory.create.return_value = mock_adapter
        client = TestClient(app)

        legacy = client.get(f"{PREFIX}/search?q=bug", headers=AUTH)
        canonical = client.get(f"{CANONICAL_PREFIX}/issues?q=bug", headers=AUTH)

        assert legacy.status_code == 200
        assert canonical.status_code == 200
        assert canonical.json() == legacy.json()
        assert legacy.headers["X-Niuu-Canonical-Route"] == f"{CANONICAL_PREFIX}/issues"


class TestGetIssue:
    def test_returns_issue(self):
        app, integration_repo, tracker_factory = _make_app()
        connection = _make_connection()
        integration_repo.list_connections.return_value = [connection]

        mock_adapter = AsyncMock()
        issue = _make_issue(issue_id="issue-99")
        mock_adapter.get_issue.return_value = issue
        tracker_factory.create.return_value = mock_adapter
        client = TestClient(app)

        resp = client.get(f"{PREFIX}/issue-99", headers=AUTH)

        assert resp.status_code == 200
        assert resp.json()["id"] == "issue-99"

    def test_returns_404_when_not_found(self):
        app, integration_repo, tracker_factory = _make_app()
        connection = _make_connection()
        integration_repo.list_connections.return_value = [connection]

        mock_adapter = AsyncMock()
        mock_adapter.get_issue.return_value = None
        tracker_factory.create.return_value = mock_adapter
        client = TestClient(app)

        resp = client.get(f"{PREFIX}/nonexistent", headers=AUTH)

        assert resp.status_code == 404
        assert "Issue not found" in resp.json()["detail"]

    def test_returns_404_when_no_connections(self):
        app, integration_repo, _ = _make_app()
        integration_repo.list_connections.return_value = []
        client = TestClient(app)

        resp = client.get(f"{PREFIX}/any-id", headers=AUTH)

        assert resp.status_code == 404

    def test_canonical_get_matches_legacy(self):
        app, integration_repo, tracker_factory = _make_app()
        connection = _make_connection()
        integration_repo.list_connections.return_value = [connection]

        mock_adapter = AsyncMock()
        issue = _make_issue(issue_id="issue-99")
        mock_adapter.get_issue.return_value = issue
        tracker_factory.create.return_value = mock_adapter
        client = TestClient(app)

        legacy = client.get(f"{PREFIX}/issue-99", headers=AUTH)
        canonical = client.get(f"{CANONICAL_PREFIX}/issues/issue-99", headers=AUTH)

        assert legacy.status_code == 200
        assert canonical.status_code == 200
        assert canonical.json() == legacy.json()


class TestUpdateIssueStatus:
    def test_updates_and_returns_issue(self):
        app, integration_repo, tracker_factory = _make_app()
        connection = _make_connection()
        integration_repo.list_connections.return_value = [connection]

        mock_adapter = AsyncMock()
        original = _make_issue(issue_id="issue-1", status="Todo")
        updated = _make_issue(issue_id="issue-1", status="In Progress")
        mock_adapter.get_issue.return_value = original
        mock_adapter.update_issue_status.return_value = updated
        tracker_factory.create.return_value = mock_adapter
        client = TestClient(app)

        resp = client.post(
            f"{PREFIX}/issue-1/status",
            json={"status": "In Progress"},
            headers=AUTH,
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "In Progress"
        mock_adapter.update_issue_status.assert_called_once_with("issue-1", "In Progress")

    def test_canonical_patch_matches_legacy(self):
        app, integration_repo, tracker_factory = _make_app()
        connection = _make_connection()
        integration_repo.list_connections.return_value = [connection]

        mock_adapter = AsyncMock()
        original = _make_issue(issue_id="issue-1", status="Todo")
        updated = _make_issue(issue_id="issue-1", status="Done")
        mock_adapter.get_issue.return_value = original
        mock_adapter.update_issue_status.return_value = updated
        tracker_factory.create.return_value = mock_adapter
        client = TestClient(app)

        legacy = client.post(f"{PREFIX}/issue-1/status", json={"status": "Done"}, headers=AUTH)
        canonical = client.patch(
            f"{CANONICAL_PREFIX}/issues/issue-1",
            json={"status": "Done"},
            headers=AUTH,
        )

        assert legacy.status_code == 200
        assert canonical.status_code == 200
        assert canonical.json() == legacy.json()

    def test_returns_404_when_issue_not_found(self):
        app, integration_repo, tracker_factory = _make_app()
        connection = _make_connection()
        integration_repo.list_connections.return_value = [connection]

        mock_adapter = AsyncMock()
        mock_adapter.get_issue.return_value = None
        tracker_factory.create.return_value = mock_adapter
        client = TestClient(app)

        resp = client.post(
            f"{PREFIX}/missing/status",
            json={"status": "Done"},
            headers=AUTH,
        )

        assert resp.status_code == 404

    def test_rejects_empty_status(self):
        app, _, _ = _make_app()
        client = TestClient(app)

        resp = client.post(
            f"{PREFIX}/issue-1/status",
            json={"status": ""},
            headers=AUTH,
        )

        assert resp.status_code == 422
