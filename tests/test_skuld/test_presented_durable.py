"""F2 at the broker: present-file hands Forge a durable copy and records its URL."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from skuld.config import PresentedDurableConfig
from skuld.presented_durable import (
    DURABLE_MARKER,
    PENDING_MARKER,
    REFUSED_MARKER,
    durable_record,
    pending_uploads,
    retry_presented_upload,
    upload_presented_file,
    write_pending_record,
)

URL = "http://thor.tail737f2a.ts.net:8380/api/v1/forge/sessions/s/files/presented/pf_x"


def _broker(response=None, exc=None, *, api="http://forge", retry_attempts=2):
    client = MagicMock()
    if exc is not None:
        client.put = AsyncMock(side_effect=exc)
    else:
        client.put = AsyncMock(return_value=response)
    return SimpleNamespace(
        volundr_api_url=api,
        session_id="11111111-1111-1111-1111-111111111111",
        _get_http_client=AsyncMock(return_value=client),
        _settings=SimpleNamespace(
            presented_durable=PresentedDurableConfig(
                retry_attempts=retry_attempts,
                retry_backoff_seconds=0.001,
                retry_backoff_max_seconds=0.002,
            )
        ),
        client=client,
    )


def _ok(url=URL):
    response = MagicMock(status_code=200)
    response.json.return_value = {"url": url, "path": "/p", "sha256": "x"}
    return response


@pytest.fixture
def staged(tmp_path):
    path = tmp_path / "pf_x" / "content"
    path.parent.mkdir()
    path.write_bytes(b"%PDF")
    return path


async def test_upload_puts_bytes_and_writes_the_receipt(staged):
    broker = _broker(_ok())
    receipt = await upload_presented_file(
        broker, "pf_x", staged, name="My report.pdf", mime="application/pdf"
    )
    assert receipt["url"] == URL
    call = broker.client.put.await_args
    assert call.args[0].endswith(
        "/sessions/11111111-1111-1111-1111-111111111111/files/presented/pf_x"
    )
    assert call.kwargs["content"] == b"%PDF"
    assert call.kwargs["headers"]["x-presented-file-name"] == "My%20report.pdf"
    assert durable_record(staged)["url"] == URL
    # Already durable: no second upload.
    await upload_presented_file(broker, "pf_x", staged, name="n", mime="m")
    assert broker.client.put.await_count == 1


@pytest.mark.parametrize(
    "broker",
    [
        _broker(exc=OSError("down")),
        _broker(MagicMock(status_code=403)),
        _broker(api=""),
    ],
)
async def test_upload_failures_return_none(staged, broker):
    assert await upload_presented_file(broker, "pf_x", staged, name="n", mime="m") is None
    assert not (staged.parent / DURABLE_MARKER).exists()


async def test_unparseable_receipt_is_a_failure(staged):
    response = MagicMock(status_code=200)
    response.json.side_effect = ValueError
    assert (
        await upload_presented_file(_broker(response), "pf_x", staged, name="n", mime="m") is None
    )
    response.json.side_effect = None
    response.json.return_value = ["not", "a", "dict"]
    assert (
        await upload_presented_file(_broker(response), "pf_x", staged, name="n", mime="m") is None
    )


async def test_retry_until_forge_accepts(staged):
    broker = _broker()
    broker.client.put = AsyncMock(side_effect=[OSError("down"), _ok()])
    receipt = await retry_presented_upload(broker, "pf_x", staged, name="n", mime="m")
    assert receipt["url"] == URL
    assert broker.client.put.await_count == 2


async def test_retry_gives_up_after_the_configured_attempts(staged, caplog):
    broker = _broker(exc=OSError("down"), retry_attempts=3)
    with caplog.at_level(logging.ERROR, logger="skuld.broker"):
        assert await retry_presented_upload(broker, "pf_x", staged, name="n", mime="m") is None
    assert broker.client.put.await_count == 3
    # Exhausting the retries is loud and names the knob.
    assert any(
        record.levelno == logging.ERROR
        and "presented_durable.retry_attempts" in record.getMessage()
        for record in caplog.records
    )


def test_durable_record_tolerates_garbage(tmp_path):
    staged = tmp_path / "content"
    assert durable_record(staged) is None
    (tmp_path / DURABLE_MARKER).write_text("[1]")
    assert durable_record(staged) is None


@pytest.fixture
def client(tmp_path, monkeypatch, module_broker_runtime):
    from skuld import broker as bmod
    from skuld import broker_api as api_mod

    monkeypatch.setattr(api_mod, "_presented_staging_dir", lambda: tmp_path / "staging")
    monkeypatch.setattr(bmod.broker, "workspace_dir", str(tmp_path))

    async def _fake_broadcast(frame):
        return None

    monkeypatch.setattr(bmod.broker, "_enqueue_event_log", lambda *_: None)
    monkeypatch.setattr(bmod.broker, "_save_conversation_history", lambda: None)
    monkeypatch.setattr(bmod.broker._channels, "broadcast", _fake_broadcast)
    monkeypatch.setattr(bmod.broker, "_conversation_turns", [])
    api_mod._presented_registry.clear()
    return TestClient(
        bmod.app,
        headers={
            "authorization": module_broker_runtime.authorization,
            "x-niuu-room-role": "owner",
        },
    )


def test_present_file_records_the_durable_url(client, tmp_path, monkeypatch):
    from skuld import broker as bmod
    from skuld import broker_api as api_mod

    async def fake_upload(broker, file_id, staged, *, name, mime, timeout=None):
        assert timeout == broker._settings.presented_durable.inline_upload_timeout_seconds
        assert staged.read_bytes() == b"hello"
        return {"url": URL}

    monkeypatch.setattr(api_mod, "upload_presented_file", fake_upload)
    src = tmp_path / "notes.txt"
    src.write_bytes(b"hello")
    body = client.post("/api/present-file", json={"path": str(src)}).json()
    assert body["url"] == URL and body["durable"] is True
    part = bmod.broker._conversation_turns[-1].parts[0]
    assert part["input"]["url"] == URL


def test_present_file_still_succeeds_when_forge_is_down(client, tmp_path, monkeypatch):
    from skuld import broker as bmod
    from skuld import broker_api as api_mod

    scheduled: list = []

    async def failing_upload(*args, **kwargs):
        return None

    async def fake_retry(*args, **kwargs):
        scheduled.append(args[1])

    monkeypatch.setattr(api_mod, "upload_presented_file", failing_upload)
    monkeypatch.setattr(api_mod, "retry_presented_upload", fake_retry)
    monkeypatch.setattr(bmod.broker, "volundr_api_url", "http://forge")
    src = tmp_path / "notes.txt"
    src.write_bytes(b"hello")
    resp = client.post("/api/present-file", json={"path": str(src)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["durable"] is False and "url" not in body
    assert "url" not in bmod.broker._conversation_turns[-1].parts[0]["input"]
    assert scheduled == [body["file_id"]]


def test_registry_rebuild_never_serves_the_receipt(tmp_path, monkeypatch):
    from skuld import broker_api as api_mod

    root = tmp_path / "staging"
    entry = root / ("pf_" + "b" * 32)
    entry.mkdir(parents=True)
    (entry / DURABLE_MARKER).write_text('{"url": "x"}')
    (entry / "content").write_bytes(b"bytes")
    monkeypatch.setattr(api_mod, "_presented_staging_dir", lambda: root)
    api_mod._rebuild_presented_registry()
    assert api_mod._presented_registry[entry.name].endswith("/content")


def _stage(root, file_id, *, pending=True, durable=False):
    entry = root / file_id
    entry.mkdir(parents=True)
    content = entry / "content"
    content.write_bytes(b"bytes-" + file_id.encode())
    if pending:
        write_pending_record(content, name=f"{file_id}.txt", mime="text/plain")
    if durable:
        (entry / DURABLE_MARKER).write_text('{"url": "x"}')
    return content


def test_pending_uploads_lists_only_copies_not_yet_durable(tmp_path):
    a, b, c = ("pf_" + ch * 32 for ch in "abc")
    _stage(tmp_path, a)
    _stage(tmp_path, b, durable=True)
    _stage(tmp_path, c, pending=False)  # staged before F2: no record, nothing to resume
    (tmp_path / "pf_bad").mkdir()
    (tmp_path / "pf_bad" / "content").write_bytes(b"x")
    (tmp_path / "pf_bad" / PENDING_MARKER).write_text("not json")
    assert [(fid, name, mime) for fid, _, name, mime in pending_uploads(tmp_path)] == [
        (a, f"{a}.txt", "text/plain")
    ]
    assert pending_uploads(tmp_path / "missing") == []


def test_present_file_writes_the_pending_record(client, tmp_path, monkeypatch):
    from skuld import broker_api as api_mod

    async def no_upload(*args, **kwargs):
        return None

    monkeypatch.setattr(api_mod, "upload_presented_file", no_upload)
    src = tmp_path / "notes.txt"
    src.write_bytes(b"hello")
    file_id = client.post("/api/present-file", json={"path": str(src)}).json()["file_id"]
    record = json.loads((tmp_path / "staging" / file_id / PENDING_MARKER).read_text())
    assert record == {"name": "notes.txt", "mime": "text/plain"}


async def test_restart_resumes_uploads_that_had_not_landed(tmp_path, monkeypatch):
    """A broker restart re-queues every staged copy without a receipt, and the retry
    lands it in Forge; copies already durable are not uploaded again."""
    import asyncio

    from skuld import broker_api as api_mod

    root = tmp_path / "staging"
    pending_id, done_id = "pf_" + "1" * 32, "pf_" + "2" * 32
    _stage(root, pending_id)
    _stage(root, done_id, durable=True)
    fake = _broker(_ok())
    monkeypatch.setattr(api_mod, "broker", fake)
    monkeypatch.setattr(api_mod, "_presented_staging_dir", lambda: root)

    assert api_mod._resume_presented_uploads() == 1
    await asyncio.gather(*list(api_mod._presented_upload_tasks))

    fake.client.put.assert_awaited_once()
    call = fake.client.put.await_args
    assert call.args[0].endswith(f"/files/presented/{pending_id}")
    assert call.kwargs["content"] == b"bytes-" + pending_id.encode()
    assert durable_record(root / pending_id / "content")["url"] == URL
    assert pending_uploads(root) == []


def test_resume_is_off_without_forge(tmp_path, monkeypatch):
    from skuld import broker_api as api_mod

    root = tmp_path / "staging"
    _stage(root, "pf_" + "3" * 32)
    monkeypatch.setattr(api_mod, "broker", _broker(api=""))
    monkeypatch.setattr(api_mod, "_presented_staging_dir", lambda: root)
    assert api_mod._resume_presented_uploads() == 0


def test_registry_rebuild_never_serves_the_pending_record(tmp_path, monkeypatch):
    from skuld import broker_api as api_mod

    root = tmp_path / "staging"
    entry = root / ("pf_" + "c" * 32)
    entry.mkdir(parents=True)
    (entry / PENDING_MARKER).write_text('{"name": "n", "mime": "m"}')
    monkeypatch.setattr(api_mod, "_presented_staging_dir", lambda: root)
    api_mod._rebuild_presented_registry()
    assert entry.name not in api_mod._presented_registry


def _status(code):
    return MagicMock(status_code=code)


@pytest.mark.parametrize("code", [403, 404, 409, 413])
async def test_a_final_refusal_stops_the_retries_for_good(staged, code):
    broker = _broker(_status(code), retry_attempts=4)
    write_pending_record(staged, name="n", mime="m")
    assert await retry_presented_upload(broker, "pf_x", staged, name="n", mime="m") is None
    assert broker.client.put.await_count == 1
    assert pending_uploads(staged.parent.parent) == []  # a restart does not resume it


@pytest.mark.parametrize("code", [429, 500, 503])
async def test_transient_answers_keep_retrying(staged, code):
    broker = _broker(_status(code), retry_attempts=3)
    assert await retry_presented_upload(broker, "pf_x", staged, name="n", mime="m") is None
    assert broker.client.put.await_count == 3
    assert not (staged.parent / REFUSED_MARKER).exists()


async def test_resume_uploads_one_file_at_a_time(tmp_path, monkeypatch):
    import asyncio

    from skuld import broker_api as api_mod

    root = tmp_path / "staging"
    ids = ["pf_" + ch * 32 for ch in "456"]
    for fid in ids:
        _stage(root, fid)
    fake = _broker()
    in_flight, peak, order = 0, 0, []

    async def put(url, **kwargs):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0)
        in_flight -= 1
        order.append(url.rsplit("/", 1)[1])
        return _ok()

    fake.client.put = put
    monkeypatch.setattr(api_mod, "broker", fake)
    monkeypatch.setattr(api_mod, "_presented_staging_dir", lambda: root)
    assert api_mod._resume_presented_uploads() == 3
    await asyncio.gather(*list(api_mod._presented_upload_tasks))
    assert order == ids and peak == 1
    assert pending_uploads(root) == []


def test_registry_rebuild_never_serves_a_partial_copy(tmp_path, monkeypatch):
    from skuld import broker_api as api_mod

    root = tmp_path / "staging"
    entry = root / ("pf_" + "d" * 32)
    entry.mkdir(parents=True)
    (entry / ".content.partial").write_bytes(b"half")
    monkeypatch.setattr(api_mod, "_presented_staging_dir", lambda: root)
    api_mod._rebuild_presented_registry()
    assert entry.name not in api_mod._presented_registry


# --- LEX-1514: the host's presented-file quota ---------------------------------


def _quota_refusal():
    response = MagicMock(status_code=507)
    response.json.return_value = {
        "detail": {
            "code": "presented_files_quota",
            "message": "Forge refused to keep this file: ... (presented_files.max_total_bytes)",
        }
    }
    return response


async def test_a_quota_refusal_raises_and_is_final(staged):
    from skuld.presented_durable import PresentedFileQuotaRefusedError

    broker = _broker(_quota_refusal())
    with pytest.raises(PresentedFileQuotaRefusedError, match="max_total_bytes"):
        await upload_presented_file(broker, "pf_x", staged, name="n", mime="m")
    assert (staged.parent / REFUSED_MARKER).exists()


async def test_the_retry_loop_stops_on_a_quota_refusal(staged):
    broker = _broker(_quota_refusal(), retry_attempts=4)
    write_pending_record(staged, name="n", mime="m")
    assert await retry_presented_upload(broker, "pf_x", staged, name="n", mime="m") is None
    assert broker.client.put.await_count == 1
    assert pending_uploads(staged.parent.parent) == []


async def test_a_plain_507_is_still_retried(staged):
    broker = _broker(_status(507), retry_attempts=3)
    assert await retry_presented_upload(broker, "pf_x", staged, name="n", mime="m") is None
    assert broker.client.put.await_count == 3


def test_present_file_fails_loudly_when_the_quota_is_full(client, tmp_path, monkeypatch):
    from skuld import broker as bmod
    from skuld import broker_api as api_mod
    from skuld.presented_durable import PresentedFileQuotaRefusedError

    async def refused(*args, **kwargs):
        raise PresentedFileQuotaRefusedError(
            "Forge refused to keep this file (presented_files.max_total_bytes). Raise "
            "presented_files.max_total_bytes, or delete sessions with presented files."
        )

    monkeypatch.setattr(api_mod, "upload_presented_file", refused)
    monkeypatch.setattr(bmod.broker, "volundr_api_url", "http://forge")
    src = tmp_path / "big.bin"
    src.write_bytes(b"x" * 10)
    turns_before = len(bmod.broker._conversation_turns)

    resp = client.post("/api/present-file", json={"path": str(src)})

    assert resp.status_code == 507
    assert "presented_files.max_total_bytes" in resp.text
    assert len(bmod.broker._conversation_turns) == turns_before  # nothing presented
    assert api_mod._presented_registry == {}
    assert not any((tmp_path / "staging").glob("pf_*"))
