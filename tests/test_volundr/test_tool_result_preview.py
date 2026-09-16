"""Volundr tool-result PREVIEW endpoint: scaled JPEG generation + disk cache.

The image-delivery feature for phone-grade bandwidth: an image ``Read`` is a
multi-hundred-KB base64 envelope, and the inline strip needs ~20 of them — so
``GET /sessions/{sid}/tool-result/{tuid}/preview`` serves a ~400px JPEG that is
generated on FIRST request and cached on disk keyed by tool_use_id. The endpoint
must live at the VOLUNDR tier because the validation target (an old broker that
pre-dates the pod tool-result endpoint entirely) is served exclusively by
volundr's durable-transcript fallback.

Same harness style as ``test_conversation_db_fallback.py``: real ``create_router``
+ real ``SessionService`` + real ``SessionArchiveService`` over in-memory repos;
the live pod is a mocked ``httpx.AsyncClient``. The PreviewCache is injected on a
pytest tmp_path so every test is hermetic on disk.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import skuld.tool_result_preview as trp_mod
from skuld.tool_result_preview import (
    PreviewCache,
    extract_image_bytes,
    generate_preview_jpeg,
)
from tests.conftest import InMemorySessionRepository, MockPodManager
from tests.test_domain.test_session_archive_service import InMemorySessionEventLog
from volundr.adapters.inbound.rest import create_router
from volundr.adapters.outbound.archive_store import FileSystemArchiveStore
from volundr.config import LocalMountsConfig
from volundr.domain.models import Session, SessionLogEntry, SessionStatus
from volundr.domain.services import SessionArchiveService, SessionService

_PREVIEW_PATH = "/api/v1/forge/sessions/{sid}/tool-result/{tuid}/preview"

_RUNNING_POD_MANAGER = MockPodManager(wait_for_ready_result=SessionStatus.RUNNING)


@pytest.fixture(autouse=True)
def _strip_ambient_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hermetic against SKULD__*/VOLUNDR__*/BIFROST__* leaked from a dev box."""
    leaked = [
        key
        for key in os.environ
        if key.startswith(("SKULD__", "SKULD_", "VOLUNDR__", "VOLUNDR_", "BIFROST__", "BIFROST_"))
    ]
    for key in leaked:
        monkeypatch.delenv(key, raising=False)


class _NoWorkspaceStorage:
    """No workspace on disk — forces SessionArchiveService onto the event-log rebuild."""

    def resolve_session_workspace_path(self, _session_id: str) -> str | None:
        return None

    async def get_workspace_by_session(self, _session_id: str):
        return None


class _CountingEventLog(InMemorySessionEventLog):
    """Counts durable reads — the expensive rebuild the cache must avoid re-paying."""

    def __init__(self) -> None:
        super().__init__()
        self.read_after_calls = 0

    async def read_after(self, session_id, after_seq, **kwargs):  # type: ignore[override]
        self.read_after_calls += 1
        return await super().read_after(session_id, after_seq, **kwargs)


def _png_b64(width: int, height: int, color=(200, 30, 30), fmt: str = "PNG") -> str:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _image_envelope(width: int = 900, height: int = 450, b64: str | None = None) -> str:
    """The Skuld Read-an-image envelope: a JSON STRING inside tool_result content."""
    return json.dumps(
        {
            "type": "image",
            "file": {
                "base64": b64 if b64 is not None else _png_b64(width, height),
                "type": "image/png",
                "dimensions": {
                    "displayWidth": width,
                    "displayHeight": height,
                    "originalWidth": width,
                    "originalHeight": height,
                },
            },
        }
    )


