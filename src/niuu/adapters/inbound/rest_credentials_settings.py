"""Mounted settings schema for the shared credentials service."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from niuu.adapters.inbound.auth import extract_principal
from niuu.domain.models import Principal
from niuu.settings_schema import (
    SettingsCredentialsResourceSchema,
    SettingsProviderSchema,
    SettingsSectionSchema,
)


def create_credentials_settings_router(prefix: str = "/api/v1/credentials") -> APIRouter:
    """Expose the shared credentials settings schema."""

    prefix = prefix.rstrip("/")
    router = APIRouter(prefix=prefix, tags=["Credentials Settings"])

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
                        list_path=f"{prefix}/user",
                        types_path=f"{prefix}/types",
                        create_path=f"{prefix}/user",
                        delete_path=f"{prefix}/user/{{name}}",
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
                            list_path=f"{prefix}/tenant",
                            types_path=f"{prefix}/types",
                            create_path=f"{prefix}/tenant",
                            delete_path=f"{prefix}/tenant/{{name}}",
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
