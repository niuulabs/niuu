"""Tests for git workflow service and confidence scorer."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tests.conftest import (
    InMemoryChronicleRepository,
    InMemorySessionRepository,
    MockEventBroadcaster,
)
from volundr.domain.models import (
    Chronicle,
    CIStatus,
    GitProviderType,
    GitSource,
    PullRequest,
    PullRequestStatus,
    Session,
    SessionStatus,
)
from volundr.domain.services.git_workflow import (
    ConfidenceScorer,
    GitWorkflowService,
    SessionNotFoundError,
)

# --- ConfidenceScorer tests ---


class TestConfidenceScorer:
    """Tests for the ConfidenceScorer."""

    @pytest.fixture
    def scorer(self) -> ConfidenceScorer:
        return ConfidenceScorer(
            auto_merge_threshold=0.9,
            notify_merge_threshold=0.6,
        )

    def test_docs_only_high_confidence(self, scorer: ConfidenceScorer):
        """Docs-only changes get auto_merge."""
        result = scorer.score(
            tests_pass=True,
            coverage_delta=0.0,
            lines_changed=10,
            files_changed=1,
            has_dependency_changes=False,
            change_categories=["docs"],
        )
        assert result.score >= 0.9
        assert result.action == "auto_merge"

    def test_tests_only_high_confidence(self, scorer: ConfidenceScorer):
        """Test-only changes get auto_merge."""
        result = scorer.score(
            tests_pass=True,
            coverage_delta=2.0,
            lines_changed=30,
            files_changed=2,
            has_dependency_changes=False,
            change_categories=["test"],
        )
        assert result.score >= 0.9
        assert result.action == "auto_merge"

    def test_small_feature_medium_confidence(self, scorer: ConfidenceScorer):
        """Small features with passing tests get notify_then_merge."""
        result = scorer.score(
            tests_pass=True,
            coverage_delta=1.0,
            lines_changed=80,
            files_changed=5,
            has_dependency_changes=False,
            change_categories=["feature"],
        )
        assert 0.6 <= result.score < 0.9
        assert result.action == "notify_then_merge"

    def test_large_feature_low_confidence(self, scorer: ConfidenceScorer):
        """Large features with dependency changes require approval."""
        result = scorer.score(
            tests_pass=True,
            coverage_delta=-3.0,
            lines_changed=500,
            files_changed=20,
            has_dependency_changes=True,
            change_categories=["feature"],
        )
        assert result.score < 0.6
        assert result.action == "require_approval"

    def test_dependency_changes_low_confidence(self, scorer: ConfidenceScorer):
        """Dependency changes with risky categories require approval."""
        result = scorer.score(
            tests_pass=True,
            coverage_delta=-3.0,
            lines_changed=200,
            files_changed=12,
            has_dependency_changes=True,
            change_categories=["security"],
        )
        assert result.action == "require_approval"

    def test_failing_tests_always_require_approval(self, scorer: ConfidenceScorer):
        """Failing tests always result in require_approval with score 0."""
        result = scorer.score(
            tests_pass=False,
            coverage_delta=0.0,
            lines_changed=5,
            files_changed=1,
            has_dependency_changes=False,
            change_categories=["docs"],
        )
        assert result.score == 0.0
        assert result.action == "require_approval"
        assert "failing" in result.reason.lower()

    def test_security_category_lowers_confidence(self, scorer: ConfidenceScorer):
        """Security changes are flagged as risky."""
        result = scorer.score(
            tests_pass=True,
            coverage_delta=0.0,
            lines_changed=30,
            files_changed=2,
            has_dependency_changes=False,
            change_categories=["security"],
        )
        assert result.factors["category"] == 0.3

    def test_lint_style_high_confidence(self, scorer: ConfidenceScorer):
        """Lint and style changes score high."""
        result = scorer.score(
            tests_pass=True,
            coverage_delta=0.0,
            lines_changed=20,
            files_changed=3,
            has_dependency_changes=False,
            change_categories=["lint", "style"],
        )
        assert result.score >= 0.9
        assert result.action == "auto_merge"

    def test_score_is_between_0_and_1(self, scorer: ConfidenceScorer):
        """Score is always in valid range."""
        result = scorer.score(
            tests_pass=True,
            coverage_delta=-10.0,
            lines_changed=1000,
            files_changed=50,
            has_dependency_changes=True,
            change_categories=["security", "database"],
        )
        assert 0.0 <= result.score <= 1.0

    def test_factors_are_present(self, scorer: ConfidenceScorer):
        """All expected factors are in the result."""
        result = scorer.score(
            tests_pass=True,
            coverage_delta=0.0,
            lines_changed=50,
            files_changed=3,
            has_dependency_changes=False,
            change_categories=["feature"],
        )
        expected_factors = {"tests", "coverage", "size", "category", "dependencies", "files"}
        assert set(result.factors.keys()) == expected_factors


# --- GitWorkflowService tests ---


def _make_session(
    repo: str = "https://github.com/user/repo",
    branch: str = "feature/test",
    name: str = "test-session",
) -> Session:
    return Session(
        id=uuid4(),
        name=name,
        model="claude-sonnet-4-20250514",
        source=GitSource(repo=repo, branch=branch),
        status=SessionStatus.RUNNING,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        last_active=datetime.now(UTC),
    )


def _make_chronicle(session_id, repo="https://github.com/user/repo") -> Chronicle:
    return Chronicle(
        id=uuid4(),
        session_id=session_id,
        status="complete",
        project="test-project",
        repo=repo,
        branch="feature/test",
        model="claude-sonnet-4-20250514",
        config_snapshot={},
        summary="Fixed authentication bug in login flow",
        key_changes=["Updated auth middleware", "Added unit tests"],
        unfinished_work=None,
        token_usage=1000,
        cost=None,
        duration_seconds=120,
        tags=["fix", "auth"],
        parent_chronicle_id=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


class MockGitRegistryWithWorkflow:
    """Mock git registry that supports workflow operations."""

    def __init__(self):
        self.create_pr_calls: list[dict] = []
        self.merge_calls: list[dict] = []
        self.list_calls: list[dict] = []
        self.get_calls: list[dict] = []
        self.ci_calls: list[dict] = []
        self._prs: list[PullRequest] = []
        self._merge_result = True
        self._ci_status = CIStatus.PASSING

    async def create_pull_request(
        self,
        repo_url: str,
        title: str,
        description: str,
        source_branch: str,
        target_branch: str,
        labels: list[str] | None = None,
    ) -> PullRequest:
        self.create_pr_calls.append(
            {
                "repo_url": repo_url,
                "title": title,
                "description": description,
                "source_branch": source_branch,
                "target_branch": target_branch,
                "labels": labels,
            }
        )
        return PullRequest(
            number=42,
            title=title,
            url=f"{repo_url}/pull/42",
            repo_url=repo_url,
            provider=GitProviderType.GITHUB,
            source_branch=source_branch,
            target_branch=target_branch,
            status=PullRequestStatus.OPEN,
            description=description,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    async def get_pull_request(self, repo_url: str, pr_number: int) -> PullRequest | None:
        self.get_calls.append({"repo_url": repo_url, "pr_number": pr_number})
        for pr in self._prs:
            if pr.repo_url == repo_url and pr.number == pr_number:
                return pr
        return None

    async def list_pull_requests(self, repo_url: str, status: str = "open") -> list[PullRequest]:
        self.list_calls.append({"repo_url": repo_url, "status": status})
        return [pr for pr in self._prs if pr.repo_url == repo_url]

    async def merge_pull_request(
        self, repo_url: str, pr_number: int, merge_method: str = "squash"
    ) -> bool:
        self.merge_calls.append(
            {
                "repo_url": repo_url,
                "pr_number": pr_number,
                "merge_method": merge_method,
            }
        )
        return self._merge_result

    async def get_ci_status(self, repo_url: str, branch: str) -> CIStatus:
        self.ci_calls.append({"repo_url": repo_url, "branch": branch})
        return self._ci_status


class TestGitWorkflowService:
    """Tests for GitWorkflowService."""

    @pytest.fixture
    def session_repo(self) -> InMemorySessionRepository:
        return InMemorySessionRepository()

    @pytest.fixture
    def chronicle_repo(self) -> InMemoryChronicleRepository:
        return InMemoryChronicleRepository()

    @pytest.fixture
    def git_registry(self) -> MockGitRegistryWithWorkflow:
        return MockGitRegistryWithWorkflow()

    @pytest.fixture
    def broadcaster(self) -> MockEventBroadcaster:
        return MockEventBroadcaster()

    @pytest.fixture
    def service(
        self,
        git_registry: MockGitRegistryWithWorkflow,
        chronicle_repo: InMemoryChronicleRepository,
        session_repo: InMemorySessionRepository,
        broadcaster: MockEventBroadcaster,
    ) -> GitWorkflowService:
        return GitWorkflowService(
            git_registry=git_registry,
            chronicle_repository=chronicle_repo,
            session_repository=session_repo,
            broadcaster=broadcaster,
        )

    @pytest.mark.asyncio
    async def test_create_pr_from_session(
        self,
        service: GitWorkflowService,
        session_repo: InMemorySessionRepository,
        chronicle_repo: InMemoryChronicleRepository,
        git_registry: MockGitRegistryWithWorkflow,
        broadcaster: MockEventBroadcaster,
    ):
        """Creates a PR with chronicle summary as description."""
        session = _make_session()
        await session_repo.create(session)
        chronicle = _make_chronicle(session.id)
        await chronicle_repo.create(chronicle)

        pr = await service.create_pr_from_session(session.id)

        assert pr.number == 42
        assert pr.status == PullRequestStatus.OPEN
        assert len(git_registry.create_pr_calls) == 1
        call = git_registry.create_pr_calls[0]
        assert call["source_branch"] == session.branch
        assert call["target_branch"] == "main"
        assert "Fixed authentication bug" in call["description"]
        assert "Updated auth middleware" in call["description"]
        # SSE event was broadcast
        assert len(broadcaster.events) == 1
        assert broadcaster.events[0].type.value == "pr_created"

    @pytest.mark.asyncio
    async def test_create_pr_from_session_no_chronicle(
        self,
        service: GitWorkflowService,
        session_repo: InMemorySessionRepository,
        git_registry: MockGitRegistryWithWorkflow,
    ):
        """Creates a PR even without a chronicle (empty description)."""
        session = _make_session()
        await session_repo.create(session)

        pr = await service.create_pr_from_session(session.id, title="My PR")

        assert pr.number == 42
        assert len(git_registry.create_pr_calls) == 1
        assert git_registry.create_pr_calls[0]["title"] == "My PR"
        assert git_registry.create_pr_calls[0]["description"] == ""

    @pytest.mark.asyncio
    async def test_create_pr_session_not_found(
        self,
        service: GitWorkflowService,
    ):
        """Raises SessionNotFoundError if session doesn't exist."""
        with pytest.raises(SessionNotFoundError):
            await service.create_pr_from_session(uuid4())

    @pytest.mark.asyncio
    async def test_create_pr_no_repo(
        self,
        service: GitWorkflowService,
        session_repo: InMemorySessionRepository,
    ):
        """Raises ValueError if session has no repo."""
        session = _make_session(repo="")
        await session_repo.create(session)

        with pytest.raises(ValueError, match="no repository"):
            await service.create_pr_from_session(session.id)

    @pytest.mark.asyncio
    async def test_merge_pr(
        self,
        service: GitWorkflowService,
        git_registry: MockGitRegistryWithWorkflow,
        broadcaster: MockEventBroadcaster,
    ):
        """Merges a PR and broadcasts an event."""
        result = await service.merge_pr("https://github.com/user/repo", 42, "squash")

        assert result is True
        assert len(git_registry.merge_calls) == 1
        assert git_registry.merge_calls[0]["pr_number"] == 42
        assert len(broadcaster.events) == 1
        assert broadcaster.events[0].type.value == "pr_merged"

    @pytest.mark.asyncio
    async def test_merge_pr_failure(
        self,
        service: GitWorkflowService,
        git_registry: MockGitRegistryWithWorkflow,
        broadcaster: MockEventBroadcaster,
    ):
        """No event broadcast on merge failure."""
        git_registry._merge_result = False

        result = await service.merge_pr("https://github.com/user/repo", 42)

        assert result is False
        assert len(broadcaster.events) == 0

    @pytest.mark.asyncio
    async def test_list_prs(
        self,
        service: GitWorkflowService,
        git_registry: MockGitRegistryWithWorkflow,
    ):
        """Delegates to git registry."""
        git_registry._prs = [
            PullRequest(
                number=1,
                title="PR 1",
                url="https://github.com/user/repo/pull/1",
                repo_url="https://github.com/user/repo",
                provider=GitProviderType.GITHUB,
                source_branch="feature/1",
                target_branch="main",
                status=PullRequestStatus.OPEN,
            ),
        ]

        prs = await service.list_prs("https://github.com/user/repo")

        assert len(prs) == 1
        assert prs[0].number == 1

    @pytest.mark.asyncio
    async def test_get_ci_status(
        self,
        service: GitWorkflowService,
        git_registry: MockGitRegistryWithWorkflow,
    ):
        """Delegates CI status to git registry."""
        git_registry._ci_status = CIStatus.FAILING

        status = await service.get_ci_status("https://github.com/user/repo", "feature/test")

        assert status == CIStatus.FAILING

    def test_calculate_confidence(self, service: GitWorkflowService):
        """Delegates to the ConfidenceScorer."""
        result = service.calculate_confidence(
            tests_pass=True,
            coverage_delta=0.0,
            lines_changed=10,
            files_changed=1,
            has_dependency_changes=False,
            change_categories=["docs"],
        )
        assert result.action == "auto_merge"
        assert result.score >= 0.9

    @pytest.mark.asyncio
    async def test_create_pr_uses_session_name_as_default_title(
        self,
        service: GitWorkflowService,
        session_repo: InMemorySessionRepository,
        git_registry: MockGitRegistryWithWorkflow,
    ):
        """Uses 'Session: <name>' as default PR title."""
        session = _make_session(name="fix-login-bug")
        await session_repo.create(session)

        await service.create_pr_from_session(session.id)

        assert git_registry.create_pr_calls[0]["title"] == "Session: fix-login-bug"
