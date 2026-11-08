"""A fake Forge for the step-session adapter: sessions, step delivery and stops, recorded.

It implements the slice of ``VolundrPort`` that workflow steps use, with Forge's own
rules where they matter to Ting: session names are unique, a step is delivered to one
session, and re-delivering an activation is idempotent.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from ting.domain.models import Saga
from ting.ports.volundr import (
    ActivityEvent,
    ActivityStreamConnected,
    ForgeProjectRef,
    StepSessionRequest,
    VolundrPort,
    VolundrSession,
)


class FakeForge(VolundrPort):
    def __init__(self, name: str = "cluster-1") -> None:
        self._name = name
        self.sessions: dict[str, VolundrSession] = {}
        self.create_requests: list[StepSessionRequest] = []
        self.deliveries: list[tuple[str, dict[str, Any]]] = []
        self.stopped: list[str] = []
        self.started: list[str] = []
        self.step_states: dict[tuple[str, str], dict[str, Any]] = {}
        #: Forge projects by id, and the ids ensure_project created.
        self.projects: dict[str, ForgeProjectRef] = {}
        self.projects_created: list[str] = []
        self.fail_create: Exception | None = None
        self.fail_deliver: Exception | None = None
        self.fail_stop: set[str] = set()
        #: idempotency key -> (session id, stored notification), as Forge's feed keeps them.
        self.notifications: dict[str, tuple[str, dict[str, Any]]] = {}
        self.resolved: dict[str, str] = {}
        self.notification_calls = 0
        self.fail_notify: Exception | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def target_id(self) -> str:
        return self._name

    async def get_project(self, project_id, *, auth_token=None, principal=None):
        return self.projects.get(project_id)

    async def ensure_project(self, project_id, name, *, auth_token=None, principal=None):
        if project_id not in self.projects:
            self.projects[project_id] = ForgeProjectRef(
                id=project_id, name=name, instance_id=self._name
            )
            self.projects_created.append(project_id)
        return self.projects[project_id]

    async def create_step_session(self, request, *, auth_token=None, principal=None):
        if self.fail_create is not None:
            raise self.fail_create
        if any(session.name == request.name for session in self.sessions.values()):
            raise RuntimeError(f"a session named {request.name!r} already exists")
        self.create_requests.append(request)
        session = VolundrSession(
            id=f"ses-{len(self.sessions) + 1}",
            name=request.name,
            status="creating",
            tracker_issue_id=request.tracker_issue_id,
        )
        self.sessions[session.id] = session
        return session

    async def deliver_workflow_step(
        self, session_id, activation, *, auth_token=None, principal=None
    ):
        if self.fail_deliver is not None:
            raise self.fail_deliver
        self.deliveries.append((session_id, activation))

    async def submit_session_notification(self, session_id, draft, *, idempotency_key):
        self.notification_calls += 1
        if self.fail_notify is not None:
            raise self.fail_notify
        if session_id not in self.sessions:
            raise RuntimeError(f"Session not found: {session_id}")
        if idempotency_key not in self.notifications:
            stored = {**draft, "id": f"note-{len(self.notifications) + 1}"}
            self.notifications[idempotency_key] = (session_id, stored)
        return self.notifications[idempotency_key][1]

    async def resolve_notification(self, notification_id, *, resolution):
        self.notification_calls += 1
        self.resolved.setdefault(notification_id, resolution)
        return {"id": notification_id, "resolution": self.resolved[notification_id]}

    async def get_workflow_step(
        self, session_id, activation_id, *, auth_token=None, principal=None
    ):
        return self.step_states.get((session_id, activation_id))

    async def list_sessions(self, *, auth_token=None, principal=None):
        return list(self.sessions.values())

    async def get_session(self, session_id, *, auth_token=None, principal=None):
        return self.sessions.get(session_id)

    async def start_session(self, session_id, *, auth_token=None, principal=None):
        self.started.append(session_id)

    async def stop_session(self, session_id, *, auth_token=None, principal=None):
        if session_id in self.fail_stop:
            raise ConnectionError(f"cannot stop {session_id}")
        self.stopped.append(session_id)

    async def spawn_session(self, request, *, auth_token=None, principal=None):
        raise AssertionError("workflow steps never spawn plain sessions")

    async def get_pr_status(self, session_id):
        raise AssertionError("workflow steps never read pull requests")

    async def get_chronicle_summary(self, session_id):
        raise AssertionError("workflow steps never read chronicles")

    async def send_message(self, session_id, message, *, auth_token=None, principal=None):
        raise AssertionError("workflow steps never chat; they are delivered as steps")

    async def list_integration_ids(self, *, auth_token=None, principal=None):
        raise AssertionError("workflow steps never list integrations")

    async def list_repos(self, *, auth_token=None, principal=None):
        raise AssertionError("workflow steps never list repositories")

    async def get_last_assistant_message(self, session_id, *, auth_token=None, principal=None):
        raise AssertionError("workflow steps never read transcripts")

    async def get_conversation(self, session_id, *, auth_token=None, principal=None):
        raise AssertionError("workflow steps never read transcripts")

    async def subscribe_activity(
        self,
    ) -> AsyncGenerator[ActivityEvent | ActivityStreamConnected, None]:
        yield ActivityStreamConnected()

    def set_status(self, session_id: str, status: str) -> None:
        session = self.sessions[session_id]
        self.sessions[session_id] = VolundrSession(
            id=session.id,
            name=session.name,
            status=status,
            tracker_issue_id=session.tracker_issue_id,
        )

    def add_session(self, name: str, tracker_issue_id: str | None, status: str = "running") -> str:
        session_id = f"ses-{len(self.sessions) + 1}"
        self.sessions[session_id] = VolundrSession(
            id=session_id, name=name, status=status, tracker_issue_id=tracker_issue_id
        )
        return session_id


class FakeSagas:
    """The projects (sagas) a run's ticket can name, by id."""

    def __init__(self, *sagas: Saga) -> None:
        self._items = {saga.id: saga for saga in sagas}

    async def get_saga(self, saga_id, *, owner_id=None, conn=None):
        saga = self._items.get(saga_id)
        if saga is None or (owner_id is not None and saga.owner_id != owner_id):
            return None
        return saga


class FakeForgeFactory:
    """Resolves the one fake Forge for an owner, by connection or as the primary."""

    def __init__(self, forge: FakeForge | None = None) -> None:
        self.forge = forge

    async def for_connection(self, owner_id: str, connection_id: str):
        if self.forge is not None and connection_id in {self.forge.target_id, self.forge.name}:
            return self.forge
        return None

    async def primary_for_owner(self, owner_id: str):
        return self.forge
