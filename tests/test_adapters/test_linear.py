"""Tests for Linear issue tracker adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from tracker.ports import TrackerRequestError
from volundr.adapters.outbound.linear import (
    _GET_ISSUE_QUERY,
    _RECENT_ISSUES_QUERY,
    _SEARCH_ISSUES_QUERY,
    _UPDATE_ISSUE_STATUS_QUERY,
    LinearAdapter,
    LinearAPIError,
    _CacheEntry,
)

# --- CacheEntry tests ---


class TestCacheEntry:
    def test_not_expired(self):
        entry = _CacheEntry("val", ttl=60.0)
        assert not entry.expired
        assert entry.value == "val"

    def test_expired(self):
        entry = _CacheEntry("val", ttl=0.0)
        # Force expiry by setting expires_at to the past
        entry.expires_at = 0.0
        assert entry.expired


# --- LinearAdapter tests ---


def _make_adapter():
    adapter = LinearAdapter(api_key="test-key", api_url="https://test.linear.app/graphql")
    return adapter


def _mock_response(json_data: dict, status_code: int = 200):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


def _issue_node(**overrides):
    defaults = {
        "id": str(uuid4()),
        "identifier": "TEST-1",
        "title": "Test Issue",
        "state": {"name": "Todo"},
        "assignee": {"name": "Test User"},
        "labels": {"nodes": [{"name": "bug"}]},
        "priority": 2,
        "url": "https://linear.app/test/issue/TEST-1",
    }
    defaults.update(overrides)
    return defaults


class TestProviderName:
    def test_returns_linear(self):
        adapter = _make_adapter()
        assert adapter.provider_name == "linear"


class TestNodeToIssue:
    def test_converts_full_node(self):
        node = _issue_node()
        issue = LinearAdapter._node_to_issue(node)

        assert issue.identifier == "TEST-1"
        assert issue.title == "Test Issue"
        assert issue.status == "Todo"
        assert issue.assignee == "Test User"
        assert issue.labels == ["bug"]
        assert issue.priority == 2
        assert issue.description is None

    def test_maps_description(self):
        node = _issue_node(description="## Acceptance criteria\n- [ ] it works")
        issue = LinearAdapter._node_to_issue(node)

        assert issue.description == "## Acceptance criteria\n- [ ] it works"


class TestQueryFields:
    def test_single_issue_queries_request_description(self):
        assert "description" in _GET_ISSUE_QUERY
        assert "description" in _UPDATE_ISSUE_STATUS_QUERY

    def test_list_queries_omit_description(self):
        assert "description" not in _SEARCH_ISSUES_QUERY
        assert "description" not in _RECENT_ISSUES_QUERY

    def test_handles_missing_assignee(self):
        node = _issue_node(assignee=None)
        issue = LinearAdapter._node_to_issue(node)
        assert issue.assignee is None

    def test_handles_missing_labels(self):
        node = _issue_node(labels=None)
        issue = LinearAdapter._node_to_issue(node)
        assert issue.labels == []

    def test_handles_missing_state(self):
        node = _issue_node(state={})
        issue = LinearAdapter._node_to_issue(node)
        assert issue.status == "Unknown"


class TestCache:
    def test_get_cached_miss(self):
        adapter = _make_adapter()
        assert adapter._get_cached("nonexistent") is None

    def test_get_cached_hit(self):
        adapter = _make_adapter()
        adapter._set_cached("key", "value", ttl=60.0)
        assert adapter._get_cached("key") == "value"

    def test_get_cached_expired(self):
        adapter = _make_adapter()
        adapter._set_cached("key", "value", ttl=60.0)
        # Force expiry by setting expires_at to the past
        adapter._gql._cache["key"].expires_at = 0.0
        assert adapter._get_cached("key") is None


class TestGraphQL:
    async def test_successful_query(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response({"data": {"viewer": {"id": "1"}}})

        result = await adapter._graphql("query { viewer { id } }")
        assert result == {"viewer": {"id": "1"}}

    async def test_query_with_variables(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response({"data": {"issue": {"id": "1"}}})

        result = await adapter._graphql(
            "query($id: String!) { issue(id: $id) { id } }",
            {"id": "1"},
        )
        assert result == {"issue": {"id": "1"}}

    async def test_api_error(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {"errors": [{"message": "Not found"}]}
        )

        with pytest.raises(LinearAPIError, match="Not found"):
            await adapter._graphql("query { bad }")


class TestCheckConnection:
    async def test_successful(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {
                "data": {
                    "viewer": {"id": "1", "name": "Test", "email": "test@test.com"},
                    "organization": {"id": "org1", "name": "TestOrg"},
                }
            }
        )

        result = await adapter.check_connection()
        assert result.connected is True
        assert result.workspace == "TestOrg"
        assert result.user == "Test"

    async def test_cached(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {
                "data": {
                    "viewer": {"id": "1", "name": "Test", "email": "t@t.com"},
                    "organization": {"id": "o", "name": "Org"},
                }
            }
        )

        await adapter.check_connection()
        result = await adapter.check_connection()

        assert result.connected is True
        # Should only have called the API once due to caching
        assert adapter._gql._client.post.call_count == 1

    async def test_connection_error(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.side_effect = Exception("Connection refused")

        result = await adapter.check_connection()
        assert result.connected is False


class TestSearchIssues:
    async def test_search(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {"data": {"searchIssues": {"nodes": [_issue_node()]}}}
        )

        issues = await adapter.search_issues("test")
        assert len(issues) == 1
        assert issues[0].identifier == "TEST-1"

    async def test_search_with_project(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {"data": {"searchIssues": {"nodes": []}}}
        )

        issues = await adapter.search_issues("test", project_id="proj-1")
        assert len(issues) == 0

        call_args = adapter._gql._client.post.call_args
        payload = call_args[1]["json"]
        assert "project:proj-1" in payload["variables"]["term"]


class TestGetRecentIssues:
    async def test_get_recent(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {"data": {"issues": {"nodes": [_issue_node(), _issue_node(identifier="TEST-2")]}}}
        )

        issues = await adapter.get_recent_issues("proj-1")
        assert len(issues) == 2


class TestGetIssue:
    async def test_get_existing(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {"data": {"issue": _issue_node(description="Body with acceptance criteria")}}
        )

        issue = await adapter.get_issue("issue-1")
        assert issue is not None
        assert issue.identifier == "TEST-1"
        assert issue.description == "Body with acceptance criteria"

    async def test_get_missing(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response({"data": {"issue": None}})

        issue = await adapter.get_issue("nonexistent")
        assert issue is None

    async def test_get_entity_not_found_error_returns_none(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {"errors": [{"message": "Entity not found: Issue"}]}
        )

        issue = await adapter.get_issue("bad-id")
        assert issue is None

    async def test_get_other_graphql_error_raises(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {"errors": [{"message": "Cannot query field 'bogus' on type 'Issue'"}]}
        )

        with pytest.raises(LinearAPIError, match="Cannot query field"):
            await adapter.get_issue("issue-1")


class TestUpdateIssueStatus:
    async def test_update_success(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()

        # Call 1: get issue team
        # Call 2: get team states
        # Call 3: update issue
        adapter._gql._client.post.side_effect = [
            _mock_response({"data": {"issue": {"team": {"id": "team-1"}}}}),
            _mock_response(
                {
                    "data": {
                        "team": {
                            "states": {
                                "nodes": [
                                    {"id": "s1", "name": "Todo"},
                                    {"id": "s2", "name": "In Progress"},
                                    {"id": "s3", "name": "Done"},
                                ]
                            }
                        }
                    }
                }
            ),
            _mock_response(
                {"data": {"issueUpdate": {"issue": _issue_node(state={"name": "Done"})}}}
            ),
        ]

        # Pre-populate cache to test invalidation
        adapter._set_cached("search:test:None", [], ttl=60.0)
        adapter._set_cached("recent:proj:10", [], ttl=60.0)
        adapter._set_cached("connection", "keep", ttl=60.0)

        issue = await adapter.update_issue_status("issue-1", "Done")
        assert issue.status == "Done"

        # Search/recent caches should be invalidated
        assert adapter._get_cached("search:test:None") is None
        assert adapter._get_cached("recent:proj:10") is None
        # Non-search caches should remain
        assert adapter._get_cached("connection") == "keep"

    async def test_update_issue_not_found(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response({"data": {"issue": None}})

        with pytest.raises(LinearAPIError, match="Issue not found"):
            await adapter.update_issue_status("bad-id", "Done")

    async def test_update_status_not_found(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.side_effect = [
            _mock_response({"data": {"issue": {"team": {"id": "team-1"}}}}),
            _mock_response(
                {
                    "data": {
                        "team": {
                            "states": {
                                "nodes": [
                                    {"id": "s1", "name": "Todo"},
                                    {"id": "s2", "name": "Done"},
                                ]
                            }
                        }
                    }
                }
            ),
        ]

        with pytest.raises(TrackerRequestError, match="Status 'Invalid' not found"):
            await adapter.update_issue_status("issue-1", "Invalid")


class TestLinearAdapterConstructor:
    """Tests for LinearAdapter constructor edge cases."""

    def test_extra_kwargs_ignored(self):
        """Extra kwargs from dynamic adapter pattern don't crash."""
        adapter = LinearAdapter(
            api_key="test-key",
            api_url="https://api.linear.app/graphql",
            name="linear-prod",
            some_other="value",
        )
        assert adapter.provider_name == "linear"


