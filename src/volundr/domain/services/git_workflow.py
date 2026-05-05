"""Git workflow service — PR creation, merging, and confidence scoring."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from volundr.config import GitWorkflowConfig
from volundr.domain.models import (
    CIStatus,
    EventType,
    MergeConfidence,
    PullRequest,
    RealtimeEvent,
)
from volundr.domain.ports import (
    ChronicleRepository,
    EventBroadcaster,
    SessionRepository,
)

logger = logging.getLogger(__name__)


class SessionNotFoundError(Exception):
    """Raised when a session is not found."""


class ConfidenceScorer:
    """Calculate merge confidence for a pull request.

    Pure logic — no I/O. Scoring is based on change characteristics.
    """

    def __init__(
        self,
        auto_merge_threshold: float = 0.9,
        notify_merge_threshold: float = 0.6,
    ):
        self._auto_threshold = auto_merge_threshold
        self._notify_threshold = notify_merge_threshold

    def score(
        self,
        tests_pass: bool,
        coverage_delta: float,
        lines_changed: int,
        files_changed: int,
        has_dependency_changes: bool,
        change_categories: list[str],
    ) -> MergeConfidence:
        """Score the merge confidence for a set of changes."""
        factors: dict[str, float] = {}

        # Tests factor — most important
        factors["tests"] = 1.0 if tests_pass else 0.0
        if not tests_pass:
            return MergeConfidence(
                score=0.0,
                factors=factors,
                action="require_approval",
                reason="Tests are failing",
            )

        # Coverage factor
        if coverage_delta >= 0:
            factors["coverage"] = 1.0
        elif coverage_delta >= -2.0:
            factors["coverage"] = 0.7
        else:
            factors["coverage"] = 0.3

        # Size factor
        if lines_changed <= 50:
            factors["size"] = 1.0
        elif lines_changed <= 100:
            factors["size"] = 0.8
        elif lines_changed <= 300:
            factors["size"] = 0.5
        else:
            factors["size"] = 0.2

        # Category factor — safe categories score higher
        safe_categories = {"test", "docs", "lint", "style", "ci"}
        risky_categories = {"security", "auth", "database", "migration"}
        cats = set(change_categories)

        if cats and cats.issubset(safe_categories):
            factors["category"] = 1.0
        elif cats & risky_categories:
            factors["category"] = 0.3
        else:
            factors["category"] = 0.7

        # Dependency factor
        factors["dependencies"] = 0.3 if has_dependency_changes else 1.0

        # Files factor
        if files_changed <= 3:
            factors["files"] = 1.0
        elif files_changed <= 10:
            factors["files"] = 0.7
        else:
            factors["files"] = 0.4

        # Weighted average
        weights = {
            "tests": 0.30,
            "coverage": 0.10,
            "size": 0.20,
            "category": 0.15,
            "dependencies": 0.15,
            "files": 0.10,
        }
        score = sum(factors[k] * weights[k] for k in weights)

        # Determine action
        if score >= self._auto_threshold:
            action = "auto_merge"
            reason = "Low-risk change with passing tests"
        elif score >= self._notify_threshold:
            action = "notify_then_merge"
            reason = "Medium-risk change — review recommended"
        else:
            action = "require_approval"
            reason = "High-risk change — approval required"

        return MergeConfidence(
            score=round(score, 4),
            factors={k: round(v, 4) for k, v in factors.items()},
            action=action,
            reason=reason,
        )


class GitWorkflowService:
    """Orchestrates git workflow operations — PR creation from sessions,
    merging, CI status, and confidence scoring.

    No local PR database. GitHub/GitLab are the source of truth.
    """

    def __init__(
        self,
        git_registry,
        chronicle_repository: ChronicleRepository,
        session_repository: SessionRepository,
        broadcaster: EventBroadcaster | None = None,
        workflow_config: GitWorkflowConfig | None = None,
    ):
        self._git_registry = git_registry
        self._chronicle_repo = chronicle_repository
        self._session_repo = session_repository
        self._broadcaster = broadcaster
        self._config = workflow_config or GitWorkflowConfig()
        self._scorer = ConfidenceScorer(
            auto_merge_threshold=self._config.auto_merge_threshold,
            notify_merge_threshold=self._config.notify_merge_threshold,
        )

    async def create_pr_from_session(
        self,
        session_id: UUID,
        title: str | None = None,
        target_branch: str = "main",
    ) -> PullRequest:
        """Create a PR from a session's branch using chronicle summary as description."""
        session = await self._session_repo.get(session_id)
        if session is None:
            raise SessionNotFoundError(f"Session not found: {session_id}")

        if not session.repo:
            raise ValueError("Session has no repository configured")

        # Build description from chronicle if available
        description = ""
        chronicle = await self._chronicle_repo.get_by_session(session_id)
        if chronicle is not None:
            parts: list[str] = []
            if chronicle.summary:
                parts.append(chronicle.summary)
            if chronicle.key_changes:
                parts.append("\n## Key Changes")
                for change in chronicle.key_changes:
                    parts.append(f"- {change}")
            if chronicle.unfinished_work:
                parts.append(f"\n## Remaining Work\n{chronicle.unfinished_work}")
            description = "\n".join(parts)

        pr_title = title or f"Session: {session.name}"

        pr = await self._git_registry.create_pull_request(
            repo_url=session.repo,
            title=pr_title,
            description=description,
            source_branch=session.branch,
            target_branch=target_branch,
        )

        if self._broadcaster:
            await self._broadcaster.publish(
                RealtimeEvent(
                    type=EventType.PR_CREATED,
                    data={
                        "session_id": str(session_id),
                        "pr_number": pr.number,
                        "pr_url": pr.url,
                        "repo_url": session.repo,
                    },
                    timestamp=datetime.now(UTC),
                )
            )

        logger.info(
            "Created PR #%d from session %s: %s",
            pr.number,
            session_id,
            pr.url,
        )
        return pr

    async def get_pr(self, repo_url: str, pr_number: int) -> PullRequest | None:
        """Get a PR from the provider."""
        return await self._git_registry.get_pull_request(repo_url, pr_number)

    async def list_prs(self, repo_url: str, status: str = "open") -> list[PullRequest]:
        """List PRs from the provider."""
        return await self._git_registry.list_pull_requests(repo_url, status)

    async def merge_pr(
        self,
        repo_url: str,
        pr_number: int,
        merge_method: str | None = None,
    ) -> bool:
        """Merge a PR via the provider."""
        method = merge_method or self._config.default_merge_method
        result = await self._git_registry.merge_pull_request(repo_url, pr_number, method)

        if result and self._broadcaster:
            await self._broadcaster.publish(
                RealtimeEvent(
                    type=EventType.PR_MERGED,
                    data={
                        "pr_number": pr_number,
                        "repo_url": repo_url,
                        "merge_method": method,
                    },
                    timestamp=datetime.now(UTC),
                )
            )

        return result

    async def get_ci_status(self, repo_url: str, branch: str) -> CIStatus:
        """Get CI status from the provider."""
        return await self._git_registry.get_ci_status(repo_url, branch)

    def calculate_confidence(
        self,
        tests_pass: bool,
        coverage_delta: float,
        lines_changed: int,
        files_changed: int,
        has_dependency_changes: bool,
        change_categories: list[str],
    ) -> MergeConfidence:
        """Calculate merge confidence for a set of changes."""
        return self._scorer.score(
            tests_pass=tests_pass,
            coverage_delta=coverage_delta,
            lines_changed=lines_changed,
            files_changed=files_changed,
            has_dependency_changes=has_dependency_changes,
            change_categories=change_categories,
        )
