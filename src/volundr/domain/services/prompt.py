"""Domain service for saved prompt management."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from volundr.domain.models import PromptScope, SavedPrompt
from volundr.domain.ports import SavedPromptRepository

logger = logging.getLogger(__name__)


class PromptNotFoundError(Exception):
    """Raised when a saved prompt is not found."""


class PromptService:
    """Service for managing saved prompts."""

    def __init__(self, repository: SavedPromptRepository):
        self._repository = repository

    async def create_prompt(
        self,
        name: str,
        content: str,
        scope: PromptScope = PromptScope.GLOBAL,
        project_repo: str | None = None,
        tags: list[str] | None = None,
    ) -> SavedPrompt:
        """Create a new saved prompt."""
        prompt = SavedPrompt(
            name=name,
            content=content,
            scope=scope,
            project_repo=project_repo,
            tags=tags or [],
        )
        created = await self._repository.create(prompt)
        logger.info("Created saved prompt: id=%s, name=%s", created.id, created.name)
        return created

    async def get_prompt(self, prompt_id: UUID) -> SavedPrompt:
        """Get a saved prompt by ID."""
        prompt = await self._repository.get(prompt_id)
        if prompt is None:
            raise PromptNotFoundError(f"Prompt not found: {prompt_id}")
        return prompt

    async def list_prompts(
        self,
        scope: PromptScope | None = None,
        repo: str | None = None,
    ) -> list[SavedPrompt]:
        """List saved prompts with optional filters."""
        return await self._repository.list(scope=scope, repo=repo)

    async def update_prompt(
        self,
        prompt_id: UUID,
        name: str | None = None,
        content: str | None = None,
        scope: PromptScope | None = None,
        project_repo: str | None = None,
        tags: list[str] | None = None,
    ) -> SavedPrompt:
        """Update an existing saved prompt."""
        prompt = await self._repository.get(prompt_id)
        if prompt is None:
            raise PromptNotFoundError(f"Prompt not found: {prompt_id}")

        if name is not None:
            prompt.name = name
        if content is not None:
            prompt.content = content
        if scope is not None:
            prompt.scope = scope
        if project_repo is not None:
            prompt.project_repo = project_repo
        if tags is not None:
            prompt.tags = tags
        prompt.updated_at = datetime.now(UTC)

        updated = await self._repository.update(prompt)
        logger.info("Updated saved prompt: id=%s", updated.id)
        return updated

    async def delete_prompt(self, prompt_id: UUID) -> bool:
        """Delete a saved prompt."""
        deleted = await self._repository.delete(prompt_id)
        if not deleted:
            raise PromptNotFoundError(f"Prompt not found: {prompt_id}")
        logger.info("Deleted saved prompt: id=%s", prompt_id)
        return True

    async def search_prompts(self, query: str) -> list[SavedPrompt]:
        """Search prompts by name and content."""
        return await self._repository.search(query)
