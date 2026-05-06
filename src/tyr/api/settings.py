"""Compatibility settings endpoints for Tyr's web-next HTTP adapter.

These routes own operator-facing settings under ``/api/v1/tyr/settings/*``.
They intentionally remain separate from runtime flock execution config served by
``/api/v1/tyr/flock/config`` so the host can mount settings independently from
workflow execution concerns.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from tyr.api.settings_store import get_tyr_settings_store, now_utc


class RetryPolicyResponse(BaseModel):
    max_retries: int
    retry_delay_seconds: int
    escalate_on_exhaustion: bool


class FlockSettingsResponse(BaseModel):
    flock_name: str
    default_base_branch: str
    default_tracker_type: str
    default_repos: list[str]
    max_active_sagas: int
    auto_create_milestones: bool
    updated_at: datetime


class DispatchDefaultsResponse(BaseModel):
    confidence_threshold: float
    max_concurrent_raids: int
    auto_continue: bool
    batch_size: int
    retry_policy: RetryPolicyResponse
    quiet_hours: str | None = None
    escalate_after: str | None = None
    updated_at: datetime


class NotificationSettingsResponse(BaseModel):
    channel: Literal["telegram", "email", "webhook", "none"]
    on_raid_pending_approval: bool
    on_raid_merged: bool
    on_raid_failed: bool
    on_saga_complete: bool
    on_dispatcher_error: bool
    webhook_url: str | None = None
    updated_at: datetime


class FlockSettingsPatch(BaseModel):
    flock_name: str | None = None
    default_base_branch: str | None = None
    default_tracker_type: str | None = None
    default_repos: list[str] | None = None
    max_active_sagas: int | None = Field(default=None, ge=1)
    auto_create_milestones: bool | None = None


class RetryPolicyPatch(BaseModel):
    max_retries: int | None = Field(default=None, ge=0)
    retry_delay_seconds: int | None = Field(default=None, ge=0)
    escalate_on_exhaustion: bool | None = None


class DispatchDefaultsPatch(BaseModel):
    confidence_threshold: float | None = Field(default=None, ge=0.0, le=100.0)
    max_concurrent_raids: int | None = Field(default=None, ge=1, le=20)
    auto_continue: bool | None = None
    batch_size: int | None = Field(default=None, ge=1)
    retry_policy: RetryPolicyPatch | None = None
    quiet_hours: str | None = None
    escalate_after: str | None = None


class NotificationSettingsPatch(BaseModel):
    channel: Literal["telegram", "email", "webhook", "none"] | None = None
    on_raid_pending_approval: bool | None = None
    on_raid_merged: bool | None = None
    on_raid_failed: bool | None = None
    on_saga_complete: bool | None = None
    on_dispatcher_error: bool | None = None
    webhook_url: str | None = None


def create_settings_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/tyr/settings", tags=["Tyr Settings"])

    @router.get("/flock", response_model=FlockSettingsResponse)
    async def get_flock_settings(request: Request) -> FlockSettingsResponse:
        return FlockSettingsResponse.model_validate(get_tyr_settings_store(request)["flock"])

    @router.patch("/flock", response_model=FlockSettingsResponse)
    async def patch_flock_settings(
        request: Request,
        body: FlockSettingsPatch,
    ) -> FlockSettingsResponse:
        flock = get_tyr_settings_store(request)["flock"]
        flock.update(body.model_dump(exclude_none=True))
        flock["updated_at"] = now_utc()
        return FlockSettingsResponse.model_validate(flock)

    @router.get("/dispatch", response_model=DispatchDefaultsResponse)
    async def get_dispatch_defaults(request: Request) -> DispatchDefaultsResponse:
        return DispatchDefaultsResponse.model_validate(get_tyr_settings_store(request)["dispatch"])

    @router.patch("/dispatch", response_model=DispatchDefaultsResponse)
    async def patch_dispatch_defaults(
        request: Request,
        body: DispatchDefaultsPatch,
    ) -> DispatchDefaultsResponse:
        dispatch = get_tyr_settings_store(request)["dispatch"]
        patch = body.model_dump(exclude_none=True)
        retry_policy = patch.pop("retry_policy", None)
        dispatch.update(patch)
        if retry_policy:
            dispatch["retry_policy"].update(retry_policy)
        dispatch["updated_at"] = now_utc()
        request.app.state.settings.notification.confidence_threshold = dispatch[
            "confidence_threshold"
        ]
        request.app.state.settings.watcher.batch_size = dispatch["batch_size"]
        return DispatchDefaultsResponse.model_validate(dispatch)

    @router.get("/notifications", response_model=NotificationSettingsResponse)
    async def get_notification_settings(request: Request) -> NotificationSettingsResponse:
        notifications = get_tyr_settings_store(request)["notifications"]
        return NotificationSettingsResponse.model_validate(notifications)

    @router.patch("/notifications", response_model=NotificationSettingsResponse)
    async def patch_notification_settings(
        request: Request,
        body: NotificationSettingsPatch,
    ) -> NotificationSettingsResponse:
        notifications = get_tyr_settings_store(request)["notifications"]
        patch = body.model_dump(exclude_none=True)
        notifications.update(patch)
        if notifications["channel"] != "webhook":
            notifications["webhook_url"] = None
        if notifications["channel"] == "webhook" and not notifications["webhook_url"]:
            raise HTTPException(
                status_code=422,
                detail="webhook_url is required when channel is webhook",
            )
        notifications["updated_at"] = now_utc()
        request.app.state.settings.notification.enabled = notifications[
            "channel"
        ] != "none" and any(
            notifications[key]
            for key in (
                "on_raid_pending_approval",
                "on_raid_merged",
                "on_raid_failed",
                "on_saga_complete",
                "on_dispatcher_error",
            )
        )
        return NotificationSettingsResponse.model_validate(notifications)

    return router
