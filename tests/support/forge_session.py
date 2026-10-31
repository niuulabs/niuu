"""Test support for Forge session credentials: a real Forge app slice, in memory.

The app has the production middleware, auth dependency and routers (sessions,
notifications, session log, message delivery, HTTP MCP) over in-memory
repositories, a real ``ForgeSessionTokenService`` and an allow-all identity, so
tests exercise session-token verification and enforcement end to end without a
database.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.domain.services.forge_session_token import (
    ForgeSessionTokenService,
    IssuedForgeSessionToken,
)
from niuu.domain.services.workload_identity import WorkloadIdentityService
from tests.conftest import (
    InMemorySessionRepository,
    MockPodManager,
    make_session_participant_service,
)
from tests.support.notifications import InMemoryNotificationStore
from volundr.adapters.inbound.forge_session_auth import ForgeSessionAuthMiddleware
from volundr.adapters.inbound.rest import create_router
from volundr.adapters.inbound.rest_forge_mcp import create_forge_mcp_router
from volundr.adapters.inbound.rest_message_delivery import create_message_delivery_router
from volundr.adapters.inbound.rest_notifications import create_notifications_router
from volundr.adapters.inbound.rest_session_log import create_session_log_router
from volundr.adapters.outbound.identity import AllowAllIdentityAdapter
from volundr.config import ForgeMcpHttpConfig
from volundr.domain.models import Session, SessionLogEntry, SessionStatus, User
from volundr.domain.notifications import NotificationSinkInfo
from volundr.domain.ports import SessionEventLogRepository
from volundr.domain.services.notifications import NotificationService
from volundr.domain.services.session import SessionService

PREFIX = "/api/v1/forge"
OWNER = "owner-1"
TENANT = "tenant-1"
TOKEN_TTL_SECONDS = 3600


class DeliveringPodManager(MockPodManager):
    """A pod manager that, like the local process manager, delivers the credential."""

    @property
    def delivers_forge_session_token(self) -> bool:
        return True


class InMemoryUsers:
    def __init__(self) -> None:
        self.users: dict[str, User] = {}

    async def get(self, user_id: str) -> User | None:
        return self.users.get(user_id)

    async def create(self, user: User) -> User:
        self.users[user.id] = user
        return user


class InMemoryLog(SessionEventLogRepository):
    def __init__(self) -> None:
        self.rows: dict[tuple, SessionLogEntry] = {}

    async def append(self, entries: list[SessionLogEntry]) -> int:
        for entry in entries:
            self.rows.setdefault((entry.session_id, entry.seq), entry)
        return len(entries)

    async def read_after(self, session_id, after_seq=0, limit=1000) -> list[SessionLogEntry]:
        rows = [e for (sid, seq), e in self.rows.items() if sid == session_id and seq > after_seq]
        return sorted(rows, key=lambda e: e.seq)[:limit]

    async def latest_seq(self, session_id) -> int:
        return max((seq for (sid, seq) in self.rows if sid == session_id), default=0)


class InMemoryDeliveries:
    async def claim(self, *args: Any) -> Any:
        raise AssertionError("not used")

    async def settle(self, *args: Any) -> Any:
        raise AssertionError("not used")

    async def get(self, session_id: UUID, request_id: str) -> Any:
        return None


def token_issuer() -> WorkloadIdentityService:
    return WorkloadIdentityService(
        SimpleNamespace(
            enabled=True,
            issuer="niuu-forge-session",
            audiences=["volundr-api"],
            key_id="niuu-forge-session",
        )
    )


def token_service(issuer: WorkloadIdentityService | None = None) -> ForgeSessionTokenService:
    return ForgeSessionTokenService(
        issuer or token_issuer(),
        audiences=["volundr-api"],
        ttl_seconds=TOKEN_TTL_SECONDS,
        key_source="key_file",
    )


@dataclass
class ForgeApp:
    app: FastAPI
    client: TestClient
    sessions: InMemorySessionRepository
    session_service: SessionService
    notifications: NotificationService
    store: InMemoryNotificationStore
    pod_manager: DeliveringPodManager
    tokens: ForgeSessionTokenService
    log: InMemoryLog

    async def session(self, name: str = "self", owner: str = OWNER, **extra: Any) -> Session:
        session = Session(name=name, owner_id=owner, tenant_id=TENANT, **extra)
        return await self.sessions.create(session)

    async def start(self, session: Session, **kwargs: Any) -> IssuedForgeSessionToken:
        """Start ``session`` through the real service; return the credential it minted."""
        await self.session_service.start_session(session.id, **kwargs)
        task = self.session_service._provisioning_tasks.get(session.id)
        if task is not None:
            await task
        started, spec = self.pod_manager.start_calls[-1]
        assert started.id == session.id
        assert spec.forge_session is not None
        return spec.forge_session

    async def set_status(self, session_id: UUID, status: SessionStatus) -> None:
        stored = await self.sessions.get(session_id)
        await self.sessions.update(stored.model_copy(update={"status": status}))


def build_forge_app(
    *,
    tokens: ForgeSessionTokenService | None = None,
    mcp_config: ForgeMcpHttpConfig | None = None,
    **service_kwargs: Any,
):
    """The Forge app slice. ``tokens=None`` builds one; pass ``False`` for none."""
    sessions = InMemorySessionRepository()
    pod_manager = DeliveringPodManager()
    minted = token_service() if tokens is None else tokens or None
    session_service = SessionService(
        sessions,
        pod_manager,
        provisioning_initial_delay=0,
        forge_session_tokens=minted,
        **service_kwargs,
    )
    store = InMemoryNotificationStore()
    notifications = NotificationService(
        store.feed,
        store.rule_repo,
        store.outbox,
        reply_ready_enabled=True,
        reply_title_chars=80,
        reply_body_chars=100,
        sinks=[NotificationSinkInfo(name="ops", label="Ops")],
    )
    log = InMemoryLog()
    app = FastAPI()
    app.add_middleware(ForgeSessionAuthMiddleware)
    app.state.identity = AllowAllIdentityAdapter(user_repository=InMemoryUsers())
    app.state.session_service = session_service
    app.state.forge_session_tokens = minted
    app.state.settings = SimpleNamespace(local_mounts=SimpleNamespace())
    app.include_router(
        create_router(
            session_service,
            prefix=PREFIX,
            session_participant_service=make_session_participant_service(session_service),
        )
    )
    app.include_router(
        create_notifications_router(
            notifications, session_service, prefix=PREFIX, default_page_size=20, max_page_size=50
        )
    )
    app.include_router(
        create_session_log_router(
            log, session_service=session_service, prefix=PREFIX, notification_service=notifications
        )
    )
    app.include_router(create_message_delivery_router(InMemoryDeliveries(), session_service))
    app.include_router(create_forge_mcp_router(mcp_config or ForgeMcpHttpConfig(), prefix=PREFIX))
    return ForgeApp(
        app=app,
        client=TestClient(app),
        sessions=sessions,
        session_service=session_service,
        notifications=notifications,
        store=store,
        pod_manager=pod_manager,
        tokens=minted,
        log=log,
    )


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
