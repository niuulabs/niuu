"""present-file: stage a host file, emit a durable present_file turn, serve it by opaque id.

Covers the in-app SendUserFile analogue end to end at the broker app: POST /api/present-file stages
copy + emits a self-contained conversation.turn carrying a present_file tool_use part, and
GET /api/files/presented/{file_id} serves the staged bytes by opaque id (traversal-safe).
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from skuld import broker as bmod
    from skuld import broker_api as api_mod

    # Stage into a temp dir (not the real session home) and capture emitted frames instead of
    # persisting/broadcasting them.
    monkeypatch.setattr(api_mod, "_presented_staging_dir", lambda: tmp_path / "staging")
    monkeypatch.setattr(bmod.broker, "workspace_dir", str(tmp_path))
    captured: list = []

    async def _fake_emit(frame):
        captured.append(frame)

    monkeypatch.setattr(bmod.broker, "_emit_broker_frame", _fake_emit)
    api_mod._presented_registry.clear()
    c = TestClient(bmod.app)
    c.captured = captured  # type: ignore[attr-defined]
    return c


def test_present_file_end_to_end(client, tmp_path):
    payload = b"%PDF-1.4 hello world"
    src = tmp_path / "report.pdf"
    src.write_bytes(payload)

    r = client.post("/api/present-file", json={"path": str(src), "caption": "the report"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "report.pdf"
    assert body["mime"] == "application/pdf"
    assert body["size"] == len(payload)
    fid = body["file_id"]
    assert fid.startswith("pf_") and len(fid) == 35

    # exactly one self-contained conversation.turn with a present_file tool_use part
    assert len(client.captured) == 1
    frame = client.captured[0]
    assert frame["type"] == "conversation.turn"
    turn = frame["turn"]
    assert turn["role"] == "assistant"
    part = turn["parts"][0]
    assert part["type"] == "tool_use" and part["name"] == "present_file"
    assert part["input"]["file_id"] == fid
    assert part["input"]["caption"] == "the report"
    assert turn["metadata"].get("present_file") is True

    # download by opaque id returns the staged bytes verbatim
    d = client.get(f"/api/files/presented/{fid}")
    assert d.status_code == 200
    assert d.content == payload


def test_title_overrides_name(client, tmp_path):
    src = tmp_path / "raw.bin"
    src.write_bytes(b"x")
    r = client.post("/api/present-file", json={"path": str(src), "title": "Pretty Name.bin"})
    assert r.status_code == 200
    assert r.json()["name"] == "Pretty Name.bin"


def test_present_file_log_escapes_forged_newline(client, tmp_path, caplog):
    src = tmp_path / "raw.bin"
    src.write_bytes(b"x")
    title = "report\nFORGED"
    caplog.set_level("INFO", logger="skuld.broker")

    response = client.post("/api/present-file", json={"path": str(src), "title": title})

    assert response.status_code == 200
    message = next(
        record.getMessage() for record in caplog.records if "staged" in record.getMessage()
    )
    assert title not in message
    assert "report\\nFORGED" in message


def test_present_file_rejects_bad_input(client):
    assert client.post("/api/present-file", json={}).status_code == 400
    response = client.post("/api/present-file", json={"path": "/no/such/file/xyz"})
    assert response.status_code == 400
    # opaque-id guard: a client-supplied path can never reach the filesystem
    assert client.get("/api/files/presented/not-an-id").status_code == 400
    assert client.get("/api/files/presented/../../etc/passwd").status_code in (400, 404)
    assert client.get("/api/files/presented/pf_" + "0" * 32).status_code == 404


def test_present_file_size_cap(client, tmp_path, monkeypatch):
    from skuld import broker as bmod

    monkeypatch.setattr(bmod.broker._settings, "max_presented_file_bytes", 8)
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * 100)
    assert client.post("/api/present-file", json={"path": str(big)}).status_code == 413


def test_registry_rebuild_recovers_after_restart(client, tmp_path):
    from skuld import broker_api as api_mod

    src = tmp_path / "doc.txt"
    src.write_bytes(b"recovered")
    fid = client.post("/api/present-file", json={"path": str(src)}).json()["file_id"]
    # Simulate a broker restart: the in-memory registry is lost, then rebuilt from the staging dir.
    api_mod._presented_registry.clear()
    api_mod._rebuild_presented_registry()
    assert fid in api_mod._presented_registry
    assert client.get(f"/api/files/presented/{fid}").content == b"recovered"
