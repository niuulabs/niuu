"""FastAPI REST adapter for admin settings."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
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
from volundr.domain.ports import AdminSettingsRepository

logger = logging.getLogger(__name__)

STORAGE_SECTION = "storage"


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


def create_admin_settings_router(
    repository: AdminSettingsRepository,
    *,
    home_volumes_supported: bool,
) -> APIRouter:
    """Create the Forge admin settings router.

    ``repository`` persists every save; ``request.app.state.admin_settings`` is
    the in-process copy the session contributors and feature flags read, loaded
    from the repository at startup. ``home_volumes_supported`` is the storage
    adapter's capability: without it the Home Volumes toggle is not offered and
    not accepted.
    """
    router = APIRouter(prefix="/api/v1/forge", tags=["Admin Settings"])

    def _storage_fields(storage: dict) -> list[SettingsFieldSchema]:
        fields: list[SettingsFieldSchema] = []
        if home_volumes_supported:
            fields.append(
                SettingsFieldSchema(
                    key="homeEnabled",
                    label="Home Volumes Enabled",
                    type="boolean",
                    value=storage.get("home_enabled", True),
                    description="Whether home PVC provisioning is available for users.",
                )
            )
        fields.append(
            SettingsFieldSchema(
                key="fileManagerEnabled",
                label="File Manager Enabled",
                type="boolean",
                value=storage.get("file_manager_enabled", True),
                description="Whether the file manager tab is visible in Forge sessions.",
            )
        )
        return fields

    def _build_mounted_settings_schema(request: Request) -> SettingsProviderSchema:
        settings = request.app.state.admin_settings
        storage = settings.get(STORAGE_SECTION, {})
        return SettingsProviderSchema(
            title="Forge",
            subtitle="forge platform settings",
            scope="admin",
            sections=[
                SettingsSectionSchema(
                    id=STORAGE_SECTION,
                    label="Storage",
                    description="Administrative storage controls for the mounted Forge host.",
                    path="/admin/settings/storage",
                    save_label="Save storage settings",
                    fields=_storage_fields(storage),
                )
            ],
        )

    def _current_storage(request: Request) -> AdminStorageSettings:
        storage = request.app.state.admin_settings.get(STORAGE_SECTION, {})
        return AdminStorageSettings(
            home_enabled=storage.get("home_enabled", True),
            file_manager_enabled=storage.get("file_manager_enabled", True),
        )

    async def _apply_storage_update(
        request: Request, update: AdminStorageUpdate
    ) -> AdminStorageSettings:
        if update.home_enabled is not None and not home_volumes_supported:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "Home volumes are not available with this storage adapter, "
                    "so homeEnabled cannot be set here."
                ),
            )
        settings = request.app.state.admin_settings
        storage = settings.setdefault(STORAGE_SECTION, {})
        if update.home_enabled is not None:
            storage["home_enabled"] = update.home_enabled
        if update.file_manager_enabled is not None:
            storage["file_manager_enabled"] = update.file_manager_enabled
        await repository.save(STORAGE_SECTION, dict(storage))
        logger.info(
            "Admin updated storage settings: home_enabled=%s, file_manager_enabled=%s",
            _sanitize_log(storage.get("home_enabled", True)),
            _sanitize_log(storage.get("file_manager_enabled", True)),
        )
        return _current_storage(request)

    @router.get("/admin/settings", response_model=AdminSettingsResponse)
    async def get_admin_settings(
        request: Request,
        _: Principal = Depends(require_role("volundr:admin")),
    ):
        """Get admin settings (admin only)."""
        return AdminSettingsResponse(storage=_current_storage(request))

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

    @router.patch("/admin/settings/storage", response_model=AdminStorageSettings)
    async def update_storage_settings(
        body: AdminStorageUpdate,
        request: Request,
        _: Principal = Depends(require_role("volundr:admin")),
    ) -> AdminStorageSettings:
        """Save the Storage section of the settings page (admin only)."""
        return await _apply_storage_update(request, body)

    @router.patch("/admin/settings", response_model=AdminSettingsResponse)
    @router.put("/admin/settings", response_model=AdminSettingsResponse)
    async def update_admin_settings(
        body: AdminSettingsUpdate,
        request: Request,
        _: Principal = Depends(require_role("volundr:admin")),
    ):
        """Update admin settings (admin only)."""
        return AdminSettingsResponse(storage=await _apply_storage_update(request, body.storage))

    return router
