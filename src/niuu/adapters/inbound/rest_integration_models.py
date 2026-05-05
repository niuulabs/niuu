"""Shared Pydantic models for integration REST endpoints.

Used by both Tyr and Volundr integration routers.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_serializer

from niuu.domain.models import IntegrationConnection


class IntegrationResponse(BaseModel):
    """Response model for an integration connection."""

    id: str = Field(description="Unique connection identifier")
    integration_type: str = Field(description="Integration category")
    adapter: str = Field(description="Fully-qualified adapter class path")
    credential_name: str = Field(description="Stored credential name")
    config: dict[str, Any] = Field(description="Adapter-specific configuration")
    enabled: bool = Field(description="Whether the integration is active")
    created_at: str = Field(description="ISO 8601 creation timestamp")
    updated_at: str = Field(description="ISO 8601 last update timestamp")
    slug: str = Field(default="", description="Catalog entry slug")

    @model_serializer(mode="wrap")
    def _serialize_with_camel_case_aliases(self, handler):
        """Expose camelCase compatibility keys alongside canonical snake_case ones."""
        data = handler(self)
        data["integrationType"] = data["integration_type"]
        data["credentialName"] = data["credential_name"]
        data["createdAt"] = data["created_at"]
        data["updatedAt"] = data["updated_at"]
        return data

    @classmethod
    def from_connection(cls, conn: IntegrationConnection) -> IntegrationResponse:
        """Create response from domain model."""
        return cls(
            id=conn.id,
            integration_type=conn.integration_type,
            adapter=conn.adapter,
            credential_name=conn.credential_name,
            config=conn.config,
            enabled=conn.enabled,
            created_at=conn.created_at.isoformat(),
            updated_at=conn.updated_at.isoformat(),
            slug=conn.slug,
        )


class IntegrationToggleRequest(BaseModel):
    """Request model for toggling the enabled flag."""

    enabled: bool = Field(..., description="New enabled status")
