"""FastAPI REST adapter for the feature module system."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from niuu.http_compat import LegacyRouteNotice, warn_on_legacy_route
from volundr.adapters.inbound.auth import extract_principal, require_role
from volundr.domain.models import Principal
from volundr.domain.services.feature import FeatureModule, FeatureService, UserFeaturePreference

logger = logging.getLogger(__name__)


# ── Response / request models ──────────────────────────────────────


class FeatureModuleResponse(BaseModel):
    """A single feature module in the catalog."""

    key: str
    label: str
    icon: str
    scope: str
    enabled: bool
    default_enabled: bool
    admin_only: bool
    order: int


class FeatureToggleRequest(BaseModel):
    """Request body for toggling a feature."""

    enabled: bool = Field(description="Whether the feature should be enabled")


class UserFeaturePreferenceResponse(BaseModel):
    """A single user feature preference."""

    feature_key: str
    visible: bool
    sort_order: int


class UserFeaturePreferenceUpdate(BaseModel):
    """A single preference entry in an update request."""

    feature_key: str
    visible: bool = True
    sort_order: int = 0


class UserPreferencesUpdateRequest(BaseModel):
    """Request body for updating user feature preferences."""

    preferences: list[UserFeaturePreferenceUpdate]


FeaturePreferencesPayload = list[UserFeaturePreferenceUpdate] | UserPreferencesUpdateRequest


# ── Helpers ────────────────────────────────────────────────────────


def _module_to_response(m: FeatureModule) -> FeatureModuleResponse:
    return FeatureModuleResponse(
        key=m.key,
        label=m.label,
        icon=m.icon,
        scope=m.scope,
        enabled=m.enabled,
        default_enabled=m.default_enabled,
        admin_only=m.admin_only,
        order=m.order,
    )


def _pref_to_response(p: UserFeaturePreference) -> UserFeaturePreferenceResponse:
    return UserFeaturePreferenceResponse(
        feature_key=p.feature_key,
        visible=p.visible,
        sort_order=p.sort_order,
    )


def _normalize_preferences(body: FeaturePreferencesPayload) -> list[UserFeaturePreferenceUpdate]:
    if isinstance(body, UserPreferencesUpdateRequest):
        return body.preferences
    return body


async def _get_catalog_response(
    *,
    feature_service: FeatureService,
    principal: Principal,
    scope: str | None,
) -> list[FeatureModuleResponse]:
    is_admin = "volundr:admin" in principal.roles
    include_disabled = is_admin

    modules = await feature_service.get_catalog(
        scope=scope,
        include_disabled=include_disabled,
    )

    if not is_admin:
        modules = [m for m in modules if not m.admin_only]

    return [_module_to_response(m) for m in modules]


async def _toggle_feature_response(
    *,
    feature_service: FeatureService,
    key: str,
    enabled: bool,
) -> FeatureModuleResponse:
    try:
        await feature_service.set_feature_enabled(key, enabled)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    modules = await feature_service.get_catalog(include_disabled=True)
    for module in modules:
        if module.key == key:
            return _module_to_response(module)

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Feature '{key}' not found",
    )


async def _get_preferences_response(
    *,
    feature_service: FeatureService,
    principal: Principal,
) -> list[UserFeaturePreferenceResponse]:
    prefs = await feature_service.get_user_preferences(principal.user_id)
    return [_pref_to_response(p) for p in prefs]


async def _update_preferences_response(
    *,
    feature_service: FeatureService,
    principal: Principal,
    body: FeaturePreferencesPayload,
) -> list[UserFeaturePreferenceResponse]:
    prefs = [
        UserFeaturePreference(
            feature_key=p.feature_key,
            visible=p.visible,
            sort_order=p.sort_order,
        )
        for p in _normalize_preferences(body)
    ]
    updated = await feature_service.update_user_preferences(principal.user_id, prefs)
    return [_pref_to_response(p) for p in updated]


# ── Router factory ─────────────────────────────────────────────────


def create_features_router(feature_service: FeatureService) -> APIRouter:
    """Create the feature modules router."""
    router = APIRouter(prefix="/api/v1/volundr", tags=["Features"])

    @router.get("/features", response_model=list[FeatureModuleResponse])
    @router.get("/features/modules", response_model=list[FeatureModuleResponse])
    async def get_features(
        request: Request,
        response: Response,
        scope: str | None = None,
        principal: Principal = Depends(extract_principal),
    ):
        """Get the legacy feature catalog route."""
        warn_on_legacy_route(
            request=request,
            response=response,
            notice=LegacyRouteNotice(
                legacy_path=request.url.path,
                canonical_path="/api/v1/features/modules",
            ),
        )
        return await _get_catalog_response(
            feature_service=feature_service,
            principal=principal,
            scope=scope,
        )

    @router.put("/features/{key}/toggle", response_model=FeatureModuleResponse)
    @router.post("/features/modules/{key}/toggle", response_model=FeatureModuleResponse)
    async def toggle_feature(
        request: Request,
        response: Response,
        key: str,
        body: FeatureToggleRequest,
        _: Principal = Depends(require_role("volundr:admin")),
    ):
        """Admin: enable or disable a feature globally via the legacy route."""
        warn_on_legacy_route(
            request=request,
            response=response,
            notice=LegacyRouteNotice(
                legacy_path=request.url.path,
                canonical_path=f"/api/v1/features/modules/{key}/toggle",
            ),
        )
        return await _toggle_feature_response(
            feature_service=feature_service,
            key=key,
            enabled=body.enabled,
        )

    @router.get(
        "/features/preferences",
        response_model=list[UserFeaturePreferenceResponse],
    )
    async def get_user_preferences(
        request: Request,
        response: Response,
        principal: Principal = Depends(extract_principal),
    ):
        """Get the current user's preferences via the legacy route."""
        warn_on_legacy_route(
            request=request,
            response=response,
            notice=LegacyRouteNotice(
                legacy_path="/api/v1/volundr/features/preferences",
                canonical_path="/api/v1/features/preferences",
            ),
        )
        return await _get_preferences_response(
            feature_service=feature_service,
            principal=principal,
        )

    @router.put(
        "/features/preferences",
        response_model=list[UserFeaturePreferenceResponse],
    )
    async def update_user_preferences(
        request: Request,
        response: Response,
        body: FeaturePreferencesPayload,
        principal: Principal = Depends(extract_principal),
    ):
        """Update the current user's preferences via the legacy route."""
        warn_on_legacy_route(
            request=request,
            response=response,
            notice=LegacyRouteNotice(
                legacy_path="/api/v1/volundr/features/preferences",
                canonical_path="/api/v1/features/preferences",
            ),
        )
        return await _update_preferences_response(
            feature_service=feature_service,
            principal=principal,
            body=body,
        )

    return router


