"""Configuration for the standalone Mímir service."""

from __future__ import annotations

from pydantic import BaseModel, Field


class MimirServiceConfig(BaseModel):
    """Configuration for a standalone Mímir service instance."""

    path: str = Field(
        default="~/.ravn/mimir",
        description="Root directory for the Mímir knowledge base.",
    )
    host: str = Field(
        default="0.0.0.0",
        description="Host address to bind the service to.",
    )
    port: int = Field(
        default=7477,
        description="Port to bind the service to.",
    )
    name: str = Field(
        default="local",
        description="Instance name used in Sleipnir announce events.",
    )
    role: str = Field(
        default="local",
        description="Instance role: 'shared', 'local', or 'domain'.",
    )
    categories: list[str] | None = Field(
        default=None,
        description="Category filter for domain-scoped Mímirs. None means all categories.",
    )
    announce_url: str | None = Field(
        default=None,
        description=(
            "Public URL this service is reachable at, announced on Sleipnir. "
            "If None, announcement is skipped."
        ),
    )
    search_db: str | None = Field(
        default=None,
        description=(
            "Path to the SQLite database for the hybrid search index. "
            "Defaults to <path>/search.db when None."
        ),
    )
    embedding_model: str | None = Field(
        default=None,
        description=(
            "sentence-transformers model name for semantic search "
            "(e.g. 'all-MiniLM-L6-v2'). "
            "Set to null for FTS-only mode (no sentence-transformers required)."
        ),
    )
