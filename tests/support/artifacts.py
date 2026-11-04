"""Harness for the control plane's burst and capture oracles (Slice 0: F1, F1n).

The fixtures in ``tests/fixtures/control_plane`` are copied verbatim from lexi-ios
``plan/control-plane`` (``docs/control-plane``):

* ``platform-bursts.json``: ``platform/api/fixtures/bursts.json`` (B1-B9), seconds;
* ``model-bursts.json``: ``model/acceptance/cases/bursts.json`` (the model's B1-B6), minutes;
* ``capture-manifest-v1.json``: the judge's 230 durable publications (both hosts);
* ``slice0-burst-replay.json``: the measured agent-notice offsets per burst (the 25 golden).

Every runner drives the real :class:`ArtifactService` over any
:class:`ArtifactPublicationRepository`, so the in-memory and Postgres stores answer the
same cases.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from niuu.domain.notifications import (
    NotificationKind,
    NotificationLink,
    NotificationLinkKind,
    NotificationSeverity,
    NotificationSource,
)
from volundr.domain.artifact_ports import ArtifactPublicationRepository
from volundr.domain.artifacts import (
    BurstPolicy,
    Origin,
    Provider,
    PublicationCandidate,
    PublicationSource,
    TypeCatalog,
    announces,
    classify_origin,
)
from volundr.domain.models import Session, SessionLogEntry
from volundr.domain.notifications import Notification
from volundr.domain.services.artifacts import ArtifactService

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "control_plane"
T0 = datetime(2026, 10, 21, tzinfo=UTC)
FAR_FUTURE = datetime(2100, 1, 1, tzinfo=UTC)
POLICY = BurstPolicy(
    window=timedelta(minutes=30), hold=timedelta(seconds=120), live_lag=timedelta(minutes=10)
)
SESSION_NS = uuid.UUID("2b1f0a8e-9c4d-4e6f-8a7b-1c2d3e4f5a6b")
CATALOG = TypeCatalog.from_config(
    {
        "https://claude.ai/artifact/8jTsAFQMFDb2oA8MsPJ2eL": "deck",
        "https://claude.ai/artifact/QKN21svewxgyPb6SYRqWnd": "design",
        "https://claude.ai/artifact/Rp9naXUCj2xozpUkyQy19W": "doc",
        "https://claude.ai/artifact/5M7UeXXcx16TP3vzVFNDzd": "design_system",
    },
    {"slides": "deck", "design": "design", "pages": "doc", "docs": "doc"},
)

RepoFactory = Callable[[], Awaitable[ArtifactPublicationRepository]]
Restart = Callable[[ArtifactPublicationRepository], Awaitable[ArtifactPublicationRepository]]
AddNotice = Callable[[ArtifactPublicationRepository, Notification], Awaitable[None]]


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def session_uuid(name: str) -> uuid.UUID:
    try:
        return uuid.UUID(name)
    except ValueError:
        return uuid.uuid5(SESSION_NS, name)


def artifact_url(key: str) -> str:
    return f"https://claude.ai/artifact/{key}"


class FakeNotices:
    """The notification service's two emission hooks, recording broadcasts."""

    def __init__(self) -> None:
        self.broadcast: list[Notification] = []

    def burst_candidate(self, session, *, dedupe_key, draft, metadata):
        from volundr.domain.notifications import NotificationCandidate

        return NotificationCandidate(
            dedupe_key=dedupe_key,
            session_id=session.id,
            session_name=session.name,
            owner_id=session.owner_id or "owner",
            tenant_id=session.tenant_id,
            kind=draft.kind,
            severity=draft.severity,
            source=NotificationSource.SYSTEM,
            title=draft.title,
            body=draft.body,
            links=draft.links,
            correlation_id=draft.correlation_id,
            metadata=metadata,
        )

    async def broadcast_created(self, notification: Notification) -> None:
        self.broadcast.append(notification)


async def lookup(session_id: uuid.UUID) -> Session:
    return Session(id=session_id, name=f"session-{str(session_id)[:8]}", owner_id="owner")