def create_feature_catalog_router(feature_service: FeatureService) -> APIRouter:
    """Create the canonical feature catalog router."""
    router = APIRouter(prefix="/api/v1/features", tags=["Features"])

    @router.get("", response_model=list[FeatureModuleResponse])
    @router.get("/modules", response_model=list[FeatureModuleResponse])
    async def get_features(
        scope: str | None = None,
        principal: Principal = Depends(extract_principal),
    ):
        """Get the canonical feature catalog."""
        return await _get_catalog_response(
            feature_service=feature_service,
            principal=principal,
            scope=scope,
        )

    @router.patch("/modules/{key}", response_model=FeatureModuleResponse)
    @router.post("/modules/{key}/toggle", response_model=FeatureModuleResponse)
    @router.patch("/{key}", response_model=FeatureModuleResponse)
    async def toggle_feature(
        key: str,
        body: FeatureToggleRequest,
        _: Principal = Depends(require_role("volundr:admin")),
    ):
        """Admin: enable or disable a feature via canonical routes."""
        return await _toggle_feature_response(
            feature_service=feature_service,
            key=key,
            enabled=body.enabled,
        )

    @router.get(
        "/preferences",
        response_model=list[UserFeaturePreferenceResponse],
    )
    async def get_user_preferences(
        principal: Principal = Depends(extract_principal),
    ):
        """Get the current user's feature layout preferences."""
        return await _get_preferences_response(
            feature_service=feature_service,
            principal=principal,
        )

    @router.put(
        "/preferences",
        response_model=list[UserFeaturePreferenceResponse],
    )
    async def update_user_preferences(
        body: FeaturePreferencesPayload,
        principal: Principal = Depends(extract_principal),
    ):
        """Update the current user's feature layout preferences."""
        return await _update_preferences_response(
            feature_service=feature_service,
            principal=principal,
            body=body,
        )

    return router
