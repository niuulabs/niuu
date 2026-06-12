"""REST surface for the central ODIN review queue."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ravn.adapters.review import FileReviewQueueStore
from ravn.odin.review import ReviewKind, ReviewStatus, capability_for_kind
from ravn.odin.review_service import OdinReviewService, ReviewDecisionError
from ravn.ports.review_queue import ReviewQueueStore

logger = logging.getLogger(__name__)

REVIEW_DATABASE_URL_ENV = "RAVN_ODIN_REVIEW_DATABASE_URL"
REVIEW_STORE_PATH_ENV = "RAVN_ODIN_REVIEW_STORE_PATH"
REVIEW_DEFAULT_TTL_ENV = "RAVN_ODIN_REVIEW_DEFAULT_TTL_SECONDS"
REVIEW_TTL_KIND_ENV_PREFIX = "RAVN_ODIN_REVIEW_TTL_SECONDS_"
REVIEW_SWEEP_INTERVAL_ENV = "RAVN_ODIN_REVIEW_SWEEP_INTERVAL_SECONDS"

DEFAULT_REVIEW_STORE_PATH = "~/.ravn/odin_review_queue.json"
DEFAULT_REVIEW_SWEEP_INTERVAL_SECONDS = 60.0


class ReviewDecisionRequest(BaseModel):
    decision: str
    reason: str = ""
    participantId: str = ""  # noqa: N815


def build_review_queue_store_from_env() -> ReviewQueueStore:
    """Postgres when a DSN is configured, otherwise a durable local file."""
    dsn = os.environ.get(REVIEW_DATABASE_URL_ENV, "").strip()
    if dsn:
        from ravn.adapters.review import PostgresReviewQueueStore  # noqa: PLC0415

        return _LazyPostgresReviewQueueStore(dsn, PostgresReviewQueueStore)
    path = os.environ.get(REVIEW_STORE_PATH_ENV, "").strip() or DEFAULT_REVIEW_STORE_PATH
    return FileReviewQueueStore(Path(path).expanduser())


def build_review_ttls_from_env() -> tuple[dict[str, float], float]:
    """Per-kind TTLs plus the default; 0 means a kind never expires."""
    default_ttl = _env_float(os.environ.get(REVIEW_DEFAULT_TTL_ENV), 0.0)
    ttls: dict[str, float] = {}
    for kind in ReviewKind:
        raw = os.environ.get(f"{REVIEW_TTL_KIND_ENV_PREFIX}{kind.value.upper()}")
        if raw is not None:
            ttls[kind.value] = _env_float(raw, default_ttl)
    return ttls, default_ttl


def review_sweep_interval_from_env() -> float:
    return _env_float(
        os.environ.get(REVIEW_SWEEP_INTERVAL_ENV),
        DEFAULT_REVIEW_SWEEP_INTERVAL_SECONDS,
    )


class _LazyPostgresReviewQueueStore(ReviewQueueStore):
    """Connect to Postgres on first use so app startup never blocks on the DB."""

    def __init__(self, dsn: str, store_cls: type) -> None:
        self._dsn = dsn
        self._store_cls = store_cls
        self._store: ReviewQueueStore | None = None

    async def _delegate(self) -> ReviewQueueStore:
        if self._store is None:
            import asyncpg  # noqa: PLC0415

            pool = await asyncpg.create_pool(dsn=self._dsn)
            self._store = self._store_cls(pool)
        return self._store

    async def upsert(self, item):  # noqa: ANN001, ANN201
        return await (await self._delegate()).upsert(item)

    async def get(self, item_id: str):  # noqa: ANN201
        return await (await self._delegate()).get(item_id)

    async def list_items(self, **kwargs):  # noqa: ANN003, ANN201
        return await (await self._delegate()).list_items(**kwargs)

    async def counts(self):  # noqa: ANN201
        return await (await self._delegate()).counts()


def create_odin_review_router(
    service: OdinReviewService,
    *,
    room_client: Any | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/ravn/odin", tags=["ODIN Review"])

    async def _require_capability(participant_id: str, capability: str) -> None:
        if room_client is None:
            logger.warning(
                "odin review: no Skuld room client configured; %s capability for %r not enforced",
                capability,
                participant_id or "anonymous",
            )
            return
        if not participant_id:
            raise HTTPException(
                status_code=403,
                detail=f"participantId with the {capability!r} capability is required",
            )
        await room_client.require_capability(participant_id, capability)

    @router.get("/reviews")
    async def list_reviews(
        status: str = "",
        kind: str = "",
        environment_id: str = "",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        items = await service.list_items(
            status=status or None,
            kind=kind or None,
            environment_id=environment_id or None,
            limit=limit,
        )
        return [item.to_payload() for item in items]

    @router.get("/reviews/summary")
    async def review_summary() -> dict[str, Any]:
        counts = await service.counts()
        pending = await service.list_items(status=ReviewStatus.PENDING.value)
        by_kind: dict[str, int] = {}
        by_risk: dict[str, int] = {}
        for item in pending:
            by_kind[item.kind] = by_kind.get(item.kind, 0) + 1
            by_risk[item.risk_class] = by_risk.get(item.risk_class, 0) + 1
        return {
            "countsByStatus": counts,
            "pendingByKind": by_kind,
            "pendingByRisk": by_risk,
            "pendingTotal": len(pending),
        }

    @router.get("/reviews/{item_id}")
    async def get_review(item_id: str) -> dict[str, Any]:
        item = await service.get(item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Review item not found")
        return item.to_payload()

    @router.post("/reviews/{item_id}/decide")
    async def decide_review(item_id: str, request: ReviewDecisionRequest) -> dict[str, Any]:
        item = await service.get(item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Review item not found")
        capability = item.requested_capability or capability_for_kind(item.kind)
        await _require_capability(request.participantId, capability)
        operator_id = request.participantId or "operator"
        try:
            decided, delivery = await service.decide(
                item_id,
                decision=request.decision,
                operator_id=operator_id,
                reason=request.reason,
            )
        except ReviewDecisionError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        return {"item": decided.to_payload(), "commandDelivery": delivery}

    return router


def _env_float(value: str | None, default: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default
