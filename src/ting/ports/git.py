"""Git port — interface for branch and PR operations."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ting.domain.models import PRStatus


class GitPort(ABC):
    """Abstract interface for Git/SCM operations."""

    @abstractmethod
    async def create_branch(self, repo: str, branch: str, base: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def merge_branch(self, repo: str, source: str, target: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def delete_branch(self, repo: str, branch: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def create_pr(self, repo: str, source: str, target: str, title: str) -> str:
        raise NotImplementedError

    @abstractmethod
    async def get_pr_status(self, pr_id: str) -> PRStatus:
        raise NotImplementedError

    @abstractmethod
    async def get_pr_changed_files(self, pr_id: str) -> list[str]:
        """Return the list of file paths changed in the PR."""
        raise NotImplementedError
