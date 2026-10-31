"""Tests for canonical tracker issue endpoints."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tracker.ports import TrackerRequestError
from volundr.adapters.inbound.rest_issues import create_issues_router
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
    app.include_router(
        create_issues_router(
            integration_repo=integration_repo,
            tracker_factory=tracker_factory,
        )
    )
    return app, integration_repo, tracker_factory


AUTH = {"Authorization": "Bearer tok"}
PREFIX = "/api/v1/tracker"


class TestSearchIssues:
    def test_returns_empty_when_no_connections(self):
        app, integration_repo, _ = _make_app()
        integration_repo.list_connections.return_value = []
        client = TestClient(app)

        resp = client.get(f"{PREFIX}/issues?q=bug", headers=AUTH)

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

        resp = client.get(f"{PREFIX}/issues?q=bug", headers=AUTH)

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

        resp = client.get(f"{PREFIX}/issues?q=test", headers=AUTH)

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

        resp = client.get(f"{PREFIX}/issues?q=bug", headers=AUTH)

        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_search_fails_with_502_naming_the_broken_connection(self):
        app, integration_repo, tracker_factory = _make_app()
        conn1 = _make_connection(conn_id="conn-1")
        conn2 = _make_connection(conn_id="conn-2")
        integration_repo.list_connections.return_value = [conn1, conn2]

        adapter1 = AsyncMock()
        adapter1.search_issues.side_effect = RuntimeError("network error")
        adapter2 = AsyncMock()
        adapter2.search_issues.return_value = [_make_issue(issue_id="i2")]
        tracker_factory.create.side_effect = [adapter1, adapter2]
        client = TestClient(app)

        resp = client.get(f"{PREFIX}/issues?q=bug", headers=AUTH)

        assert resp.status_code == 502
        assert resp.json()["detail"] == (
            "Issue tracker connection conn-1 failed to search issues: RuntimeError: network error"
        )
        adapter2.search_issues.assert_not_called()

    def test_search_fails_with_502_when_adapter_cannot_be_built(self):
        app, integration_repo, tracker_factory = _make_app()
        integration_repo.list_connections.return_value = [_make_connection()]
        tracker_factory.create.side_effect = ValueError("credential linear-token missing")
        client = TestClient(app)

        resp = client.get(f"{PREFIX}/issues?q=bug", headers=AUTH)

        assert resp.status_code == 502
        assert "conn-1" in resp.json()["detail"]
        assert "credential linear-token missing" in resp.json()["detail"]

    def test_search_skips_non_volundr_adapters(self):
        app, integration_repo, tracker_factory = _make_app()
        connection = replace(
            _make_connection(), adapter="ting.adapters.linear.LinearTrackerAdapter"
        )
        integration_repo.list_connections.return_value = [connection]
        client = TestClient(app)

        resp = client.get(f"{PREFIX}/issues?q=bug", headers=AUTH)

        assert resp.status_code == 200
        assert resp.json() == []
        tracker_factory.create.assert_not_called()


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

        resp = client.get(f"{PREFIX}/issues/issue-99", headers=AUTH)

        assert resp.status_code == 200
        assert resp.json()["id"] == "issue-99"

    def test_returns_issue_description(self):
        app, integration_repo, tracker_factory = _make_app()
        integration_repo.list_connections.return_value = [_make_connection()]

        mock_adapter = AsyncMock()
        issue = _make_issue(issue_id="issue-99")
        issue.description = "## Acceptance criteria\n- [ ] works"
        mock_adapter.get_issue.return_value = issue
        tracker_factory.create.return_value = mock_adapter
        client = TestClient(app)

        resp = client.get(f"{PREFIX}/issues/NIU-1092", headers=AUTH)

        assert resp.status_code == 200
        assert resp.json()["description"] == "## Acceptance criteria\n- [ ] works"

    def test_checks_next_connection_when_first_has_no_match(self):
        app, integration_repo, tracker_factory = _make_app()
        integration_repo.list_connections.return_value = [
            _make_connection(conn_id="conn-1", enabled=False),
            _make_connection(conn_id="conn-2"),
            _make_connection(conn_id="conn-3"),
        ]
        adapter2 = AsyncMock()
        adapter2.get_issue.return_value = None
        adapter3 = AsyncMock()
        adapter3.get_issue.return_value = _make_issue(issue_id="issue-3")
        tracker_factory.create.side_effect = [adapter2, adapter3]
        client = TestClient(app)

        resp = client.get(f"{PREFIX}/issues/issue-3", headers=AUTH)

        assert resp.status_code == 200
        assert resp.json()["id"] == "issue-3"

    def test_broken_connection_fails_with_502_not_404(self):
        app, integration_repo, tracker_factory = _make_app()
        integration_repo.list_connections.return_value = [_make_connection()]

        mock_adapter = AsyncMock()
        mock_adapter.get_issue.side_effect = RuntimeError("401 Unauthorized")
        tracker_factory.create.return_value = mock_adapter
        client = TestClient(app)

        resp = client.get(f"{PREFIX}/issues/NIU-1092", headers=AUTH)

        assert resp.status_code == 502
        assert resp.json()["detail"] == (
            "Issue tracker connection conn-1 failed to get issue NIU-1092: "
            "RuntimeError: 401 Unauthorized"
        )

    def test_returns_404_when_not_found(self):
        app, integration_repo, tracker_factory = _make_app()
        connection = _make_connection()
        integration_repo.list_connections.return_value = [connection]

        mock_adapter = AsyncMock()
        mock_adapter.get_issue.return_value = None
        tracker_factory.create.return_value = mock_adapter
        client = TestClient(app)

        resp = client.get(f"{PREFIX}/issues/nonexistent", headers=AUTH)

        assert resp.status_code == 404
        assert "Issue not found" in resp.json()["detail"]

    def test_returns_404_when_no_connections(self):
        app, integration_repo, _ = _make_app()
        integration_repo.list_connections.return_value = []
        client = TestClient(app)

        resp = client.get(f"{PREFIX}/issues/any-id", headers=AUTH)

        assert resp.status_code == 404


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

        resp = client.patch(
            f"{PREFIX}/issues/issue-1",
            json={"status": "In Progress"},
            headers=AUTH,
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "In Progress"
        mock_adapter.update_issue_status.assert_called_once_with("issue-1", "In Progress")

    def test_returns_404_when_issue_not_found(self):
        app, integration_repo, tracker_factory = _make_app()
        connection = _make_connection()
        integration_repo.list_connections.return_value = [connection]

        mock_adapter = AsyncMock()
        mock_adapter.get_issue.return_value = None
        tracker_factory.create.return_value = mock_adapter
        client = TestClient(app)

        resp = client.patch(
            f"{PREFIX}/issues/missing",
            json={"status": "Done"},
            headers=AUTH,
        )

        assert resp.status_code == 404

    def test_update_failure_fails_with_502(self):
        app, integration_repo, tracker_factory = _make_app()
        integration_repo.list_connections.return_value = [_make_connection()]

        mock_adapter = AsyncMock()
        mock_adapter.get_issue.return_value = _make_issue(issue_id="issue-1")
        mock_adapter.update_issue_status.side_effect = RuntimeError("Status 'Shipped' not found")
        tracker_factory.create.return_value = mock_adapter
        client = TestClient(app)

        resp = client.patch(
            f"{PREFIX}/issues/issue-1",
            json={"status": "Shipped"},
            headers=AUTH,
        )

        assert resp.status_code == 502
        assert resp.json()["detail"] == (
            "Issue tracker connection conn-1 failed to update issue issue-1: "
            "RuntimeError: Status 'Shipped' not found"
        )

    def test_unknown_status_is_a_400_naming_the_valid_statuses(self):
        """A misspelled status is the caller's to fix, not a tracker outage."""
        from tracker.ports import TrackerRequestError

        app, integration_repo, tracker_factory = _make_app()
        integration_repo.list_connections.return_value = [_make_connection()]

        mock_adapter = AsyncMock()
        mock_adapter.get_issue.return_value = _make_issue(issue_id="issue-1")
        mock_adapter.update_issue_status.side_effect = TrackerRequestError(
            "Status 'Cancelled' not found. Available: Todo, Done, Canceled"
        )
        tracker_factory.create.return_value = mock_adapter
        client = TestClient(app)

        resp = client.patch(
            f"{PREFIX}/issues/issue-1",
            json={"status": "Cancelled"},
            headers=AUTH,
        )

        assert resp.status_code == 400
        assert resp.json()["detail"] == (
            "Issue tracker connection conn-1 cannot update issue issue-1: "
            "Status 'Cancelled' not found. Available: Todo, Done, Canceled"
        )

    def test_update_skips_disabled_foreign_and_unmatched_connections(self):
        app, integration_repo, tracker_factory = _make_app()
        integration_repo.list_connections.return_value = [
            _make_connection(conn_id="conn-1", enabled=False),
            replace(
                _make_connection(conn_id="conn-ting"),
                adapter="ting.adapters.linear.LinearTrackerAdapter",
            ),
            _make_connection(conn_id="conn-2"),
            _make_connection(conn_id="conn-3"),
        ]
        adapter2 = AsyncMock()
        adapter2.get_issue.return_value = None
        adapter3 = AsyncMock()
        adapter3.get_issue.return_value = _make_issue(issue_id="issue-1")
        adapter3.update_issue_status.return_value = _make_issue(issue_id="issue-1", status="Done")
        tracker_factory.create.side_effect = [adapter2, adapter3]
        client = TestClient(app)

        resp = client.patch(f"{PREFIX}/issues/issue-1", json={"status": "Done"}, headers=AUTH)

        assert resp.status_code == 200
        assert resp.json()["status"] == "Done"
        adapter2.update_issue_status.assert_not_called()

    def test_rejects_empty_status(self):
        app, _, _ = _make_app()
        client = TestClient(app)

        resp = client.patch(
            f"{PREFIX}/issues/issue-1",
            json={"status": ""},
            headers=AUTH,
        )

        assert resp.status_code == 422


