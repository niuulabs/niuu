"""present-file: stage a host file, emit a durable present_file turn, serve it by opaque id.

Covers the in-app SendUserFile analogue end to end at the broker app: POST /api/present-file stages
copy + emits a self-contained conversation.turn carrying a present_file tool_use part, and
GET /api/files/presented/{file_id} serves the staged bytes by opaque id (traversal-safe).
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch, module_broker_runtime):
    from skuld import broker as bmod
    from skuld import broker_api as api_mod

    # Stage into a temp dir and capture durable-log and live-broadcast parity.
    monkeypatch.setattr(api_mod, "_presented_staging_dir", lambda: tmp_path / "staging")
    monkeypatch.setattr(bmod.broker, "workspace_dir", str(tmp_path))
    logged: list = []
    broadcast: list = []

    async def _fake_broadcast(frame):
        broadcast.append(frame)

    monkeypatch.setattr(bmod.broker, "_enqueue_event_log", logged.append)
    monkeypatch.setattr(bmod.broker, "_save_conversation_history", lambda: None)
    monkeypatch.setattr(bmod.broker._channels, "broadcast", _fake_broadcast)
    monkeypatch.setattr(bmod.broker, "_conversation_turns", [])
    api_mod._presented_registry.clear()
    c = TestClient(
        bmod.app,
        headers={
            "authorization": module_broker_runtime.authorization,
            "x-niuu-room-role": "owner",
        },
    )
    c.logged = logged  # type: ignore[attr-defined]
    c.broadcast = broadcast  # type: ignore[attr-defined]
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

    # One identical turn lands in live memory, durable log, and broadcast.
    from skuld import broker as bmod

    assert len(bmod.broker._conversation_turns) == 1
    mem = bmod.broker._conversation_turns[0]
    assert mem.role == "assistant"
    assert mem.parts[0]["name"] == "present_file"

    assert len(client.logged) == 1
    frame = client.logged[0]
    assert frame["type"] == "conversation.turn"
    turn = frame["turn"]
    assert turn["role"] == "assistant"
    assert turn["id"] == mem.id
    part = turn["parts"][0]
    assert part["type"] == "tool_use" and part["name"] == "present_file"
    assert part["input"]["file_id"] == fid
    assert part["input"]["caption"] == "the report"
    assert turn["metadata"].get("present_file") is True

    assert len(client.broadcast) == 1
    assert client.broadcast[0]["turn"]["id"] == mem.id

    hist = client.get("/api/conversation/history").json()
    assert mem.id in [item["id"] for item in hist["turns"]]

    # download by opaque id returns the staged bytes verbatim
    d = client.get(f"/api/files/presented/{fid}")
    assert d.status_code == 200
    assert d.content == payload


def test_a_presented_file_records_where_it_was_in_the_session(client, tmp_path, monkeypatch):
    """Its links in the conversation find the durable copy once the session's files are gone."""
    from skuld import broker as bmod

    home = tmp_path / "home"
    (tmp_path / "docs").mkdir()
    (home / "artifacts").mkdir(parents=True)
    monkeypatch.setattr(bmod.broker._settings, "home_dir", str(home))
    (tmp_path / "docs" / "plan.md").write_text("# Plan")
    (home / "artifacts" / "diagram.svg").write_text("<svg/>")

    client.post("/api/present-file", json={"path": str(tmp_path / "docs" / "plan.md")})
    client.post(
        "/api/present-file", json={"path": "~/artifacts/diagram.svg".replace("~", str(home))}
    )

    paths = [frame["turn"]["parts"][0]["input"]["path"] for frame in client.logged]
    assert paths == ["/workspace/docs/plan.md", "/home/artifacts/diagram.svg"]


