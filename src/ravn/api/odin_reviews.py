"""REST surface for the central ODIN review queue."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ravn.adapters._pool_sizing import AUX_POOL_MAX_SIZE, AUX_POOL_MIN_SIZE
from ravn.adapters.review import FileReviewQueueStore
from ravn.config import OdinReviewConfig
from ravn.odin.review import ReviewStatus, capability_for_kind
from ravn.odin.review_service import OdinReviewService, ReviewDecisionError
from ravn.ports.review_queue import ReviewQueueStore

logger = logging.getLogger(__name__)


class ReviewDecisionRequest(BaseModel):
    decision: str
    reason: str = ""
    participantId: str = ""  # noqa: N815


def build_review_queue_store(config: OdinReviewConfig) -> ReviewQueueStore:
    """Postgres when a DSN is configured, otherwise a durable local file."""
    dsn = config.database_url.strip()
    if dsn:
        from ravn.adapters.review import PostgresReviewQueueStore  # noqa: PLC0415

        return _LazyPostgresReviewQueueStore(dsn, PostgresReviewQueueStore)
    return FileReviewQueueStore(Path(config.store_path).expanduser())


class _LazyPostgresReviewQueueStore(ReviewQueueStore):
    """Connect to Postgres on first use so app startup never blocks on the DB."""

    def __init__(self, dsn: str, store_cls: type) -> None:
        self._dsn = dsn
        self._store_cls = store_cls
        self._store: ReviewQueueStore | None = None

    async def _delegate(self) -> ReviewQueueStore:
        if self._store is None:
            import asyncpg  # noqa: PLC0415

            pool = await asyncpg.create_pool(
                dsn=self._dsn,
                min_size=AUX_POOL_MIN_SIZE,
                max_size=AUX_POOL_MAX_SIZE,
            )
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
        risk_class: str = "",
        q: str = "",
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        items = await service.list_items(
            status=status or None,
            kind=kind or None,
            environment_id=environment_id or None,
            risk_class=risk_class or None,
            query=q.strip() or None,
            limit=limit,
            offset=offset,
        )
        return [item.to_payload() for item in items]

    @router.get("/reviews/summary")
    async def review_summary(
        kind: str = "",
        environment_id: str = "",
        risk_class: str = "",
        q: str = "",
    ) -> dict[str, Any]:
        counts = await service.counts()
        pending = await service.list_items(
            status=ReviewStatus.PENDING.value,
            kind=kind or None,
            environment_id=environment_id or None,
            risk_class=risk_class or None,
            query=q.strip() or None,
        )
        by_kind: dict[str, int] = {}
        by_risk: dict[str, int] = {}
        by_environment: dict[str, int] = {}
        for item in pending:
            by_kind[item.kind] = by_kind.get(item.kind, 0) + 1
            by_risk[item.risk_class] = by_risk.get(item.risk_class, 0) + 1
            by_environment[item.environment_id] = by_environment.get(item.environment_id, 0) + 1
        return {
            "countsByStatus": counts,
            "pendingByKind": by_kind,
            "pendingByRisk": by_risk,
            "pendingByEnvironment": by_environment,
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
