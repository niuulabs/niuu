"""Mounted settings schema for the shared integrations service."""

from __future__ import annotations

from fastapi import APIRouter

from niuu.settings_schema import (
    SettingsIntegrationsResourceSchema,
    SettingsProviderSchema,
    SettingsSectionSchema,
)


def create_integrations_settings_router() -> APIRouter:
    """Expose the shared integrations settings schema."""

    router = APIRouter(prefix="/api/v1/integrations", tags=["Integrations Settings"])

    @router.get("/settings", response_model=SettingsProviderSchema)
    async def get_integrations_settings() -> SettingsProviderSchema:
        return SettingsProviderSchema(
            title="Integrations",
            subtitle="connected services and providers",
            scope="user",
            sections=[
                SettingsSectionSchema(
                    id="connections",
                    label="Connections",
                    description=(
                        "Connect external services, reusable providers, and operator channels to"
                        " the platform."
                    ),
                    fields=[],
                    resources=[
                        SettingsIntegrationsResourceSchema(
                            id="integration_connections",
                            label="Integration connections",
                            description=(
                                "Browse available integrations, connect them with stored or inline"
                                " credentials, and validate that they work."
                            ),
                            list_path="/api/v1/integrations",
                            catalog_path="/api/v1/integrations/catalog",
                            create_path="/api/v1/integrations",
                            delete_path="/api/v1/integrations/{id}",
                            credential_list_path="/api/v1/credentials/user",
                            test_path="/api/v1/integrations/{id}/test",
                            oauth_authorize_path="/api/v1/integrations/oauth/{slug}/authorize",
                            oauth_disconnect_path="/api/v1/integrations/oauth/{slug}/disconnect",
                            enrollment_start_path="/api/v1/integrations/enrollments",
                            enrollment_status_path="/api/v1/integrations/enrollments/{id}",
                            enrollment_cancel_path="/api/v1/integrations/enrollments/{id}",
                        )
                    ],
                )
            ],
        )

    return router
