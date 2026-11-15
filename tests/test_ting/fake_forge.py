"""A fake Forge for the step-session adapter: sessions, offers, session logs and stops, recorded.

It implements the slice of ``VolundrPort`` that workflow steps use, with Forge's own
rules where they matter to Ting: session names are unique, an offer is handed to a run's
root, and a session's durable log is read a page at a time.

A run is one unit, as on Forge: a root created with ``hosts_members`` is held (``created``),
members are created ``hosted_by`` it while it is held (a member for a root that started, for
another owner's root or another run's is refused with 409), starting the root starts the
unit with every member, and stopping it stops them all. A member alone can be stopped and
started again while its unit runs; that is a restart in place under a new launch, counted in
``launches``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import httpx

from ting.domain.models import Saga
from ting.ports.volundr import (
    ActivityEvent,
    ActivityStreamConnected,
    ForgeProjectRef,
    PublicSessionLogEntry,
    PublicSessionLogPage,
    StepSessionRequest,
    VolundrPort,
    VolundrSession,
)


class FakeForge(VolundrPort):
    def __init__(self, name: str = "cluster-1") -> None:
        self._name = name
        self.sessions: dict[str, VolundrSession] = {}
        self.create_requests: list[StepSessionRequest] = []
        self.stopped: list[str] = []
        self.started: list[str] = []
        #: Per session id: its durable log, as Forge serves it (``get_public_session_log_page``).
        self.session_logs: dict[str, list[PublicSessionLogEntry]] = {}
        #: (session id, after, limit) of each log page asked for, in order.
        self.log_reads: list[tuple[str, int, int]] = []
        self.fail_log: Exception | None = None
        #: Sessions whose log answers ``has_more`` without ever advancing (a broken Forge).
        self.stalled_logs: set[str] = set()
        #: Forge projects by id, and the ids ensure_project created.
        self.projects: dict[str, ForgeProjectRef] = {}
        self.projects_created: list[str] = []
        self.run_room_requests: list = []
        #: (session id, text, notice id) of each notice posted in a room.
        self.room_notices: list[tuple[str, str, str]] = []
        self.fail_notice: Exception | None = None
        #: (session id, offer body) of each offer handed to a root's room.
        self.room_offers: list[tuple[str, dict[str, Any]]] = []
        self.fail_offer: Exception | None = None
        #: Roots created with ``hosts_members``, and the run each belongs to.
        self.unit_roots: dict[str, str] = {}
        #: Launches of each hosted session: 1 once its unit started, +1 per restart in place.
        self.launches: dict[str, int] = {}
        #: Every call that changed a session's lifecycle, in order: ("start" | "stop", id).
        self.lifecycle: list[tuple[str, str]] = []
        self.fail_create: Exception | None = None
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

    async def check_playability(self, members, *, auth_token=None, principal=None):
        from ting.ports.volundr import MemberPlayability

        self.playability_checks = [*getattr(self, "playability_checks", []), list(members)]
        refusals = getattr(self, "playability_refusals", {})
        return [
            MemberPlayability(
                member.member, member.model, "test-harness", tuple(refusals.get(member.member, ()))
            )
            for member in members
        ]

    async def get_project(self, project_id, *, auth_token=None, principal=None):
        return self.projects.get(project_id)

    async def ensure_project(self, project_id, name, *, auth_token=None, principal=None):
        if project_id not in self.projects:
            self.projects[project_id] = ForgeProjectRef(
                id=project_id, name=name, instance_id=self._name
            )
            self.projects_created.append(project_id)
        return self.projects[project_id]

    async def post_room_notice(
        self, session_id, content, *, notice_id, auth_token=None, principal=None
    ):
        if self.fail_notice is not None:
            raise self.fail_notice
        self.room_notices.append((session_id, content, notice_id))

    async def create_run_room(self, request, *, auth_token=None, principal=None):
        if any(session.name == request.name for session in self.sessions.values()):
            raise RuntimeError(f"a session named {request.name!r} already exists")
        self.run_room_requests.append(request)
        session = VolundrSession(
            id=f"room-{len(self.run_room_requests)}",
            name=request.name,
            status="created" if request.hosts_members else "creating",
            tracker_issue_id=request.tracker_issue_id,
            project_id=request.membership.project_id if request.membership else "",
        )
        self.sessions[session.id] = session
        if request.hosts_members:
            self.unit_roots[session.id] = request.run_id
        return session

    async def post_room_offer(self, session_id, offer, *, auth_token=None, principal=None):
        if self.fail_offer is not None:
            raise self.fail_offer
        if session_id not in self.sessions:
            raise RuntimeError(f"Session not found: {session_id}")
        self.room_offers.append((session_id, offer))

    @staticmethod
    def _refusal(detail: str) -> httpx.HTTPStatusError:
        request = httpx.Request("POST", "http://forge/api/v1/forge/sessions")
        response = httpx.Response(409, request=request, json={"detail": detail})
        return httpx.HTTPStatusError(
            f"Forge could not start the step session: {detail}", request=request, response=response
        )

    def _require_open_unit(self, request) -> None:
        unit = self.sessions.get(request.hosted_by)
        if unit is None or request.hosted_by not in self.unit_roots:
            raise self._refusal(f"session {request.hosted_by} is not a unit")
        if unit.status != "created":
            raise self._refusal(
                "a run's members are fixed when its unit starts: unit session "
                f"{request.hosted_by} is {unit.status}"
            )
        if self.unit_roots[request.hosted_by] != request.run_id:
            raise self._refusal(
                f"unit session {request.hosted_by} hosts workflow run "
                f"{self.unit_roots[request.hosted_by]}, not {request.run_id}"
            )

    async def create_step_session(self, request, *, auth_token=None, principal=None):
        if self.fail_create is not None:
            raise self.fail_create
        if any(session.name == request.name for session in self.sessions.values()):
            raise RuntimeError(f"a session named {request.name!r} already exists")
        if request.hosted_by:
            self._require_open_unit(request)
        self.create_requests.append(request)
        membership = request.membership
        session = VolundrSession(
            id=f"ses-{len(self.sessions) + 1}",
            name=request.name,
            status="created" if request.hosted_by else "creating",
            tracker_issue_id=request.tracker_issue_id,
            project_id=membership.project_id if membership else "",
            parent_session_id=membership.parent_session_id if membership else "",
            hosted_by=request.hosted_by,
        )
        self.sessions[session.id] = session
        return session

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

    def log_entry(
        self, session_id: str, payload: dict[str, Any], *, kind: str = "workflow_step"
    ) -> PublicSessionLogEntry:
        """Append a record to a session's durable log, numbered as Forge numbers it."""
        entries = self.session_logs.setdefault(session_id, [])
        entry = PublicSessionLogEntry(
            session_id=session_id,
            seq=len(entries) + 1,
            kind=kind,
            payload=payload,
            ts=datetime(2026, 11, 8, 9, 0, tzinfo=UTC),
        )
        entries.append(entry)
        return entry

    async def get_public_session_log_page(
        self, session_id, *, after, limit, auth_token=None, principal=None
    ):
        self.log_reads.append((session_id, after, limit))
        if self.fail_log is not None:
            raise self.fail_log
        entries = self.session_logs.get(session_id, [])
        if session_id in self.stalled_logs:
            return PublicSessionLogPage(entries=(), scanned_through=after, has_more=True)
        later = [entry for entry in entries if entry.seq > after]
        page = tuple(later[:limit])
        scanned = page[-1].seq if page else max(after, entries[-1].seq if entries else after)
        return PublicSessionLogPage(
            entries=page, scanned_through=scanned, has_more=len(later) > len(page)
        )

    async def list_sessions(self, *, auth_token=None, principal=None):
        return list(self.sessions.values())

    async def get_session(self, session_id, *, auth_token=None, principal=None):
        return self.sessions.get(session_id)

    async def start_session(self, session_id, *, auth_token=None, principal=None):
        self.started.append(session_id)
        self.lifecycle.append(("start", session_id))
        session = self.sessions[session_id]
        if session_id in self.unit_roots:
            if session.status != "created":
                raise self._refusal(f"unit session {session_id} is {session.status}")
            self._set(session_id, "starting")
            for member in self._members_of(session_id):
                self._set(member.id, "starting")
                self.launches[member.id] = 1
            return
        if not session.hosted_by:
            return
        unit = self.sessions[session.hosted_by]
        if unit.status not in {"starting", "running"}:
            raise self._refusal(
                f"session {session_id} runs in unit session {unit.id}, which is {unit.status}"
            )
        if session.status not in {"stopped", "failed"}:
            raise self._refusal(f"session {session_id} is {session.status} and cannot start")
        self._set(session_id, "starting")
        self.launches[session_id] += 1

    async def stop_session(self, session_id, *, auth_token=None, principal=None):
        if session_id in self.fail_stop:
            raise ConnectionError(f"cannot stop {session_id}")
        self.stopped.append(session_id)
        self.lifecycle.append(("stop", session_id))
        session = self.sessions.get(session_id)
        if session is None:
            return
        if session_id in self.unit_roots or session.hosted_by:
            if session.status == "created":
                raise self._refusal(f"session {session_id} is created and cannot stop")
            self._set(session_id, "stopped")
        if session_id in self.unit_roots:
            for member in self._members_of(session_id):
                self._set(member.id, "stopped")

    def _members_of(self, root_id: str) -> list[VolundrSession]:
        return [s for s in self.sessions.values() if s.hosted_by == root_id]

    def _set(self, session_id: str, status: str) -> None:
        self.sessions[session_id] = replace(self.sessions[session_id], status=status)

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
        self._set(session_id, status)

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