class TestClose:
    async def test_close(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()

        await adapter.close()

        adapter._gql._client.aclose.assert_called_once()


class TestCreateIssue:
    @staticmethod
    def _posted(adapter, call: int) -> dict:
        return adapter._gql._client.post.call_args_list[call].kwargs["json"]

    async def test_create_resolves_team_and_labels(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.side_effect = [
            _mock_response({"data": {"teams": {"nodes": [{"id": "team-niu", "key": "NIU"}]}}}),
            _mock_response(
                {
                    "data": {
                        "issueLabels": {
                            "nodes": [
                                {"id": "l-bug", "name": "Bug", "team": None},
                                {
                                    "id": "l-plat",
                                    "name": "platform-defect",
                                    "team": {"id": "team-niu"},
                                },
                                {"id": "l-other", "name": "Bug", "team": {"id": "team-other"}},
                            ]
                        }
                    }
                }
            ),
            _mock_response(
                {
                    "data": {
                        "issueCreate": {
                            "success": True,
                            "issue": _issue_node(identifier="NIU-2000", description="Body"),
                        }
                    }
                }
            ),
        ]
        adapter._set_cached("search:x:None", [], ttl=60.0)

        issue = await adapter.create_issue(
            "Tracker API lacks create",
            "Body",
            team="niu",
            labels=["bug", "platform-defect", "Bug"],
            priority=2,
        )

        assert issue.identifier == "NIU-2000"
        assert issue.description == "Body"
        assert self._posted(adapter, 0)["variables"] == {"key": "niu"}
        assert self._posted(adapter, 1)["variables"] == {
            "filter": {
                "or": [
                    {"name": {"eqIgnoreCase": "bug"}},
                    {"name": {"eqIgnoreCase": "platform-defect"}},
                ]
            }
        }
        create_vars = self._posted(adapter, 2)["variables"]["input"]
        assert create_vars == {
            "teamId": "team-niu",
            "title": "Tracker API lacks create",
            "description": "Body",
            "labelIds": ["l-bug", "l-plat"],
            "priority": 2,
        }
        assert adapter._get_cached("search:x:None") is None

    async def test_create_without_labels_or_priority_skips_label_lookup(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.side_effect = [
            _mock_response({"data": {"teams": {"nodes": [{"id": "team-niu", "key": "NIU"}]}}}),
            _mock_response({"data": {"issueCreate": {"success": True, "issue": _issue_node()}}}),
        ]

        await adapter.create_issue("T", "B", team="NIU")

        assert self._posted(adapter, 1)["variables"]["input"] == {
            "teamId": "team-niu",
            "title": "T",
            "description": "B",
        }

    async def test_unknown_team_raises_request_error(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response({"data": {"teams": {"nodes": []}}})

        with pytest.raises(TrackerRequestError, match="Linear team not found: NOPE"):
            await adapter.create_issue("T", "B", team="NOPE")

    async def test_unknown_label_raises_request_error(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.side_effect = [
            _mock_response({"data": {"teams": {"nodes": [{"id": "team-niu", "key": "NIU"}]}}}),
            _mock_response(
                {
                    "data": {
                        "issueLabels": {
                            "nodes": [
                                {"id": "l-x", "name": "missing", "team": {"id": "team-other"}}
                            ]
                        }
                    }
                }
            ),
        ]

        with pytest.raises(TrackerRequestError, match="not found in team NIU.*: missing"):
            await adapter.create_issue("T", "B", team="NIU", labels=["missing"])
        assert adapter._gql._client.post.call_count == 2

    async def test_ambiguous_label_raises_request_error(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.side_effect = [
            _mock_response({"data": {"teams": {"nodes": [{"id": "team-niu", "key": "NIU"}]}}}),
            _mock_response(
                {
                    "data": {
                        "issueLabels": {
                            "nodes": [
                                {"id": "l-1", "name": "dup", "team": None},
                                {"id": "l-2", "name": "Dup", "team": {"id": "team-niu"}},
                            ]
                        }
                    }
                }
            ),
        ]

        with pytest.raises(TrackerRequestError, match="more than one label.*: dup"):
            await adapter.create_issue("T", "B", team="NIU", labels=["dup"])

    async def test_unsuccessful_create_raises(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.side_effect = [
            _mock_response({"data": {"teams": {"nodes": [{"id": "team-niu", "key": "NIU"}]}}}),
            _mock_response({"data": {"issueCreate": {"success": False, "issue": None}}}),
        ]

        with pytest.raises(LinearAPIError, match="did not create the issue in team NIU"):
            await adapter.create_issue("T", "B", team="NIU")

    async def test_update_rewrites_title_and_description(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.return_value = _mock_response(
            {
                "data": {
                    "issueUpdate": {
                        "success": True,
                        "issue": _issue_node(
                            title="Start from regin", description="Branch from regin."
                        ),
                    }
                }
            }
        )
        adapter._set_cached("search:test:None", [], ttl=60.0)

        issue = await adapter.update_issue(
            "NIU-1116", title="Start from regin", description="Branch from regin."
        )

        assert issue.title == "Start from regin"
        assert issue.description == "Branch from regin."
        sent = adapter._gql._client.post.call_args.kwargs["json"]["variables"]
        assert sent == {
            "issueId": "NIU-1116",
            "input": {"title": "Start from regin", "description": "Branch from regin."},
        }
        assert adapter._get_cached("search:test:None") is None

    async def test_update_sends_only_the_fields_given_and_rejects_nothing(self):
        adapter = _make_adapter()
        adapter._gql._client = AsyncMock()
        adapter._gql._client.post.side_effect = [
            _mock_response({"data": {"issueUpdate": {"success": True, "issue": _issue_node()}}}),
            _mock_response({"data": {"issueUpdate": {"success": False, "issue": None}}}),
        ]

        await adapter.update_issue("NIU-1", description="New body")
        sent = adapter._gql._client.post.call_args.kwargs["json"]["variables"]["input"]
        assert sent == {"description": "New body"}
        with pytest.raises(LinearAPIError, match="did not update issue NIU-1"):
            await adapter.update_issue("NIU-1", title="T")
        with pytest.raises(TrackerRequestError, match="Nothing to update"):
            await adapter.update_issue("NIU-1")