def _frames(session_id, results: dict[str, str]) -> list[SessionLogEntry]:
    """One closed turn carrying a tool_use + tool_result per (tool_use_id → content)."""
    now = datetime(2026, 7, 4, 9, 0, 0, tzinfo=UTC)
    entries = [
        SessionLogEntry(
            session_id=session_id,
            seq=1,
            kind="user",
            payload={"uuid": "U1", "message": {"content": "read the screenshots"}},
            ts=now,
            role="user",
        )
    ]
    seq = 2
    for tool_use_id, content in results.items():
        entries.append(
            SessionLogEntry(
                session_id=session_id,
                seq=seq,
                kind="assistant",
                payload={
                    "message": {
                        "content": [
                            {"type": "tool_use", "id": tool_use_id, "name": "Read", "input": {}}
                        ]
                    }
                },
                ts=now,
                role="assistant",
                request_id="req-1",
            )
        )
        entries.append(
            SessionLogEntry(
                session_id=session_id,
                seq=seq + 1,
                kind="user",
                payload={
                    "message": {
                        "content": [
                            {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
                        ]
                    }
                },
                ts=now,
                role="user",
                request_id="req-1",
            )
        )
        seq += 2
    entries.append(
        SessionLogEntry(
            session_id=session_id,
            seq=seq,
            kind="result",
            payload={"subtype": "success"},
            ts=now,
            request_id="req-1",
        )
    )
    return entries


def _build(
    tmp_path,
    event_log: InMemorySessionEventLog,
    *,
    max_entries: int = 2000,
) -> tuple[TestClient, InMemorySessionRepository, PreviewCache, FastAPI]:
    repository = InMemorySessionRepository()
    session_service = SessionService(
        repository=repository,
        pod_manager=_RUNNING_POD_MANAGER,
        validate_repos=False,
    )
    archive_service = SessionArchiveService(
        session_service,
        _NoWorkspaceStorage(),
        FileSystemArchiveStore(),
        event_log_repository=event_log,
    )
    cache = PreviewCache(tmp_path / "preview-cache", max_entries=max_entries)

    app = FastAPI()
    app.include_router(
        create_router(
            session_service,
            archive_service=archive_service,
            preview_cache=cache,
        )
    )

    class _SettingsStub:
        local_mounts = LocalMountsConfig()

    app.state.settings = _SettingsStub()
    app.state.admin_settings = {}
    return TestClient(app), repository, cache, app


async def _stopped_session(repository: InMemorySessionRepository) -> Session:
    """A STOPPED session: no chat_endpoint, so previews come from the durable log
    without any httpx mocking (the live-pod branch is skipped entirely)."""
    session = Session(name="img-session", model="claude-opus-4-8", status=SessionStatus.STOPPED)
    await repository.create(session)
    return session


async def _running_session(repository: InMemorySessionRepository) -> Session:
    session = Session(name="img-live", model="claude-opus-4-8", status=SessionStatus.STOPPED)
    running = session.with_endpoints(
        f"ws://localhost:8080/s/{session.id}/session",
        f"https://localhost:8080/s/{session.id}/session",
    ).with_status(SessionStatus.RUNNING)
    await repository.create(running)
    return running


def _pod_response(status_code: int, body: dict | None):
    """A single mocked ``httpx.Response`` with a status-driven ``raise_for_status``."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = body

    def _raise_for_status():
        if status_code >= 400:
            raise httpx.HTTPStatusError("err", request=MagicMock(), response=resp)

    resp.raise_for_status.side_effect = _raise_for_status
    return resp


def _patch_tool_result_pod(*, status_code: int, body: dict | None):
    """Mock ``rest.httpx.AsyncClient``: EVERY live-pod GET returns the same status/body
    (per-id tool-result AND the old-broker conversation-history fallback both see it)."""
    mock_response = _pod_response(status_code, body)
    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    return mock_response, mock_client


def _patch_pod_routes(*, tool_result: MagicMock, history: MagicMock):
    """Mock ``rest.httpx.AsyncClient`` so the per-id tool-result endpoint and the
    conversation-history endpoint return DIFFERENT responses (the old-broker recovery
    path: per-id 404, but the full history carries the image inline)."""

    def _route(url, *args, **kwargs):
        return history if "history" in str(url) else tool_result

    mock_client = AsyncMock()
    mock_client.get.side_effect = _route
    return mock_client


# --- (1) happy path: 200 image/jpeg, scaled, immutable-cacheable -------------------


@pytest.mark.asyncio
async def test_preview_scales_to_max_edge_jpeg_with_immutable_cache_headers(tmp_path) -> None:
    event_log = _CountingEventLog()
    client, repository, _, _ = _build(tmp_path, event_log)
    session = await _stopped_session(repository)
    await event_log.append(_frames(session.id, {"tu-img": _image_envelope(900, 450)}))

    resp = client.get(_PREVIEW_PATH.format(sid=session.id, tuid="tu-img"))

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert "immutable" in resp.headers["cache-control"]
    with Image.open(io.BytesIO(resp.content)) as img:
        assert img.format == "JPEG"
        assert max(img.size) <= 400
        # Aspect preserved: 900x450 → 400x200.
        assert img.size == (400, 200)
    # The preview is a real transit win vs the full envelope.
    assert len(resp.content) < len(_image_envelope(900, 450))


# --- (2) disk cache: second request never re-reads the durable log ----------------


@pytest.mark.asyncio
async def test_preview_second_request_is_a_cache_hit(tmp_path) -> None:
    event_log = _CountingEventLog()
    client, repository, cache, _ = _build(tmp_path, event_log)
    session = await _stopped_session(repository)
    await event_log.append(_frames(session.id, {"tu-img": _image_envelope(640, 640)}))

    first = client.get(_PREVIEW_PATH.format(sid=session.id, tuid="tu-img"))
    assert first.status_code == 200
    reads_after_first = event_log.read_after_calls
    assert reads_after_first >= 1
    assert cache.has(str(session.id), "tu-img")
    # The cache file is on the injected tmp root (hermetic + restart-durable shape).
    files = list((tmp_path / "preview-cache").glob("*/*.jpg"))
    assert len(files) == 1

    second = client.get(_PREVIEW_PATH.format(sid=session.id, tuid="tu-img"))
    assert second.status_code == 200
    assert second.content == first.content
    # No new durable read: the hit was served from disk before any fetch chain.
    assert event_log.read_after_calls == reads_after_first


# --- (3) non-image → 404, and the negative cache stops repeat rebuilds ------------


@pytest.mark.asyncio
async def test_preview_non_image_404_and_negative_cached(tmp_path) -> None:
    event_log = _CountingEventLog()
    client, repository, _, _ = _build(tmp_path, event_log)
    session = await _stopped_session(repository)
    await event_log.append(_frames(session.id, {"tu-text": "Z" * 6000}))

    first = client.get(_PREVIEW_PATH.format(sid=session.id, tuid="tu-text"))
    assert first.status_code == 404
    reads_after_first = event_log.read_after_calls

    second = client.get(_PREVIEW_PATH.format(sid=session.id, tuid="tu-text"))
    assert second.status_code == 404
    # Negative-cached: the repeat 404 did not pay another durable read.
    assert event_log.read_after_calls == reads_after_first


# --- (4) corrupt base64 in an image envelope → 404 --------------------------------


@pytest.mark.asyncio
async def test_preview_corrupt_base64_404(tmp_path) -> None:
    event_log = _CountingEventLog()
    client, repository, _, _ = _build(tmp_path, event_log)
    session = await _stopped_session(repository)
    corrupt = json.dumps(
        {"type": "image", "file": {"base64": "!!!not-base64!!!", "type": "image/png"}}
    )
    await event_log.append(_frames(session.id, {"tu-corrupt": corrupt}))

    resp = client.get(_PREVIEW_PATH.format(sid=session.id, tuid="tu-corrupt"))
    assert resp.status_code == 404


# --- (5) single-flight: two concurrent first-requests, one generation --------------


@pytest.mark.asyncio
async def test_preview_concurrent_first_requests_generate_once(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    event_log = _CountingEventLog()
    _, repository, _, app = _build(tmp_path, event_log)
    session = await _stopped_session(repository)
    await event_log.append(_frames(session.id, {"tu-img": _image_envelope(500, 500)}))

    calls = {"n": 0}
    real_generate = generate_preview_jpeg

    def _slow_generate(raw: bytes, **kwargs) -> bytes:
        calls["n"] += 1
        time.sleep(0.15)  # in a worker thread (asyncio.to_thread) — widens the race window
        return real_generate(raw, **kwargs)

    # The warm pass calls the module-global inside skuld.tool_result_preview; the
    # direct path uses the name imported into volundr rest. Patch both.
    monkeypatch.setattr(trp_mod, "generate_preview_jpeg", _slow_generate)
    monkeypatch.setattr("volundr.adapters.inbound.rest.generate_preview_jpeg", _slow_generate)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        url = _PREVIEW_PATH.format(sid=session.id, tuid="tu-img")
        r1, r2 = await asyncio.gather(client.get(url), client.get(url))

    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.content == r2.content
    assert calls["n"] == 1  # single-flight: the second request woke into a cache hit
    assert event_log.read_after_calls == 1  # and exactly one durable rebuild was paid


# --- (6) eviction: oldest-mtime previews pruned past max_entries -------------------


def test_preview_cache_evicts_oldest_past_max_entries(tmp_path) -> None:
    cache = PreviewCache(tmp_path / "cache", max_entries=3)
    jpeg = generate_preview_jpeg(base64.b64decode(_png_b64(64, 64)))
    for index, tuid in enumerate(["tu-a", "tu-b", "tu-c"]):
        cache.put("session-1", tuid, jpeg)
        # Distinct, strictly increasing mtimes (same-second writes are unordered).
        stamp = time.time() - 1000 + index
        os.utime(cache._path("session-1", tuid), (stamp, stamp))

    cache.put("session-1", "tu-d", jpeg)  # 4th insert triggers the prune

    assert cache.get("session-1", "tu-a") is None  # oldest evicted
    for kept in ["tu-b", "tu-c", "tu-d"]:
        assert cache.get("session-1", kept) is not None


# --- (7) warm pass: one request caches EVERY image in the durable transcript -------


@pytest.mark.asyncio
async def test_preview_warm_pass_caches_all_images_in_one_rebuild(tmp_path) -> None:
    event_log = _CountingEventLog()
    client, repository, cache, _ = _build(tmp_path, event_log)
    session = await _stopped_session(repository)
    await event_log.append(
        _frames(
            session.id,
            {
                "tu-one": _image_envelope(800, 400),
                "tu-two": _image_envelope(300, 600),
                "tu-text": "not an image " * 200,
            },
        )
    )

    resp = client.get(_PREVIEW_PATH.format(sid=session.id, tuid="tu-one"))
    assert resp.status_code == 200
    assert event_log.read_after_calls == 1

    # The OTHER image was warmed by the same rebuild; the text result was not.
    assert cache.has(str(session.id), "tu-two")
    assert not cache.has(str(session.id), "tu-text")

    other = client.get(_PREVIEW_PATH.format(sid=session.id, tuid="tu-two"))
    assert other.status_code == 200
    assert event_log.read_after_calls == 1  # served from the warm cache, no new rebuild


# --- (8) the validation-session shape: live pod 404s (old broker) → durable feed ---


@pytest.mark.asyncio
async def test_preview_old_broker_pod_404_served_from_durable_log(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    event_log = _CountingEventLog()
    client, repository, _, _ = _build(tmp_path, event_log)
    session = await _running_session(repository)
    await event_log.append(_frames(session.id, {"tu-img": _image_envelope(1280, 720)}))

    _, mock_client = _patch_tool_result_pod(status_code=404, body=None)
    with monkeypatch.context() as m:
        client_cls = MagicMock()
        client_cls.return_value.__aenter__.return_value = mock_client
        m.setattr("volundr.adapters.inbound.rest.httpx.AsyncClient", client_cls)
        resp = client.get(_PREVIEW_PATH.format(sid=session.id, tuid="tu-img"))

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    # BOTH live-pod sources are consulted before the durable feed: the per-id endpoint
    # (404 — old broker) then the conversation-history recovery (also 404 here), then durable.
    assert mock_client.get.await_count == 2
    with Image.open(io.BytesIO(resp.content)) as img:
        assert max(img.size) <= 400


# --- (8b) Finding-3: per-id 404 + durable MISS, but the live history has the image --


@pytest.mark.asyncio
async def test_preview_old_broker_recovered_from_live_history_and_warms(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lexi-frontend-presentation shape: an OLD broker 404s the per-id endpoint AND
    the durable rebuild misses the big image ids — but the pod's FULL conversation-history
    still carries them inline. The preview must be recovered from that history, WITHOUT any
    durable read, and the one fetch must warm every image in the session."""
    event_log = _CountingEventLog()  # durable log is EMPTY → a durable scan would miss
    client, repository, cache, _ = _build(tmp_path, event_log)
    session = await _running_session(repository)

    history_body = {
        "turns": [
            {
                "role": "user",
                "parts": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu-hist",
                        "content": _image_envelope(1000, 500),
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu-hist-2",
                        "content": _image_envelope(400, 800),
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu-text",
                        "content": "not an image " * 100,
                    },
                ],
            }
        ]
    }
    mock_client = _patch_pod_routes(
        tool_result=_pod_response(404, None),
        history=_pod_response(200, history_body),
    )
    with monkeypatch.context() as m:
        client_cls = MagicMock()
        client_cls.return_value.__aenter__.return_value = mock_client
        m.setattr("volundr.adapters.inbound.rest.httpx.AsyncClient", client_cls)
        resp = client.get(_PREVIEW_PATH.format(sid=session.id, tuid="tu-hist"))

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(resp.content)) as img:
        assert max(img.size) <= 400
    # Recovered from the LIVE HISTORY, not the durable log (which is empty).
    assert event_log.read_after_calls == 0
    # The per-id endpoint (404) AND the history (200) were both consulted.
    assert mock_client.get.await_count == 2
    # One fetch warmed EVERY image in the history; the text result was skipped.
    assert cache.has(str(session.id), "tu-hist-2")
    assert not cache.has(str(session.id), "tu-text")


