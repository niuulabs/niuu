"""Detailed health endpoint for Ting API.

Returns the status of internal services and infrastructure:
  - Database connection
  - EventBus subscriber count
  - ActivitySubscriber running state
  - NotificationService running state
  - ReviewEngine running state
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class DetailedHealthResponse(BaseModel):
    """Detailed health status for Ting services."""

    status: Literal["ok", "degraded"]
    database: Literal["ok", "unavailable"]
    event_bus_subscriber_count: int
    activity_subscriber_running: bool
    notification_service_running: bool
    review_engine_running: bool


def create_health_router() -> APIRouter:
    """Create a router with the detailed health endpoint."""
    router = APIRouter(prefix="/api/v1/ting/health", tags=["Health"])

    @router.get("/detailed", response_model=DetailedHealthResponse)
    async def detailed_health(request: Request) -> DetailedHealthResponse:
        """Return detailed health status of internal Ting services."""
        db_status = await _check_database(request)

        event_bus = getattr(request.app.state, "event_bus", None)
        event_bus_count = event_bus.client_count if event_bus is not None else 0

        subscriber = getattr(request.app.state, "subscriber", None)
        activity_running = subscriber.running if subscriber is not None else False

        notification_service = getattr(request.app.state, "notification_service", None)
        notification_running = (
            notification_service.running if notification_service is not None else False
        )

        review_engine = getattr(request.app.state, "review_engine", None)
        review_running = review_engine.running if review_engine is not None else False

        services_healthy = activity_running and notification_running and review_running
        overall = "ok" if db_status == "ok" and services_healthy else "degraded"

        return DetailedHealthResponse(
            status=overall,
            database=db_status,
            event_bus_subscriber_count=event_bus_count,
            activity_subscriber_running=activity_running,
            notification_service_running=notification_running,
            review_engine_running=review_running,
        )

    return router


async def _check_database(request: Request) -> str:
    """Return 'ok' if the database pool is reachable, 'unavailable' otherwise."""
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        return "unavailable"
    try:
        await pool.fetchval("SELECT 1")
        return "ok"
    except Exception:
        logger.exception("Database health check failed")
        return "unavailable"
