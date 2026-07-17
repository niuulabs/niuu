"""Tests for Ting port interfaces."""

import inspect

import pytest

from ting.ports.confidence import ConfidencePort
from ting.ports.git import GitPort
from ting.ports.llm import LLMPort
from ting.ports.tracker import TrackerPort
from ting.ports.volundr import VolundrPort


class TestTrackerPort:
    def test_is_abstract(self) -> None:
        with pytest.raises(TypeError):
            TrackerPort()  # type: ignore[abstract]

    def test_methods_exist(self) -> None:
        methods = {
            "create_saga",
            "create_phase",
            "create_run",
            "update_run_state",
            "close_run",
            "get_saga",
            "get_phase",
            "get_run",
            "list_pending_runs",
            "list_projects",
            "get_project",
            "list_milestones",
            "list_issues",
            "update_run_progress",
            "get_run_progress_for_saga",
            "get_run_by_session",
            "list_runs_by_status",
            "get_run_by_id",
            "add_confidence_event",
            "get_confidence_events",
            "all_runs_merged",
            "list_phases_for_saga",
            "update_phase_status",
            "get_saga_for_run",
            "get_phase_for_run",
            "get_owner_for_run",
            "save_session_message",
            "get_session_messages",
            "attach_document",
            "add_comment",
            "attach_issue_document",
            "get_blocked_identifiers",
        }
        abstract_methods = {
            name
            for name, _ in inspect.getmembers(TrackerPort, predicate=inspect.isfunction)
            if not name.startswith("_")
        }
        assert methods == abstract_methods


class TestVolundrPort:
    def test_is_abstract(self) -> None:
        with pytest.raises(TypeError):
            VolundrPort()  # type: ignore[abstract]

    def test_methods_exist(self) -> None:
        methods = {
            "spawn_session",
            "get_session",
            "list_sessions",
            "get_pr_status",
            "get_chronicle_summary",
            "send_message",
            "send_directed_room_message",
            "get_workflow_gates",
            "resolve_workflow_gate",
            "get_help_requests",
            "answer_help_request",
            "stop_session",
            "list_integration_ids",
            "list_repos",
            "get_conversation",
            "get_last_assistant_message",
            "subscribe_activity",
        }
        abstract_methods = {
            name
            for name, _ in inspect.getmembers(VolundrPort, predicate=inspect.isfunction)
            if not name.startswith("_")
        }
        assert methods == abstract_methods


class TestLLMPort:
    def test_is_abstract(self) -> None:
        with pytest.raises(TypeError):
            LLMPort()  # type: ignore[abstract]

    def test_methods_exist(self) -> None:
        methods = {"decompose_spec"}
        abstract_methods = {
            name
            for name, _ in inspect.getmembers(LLMPort, predicate=inspect.isfunction)
            if not name.startswith("_")
        }
        assert methods == abstract_methods


class TestGitPort:
    def test_is_abstract(self) -> None:
        with pytest.raises(TypeError):
            GitPort()  # type: ignore[abstract]

    def test_methods_exist(self) -> None:
        methods = {
            "create_branch",
            "merge_branch",
            "delete_branch",
            "create_pr",
            "get_pr_status",
            "get_pr_changed_files",
        }
        abstract_methods = {
            name
            for name, _ in inspect.getmembers(GitPort, predicate=inspect.isfunction)
            if not name.startswith("_")
        }
        assert methods == abstract_methods


class TestConfidencePort:
    def test_is_abstract(self) -> None:
        with pytest.raises(TypeError):
            ConfidencePort()  # type: ignore[abstract]

    def test_methods_exist(self) -> None:
        methods = {"score_initial", "update_score", "get_score"}
        abstract_methods = {
            name
            for name, _ in inspect.getmembers(ConfidencePort, predicate=inspect.isfunction)
            if not name.startswith("_")
        }
        assert methods == abstract_methods
