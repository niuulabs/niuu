"""Models and ports for Yggdrasil realm health streaming."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class RealmSummary(BaseModel):
    """The health and selected metrics for one configured realm."""

    model_config = ConfigDict(frozen=True)

    realm_id: str = Field(min_length=1)
    health_status: str = Field(min_length=1)
    metrics: dict[str, bool | float | int | str | None] = Field(default_factory=dict)


class RealmAdapter(Protocol):
    """Fetch the current summary for one configured realm."""

    realm_id: str

    async def get_summary(self) -> RealmSummary:
        """Return the realm's current health summary."""
