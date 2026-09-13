"""FastAPI REST adapter for admin settings."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    model_serializer,
    model_validator,
)

from niuu.settings_schema import (
    SettingsFieldSchema,
    SettingsProviderSchema,
    SettingsSectionSchema,
)
from volundr.adapters.inbound.auth import require_role
from volundr.domain.models import Principal

logger = logging.getLogger(__name__)


def _sanitize_log(value: object) -> str:
    """Sanitize a value for safe log output (prevent log injection)."""
    return str(value).replace("\n", "\\n").replace("\r", "\\r")


class AdminStorageSettings(BaseModel):
    """Response/request model for storage settings."""

    model_config = ConfigDict(populate_by_name=True)

    home_enabled: bool = Field(
        description="Whether home PVC provisioning is enabled for users",
        validation_alias=AliasChoices("home_enabled", "homeEnabled"),
    )
    file_manager_enabled: bool = Field(
        default=True,
        description="Whether the file manager tab is visible in sessions",
        validation_alias=AliasChoices("file_manager_enabled", "fileManagerEnabled"),
    )

    @model_serializer(mode="wrap")
    def serialize_with_aliases(self, handler):
        payload = handler(self)
        payload["homeEnabled"] = payload.get("home_enabled")
        payload["fileManagerEnabled"] = payload.get("file_manager_enabled")
        return payload


class AdminStorageUpdate(BaseModel):
    """The Settings → Forge → Storage form: the fields it shows, any subset."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    home_enabled: bool | None = Field(
        default=None,
        description="Whether home PVC provisioning is enabled for users",
        validation_alias=AliasChoices("home_enabled", "homeEnabled"),
    )
    file_manager_enabled: bool | None = Field(
        default=None,
        description="Whether the file manager tab is visible in sessions",
        validation_alias=AliasChoices("file_manager_enabled", "fileManagerEnabled"),
    )

    @model_validator(mode="after")
    def _at_least_one_field(self) -> AdminStorageUpdate:
        if self.home_enabled is None and self.file_manager_enabled is None:
            raise ValueError("send homeEnabled and/or fileManagerEnabled; nothing to update")
        return self


class AdminSettingsResponse(BaseModel):
    """Full admin settings response."""

    storage: AdminStorageSettings = Field(
        description="Storage-related settings",
    )


class AdminSettingsUpdate(BaseModel):
    """Request model for updating admin settings (nested form)."""

    model_config = ConfigDict(extra="forbid")

    storage: AdminStorageUpdate = Field(
        description="Storage settings to update",
    )


def create_admin_settings_router() -> APIRouter:
    """Create the Forge admin settings router."""
    router = APIRouter(prefix="/api/v1/forge", tags=["Admin Settings"])

    def _build_mounted_settings_schema(request: Request) -> SettingsProviderSchema:
        settings = request.app.state.admin_settings
        storage = settings.get("storage", {})
        return SettingsProviderSchema(
            title="Forge",
            subtitle="forge platform settings",
            scope="admin",
            sections=[
                SettingsSectionSchema(
                    id="storage",
                    label="Storage",
                    description="Administrative storage controls for the mounted Forge host.",
                    path="/admin/settings/storage",
                    save_label="Save storage settings",
                    fields=[
                        SettingsFieldSchema(
                            key="homeEnabled",
                            label="Home Volumes Enabled",
                            type="boolean",
                            value=storage.get("home_enabled", True),
                            description="Whether home PVC provisioning is available for users.",
                        ),
                        SettingsFieldSchema(
                            key="fileManagerEnabled",
                            label="File Manager Enabled",
                            type="boolean",
                            value=storage.get("file_manager_enabled", True),
                            description=(
                                "Whether the file manager tab is visible in Forge sessions."
                            ),
                        ),
                    ],
                )
            ],
        )

    @router.get("/admin/settings", response_model=AdminSettingsResponse)
    async def get_admin_settings(
        request: Request,
        _: Principal = Depends(require_role("volundr:admin")),
    ):
        """Get admin settings (admin only)."""
        settings = request.app.state.admin_settings
        storage = settings.get("storage", {})
        return AdminSettingsResponse(
            storage=AdminStorageSettings(
                home_enabled=storage.get("home_enabled", True),
                file_manager_enabled=storage.get("file_manager_enabled", True),
            ),
        )

    @router.get("/admin/settings/schema", response_model=SettingsProviderSchema)
    async def get_mounted_settings_schema(
        request: Request,
        _: Principal = Depends(require_role("volundr:admin")),
    ) -> SettingsProviderSchema:
        return _build_mounted_settings_schema(request)

    @router.get("/settings", response_model=SettingsProviderSchema)
    async def get_settings_schema(
        request: Request,
        _: Principal = Depends(require_role("volundr:admin")),
    ) -> SettingsProviderSchema:
        """Return the canonical mounted settings schema for the unified settings shell."""
        return _build_mounted_settings_schema(request)

    def _apply_storage_update(request: Request, update: AdminStorageUpdate) -> AdminStorageSettings:
        settings = request.app.state.admin_settings
        storage = settings.setdefault("storage", {})
        if update.home_enabled is not None:
            storage["home_enabled"] = update.home_enabled
        if update.file_manager_enabled is not None:
            storage["file_manager_enabled"] = update.file_manager_enabled
        logger.info(
            "Admin updated storage settings: home_enabled=%s, file_manager_enabled=%s",
            _sanitize_log(storage.get("home_enabled", True)),
            _sanitize_log(storage.get("file_manager_enabled", True)),
        )
        return AdminStorageSettings(
            home_enabled=storage.get("home_enabled", True),
            file_manager_enabled=storage.get("file_manager_enabled", True),
        )

    @router.patch("/admin/settings/storage", response_model=AdminStorageSettings)
    async def update_storage_settings(
        body: AdminStorageUpdate,
        request: Request,
        _: Principal = Depends(require_role("volundr:admin")),
    ) -> AdminStorageSettings:
        """Save the Storage section of the settings page (admin only)."""
        return _apply_storage_update(request, body)

    @router.patch("/admin/settings", response_model=AdminSettingsResponse)
    @router.put("/admin/settings", response_model=AdminSettingsResponse)
    async def update_admin_settings(
        body: AdminSettingsUpdate,
        request: Request,
        _: Principal = Depends(require_role("volundr:admin")),
    ):
        """Update admin settings (admin only)."""
        return AdminSettingsResponse(storage=_apply_storage_update(request, body.storage))

    return router