def service(
    repository: ArtifactPublicationRepository, host: str = "thor", notices: Any = None
) -> ArtifactService:
    return ArtifactService(
        repository,
        host=host,
        policy=POLICY,
        catalog=CATALOG,
        notices=notices if notices is not None else FakeNotices(),
        session_lookup=lookup,
        emit_batch_size=500,
        backfill_page_size=50,
        clock=lambda: FAR_FUTURE,
    )


def candidate(key: str, session: str, seq: int, at: datetime, **extra) -> PublicationCandidate:
    return PublicationCandidate(
        session_id=session_uuid(session),
        session_seq=seq,
        ordinal=0,
        source=PublicationSource.ARTIFACT_TOOL,
        provider=Provider.CLAUDE_AI,
        provider_key=key,
        at=at,
        url=artifact_url(key),
        title=extra.pop("title", f"Artifact {key}"),
        **extra,
    )


def agent_notice(
    key: str, at: datetime, *, source: str = "agent", correlation_id: str | None = None, seq: int
) -> Notification:
    return Notification(
        id=uuid.uuid4(),
        seq=seq,
        dedupe_key=f"test:{uuid.uuid4()}",
        session_id=session_uuid("announcer"),
        owner_id="owner",
        kind=NotificationKind.MILESTONE,
        severity=NotificationSeverity.INFO,
        source=NotificationSource(source),
        title=f"{key} is up",
        links=[
            NotificationLink(
                label="open", url=artifact_url(key), kind=NotificationLinkKind.ARTIFACT
            )
        ],
        correlation_id=correlation_id,
        created_at=at,
    )


async def burst_of(repository: ArtifactPublicationRepository, host: str, session: str, seq: int):
    rows = await repository.list_after(None, after=0, limit=100_000)
    for row in rows:
        if row.host == host and row.session_id == session_uuid(session) and row.session_seq == seq:
            return row.burst_id
    raise AssertionError(f"no publication {host}:{session}:{seq}")


async def burst_notices(repository: ArtifactPublicationRepository) -> int:
    rows = await repository.list_bursts_after(None, after=0, limit=100_000)
    return sum(1 for _, burst in rows if burst.notification_id is not None)


async def burst_count(repository: ArtifactPublicationRepository) -> int:
    return len(await repository.list_bursts_after(None, after=0, limit=100_000))


# -- platform B1-B9 (seconds) ------------------------------------------------------


async def run_platform_case(
    case: dict, make: RepoFactory, restart: Restart, add_notice: AddNotice
) -> list[str]:
    repository = await make()
    svc = service(repository)
    names: dict[str, tuple[str, int]] = {}
    snapshot: dict[str, uuid.UUID] = {}
    notice_seq = 0
    for step in case["steps"]:
        kind, value = next(iter(step.items()))
        if kind == "publish":
            at = T0 + timedelta(seconds=value["at"])
            origin = Origin(value.get("origin", "live"))
            item = candidate(value["key"], value["session"], value["seq"], at)
            await svc.record([(item, origin)])
            names[value.get("name", f"p{len(names)}")] = (value["session"], value["seq"])
        elif kind == "notice":
            notice_seq += 1
            await add_notice(
                repository,
                agent_notice(
                    value["key"],
                    T0 + timedelta(seconds=value["at"]),
                    source=value.get("source", "agent"),
                    seq=notice_seq,
                ),
            )
        elif kind == "tick":
            await svc.emit_due(T0 + timedelta(seconds=value))
        elif kind == "restart":
            repository = await restart(repository)
            svc = service(repository)
        elif kind == "snapshot_ids":
            for name in value:
                snapshot[name] = await burst_of(repository, "thor", *names[name])
    expect = case["expect"]
    bad: list[str] = []
    got = {"bursts": await burst_count(repository), "notices": await burst_notices(repository)}
    for field in ("bursts", "notices"):
        if field in expect and expect[field] != got[field]:
            bad.append(f"{field}: want {expect[field]} got {got[field]}")

    async def bid(name: str) -> uuid.UUID:
        return await burst_of(repository, "thor", *names[name])

    for a, b in expect.get("same", []):
        if await bid(a) != await bid(b):
            bad.append(f"{a} and {b} should share a burst")
    for a, b in expect.get("different", []):
        if await bid(a) == await bid(b):
            bad.append(f"{a} and {b} should be different bursts")
    for name in expect.get("unchanged", []):
        if await bid(name) != snapshot[name]:
            bad.append(f"{name}'s burst id changed")
        if await repository.get_burst(snapshot[name]) is None:
            bad.append(f"{name}'s burst disappeared")
    if expect.get("bridges"):
        bridged = await bridges(repository)
        if not bridged:
            bad.append("bridge not recorded")
    return bad


