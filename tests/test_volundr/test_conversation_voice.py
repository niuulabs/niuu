"""LEX-1677: voice reads — ``detail=voice`` conversations, ``limit``/``fields=summary``
on ``GET /sessions``, ``X-Forge-Read-Ms``, and the capability flags that advertise them.

Same real router + services over in-memory repos as ``test_conversation_tail.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from niuu.domain.transcript_rebuild import rebuild_turns
from tests.conftest import InMemorySessionRepository
from tests.test_volundr.test_conversation_tail import (
    _CONV_PATH,
    _client,
    _CountingEventLog,
    _exchanges,
    _patch_live,
    _running_session,
    _stopped_session,
)
from volundr.adapters.inbound.conversation_voice import voice_messages, voice_page
from volundr.domain.models import Session, SessionStatus
from volundr.domain.projects import SessionCoordination
from volundr.domain.session_startup import SessionStartup, StartupStage, StartupStageState

_SESSIONS = "/api/v1/forge/sessions"


# ── projection ────────────────────────────────────────────────────────


def test_projection_keeps_text_and_drops_tools_and_chrome() -> None:
    turns = [
        {"id": "u1", "role": "user", "content": "Deploy it?", "created_at": "t1"},
        {
            "id": "a1",
            "role": "assistant",
            "content": "",
            "parts": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                {"type": "tool_result", "content": "x" * 5000},
                {"type": "text", "text": "Deployed."},
                {"type": "text", "text": "All green."},
            ],
            "created_at": "t2",
        },
        {"id": "a2", "role": "assistant", "content": "", "parts": [{"type": "tool_use"}]},
        {"id": "s1", "role": "system", "content": "compacted"},
        {"id": "h1", "role": "assistant", "content": "hidden", "visibility": "internal"},
        {
            "id": "a3",
            "role": "assistant",
            "content": "Still working",
            "metadata": {"status": "in_progress"},
            "created_at": "t3",
        },
    ]

    assert voice_messages(turns) == [
        {"role": "user", "text": "Deploy it?", "created_at": "t1", "turn_id": "u1"},
        {"role": "agent", "text": "Deployed.\n\nAll green.", "created_at": "t2", "turn_id": "a1"},
        {
            "role": "agent",
            "text": "Still working",
            "created_at": "t3",
            "turn_id": "a3",
            "in_progress": True,
        },
    ]


def test_since_turn_inside_and_outside_the_page() -> None:
    page = {
        "turns": [
            {"id": "t1", "role": "user", "content": "a"},
            {"id": "t2", "role": "assistant", "content": "b"},
            {"id": "t3", "role": "user", "content": "c"},
        ],
        "activity_state": "idle",
    }

    inside = voice_page(page, session_id="s", limit=3, since_turn="t2")
    outside = voice_page(page, session_id="s", limit=3, since_turn="t0")
    latest = voice_page(page, session_id="s", limit=3, since_turn="t3")

    assert [m["turn_id"] for m in inside["messages"]] == ["t3"]
    assert inside["since_turn_found"] is True
    assert [m["turn_id"] for m in outside["messages"]] == ["t1", "t2", "t3"]
    assert outside["since_turn_found"] is False
    assert latest["messages"] == [] and latest["since_turn_found"] is True
    assert inside["activity_state"] == "idle"


# ── REST: conversation ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_voice_on_a_stopped_session_reads_only_the_log_tail() -> None:
    repository = InMemorySessionRepository()
    session = await _stopped_session(repository)
    frames = _exchanges(session.id, 40)
    event_log = _CountingEventLog(frames)
    client = _client(event_log, repository, initial_frames=8)

    resp = client.get(_CONV_PATH.format(sid=session.id), params={"detail": "voice", "limit": 4})

    assert resp.status_code == 200
    body = resp.json()
    expected = rebuild_turns(frames).turns[-4:]
    assert [m["turn_id"] for m in body["messages"]] == [t["id"] for t in expected]
    assert [m["role"] for m in body["messages"]] == ["user", "agent", "user", "agent"]
    assert body["messages"][-1]["text"].startswith("answer 40")
    assert set(body["messages"][0]) == {"role", "text", "created_at", "turn_id"}
    assert body["detail"] == "voice" and body["limit"] == 4
    assert "turns" not in body and "_prep" not in body
    assert event_log.frames_read < len(frames)
    assert float(resp.headers["X-Forge-Read-Ms"]) >= 0
    assert len(resp.content) < 5 * 1024


@pytest.mark.asyncio
async def test_voice_since_turn_over_rest() -> None:
    repository = InMemorySessionRepository()
    session = await _stopped_session(repository)
    frames = _exchanges(session.id, 10)
    client = _client(_CountingEventLog(frames), repository)
    turns = rebuild_turns(frames).turns

    body = client.get(
        _CONV_PATH.format(sid=session.id),
        params={"detail": "voice", "limit": 4, "since_turn": turns[-3]["id"]},
    ).json()

    assert [m["turn_id"] for m in body["messages"]] == [turns[-2]["id"], turns[-1]["id"]]
    assert body["since_turn_found"] is True


@pytest.mark.asyncio
async def test_voice_asks_a_running_broker_for_a_shallow_recent_page(monkeypatch) -> None:
    repository = InMemorySessionRepository()
    session = await _running_session(repository)
    live = _patch_live(
        monkeypatch,
        {
            "history_protocol": 2,
            "total_turns": 120,
            "activity_state": "active",
            "turns": [
                {"id": "u9", "role": "user", "content": "status?"},
                {
                    "id": "a9",
                    "role": "assistant",
                    "content": "",
                    "parts": [{"type": "tool_use"}, {"type": "text", "text": "Tests pass."}],
                },
            ],
        },
    )
    client = _client(_CountingEventLog([]), repository)

    body = client.get(
        _CONV_PATH.format(sid=session.id), params={"detail": "voice", "limit": 2}
    ).json()

    params = live.get.call_args.kwargs["params"]
    assert params["detail"] == "shallow"
    assert params["history_protocol"] == 2 and params["limit"] == 2
    assert [m["text"] for m in body["messages"]] == ["status?", "Tests pass."]
    assert body["total_turns"] == 120 and body["activity_state"] == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"detail": "voice"}, 422),
        ({"detail": "voice", "limit": 10_000}, 422),
        ({"detail": "voice", "limit": 2, "tail": 2}, 400),
        ({"detail": "voice", "limit": 2, "history_protocol": 2}, 400),
        ({"detail": "voice", "limit": 2, "after": 3}, 400),
        ({"since_turn": "t1"}, 400),
        ({"detail": "shallow", "since_turn": "t1"}, 400),
    ],
)
async def test_voice_parameter_rules(params: dict, code: int) -> None:
    repository = InMemorySessionRepository()
    session = await _stopped_session(repository)
    client = _client(_CountingEventLog(_exchanges(session.id, 2)), repository)

    resp = client.get(_CONV_PATH.format(sid=session.id), params=params)

    assert resp.status_code == code, resp.text


# ── REST: session list ────────────────────────────────────────────────


async def _sessions(repository: InMemorySessionRepository, count: int) -> list[Session]:
    base = datetime(2026, 10, 30, 8, 0, tzinfo=UTC)
    made = []
    for n in range(count):
        session = Session(
            name=f"s{n}",
            model="m",
            status=SessionStatus.STOPPED,
            last_active=base + timedelta(minutes=(n * 7) % count),
            short_description=f"doing {n}",
            coordination=(
                SessionCoordination(project_id=uuid4(), role="coordinator") if n == 0 else None
            ),
        )
        made.append(await repository.create(session))
    return made


@pytest.mark.asyncio
async def test_list_without_limit_is_unchanged() -> None:
    repository = InMemorySessionRepository()
    await _sessions(repository, 6)
    client = _client(_CountingEventLog([]), repository)

    resp = client.get(_SESSIONS)

    assert resp.status_code == 200
    assert len(resp.json()) == 6
    assert "message_count" in resp.json()[0]  # the full shape
    assert "X-Forge-Read-Ms" in resp.headers


@pytest.mark.asyncio
async def test_list_limit_keeps_the_newest_activity() -> None:
    repository = InMemorySessionRepository()
    made = await _sessions(repository, 6)
    client = _client(_CountingEventLog([]), repository)
    newest = sorted(made, key=lambda s: s.last_active, reverse=True)[:3]

    rows = client.get(_SESSIONS, params={"limit": 3}).json()

    assert [r["id"] for r in rows] == [str(s.id) for s in newest]
    assert client.get(_SESSIONS, params={"limit": 0}).status_code == 422


@pytest.mark.asyncio
async def test_summary_fields() -> None:
    repository = InMemorySessionRepository()
    made = await _sessions(repository, 4)
    client = _client(_CountingEventLog([]), repository)

    full = client.get(_SESSIONS).content
    resp = client.get(_SESSIONS, params={"fields": "summary"})

    rows = resp.json()
    assert len(rows) == 4
    assert set(rows[0]) == {
        "id",
        "name",
        "status",
        "activity_state",
        "startup",
        "short_description",
        "project",
        "last_activity_at",
        "worked_seconds_24h",
        "unread",
        "node",
    }
    stamps = [r["last_activity_at"] for r in rows]
    assert stamps == sorted(stamps, reverse=True)
    coordinator = next(r for r in rows if r["id"] == str(made[0].id))
    assert coordinator["project"]["role"] == "coordinator"
    assert coordinator["short_description"] == "doing 0"
    assert len(resp.content) * 3 < len(full)
    assert client.get(_SESSIONS, params={"fields": "everything"}).status_code == 422


@pytest.mark.asyncio
async def test_summary_rows_carry_the_launch_stages_as_json() -> None:
    repository = InMemorySessionRepository()
    made = await _sessions(repository, 3)
    started = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    startup = SessionStartup(
        started_at=started,
        stages=(
            StartupStage(
                key="schedule",
                label="Scheduling",
                state=StartupStageState.DONE,
                detail="On node hnoss",
                started_at=started,
                finished_at=started + timedelta(seconds=2),
            ),
            StartupStage(
                key="containers", label="Starting containers", state=StartupStageState.PENDING
            ),
        ),
    )
    await repository.update_startup(made[1].id, startup)
    client = _client(_CountingEventLog([]), repository)

    rows = {r["id"]: r for r in client.get(_SESSIONS, params={"fields": "summary"}).json()}

    assert rows[str(made[0].id)]["startup"] is None
    launched = rows[str(made[1].id)]["startup"]
    assert launched["started_at"] == "2026-11-06T12:00:00Z"
    assert [(s["key"], s["state"]) for s in launched["stages"]] == [
        ("schedule", "done"),
        ("containers", "pending"),
    ]
    assert launched["stages"][0]["detail"] == "On node hnoss"
    assert launched["stages"][0]["finished_at"] == "2026-11-06T12:00:02Z"


@pytest.mark.asyncio
async def test_feature_flags_advertise_both_reads() -> None:
    repository = InMemorySessionRepository()
    client = _client(_CountingEventLog([]), repository)
    client.app.state.storage = type("S", (), {"supports_home_volumes": False})()

    capabilities = client.get("/api/v1/forge/feature-flags").json()["capabilities"]

    assert capabilities["voice_conversation"] is True
    assert capabilities["summary_sessions"] is True
