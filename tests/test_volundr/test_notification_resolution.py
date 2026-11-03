"""LEX-1442: notifications can be resolved, so a stale critical stops alarming."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from tests.support.forge_session import PREFIX, bearer, build_forge_app

#: The allow-all identity's anonymous principal: the operator in these tests.
DEV = "dev-user"


def _submit(forge, session_id, key: str, **fields: Any) -> dict:
    body = {"kind": "info", "title": key, "idempotency_key": key, **fields}
    response = forge.client.post(f"{PREFIX}/sessions/{session_id}/notifications", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _row(forge, notification_id: str) -> Any:
    return next(n for n in forge.store.notifications.values() if str(n.id) == notification_id)


async def test_a_milestone_resolves_its_incidents_problems_only() -> None:
    forge = build_forge_app()
    session = await forge.session()
    critical = _submit(
        forge, session.id, "c1", kind="attention", severity="critical", correlation_id="LEX-1"
    )
    error = _submit(forge, session.id, "e1", kind="error", correlation_id="LEX-1")
    warning_info = _submit(forge, session.id, "i1", severity="critical", correlation_id="LEX-1")
    progress = _submit(forge, session.id, "p0", correlation_id="LEX-1")  # an info note
    other_incident = _submit(forge, session.id, "c2", kind="attention", correlation_id="LEX-2")
    uncorrelated = _submit(forge, session.id, "c3", kind="attention", severity="critical")

    done = _submit(forge, session.id, "m1", kind="milestone", correlation_id="LEX-1")
    later = _submit(forge, session.id, "c4", kind="attention", correlation_id="LEX-1")

    for item in (critical, error, warning_info):
        row = _row(forge, item["id"])
        assert row.resolved_at is not None, item["title"]
        assert str(row.resolved_by_notification_id) == done["id"]
    for item in (progress, other_incident, uncorrelated, later, done):
        assert _row(forge, item["id"]).resolved_at is None, item["title"]


async def test_another_owners_incident_is_never_touched() -> None:
    forge = build_forge_app()
    mine = await forge.session("mine")
    theirs = await forge.session("theirs", owner="someone-else")
    foreign = _submit(forge, theirs.id, "x1", kind="attention", correlation_id="LEX-1")

    _submit(
        forge, mine.id, "m1", kind="milestone", correlation_id="LEX-1", resolves=[foreign["id"]]
    )

    assert _row(forge, foreign["id"]).resolved_at is None


async def test_resolves_by_id_and_by_correlation_id_any_kind() -> None:
    forge = build_forge_app()
    session = await forge.session()
    decision = _submit(forge, session.id, "d1", kind="decision", title="Pick a DB?")
    info = _submit(forge, session.id, "i1", correlation_id="deploy-7")
    note = _submit(forge, session.id, "i2", correlation_id="deploy-7")

    closer = _submit(forge, session.id, "i3", resolves=[decision["id"], "deploy-7"])

    assert closer["resolves"] == [decision["id"], "deploy-7"]
    for item in (decision, info, note):
        assert str(_row(forge, item["id"]).resolved_by_notification_id) == closer["id"]
    assert _row(forge, closer["id"]).resolved_at is None


async def test_operator_resolves_by_hand_once() -> None:
    forge = build_forge_app()
    session = await forge.session(owner=DEV)
    alarm = _submit(forge, session.id, "c1", kind="attention", severity="critical")

    first = forge.client.put(
        f"{PREFIX}/notifications/{alarm['id']}/resolve", json={"resolution": "keychain fixed"}
    )
    again = forge.client.put(
        f"{PREFIX}/notifications/{alarm['id']}/resolve", json={"resolution": "other words"}
    )
    missing = forge.client.put(f"{PREFIX}/notifications/{uuid4()}/resolve")

    assert first.status_code == 200, first.text
    body = first.json()
    assert body["resolved"] is True and body["resolution"] == "keychain fixed"
    assert body["resolved_by_notification_id"] is None
    assert again.json()["resolution"] == "keychain fixed"  # the first resolution is kept
    assert again.json()["resolved_at"] == body["resolved_at"]
    assert missing.status_code == 404


async def test_session_credentials_cannot_resolve_by_hand() -> None:
    forge = build_forge_app()
    session = await forge.session()
    alarm = _submit(forge, session.id, "c1", kind="attention")
    credential = await forge.start(session)

    response = forge.client.put(
        f"{PREFIX}/notifications/{alarm['id']}/resolve", headers=bearer(credential.token)
    )

    assert response.status_code == 403
    assert _row(forge, alarm["id"]).resolved_at is None


async def test_feed_filters_on_resolution() -> None:
    forge = build_forge_app()
    session = await forge.session(owner=DEV)
    alarm = _submit(forge, session.id, "c1", kind="attention", correlation_id="LEX-9")
    _submit(forge, session.id, "m1", kind="milestone", correlation_id="LEX-9")
    open_one = _submit(forge, session.id, "c2", kind="attention")

    def ids(**params) -> list[str]:
        response = forge.client.get(f"{PREFIX}/notifications", params=params)
        assert response.status_code == 200, response.text
        return [item["id"] for item in response.json()["items"]]

    assert alarm["id"] not in ids(unresolved=True)
    assert open_one["id"] in ids(unresolved=True)
    assert ids(unresolved=False) == [alarm["id"]]
    assert len(ids()) == 3


class _Recorder:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)


async def test_resolved_rows_are_sent_again_on_the_live_stream() -> None:
    forge = build_forge_app()
    recorder = _Recorder()
    forge.notifications._broadcaster = recorder
    session = await forge.session()
    alarm = _submit(forge, session.id, "c1", kind="attention", correlation_id="LEX-5")

    done = _submit(forge, session.id, "m1", kind="milestone", correlation_id="LEX-5")

    payloads = [event.data for event in recorder.events]
    assert [p["id"] for p in payloads] == [alarm["id"], done["id"], alarm["id"]]
    assert payloads[-1]["resolved"] is True
    assert payloads[-1]["resolved_by_notification_id"] == done["id"]


def test_the_draft_carries_resolves_on_the_wire_only_when_set() -> None:
    from niuu.domain.notifications import NotificationDraft, draft_payload

    plain = draft_payload(NotificationDraft(kind="milestone", title="Done"))
    closing = draft_payload(
        NotificationDraft(kind="milestone", title="Done", resolves=[" LEX-1 ", "LEX-1", "x"])
    )

    assert "resolves" not in plain
    assert closing["resolves"] == ["LEX-1", "x"]  # trimmed and de-duplicated


async def test_an_old_alarm_whose_session_moved_on_is_stale() -> None:
    from datetime import UTC, datetime, timedelta

    forge = build_forge_app()
    forge.notifications._stale_after = timedelta(hours=4)
    session = await forge.session(owner=DEV)
    other = await forge.session("other", owner=DEV)
    now = datetime.now(UTC)
    forge.store.clock = lambda: now - timedelta(hours=5)
    alarm = _submit(forge, session.id, "c1", kind="attention", severity="critical")
    quiet = _submit(forge, other.id, "c2", kind="attention")
    note = _submit(forge, session.id, "i1")
    forge.store.clock = lambda: now - timedelta(hours=1)
    young = _submit(forge, session.id, "c3", kind="error")
    _submit(forge, session.id, "m1", kind="milestone", title="moved on")

    items = forge.client.get(f"{PREFIX}/notifications").json()["items"]
    stale = {item["id"]: item["stale"] for item in items}
    per_session = forge.client.get(f"{PREFIX}/sessions/{session.id}/notifications").json()
    rows = per_session.get("items", per_session) if isinstance(per_session, dict) else per_session

    assert stale[alarm["id"]] is True  # old, unresolved, its session has a newer milestone
    assert stale[young["id"]] is False  # not old enough yet
    assert stale[quiet["id"]] is False  # its session posted no milestone
    assert stale[note["id"]] is False  # info is not an alarm
    assert next(r for r in rows if r["id"] == alarm["id"])["stale"] is True
    # Display only: nothing was resolved.
    assert _row(forge, alarm["id"]).resolved_at is None