def test_a_run_member_shares_what_it_presents_with_the_runs_room(client, tmp_path, monkeypatch):
    from skuld import broker as bmod

    shared: list = []

    class _Sharer:
        async def presented(self, record):
            shared.append(record)

    monkeypatch.setattr(bmod.broker, "_work_sharer", _Sharer())
    (tmp_path / "review.md").write_text("# Review")

    r = client.post("/api/present-file", json={"path": str(tmp_path / "review.md")})

    assert r.status_code == 200, r.text
    [record] = shared
    assert record["file_id"] == r.json()["file_id"]
    assert record["name"] == "review.md"


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


@pytest.mark.parametrize("authorization", [None, "Bearer wrong", "Basic abc", "Bearer "])
def test_present_file_requires_the_loopback_secret(client, tmp_path, authorization):
    src = tmp_path / "doc.txt"
    src.write_bytes(b"secret-guarded")
    headers = {"authorization": authorization} if authorization is not None else {}
    unauthenticated = TestClient(client.app, headers=headers)
    response = unauthenticated.post("/api/present-file", json={"path": str(src)})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert client.logged == []


def test_configured_staging_dir_is_created_outside_the_home(tmp_path, monkeypatch):
    from skuld import broker as bmod
    from skuld import broker_api as api_mod

    target = tmp_path / "presented" / "session-1"
    monkeypatch.setattr(bmod.broker._settings, "presented_files_dir", str(target))
    monkeypatch.setattr(bmod.broker._settings, "home_dir", str(tmp_path / "missing-home"))
    staged = api_mod._presented_staging_dir()
    assert staged == target.resolve()
    assert (target.stat().st_mode & 0o777) == 0o700


def test_local_mode_accepts_files_from_the_real_home(tmp_path, monkeypatch, module_broker_runtime):
    """Local mode: home_dir is the HOME the agent runs with and staging is explicit."""
    from skuld import broker as bmod
    from skuld import broker_api as api_mod

    home = tmp_path / "home"
    (home / "notes").mkdir(parents=True)
    report = home / "notes" / "report.md"
    report.write_text("# from home")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    staging = tmp_path / "state" / "presented"
    monkeypatch.setattr(bmod.broker, "workspace_dir", str(workspace))
    monkeypatch.setattr(bmod.broker._settings, "home_dir", str(home))
    monkeypatch.setattr(bmod.broker._settings, "presented_files_dir", str(staging))
    monkeypatch.setattr(bmod.broker, "_enqueue_event_log", lambda frame: None)
    monkeypatch.setattr(bmod.broker, "_save_conversation_history", lambda: None)
    monkeypatch.setattr(bmod.broker, "_conversation_turns", [])

    async def _no_broadcast(frame):
        return None

    monkeypatch.setattr(bmod.broker._channels, "broadcast", _no_broadcast)
    api_mod._presented_registry.clear()
    client = TestClient(bmod.app, headers={"authorization": module_broker_runtime.authorization})

    response = client.post("/api/present-file", json={"path": str(report)})

    assert response.status_code == 200, response.text
    fid = response.json()["file_id"]
    assert (staging / fid / "content").read_text() == "# from home"
    assert client.get(f"/api/files/presented/{fid}").content == b"# from home"
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("x")
    refused = client.post("/api/present-file", json={"path": str(outside)})
    assert refused.status_code == 400
    # LEX-1515: the refusal names both roots and how to get a scratch file in.
    detail = refused.json()["detail"]
    assert "outside the session roots" in detail
    assert bmod.broker._settings.home_path in detail or "home (" in detail
    assert "~/tmp" in detail


