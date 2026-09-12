"""Let a session hand a refreshed sign-in credential back to the platform.

CLIs such as Grok Build rotate the session tokens in their own auth file
while they run. The file a session gets is a read-only copy, so without this
route the rotated tokens would be lost when the session ends. Only the field
an enrollment produced can be written, only for the caller's own connection.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from volundr.adapters.inbound.auth import extract_principal
from volundr.domain.models import Principal, SecretType
from volundr.domain.ports import CredentialStorePort, IntegrationRepository
from volundr.domain.services.integration_registry import IntegrationRegistry

MAX_VALUE_BYTES = 64 * 1024


class CredentialWriteBackRequest(BaseModel):
    credential_name: str = Field(min_length=1, max_length=253)
    credential_field: str = Field(min_length=1, max_length=253)
    value: str = Field(min_length=1, max_length=MAX_VALUE_BYTES)


class CredentialWriteBackResponse(BaseModel):
    stored: bool
    credential_name: str


def create_credential_writeback_router(
    *,
    integration_repository: IntegrationRepository,
    integration_registry: IntegrationRegistry,
    credential_store: CredentialStorePort,
    prefix: str = "/api/v1/internal/credentials",
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["credentials-internal"])

    @router.post("/writeback", response_model=CredentialWriteBackResponse, include_in_schema=False)
    async def write_back(
        body: CredentialWriteBackRequest,
        principal: Principal = Depends(extract_principal),
    ) -> CredentialWriteBackResponse:
        connections = await integration_repository.list_connections(principal.user_id)
        connection = next(
            (c for c in connections if c.credential_name == body.credential_name), None
        )
        if connection is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No integration connection uses credential {body.credential_name!r}",
            )
        definition = integration_registry.get_definition(connection.slug)
        spec = definition.credential_enrollment if definition is not None else None
        if spec is None or spec.credential_field != body.credential_field:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"Credential {body.credential_name!r} does not accept write-back of field "
                    f"{body.credential_field!r}"
                ),
            )
        stored = await credential_store.get("user", principal.user_id, body.credential_name)
        values = await credential_store.get_value("user", principal.user_id, body.credential_name)
        if stored is None or values is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Credential {body.credential_name!r} is not stored",
            )
        if values.get(body.credential_field) == body.value:
            return CredentialWriteBackResponse(stored=False, credential_name=body.credential_name)
        updated = dict(values)
        updated[body.credential_field] = body.value
        metadata = dict(stored.metadata)
        metadata.update(
            {
                "auth_state": "active",
                "auth_state_updated_at": datetime.now(UTC).isoformat(),
                "auth_written_back_at": datetime.now(UTC).isoformat(),
            }
        )
        metadata.pop("auth_error_code", None)
        await credential_store.store(
            "user",
            principal.user_id,
            body.credential_name,
            stored.secret_type or SecretType.OAUTH_TOKEN,
            updated,
            metadata,
        )
        return CredentialWriteBackResponse(stored=True, credential_name=body.credential_name)

    return router