async def bridges(repository: ArtifactPublicationRepository) -> bool:
    bridges_of = getattr(repository, "bridges_of", None)
    rows = await repository.list_after(None, after=0, limit=100_000)
    if bridges_of is not None:
        return any(bridges_of(row.publication_id) for row in rows)
    return await repository.any_bridges()  # type: ignore[attr-defined]


# -- the model's B1-B6 (minutes; origin by ingest lag) -------------------------------


async def run_model_case(case: dict, make: RepoFactory, add_notice: AddNotice) -> list[str]:
    repository = await make()
    services: dict[str, ArtifactService] = {}
    labels: dict[uuid.UUID, str] = {}
    pubs: dict[str, tuple[str, int]] = {}
    notices: list[dict] = []
    bad: list[str] = []
    seq = 0
    for index, (step, expect) in enumerate(zip(case["steps"], case["expect"], strict=True)):
        joined = None
        if step["t"] == "publish":
            seq += 1
            host = step["host"]
            svc = services.setdefault(host, service(repository, host))
            at = T0 + timedelta(minutes=step["at"])
            seen = T0 + timedelta(minutes=step["ingested_at"])
            origin = classify_origin(at, seen, POLICY.live_lag)
            session = step.get("session", "s1")
            await svc.record([(candidate(step["artifact"], session, seq, at), origin)])
            pubs[step["id"]] = (host, seq)
            bid = await burst_of(repository, host, session, seq)
            labels.setdefault(bid, f"burst-{len(labels) + 1}")
            joined = labels[bid]
        else:
            notices.append(step)
        if expect is None:
            continue
        if "joined" in expect and expect["joined"] != joined:
            bad.append(f"step {index}: joined {joined}, want {expect['joined']}")
        if "bursts" in expect and expect["bursts"] != await burst_count(repository):
            bad.append(
                f"step {index}: {await burst_count(repository)} bursts, want {expect['bursts']}"
            )
        if "members" in expect:
            members = await member_labels(repository, labels, pubs)
            if members != {k: sorted(v) for k, v in expect["members"].items()}:
                bad.append(f"step {index}: members {members}, want {expect['members']}")
        if "announced" in expect:
            got = await announced(repository, labels, notices)
            if got != expect["announced"]:
                bad.append(f"step {index}: announced {got}, want {expect['announced']}")
    return bad


async def member_labels(repository, labels, pubs) -> dict[str, list[str]]:
    by_occurrence = {occ: name for name, occ in pubs.items()}
    out: dict[str, list[str]] = {}
    for bid, label in labels.items():
        rows = await repository.burst_publications(bid)
        out[label] = sorted(by_occurrence[(row.host, row.session_seq)] for row in rows)
    return out


async def announced(repository, labels, notices) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for bid, label in labels.items():
        burst = await repository.get_burst(bid)
        found = None
        for notice in notices:
            if announces(
                source=notice["source"],
                correlation_id=notice.get("correlation_id"),
                link_urls=[artifact_url(key) for key in notice.get("links", [])],
                created_at=T0 + timedelta(minutes=notice["at"]),
                burst=burst,
                window=POLICY.window,
            ):
                found = notice["id"]
                break
        out[label] = found
    return out


# -- the Slice 0 golden: 33 bursts, 25 notices ---------------------------------------