class TestEditIssue:
    def test_rewrites_the_description_without_touching_status(self):
        app, integration_repo, tracker_factory = _make_app()
        integration_repo.list_connections.return_value = [_make_connection()]
        mock_adapter = AsyncMock()
        mock_adapter.get_issue.return_value = _make_issue(issue_id="NIU-1116")
        mock_adapter.update_issue.return_value = _make_issue(issue_id="NIU-1116")
        tracker_factory.create.return_value = mock_adapter
        client = TestClient(app)

        resp = client.patch(
            f"{PREFIX}/issues/NIU-1116",
            json={"description": "Branch from regin."},
            headers=AUTH,
        )

        assert resp.status_code == 200
        mock_adapter.update_issue.assert_called_once_with(
            "NIU-1116", title=None, description="Branch from regin."
        )
        mock_adapter.update_issue_status.assert_not_called()

    def test_edits_then_moves_status_in_one_request(self):
        app, integration_repo, tracker_factory = _make_app()
        integration_repo.list_connections.return_value = [_make_connection()]
        mock_adapter = AsyncMock()
        mock_adapter.get_issue.return_value = _make_issue(issue_id="NIU-1116", status="Todo")
        mock_adapter.update_issue_status.return_value = _make_issue(
            issue_id="NIU-1116", status="In Progress"
        )
        tracker_factory.create.return_value = mock_adapter
        client = TestClient(app)

        resp = client.patch(
            f"{PREFIX}/issues/NIU-1116",
            json={"title": "Start from regin", "status": "In Progress"},
            headers=AUTH,
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "In Progress"
        mock_adapter.update_issue.assert_called_once_with(
            "NIU-1116", title="Start from regin", description=None
        )

    def test_an_empty_edit_is_rejected(self):
        app, _, _ = _make_app()
        resp = TestClient(app).patch(f"{PREFIX}/issues/NIU-1116", json={}, headers=AUTH)
        assert resp.status_code == 422


class TestCreateIssue:
    BODY = {
        "title": "Tracker API lacks create",
        "description": "## Acceptance criteria\n- [ ] POST works",
        "team": "NIU",
        "labels": ["platform-defect"],
        "priority": 2,
    }

    def test_creates_in_the_only_connection(self):
        app, integration_repo, tracker_factory = _make_app()
        integration_repo.list_connections.return_value = [
            _make_connection(conn_id="conn-1"),
            _make_connection(conn_id="conn-off", enabled=False),
            replace(
                _make_connection(conn_id="conn-ting"),
                adapter="ting.adapters.linear.LinearTrackerAdapter",
            ),
        ]
        mock_adapter = AsyncMock()
        created = _make_issue(issue_id="new-1")
        created.description = self.BODY["description"]
        mock_adapter.create_issue.return_value = created
        tracker_factory.create.return_value = mock_adapter
        client = TestClient(app)

        resp = client.post(f"{PREFIX}/issues", json=self.BODY, headers=AUTH)

        assert resp.status_code == 201
        assert resp.json()["id"] == "new-1"
        assert resp.json()["description"] == self.BODY["description"]
        mock_adapter.create_issue.assert_awaited_once_with(
            "Tracker API lacks create",
            "## Acceptance criteria\n- [ ] POST works",
            team="NIU",
            labels=["platform-defect"],
            priority=2,
        )

    def test_omitted_labels_and_priority_pass_none(self):
        app, integration_repo, tracker_factory = _make_app()
        integration_repo.list_connections.return_value = [_make_connection()]
        mock_adapter = AsyncMock()
        mock_adapter.create_issue.return_value = _make_issue()
        tracker_factory.create.return_value = mock_adapter
        client = TestClient(app)

        body = {"title": "T", "description": "B", "team": "NIU"}
        resp = client.post(f"{PREFIX}/issues", json=body, headers=AUTH)

        assert resp.status_code == 201
        mock_adapter.create_issue.assert_awaited_once_with(
            "T", "B", team="NIU", labels=None, priority=None
        )

    def test_requires_connection_id_when_several_could_take_it(self):
        app, integration_repo, tracker_factory = _make_app()
        integration_repo.list_connections.return_value = [
            _make_connection(conn_id="conn-1"),
            _make_connection(conn_id="conn-2"),
        ]
        client = TestClient(app)

        resp = client.post(f"{PREFIX}/issues", json=self.BODY, headers=AUTH)

        assert resp.status_code == 400
        assert resp.json()["detail"] == (
            "More than one issue tracker connection could take this issue; "
            "set connection_id to one of: conn-1 (linear), conn-2 (linear)"
        )
        tracker_factory.create.assert_not_called()

    def test_connection_id_selects_the_target(self):
        app, integration_repo, tracker_factory = _make_app()
        conn2 = _make_connection(conn_id="conn-2")
        integration_repo.list_connections.return_value = [
            _make_connection(conn_id="conn-1"),
            conn2,
        ]
        mock_adapter = AsyncMock()
        mock_adapter.create_issue.return_value = _make_issue()
        tracker_factory.create.return_value = mock_adapter
        client = TestClient(app)

        resp = client.post(
            f"{PREFIX}/issues", json={**self.BODY, "connection_id": "conn-2"}, headers=AUTH
        )

        assert resp.status_code == 201
        tracker_factory.create.assert_awaited_once_with(conn2)

    def test_unknown_connection_id_is_rejected(self):
        app, integration_repo, tracker_factory = _make_app()
        integration_repo.list_connections.return_value = [_make_connection(conn_id="conn-1")]
        client = TestClient(app)

        resp = client.post(
            f"{PREFIX}/issues", json={**self.BODY, "connection_id": "conn-9"}, headers=AUTH
        )

        assert resp.status_code == 400
        assert "connection_id conn-9 is not one of your enabled" in resp.json()["detail"]
        tracker_factory.create.assert_not_called()

    def test_no_connection_is_rejected(self):
        app, integration_repo, _ = _make_app()
        integration_repo.list_connections.return_value = []
        client = TestClient(app)

        resp = client.post(f"{PREFIX}/issues", json=self.BODY, headers=AUTH)

        assert resp.status_code == 400
        assert resp.json()["detail"] == "No enabled issue tracker connection to create the issue in"

    def test_unknown_team_or_label_is_a_400_with_the_cause(self):
        app, integration_repo, tracker_factory = _make_app()
        integration_repo.list_connections.return_value = [_make_connection()]
        mock_adapter = AsyncMock()
        mock_adapter.create_issue.side_effect = TrackerRequestError("Linear team not found: NOPE")
        tracker_factory.create.return_value = mock_adapter
        client = TestClient(app)

        resp = client.post(f"{PREFIX}/issues", json={**self.BODY, "team": "NOPE"}, headers=AUTH)

        assert resp.status_code == 400
        assert resp.json()["detail"] == (
            "Issue tracker connection conn-1 cannot create the issue: Linear team not found: NOPE"
        )

    def test_tracker_failure_is_a_502_with_the_cause(self):
        app, integration_repo, tracker_factory = _make_app()
        integration_repo.list_connections.return_value = [_make_connection()]
        mock_adapter = AsyncMock()
        mock_adapter.create_issue.side_effect = RuntimeError("upstream timeout")
        tracker_factory.create.return_value = mock_adapter
        client = TestClient(app)

        resp = client.post(f"{PREFIX}/issues", json=self.BODY, headers=AUTH)

        assert resp.status_code == 502
        assert resp.json()["detail"] == (
            "Issue tracker connection conn-1 failed to create an issue in NIU: "
            "RuntimeError: upstream timeout"
        )

    def test_validates_body(self):
        app, _, _ = _make_app()
        client = TestClient(app)

        for body in (
            {**self.BODY, "priority": 5},
            {**self.BODY, "title": ""},
            {k: v for k, v in self.BODY.items() if k != "team"},
        ):
            resp = client.post(f"{PREFIX}/issues", json=body, headers=AUTH)
            assert resp.status_code == 422
