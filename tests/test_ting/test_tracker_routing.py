"""Connection-aware tracker routing tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ting.domain.models import Saga, SagaStatus
from ting.domain.tracker_routing import (
    TrackerRoutingError,
    select_tracker,
    select_tracker_for_run,
    select_tracker_for_saga,
)


@dataclass
class _Tracker:
    connection_id: str
    provider: str
    run_owner: str | None = None

    async def get_owner_for_run(self, tracker_id: str) -> str | None:
        return self.run_owner


def _saga(*, connection_id: str = "", provider: str = "jira") -> Saga:
    return Saga(
        id=uuid4(),
        tracker_id="VMAAS",
        tracker_type=provider,
        tracker_connection_id=connection_id,
        slug="vmaas",
        name="VMAAS",
        repos=["ncp/vmaas/services"],
        feature_branch="feat/vmaas",
        base_branch="main",
        status=SagaStatus.ACTIVE,
        confidence=0.0,
        created_at=datetime.now(UTC),
    )


def test_saga_routes_by_connection_even_when_provider_is_shared() -> None:
    first = _Tracker("jira-a", "jira")
    second = _Tracker("jira-b", "jira")

    assert select_tracker_for_saga([first, second], _saga(connection_id="jira-b")) is second


def test_legacy_saga_routes_by_provider_only_when_unique() -> None:
    jira = _Tracker("jira-a", "jira")
    linear = _Tracker("linear-a", "linear")

    assert select_tracker_for_saga([jira, linear], _saga()) is jira


def test_legacy_saga_rejects_ambiguous_provider_connections() -> None:
    with pytest.raises(TrackerRoutingError, match="Multiple 'jira'"):
        select_tracker_for_saga(
            [_Tracker("jira-a", "jira"), _Tracker("jira-b", "jira")],
            _saga(),
        )


def test_unscoped_selection_rejects_multiple_connections() -> None:
    with pytest.raises(TrackerRoutingError, match="tracker_connection_id is required"):
        select_tracker([_Tracker("jira-a", "jira"), _Tracker("linear-a", "linear")])


@pytest.mark.asyncio
async def test_run_routes_through_connection_scoped_progress_owner() -> None:
    first = _Tracker("jira-a", "jira")
    second = _Tracker("jira-b", "jira", run_owner="dev-user")

    selected = await select_tracker_for_run(
        [first, second],
        "ABC-123",
        owner_id="dev-user",
    )

    assert selected is second
