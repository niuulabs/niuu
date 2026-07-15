"""D6 / INV-9: GET /conversation falls back to the durable-log rebuild.

When a session is dead (no live pod / empty-or-seed-only live transcript), the
conversation read path must NOT 404 or return an empty body — it must rebuild the
transcript from the durable ``session_event_log`` via the SAME reducer the live
fold uses (``transcript_rebuild.rebuild_turns`` over ``read_after(0)``). This is
the volundr-tier home the old broker-harness xfail asked for: real REST endpoint,
real ForgeService + SessionArchiveService, in-memory repos, NO Postgres, NO tmux.

Cross-references:
  * The service-tier fallback is proved in
    tests/test_domain/test_session_archive_service.py
    (``..._uses_durable_event_log_when_workspace_missing``).
  * The pure reducer parity (live fold == log rebuild) is
    tests/test_skuld/test_transcript_reducer_parity.py.
  * This file adds the END-TO-END read-path assertion through the HTTP endpoint.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import InMemorySessionRepository, MockPodManager
from volundr.adapters.inbound.rest import create_router
from volundr.config import LocalMountsConfig
from volundr.domain.models import Session, SessionLogEntry, SessionStatus
from volundr.domain.ports import SessionEventLogRepository
from volundr.domain.services import SessionArchiveService, SessionService


class InMemoryEventLog(SessionEventLogRepository):
    """Idempotent in-memory durable log mirroring the Postgres contract."""

    def __init__(self, entries: list[SessionLogEntry] | None = None) -> None:
        self._rows: dict[tuple[UUID, int], SessionLogEntry] = {}
        for entry in entries or []:
            self._rows.setdefault((entry.session_id, entry.seq), entry)

    async def append(self, entries: list[SessionLogEntry]) -> int:
        for entry in entries:
            self._rows.setdefault((entry.session_id, entry.seq), entry)
        return len(entries)

    async def read_after(
        self,
        session_id: UUID,
        after_seq: int = 0,
        limit: int = 1000,
    ) -> list[SessionLogEntry]:
        rows = [
            entry
            for (sid, seq), entry in self._rows.items()
            if sid == session_id and seq > after_seq
        ]
        rows.sort(key=lambda entry: entry.seq)
        return rows[:limit]

    async def latest_seq(self, session_id: UUID) -> int:
        seqs = [seq for (sid, seq) in self._rows if sid == session_id]
        return max(seqs) if seqs else 0


class _NoWorkspaceStorage:
    """Storage with NO workspace on disk — forces the event-log rebuild branch."""

    def resolve_session_workspace_path(self, _session_id: str) -> str | None:
        return None

    async def get_workspace_by_session(self, _session_id: str):
        return None


def _archive_store():
    from volundr.adapters.outbound.archive_store import FileSystemArchiveStore

    return FileSystemArchiveStore()


def _durable_turns(session_id: UUID) -> list[SessionLogEntry]:
    """A human turn + an assistant turn persisted verbatim in the durable log."""
    return [
        SessionLogEntry(
            session_id=session_id,
            seq=1,
            kind="conversation.turn",
            payload={
                "turn": {
                    "id": "turn-user-1",
                    "role": "user",
                    "content": "what is 2+2?",
                }
            },
            ts=datetime.now(UTC),
            role="user",
        ),
        SessionLogEntry(
            session_id=session_id,
            seq=2,
            kind="conversation.turn",
            payload={
                "turn": {
                    "id": "turn-asst-1",
                    "role": "assistant",
                    "content": "4",
                }
            },
            ts=datetime.now(UTC),
            role="assistant",
        ),
    ]


def _build_client(
    session: Session,
    event_log: SessionEventLogRepository,
) -> TestClient:
    repository = InMemorySessionRepository()

    session_service = SessionService(
        repository=repository,
        pod_manager=MockPodManager(),
        validate_repos=False,
    )
    archive_service = SessionArchiveService(
        session_service,
        _NoWorkspaceStorage(),
        _archive_store(),
        event_log_repository=event_log,
    )

    app = FastAPI()
    app.include_router(create_router(session_service, archive_service=archive_service))

    class _SettingsStub:
        local_mounts = LocalMountsConfig()

    app.state.settings = _SettingsStub()
    app.state.admin_settings = {}

    client = TestClient(app)
    client._repository = repository  # type: ignore[attr-defined]
    return client


async def _seed(client: TestClient, session: Session) -> None:
    await client._repository.create(session)  # type: ignore[attr-defined]


_CONV_PATH = "/api/v1/forge/sessions/{sid}/conversation"


@pytest.mark.asyncio
async def test_dead_session_conversation_falls_back_to_rebuilt_log():
    """D6: a STOPPED session (no live pod) rebuilds its transcript from the log."""
    session = Session(
        name="dead-session",
        model="claude-sonnet-4",
        status=SessionStatus.STOPPED,
    )
    event_log = InMemoryEventLog(_durable_turns(session.id))
    client = _build_client(session, event_log)
    await _seed(client, session)

    resp = client.get(_CONV_PATH.format(sid=session.id))

    assert resp.status_code == 200
    body = resp.json()
    assert body["is_active"] is False
    assert [t["role"] for t in body["turns"]] == ["user", "assistant"]
    assert body["turns"][1]["content"] == "4"


@pytest.mark.asyncio
async def test_dead_session_conversation_uses_shared_reducer_output():
    """INV-4 at the endpoint: the HTTP turns == rebuild_turns(read_after(0))."""
    from volundr.domain.services.transcript_rebuild import rebuild_turns

    session = Session(
        name="reducer-parity",
        model="claude-sonnet-4",
        status=SessionStatus.STOPPED,
    )
    event_log = InMemoryEventLog(_durable_turns(session.id))
    client = _build_client(session, event_log)
    await _seed(client, session)

    rows = await event_log.read_after(session.id, after_seq=0)
    expected = rebuild_turns(rows).turns

    body = client.get(_CONV_PATH.format(sid=session.id)).json()

    assert body["turns"] == expected


@pytest.mark.asyncio
async def test_conversation_404_when_session_missing():
    """Deterministic close on a truly unknown session — not a silent empty body."""
    session = Session(
        name="present",
        model="claude-sonnet-4",
        status=SessionStatus.STOPPED,
    )
    client = _build_client(session, InMemoryEventLog())
    # Intentionally NOT seeded.

    resp = client.get(_CONV_PATH.format(sid=session.id))

    assert resp.status_code == 404
