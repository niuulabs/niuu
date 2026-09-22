"""A launch or restart with no free session slot is refused up front, with the
runtime's own remedy in the answer, instead of a session that fails a moment
later with a bare "max concurrent reached"."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.conftest import InMemorySessionRepository, MockPodManager
from volundr.domain.models import GitSource, SessionStatus
from volundr.domain.ports import SessionCapacity
from volundr.domain.services import ForgeService, SessionCapacityError, SessionService


class FullPodManager(MockPodManager):
    def __init__(self, active: int = 4, limit: int = 4) -> None:
        super().__init__()
        self._capacity = SessionCapacity(
            limit=limit,
            active=active,
            remedy="raise the session limit in Settings → Runtime (/settings/runtime/sessions)",
        )

    async def capacity(self) -> SessionCapacity:
        return self._capacity


class ReservedPodManager(FullPodManager):
    async def capacity_for(self, session) -> SessionCapacity:
        return SessionCapacity(
            limit=self._capacity.limit,
            active=self._capacity.active - 1,
            remedy=self._capacity.remedy,
        )


def _source() -> GitSource:
    return GitSource(repo="https://github.com/org/repo", branch="main")


async def test_uncapped_runtime_is_never_refused() -> None:
    service = SessionService(InMemorySessionRepository(), MockPodManager())
    await service.ensure_capacity()


async def test_refusal_names_the_numbers_and_the_remedy() -> None:
    service = SessionService(InMemorySessionRepository(), FullPodManager(active=4, limit=4))
    with pytest.raises(SessionCapacityError) as info:
        await service.ensure_capacity()
    message = str(info.value)
    assert "4 of 4 sessions are running" in message
    assert "Stop or archive a session" in message
    assert "/settings/runtime/sessions" in message
    assert info.value.capacity.available == 0


async def test_a_free_slot_passes() -> None:
    service = SessionService(InMemorySessionRepository(), FullPodManager(active=3, limit=4))
    await service.ensure_capacity()


async def test_start_refuses_before_the_session_flips_to_starting() -> None:
    pod_manager = FullPodManager()
    service = SessionService(InMemorySessionRepository(), pod_manager)
    created = await service.create_session(name="t", model="claude", source=_source())

    with pytest.raises(SessionCapacityError):
        await service.start_session(created.id)

    unchanged = await service.get_session(created.id)
    assert unchanged is not None
    assert unchanged.status == created.status
    assert unchanged.status != SessionStatus.STARTING
    assert pod_manager.start_calls == []


async def test_restart_reuses_capacity_reserved_for_the_same_session() -> None:
    pod_manager = ReservedPodManager(active=1, limit=1)
    service = SessionService(InMemorySessionRepository(), pod_manager)
    created = await service.create_session(name="t", model="claude", source=_source())

    restarted = await service.start_session(created.id)

    assert restarted.status == SessionStatus.STARTING


async def test_create_and_start_checks_capacity_before_creating_a_record() -> None:
    session_service = AsyncMock(spec=SessionService)
    full = SessionCapacity(limit=1, active=1, remedy="raise pod_manager.max_concurrent")
    session_service.ensure_capacity.side_effect = SessionCapacityError(full)
    forge = ForgeService(session_service)
    data = SimpleNamespace(
        name="demo",
        model="claude",
        source=_source(),
        definition="skuldClaude",
        launch_spec=None,
        launch_spec_id=None,
        workspace_id=None,
        issue_id=None,
        issue_url=None,
        terminal_restricted=False,
        credential_names=[],
        integration_ids=[],
        resource_config={},
        system_prompt="",
        initial_prompt="",
        workload_type="session",
        workload_config={},
        persona_name="",
    )

    with pytest.raises(SessionCapacityError):
        await forge.create_and_start_session(data)

    session_service.ensure_capacity.assert_awaited_once()
    session_service.create_session.assert_not_called()
    session_service.start_session.assert_not_called()
