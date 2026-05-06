from __future__ import annotations

from uuid import uuid4

from volundr.domain.models import (
    CommunicationPlatform,
    GitSource,
    Session,
    SessionCommunicationTarget,
    SessionStatus,
)
from volundr.domain.services.session import SessionService


class _FakeSessionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.updated: list[Session] = []

    async def get(self, session_id):
        if self.session.id == session_id:
            return self.session
        return None

    async def update(self, session: Session) -> Session:
        self.session = session
        self.updated.append(session)
        return session


class _FakePodManager:
    async def wait_for_ready(self, session: Session, timeout: float):
        return SessionStatus.RUNNING

    async def stop(self, session: Session):
        return True


class _FakeCommunicationRouteRepository:
    def __init__(self) -> None:
        self.upserted = []
        self.deactivated = []

    async def upsert_route(self, route):
        self.upserted.append(route)
        return route

    async def get_active_route(self, platform: str, conversation_id: str, thread_id: str | None):
        return None

    async def deactivate_routes_for_session(self, session_id):
        self.deactivated.append(session_id)
        return 1

    async def list_routes_for_session(self, session_id):
        return []


class _FakeSessionCommunicationPort:
    def __init__(self, targets: list[SessionCommunicationTarget]) -> None:
        self.targets = targets

    async def list_communication_targets(self, session_id):
        return list(self.targets)


class _FakeBroadcaster:
    def __init__(self) -> None:
        self.updated = []

    async def publish_session_updated(self, session: Session) -> None:
        self.updated.append(session)


def _provisioning_session() -> Session:
    sid = uuid4()
    return Session(
        id=sid,
        name="raid",
        model="model",
        source=GitSource(),
        status=SessionStatus.PROVISIONING,
        owner_id="dev-user",
        chat_endpoint=f"ws://127.0.0.1:8080/s/{sid}/session",
    )


async def test_registers_communication_routes_when_session_becomes_running():
    session = _provisioning_session()
    repository = _FakeSessionRepository(session)
    route_repository = _FakeCommunicationRouteRepository()
    broadcaster = _FakeBroadcaster()
    service = SessionService(
        repository,
        _FakePodManager(),
        broadcaster=broadcaster,
        communication_route_repository=route_repository,
        session_communication_port=_FakeSessionCommunicationPort(
            [
                SessionCommunicationTarget(
                    platform=CommunicationPlatform.TELEGRAM,
                    conversation_id="-100123",
                    thread_id="11",
                    metadata={"topic_mode": "topic_per_session"},
                )
            ]
        ),
    )

    await service._poll_readiness(session, skip_initial_delay=True)

    assert route_repository.upserted
    route = route_repository.upserted[0]
    assert route.session_id == session.id
    assert route.owner_id == "dev-user"
    assert route.platform == CommunicationPlatform.TELEGRAM
    assert route.conversation_id == "-100123"
    assert route.thread_id == "11"


async def test_stop_session_deactivates_communication_routes():
    session = _provisioning_session().with_status(SessionStatus.RUNNING)
    repository = _FakeSessionRepository(session)
    route_repository = _FakeCommunicationRouteRepository()
    service = SessionService(
        repository,
        _FakePodManager(),
        communication_route_repository=route_repository,
    )

    stopped = await service.stop_session(session.id)

    assert stopped.status == SessionStatus.STOPPED
    assert route_repository.deactivated == [session.id]
