"""FastAPI REST adapter for saved prompts."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from volundr.domain.models import PromptScope, SavedPrompt
from volundr.domain.services.prompt import PromptNotFoundError, PromptService

logger = logging.getLogger(__name__)


# --- Request/Response models ---


class PromptCreate(BaseModel):
    """Request model for creating a saved prompt."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Human-readable prompt name",
    )
    content: str = Field(
        ...,
        min_length=1,
        description="The prompt text content",
    )
    scope: PromptScope = Field(
        default=PromptScope.GLOBAL,
        description="Visibility scope: global or project",
    )
    project_repo: str | None = Field(
        default=None,
        max_length=500,
        description="Repository URL when scope is project",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Tags for categorization and search",
    )


class PromptUpdate(BaseModel):
    """Request model for updating a saved prompt."""

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="New prompt name",
    )
    content: str | None = Field(
        default=None,
        min_length=1,
        description="New prompt content",
    )
    scope: PromptScope | None = Field(
        default=None,
        description="New visibility scope",
    )
    project_repo: str | None = Field(
        default=None,
        max_length=500,
        description="New repository URL for project scope",
    )
    tags: list[str] | None = Field(
        default=None,
        description="New tags list",
    )


class PromptResponse(BaseModel):
    """Response model for a saved prompt."""

    id: UUID = Field(description="Unique prompt identifier")
    name: str = Field(description="Prompt name")
    content: str = Field(description="The prompt text content")
    scope: PromptScope = Field(description="Visibility scope")
    project_repo: str | None = Field(description="Scoped repository URL")
    tags: list[str] = Field(description="Tags for categorization")
    created_at: str = Field(description="ISO 8601 creation timestamp")
    updated_at: str = Field(description="ISO 8601 last update timestamp")

    @classmethod
    def from_prompt(cls, prompt: SavedPrompt) -> PromptResponse:
        """Create response from domain model."""
        return cls(
            id=prompt.id,
            name=prompt.name,
            content=prompt.content,
            scope=prompt.scope,
            project_repo=prompt.project_repo,
            tags=prompt.tags,
            created_at=prompt.created_at.isoformat(),
            updated_at=prompt.updated_at.isoformat(),
        )


class ErrorResponse(BaseModel):
    """Response model for errors."""

    detail: str = Field(description="Human-readable error message")


# --- Router factory ---


def create_prompts_router(
    prompt_service: PromptService,
    *,
    prefix: str = "/api/v1/volundr",
) -> APIRouter:
    """Create FastAPI router for saved prompt endpoints."""
    router = APIRouter(prefix=prefix)

    @router.get("/prompts", response_model=list[PromptResponse], tags=["Prompts"])
    async def list_prompts(
        scope: PromptScope | None = Query(
            default=None,
            description="Filter by scope (global or project)",
        ),
        repo: str | None = Query(
            default=None,
            description="Filter by repository URL",
        ),
    ) -> list[PromptResponse]:
        """List saved prompts with optional scope/repo filter."""
        prompts = await prompt_service.list_prompts(scope=scope, repo=repo)
        return [PromptResponse.from_prompt(p) for p in prompts]

    @router.post(
        "/prompts",
        response_model=PromptResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Prompts"],
    )
    async def create_prompt(data: PromptCreate) -> PromptResponse:
        """Create a new saved prompt."""
        prompt = await prompt_service.create_prompt(
            name=data.name,
            content=data.content,
            scope=data.scope,
            project_repo=data.project_repo,
            tags=data.tags,
        )
        return PromptResponse.from_prompt(prompt)

    @router.put(
        "/prompts/{prompt_id}",
        response_model=PromptResponse,
        responses={404: {"model": ErrorResponse}},
        tags=["Prompts"],
    )
    async def update_prompt(
        prompt_id: UUID = Path(description="Unique identifier of the prompt to update"),
        data: PromptUpdate = ...,
    ) -> PromptResponse:
        """Update a saved prompt."""
        try:
            prompt = await prompt_service.update_prompt(
                prompt_id=prompt_id,
                name=data.name,
                content=data.content,
                scope=data.scope,
                project_repo=data.project_repo,
                tags=data.tags,
            )
            return PromptResponse.from_prompt(prompt)
        except PromptNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Prompt not found: {prompt_id}",
            )

    @router.delete(
        "/prompts/{prompt_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        responses={404: {"model": ErrorResponse}},
        tags=["Prompts"],
    )
    async def delete_prompt(
        prompt_id: UUID = Path(description="Unique identifier of the prompt to delete"),
    ) -> None:
        """Delete a saved prompt."""
        try:
            await prompt_service.delete_prompt(prompt_id)
        except PromptNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Prompt not found: {prompt_id}",
            )

    @router.get(
        "/prompts/search",
        response_model=list[PromptResponse],
        tags=["Prompts"],
    )
    async def search_prompts(
        q: str = Query(
            ...,
            min_length=1,
            description="Search query to match against prompt name and content",
        ),
    ) -> list[PromptResponse]:
        """Search saved prompts by name and content."""
        prompts = await prompt_service.search_prompts(q)
        return [PromptResponse.from_prompt(p) for p in prompts]

    return router