def test_broker_serves_presented_files_inline_and_sandboxed(client, tmp_path):
    """Judge R16-1 at the source: the broker's own route (and so every proxy in front of it,
    /s/{id}/api/... included) sends the sandbox, nosniff and no caching, inline like the
    durable copy. A PDF keeps nosniff but no sandbox, which would stop Chrome showing it."""
    html = tmp_path / "page.html"
    html.write_text("<script>alert(1)</script>")
    fid = client.post("/api/present-file", json={"path": str(html)}).json()["file_id"]
    r = client.get(f"/api/files/presented/{fid}")
    assert r.headers["content-security-policy"] == "sandbox"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "private, no-store"
    assert r.headers["content-disposition"].startswith("inline")
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    fid = client.post("/api/present-file", json={"path": str(pdf)}).json()["file_id"]
    r = client.get(f"/api/files/presented/{fid}")
    assert "content-security-policy" not in r.headers
    assert r.headers["x-content-type-options"] == "nosniff"


def test_the_session_prompt_states_where_present_file_reads_from():
    """LEX-1515: the prompt every session gets must not promise /tmp."""
    from skuld.transports.session_tools import capability_instructions

    text = capability_instructions(forge_mcp_server=None)
    assert "workspace or your home directory" in text
    assert "~/tmp/" in text
    assert "ANY path" not in text


def test_keyed_presents_are_numbered_versions_of_one_artifact(client, tmp_path):
    from skuld import broker as bmod

    src = tmp_path / "plan.md"
    src.write_text("# Plan v1")
    first = client.post("/api/present-file", json={"path": str(src), "artifact_key": "plan"})
    src.write_text("# Plan v2")
    second = client.post("/api/present-file", json={"path": str(src), "artifact_key": "plan"})
    other = client.post("/api/present-file", json={"path": str(src), "artifact_key": "spec"})
    plain = client.post("/api/present-file", json={"path": str(src)})
    assert {r.status_code for r in (first, second, other, plain)} == {200}

    one, two = first.json(), second.json()
    provider_key = f"{bmod.broker.session_id}:plan"
    assert (one["provider_key"], one["revision_id"], one["version_label"]) == (
        provider_key,
        one["file_id"],
        "v1",
    )
    assert (two["provider_key"], two["version_label"]) == (provider_key, "v2")
    assert other.json()["version_label"] == "v1"
    assert "provider_key" not in plain.json() and "version_label" not in plain.json()

    parts = [frame["turn"]["parts"][0]["input"] for frame in client.logged]
    assert [(p.get("artifact_key"), p.get("version_label")) for p in parts] == [
        ("plan", "v1"),
        ("plan", "v2"),
        ("spec", "v1"),
        (None, None),
    ]


def test_malformed_artifact_key_is_refused(client, tmp_path):
    src = tmp_path / "plan.md"
    src.write_text("x")
    response = client.post("/api/present-file", json={"path": str(src), "artifact_key": "../p"})
    assert response.status_code == 400
    assert "artifact_key" in response.json()["detail"]
    assert client.logged == []


async def test_a_flock_member_presents_a_workspace_file_over_the_mesh(client, tmp_path):
    from skuld import broker_api as api_mod

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "plan.md").write_text("# Plan")
    reply = await api_mod.present_peer_file(
        {"type": "present_file", "path": "docs/plan.md", "artifact_key": "plan", "title": "Plan"}
    )
    assert reply["status"] == "complete"
    assert (reply["name"], reply["version_label"]) == ("Plan", "v1")
    assert client.logged[0]["turn"]["parts"][0]["input"]["artifact_key"] == "plan"


async def test_a_flock_member_cannot_present_outside_the_workspace(client, tmp_path, monkeypatch):
    from skuld import broker as bmod
    from skuld import broker_api as api_mod

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(bmod.broker, "workspace_dir", str(workspace))
    (tmp_path / "secret.txt").write_text("token")

    absolute = await api_mod.present_peer_file({"path": str(tmp_path / "secret.txt")})
    escaped = await api_mod.present_peer_file({"path": "../secret.txt"})
    missing = await api_mod.present_peer_file({})
    assert [r["status"] for r in (absolute, escaped, missing)] == ["error"] * 3
    assert "relative" in absolute["error"] and "outside the session workspace" in escaped["error"]
    assert escaped["http_status"] == 400
    assert client.logged == []