def _ts(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


async def run_golden(make: RepoFactory, add_notice: AddNotice) -> tuple[int, int]:
    """Thor's manifest rows replayed in occurred order with ``origin = live`` forced, the
    measured agent notices that land inside the hold, then every hold elapses."""
    manifest = load("capture-manifest-v1.json")
    replay = load("slice0-burst-replay.json")
    repository = await make()
    svc = service(repository, "thor")
    rows = sorted(
        (r for r in manifest["publications"] if r["host"] == "thor"), key=lambda r: r["at"]
    )
    for row in rows:
        item = candidate(row["provider_key"], row["session_id"], row["seq"], _ts(row["at"]))
        await svc.record([(item, Origin.LIVE)])
    offsets = {(b["key"], b["start"][:16]): b["human_notice_offset_s"] for b in replay["per_burst"]}
    seq = 0
    for _, burst in await repository.list_bursts_after(None, after=0, limit=100_000):
        start = burst.started_at.astimezone(UTC).isoformat()[:16]
        offset = offsets.get((burst.provider_key, start))
        if offset is not None and 0 <= offset <= POLICY.hold.total_seconds():
            seq += 1
            at = burst.started_at + timedelta(seconds=int(offset))
            await add_notice(repository, agent_notice(burst.provider_key, at, seq=seq))
    await svc.emit_due(datetime(2100, 1, 1, tzinfo=UTC))
    return await burst_count(repository), await burst_notices(repository)


# -- the capture golden: 230 rows, 26 keys, from synthesized durable rows -------------


def manifest_log_rows(manifest: dict, host: str) -> list[SessionLogEntry]:
    """The durable hook rows behind ``host``'s manifest publications, plus the rows the
    filter must reject (an asset upload, a failure, a refusal, a non-publish action).

    E0's operator replay also uses this builder. A manifest without titles cannot
    supply one: synthetic labels would replace real artifact metadata after ingest.
    """
    rows: list[SessionLogEntry] = []
    for pub in manifest["publications"]:
        if pub["host"] != host:
            continue
        response: dict[str, Any] = {
            "url": pub["url"],
            "version": pub["provider_version"],
        }
        title = pub.get("title")
        if isinstance(title, str) and title.strip():
            response["title"] = title
        if pub["provider_seq"] is not None:
            response["seq"] = pub["provider_seq"]
        inner: dict[str, Any] = {
            "tool_name": "Artifact",
            "tool_use_id": pub["tool_use_id"],
            "tool_input": {"file_path": "/tmp/x.html"},
            "tool_response": response,
        }
        if pub["subagent"]:
            inner["agent_id"] = "agent-1"
        rows.append(hook_row(pub["session_id"], pub["seq"], _ts(pub["at"]), inner))
    noise_session = str(session_uuid(f"noise-{host}"))
    at = _ts(manifest["window"]["start"]) + timedelta(days=1)
    url = artifact_url("NoiseKey123")
    noise = [
        {
            "tool_name": "Artifact",
            "tool_input": {"asset": True, "url": url},
            "tool_response": {"url": url},
        },
        {
            "tool_name": "Artifact",
            "tool_input": {"action": "read", "url": url},
            "tool_response": {"url": url},
        },
        {
            "tool_name": "Artifact",
            "tool_input": {},
            "tool_response": {"url": url, "files_read": ["a"]},
        },
        {"tool_name": "Artifact", "tool_input": {}, "tool_response": "Published: " + url},
        {"tool_name": "Read", "tool_input": {}, "tool_response": {"url": url}},
    ]
    for index, inner in enumerate(noise, start=1):
        rows.append(hook_row(noise_session, index, at, inner))
    rows.append(
        hook_row(noise_session, 99, at, noise[0] | {"tool_input": {}}, event="PostToolUseFailure")
    )
    return rows


def hook_row(
    session_id: str, seq: int, ts: datetime, inner: dict, *, event: str = "PostToolUse"
) -> SessionLogEntry:
    return SessionLogEntry(
        session_id=uuid.UUID(session_id),
        seq=seq,
        kind="claude_hook",
        payload={
            "type": "claude_hook",
            "event_type": "claude.hook",
            "hook_event_name": event,
            "payload": {"hook_event_name": event, **inner},
            "metadata": {"source": "claude_hook"},
        },
        ts=ts,
    )