# --- (9) live pod serves the envelope → preview without touching the durable log ---


@pytest.mark.asyncio
async def test_preview_live_pod_envelope_no_durable_read(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    event_log = _CountingEventLog()
    client, repository, _, _ = _build(tmp_path, event_log)
    session = await _running_session(repository)

    pod_body = {"tool_use_id": "tu-live", "content": _image_envelope(600, 300), "is_error": False}
    _, mock_client = _patch_tool_result_pod(status_code=200, body=pod_body)
    with monkeypatch.context() as m:
        client_cls = MagicMock()
        client_cls.return_value.__aenter__.return_value = mock_client
        m.setattr("volundr.adapters.inbound.rest.httpx.AsyncClient", client_cls)
        resp = client.get(_PREVIEW_PATH.format(sid=session.id, tuid="tu-live"))

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert event_log.read_after_calls == 0
    with Image.open(io.BytesIO(resp.content)) as img:
        assert img.size == (400, 200)


# --- (10) absent everywhere → 404 ---------------------------------------------------


@pytest.mark.asyncio
async def test_preview_absent_tool_result_404(tmp_path) -> None:
    event_log = _CountingEventLog()
    client, repository, _, _ = _build(tmp_path, event_log)
    session = await _stopped_session(repository)
    await event_log.append(_frames(session.id, {"tu-img": _image_envelope(300, 300)}))

    resp = client.get(_PREVIEW_PATH.format(sid=session.id, tuid="does-not-exist"))
    assert resp.status_code == 404


# --- extract_image_bytes unit coverage ----------------------------------------------


def test_extract_image_bytes_skuld_envelope_string() -> None:
    raw, mime = extract_image_bytes(_image_envelope(32, 16))
    assert mime == "image/png"
    with Image.open(io.BytesIO(raw)) as img:
        assert img.size == (32, 16)


def test_extract_image_bytes_anthropic_array_form() -> None:
    b64 = _png_b64(16, 16)
    content = [{"type": "image", "source": {"data": b64, "media_type": "image/png"}}]
    extracted = extract_image_bytes(content)
    assert extracted is not None
    raw, mime = extracted
    assert mime == "image/png"
    assert raw == base64.b64decode(b64)


def test_extract_image_bytes_non_image_returns_none() -> None:
    assert extract_image_bytes("plain text output") is None
    assert extract_image_bytes(json.dumps({"type": "text", "text": "hi"})) is None
    assert extract_image_bytes([{"type": "text", "text": "hi"}]) is None
    assert extract_image_bytes(None) is None
    assert extract_image_bytes(json.dumps({"type": "image", "file": {}})) is None


def test_extract_image_bytes_corrupt_base64_raises_value_error() -> None:
    corrupt = json.dumps({"type": "image", "file": {"base64": "@@@", "type": "image/png"}})
    with pytest.raises(ValueError):
        extract_image_bytes(corrupt)


def test_generate_preview_flattens_alpha_and_scales() -> None:
    buf = io.BytesIO()
    Image.new("RGBA", (500, 1000), (0, 128, 255, 128)).save(buf, format="PNG")
    jpeg = generate_preview_jpeg(buf.getvalue())
    with Image.open(io.BytesIO(jpeg)) as img:
        assert img.format == "JPEG"
        assert img.mode == "RGB"
        assert img.size == (200, 400)


def test_generate_preview_undecodable_bytes_raise_value_error() -> None:
    with pytest.raises(ValueError):
        generate_preview_jpeg(b"definitely not an image")
