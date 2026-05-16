"""Mounted settings schema for the shared credentials service."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from niuu.settings_schema import (
    SettingsCredentialsResourceSchema,
    SettingsProviderSchema,
    SettingsSectionSchema,
)
from volundr.adapters.inbound.auth import extract_principal
from volundr.domain.models import Principal


def create_credentials_settings_router() -> APIRouter:
    """Expose the shared credentials settings schema."""

    router = APIRouter(prefix="/api/v1/credentials", tags=["Credentials Settings"])

    @router.get("/settings", response_model=SettingsProviderSchema)
    async def get_credentials_settings(
        principal: Principal = Depends(extract_principal),
    ) -> SettingsProviderSchema:
        sections = [
            SettingsSectionSchema(
                id="user",
                label="User credentials",
                description=(
                    "Store API keys, runtime secrets, and reusable credentials for your own"
                    " sessions, assistants, and integrations."
                ),
                fields=[],
                resources=[
                    SettingsCredentialsResourceSchema(
                        id="user_credentials",
                        label="User credentials",
                        description=(
                            "Create reusable credentials once, then attach them to sessions,"
                            " assistants, or integrations."
                        ),
                        list_path="/api/v1/credentials/user",
                        types_path="/api/v1/credentials/types",
                        create_path="/api/v1/credentials/user",
                        delete_path="/api/v1/credentials/user/{name}",
                    )
                ],
            )
        ]

        if "volundr:admin" in principal.roles:
            sections.append(
                SettingsSectionSchema(
                    id="tenant",
                    label="Tenant credentials",
                    description=(
                        "Shared credentials available to the current tenant for admin-managed"
                        " automation and integrations."
                    ),
                    fields=[],
                    resources=[
                        SettingsCredentialsResourceSchema(
                            id="tenant_credentials",
                            label="Tenant credentials",
                            description=(
                                "Use tenant-scoped credentials for shared automation and"
                                " service accounts."
                            ),
                            list_path="/api/v1/credentials/tenant",
                            types_path="/api/v1/credentials/types",
                            create_path="/api/v1/credentials/tenant",
                            delete_path="/api/v1/credentials/tenant/{name}",
                        )
                    ],
                )
            )

        return SettingsProviderSchema(
            title="Credentials",
            subtitle="stored secrets and runtime keys",
            scope="user",
            sections=sections,
        )

    return router
