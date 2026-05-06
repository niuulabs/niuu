"""Tests for the chronicle watcher (JSONL file tailing + API reporting)."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from skuld.chronicle_watcher import (
    ChronicleWatcher,
    _load_state,
    _save_state,
)

# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


class TestStatePersistence:
    def test_save_and_load(self, tmp_path):
        path = tmp_path / "state.json"
        state = {"file.jsonl": {"offset": 100, "last_uuid": "abc"}}
        _save_state(path, state)
        loaded = _load_state(path)
        assert loaded == state

    def test_load_missing_file(self, tmp_path):
        path = tmp_path / "nonexistent.json"
        assert _load_state(path) == {}

    def test_load_corrupt_file(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json", encoding="utf-8")
        assert _load_state(path) == {}


# ---------------------------------------------------------------------------
# ChronicleWatcher construction
# ---------------------------------------------------------------------------


class TestChronicleWatcherInit:
    def test_default_construction(self, tmp_path):
        watcher = ChronicleWatcher(
            session_id="test-session",
            watch_dir=tmp_path,
            api_base_url="http://localhost:8081",
            http_headers={"x-auth-user-id": "test"},
        )
        assert watcher._session_id == "test-session"
        assert watcher._watch_dir == tmp_path
        assert watcher._debounce_s == 0.5

    def test_custom_debounce(self, tmp_path):
        watcher = ChronicleWatcher(
            session_id="test",
            watch_dir=tmp_path,
            api_base_url="http://localhost:8081",
            http_headers={},
            debounce_ms=1000,
        )
        assert watcher._debounce_s == 1.0


# ---------------------------------------------------------------------------
# File tailing
# ---------------------------------------------------------------------------


class TestFileTailing:
    @pytest.mark.asyncio
    async def test_tail_processes_jsonl_events(self, tmp_path):
        """Write JSONL lines to a file and verify the watcher reports events."""
        jsonl_file = tmp_path / "session.jsonl"

        # Write a tool_use event
        line = {
            "type": "assistant",
            "timestamp": "2026-03-15T02:42:36.450Z",
            "uuid": "evt-1",
            "message": {
                "stop_reason": None,
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Write",
                        "input": {"file_path": "/tmp/new.py"},
                    }
                ],
                "usage": {},
            },
        }
        jsonl_file.write_text(json.dumps(line) + "\n", encoding="utf-8")

        reported: list[dict] = []

        watcher = ChronicleWatcher(
            session_id="test-session",
            watch_dir=tmp_path,
            api_base_url="http://localhost:8081",
            http_headers={"x-auth-user-id": "test"},
            debounce_ms=50,
        )

        # Mock HTTP client
        mock_response = MagicMock()
        mock_response.status_code = 201

        async def mock_post(url, json=None):
            reported.append(json)
            return mock_response

        mock_client = AsyncMock()
        mock_client.post = mock_post
        watcher._http_client = mock_client

        # Run the tail task briefly
        task = asyncio.create_task(watcher._tail_file(jsonl_file))
        await asyncio.sleep(0.2)
        watcher._shutting_down = True
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass  # Expected: task cancelled during test teardown

        assert len(reported) >= 1
        assert reported[0]["type"] == "file"
        assert reported[0]["label"] == "/tmp/new.py"
        assert reported[0]["action"] == "created"

    @pytest.mark.asyncio
    async def test_tail_resumes_from_offset(self, tmp_path):
        """Verify the watcher skips already-processed lines."""
        jsonl_file = tmp_path / "session.jsonl"

        line1 = (
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-03-15T02:42:36.450Z",
                    "uuid": "old",
                    "message": {
                        "stop_reason": None,
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Write",
                                "input": {"file_path": "/tmp/old.py"},
                            },
                        ],
                        "usage": {},
                    },
                }
            )
            + "\n"
        )

        line2 = (
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-03-15T02:42:40.000Z",
                    "uuid": "new",
                    "message": {
                        "stop_reason": None,
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Write",
                                "input": {"file_path": "/tmp/new.py"},
                            },
                        ],
                        "usage": {},
                    },
                }
            )
            + "\n"
        )

        jsonl_file.write_text(line1 + line2, encoding="utf-8")

        reported: list[dict] = []

        watcher = ChronicleWatcher(
            session_id="test",
            watch_dir=tmp_path,
            api_base_url="http://localhost:8081",
            http_headers={},
            debounce_ms=50,
        )

        # Pre-set state: already processed line1
        offset = len(line1.encode("utf-8"))
        watcher._state = {"session.jsonl": {"offset": offset}}

        mock_response = MagicMock()
        mock_response.status_code = 201

        async def mock_post(url, json=None):
            reported.append(json)
            return mock_response

        mock_client = AsyncMock()
        mock_client.post = mock_post
        watcher._http_client = mock_client

        task = asyncio.create_task(watcher._tail_file(jsonl_file))
        await asyncio.sleep(0.2)
        watcher._shutting_down = True
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass  # Expected: task cancelled during test teardown

        # Should only see new.py, not old.py
        labels = [e["label"] for e in reported]
        assert "/tmp/new.py" in labels
        assert "/tmp/old.py" not in labels

    @pytest.mark.asyncio
    async def test_state_persisted_after_tail(self, tmp_path):
        """Verify state file is written with offset after tailing."""
        jsonl_file = tmp_path / "session.jsonl"
        line = (
            json.dumps(
                {
                    "type": "user",
                    "timestamp": "2026-03-15T02:42:36.450Z",
                    "uuid": "u1",
                    "message": {"content": "hello"},
                }
            )
            + "\n"
        )
        jsonl_file.write_text(line, encoding="utf-8")

        watcher = ChronicleWatcher(
            session_id="test",
            watch_dir=tmp_path,
            api_base_url="http://localhost:8081",
            http_headers={},
            debounce_ms=50,
        )

        task = asyncio.create_task(watcher._tail_file(jsonl_file))
        await asyncio.sleep(0.2)
        watcher._shutting_down = True
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass  # Expected: task cancelled during test teardown

        state = _load_state(tmp_path / ".chronicle-watcher-state.json")
        assert "session.jsonl" in state
        assert state["session.jsonl"]["offset"] > 0


# ---------------------------------------------------------------------------
# ensure_tail
# ---------------------------------------------------------------------------


class TestEnsureTail:
    def test_skips_duplicate_tasks(self, tmp_path):
        watcher = ChronicleWatcher(
            session_id="test",
            watch_dir=tmp_path,
            api_base_url="http://localhost:8081",
            http_headers={},
        )
        # Simulate a running task
        fake_task = MagicMock()
        fake_task.done.return_value = False
        watcher._tail_tasks["test.jsonl"] = fake_task

        path = tmp_path / "test.jsonl"
        watcher._ensure_tail(path)

        # Should NOT have replaced the task
        assert watcher._tail_tasks["test.jsonl"] is fake_task


# ---------------------------------------------------------------------------
# Batch reporting
# ---------------------------------------------------------------------------


class TestReportBatch:
    @pytest.mark.asyncio
    async def test_empty_batch_is_noop(self, tmp_path):
        watcher = ChronicleWatcher(
            session_id="test",
            watch_dir=tmp_path,
            api_base_url="http://localhost:8081",
            http_headers={},
        )
        # Should not raise
        await watcher._report_batch([])

    @pytest.mark.asyncio
    async def test_reports_each_event(self, tmp_path):
        watcher = ChronicleWatcher(
            session_id="test-session",
            watch_dir=tmp_path,
            api_base_url="http://localhost:8081",
            http_headers={"x-auth-user-id": "test"},
        )

        posted: list[dict] = []
        mock_response = MagicMock()
        mock_response.status_code = 201

        async def mock_post(url, json=None):
            posted.append({"url": url, "json": json})
            return mock_response

        mock_client = AsyncMock()
        mock_client.post = mock_post
        watcher._http_client = mock_client

        events = [
            {"t": 0, "type": "file", "label": "a.py", "action": "created"},
            {"t": 5, "type": "terminal", "label": "ls", "exit": 0},
        ]
        await watcher._report_batch(events)

        assert len(posted) == 2
        assert posted[0]["url"] == "/api/v1/forge/chronicles/test-session/timeline"
        assert posted[0]["json"]["type"] == "file"
        assert posted[1]["json"]["type"] == "terminal"

    @pytest.mark.asyncio
    async def test_handles_api_failure_gracefully(self, tmp_path):
        watcher = ChronicleWatcher(
            session_id="test",
            watch_dir=tmp_path,
            api_base_url="http://localhost:8081",
            http_headers={},
        )

        async def mock_post(url, json=None):
            raise ConnectionError("refused")

        mock_client = AsyncMock()
        mock_client.post = mock_post
        watcher._http_client = mock_client

        # Should not raise
        await watcher._report_batch([{"t": 0, "type": "file", "label": "x.py"}])

    @pytest.mark.asyncio
    async def test_report_logs_non_success_status(self, tmp_path):
        watcher = ChronicleWatcher(
            session_id="test",
            watch_dir=tmp_path,
            api_base_url="http://localhost:8081",
            http_headers={},
        )

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        async def mock_post(url, json=None):
            return mock_response

        mock_client = AsyncMock()
        mock_client.post = mock_post
        watcher._http_client = mock_client

        # Should not raise even on 500
        await watcher._report_batch([{"t": 0, "type": "error", "label": "oops"}])


# ---------------------------------------------------------------------------
# Lifecycle (start / stop)
# ---------------------------------------------------------------------------


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_picks_up_existing_files(self, tmp_path):
        """start() should begin tailing any .jsonl files already present."""
        jsonl_file = tmp_path / "existing.jsonl"
        line = json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-03-15T02:42:36.450Z",
                "uuid": "e1",
                "message": {
                    "stop_reason": None,
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Edit",
                            "input": {"file_path": "/tmp/x.py"},
                        }
                    ],
                    "usage": {},
                },
            }
        )
        jsonl_file.write_text(line + "\n", encoding="utf-8")

        reported: list[dict] = []
        mock_response = MagicMock()
        mock_response.status_code = 201

        async def mock_post(url, json=None):
            reported.append(json)
            return mock_response

        watcher = ChronicleWatcher(
            session_id="test",
            watch_dir=tmp_path,
            api_base_url="http://localhost:8081",
            http_headers={},
            debounce_ms=50,
        )

        mock_client = AsyncMock()
        mock_client.post = mock_post
        mock_client.aclose = AsyncMock()
        watcher._http_client = mock_client

        await watcher.start()
        await asyncio.sleep(0.5)
        await watcher.stop()

        assert len(reported) >= 1
        assert reported[0]["type"] == "file"

    @pytest.mark.asyncio
    async def test_stop_without_start(self, tmp_path):
        """stop() should be safe to call even if start() was never called."""
        watcher = ChronicleWatcher(
            session_id="test",
            watch_dir=tmp_path,
            api_base_url="http://localhost:8081",
            http_headers={},
        )
        await watcher.stop()  # Should not raise

    @pytest.mark.asyncio
    async def test_stop_cancels_tail_tasks(self, tmp_path):
        """stop() cancels all running tail tasks."""
        watcher = ChronicleWatcher(
            session_id="test",
            watch_dir=tmp_path,
            api_base_url="http://localhost:8081",
            http_headers={},
        )

        # Create a long-running dummy task
        async def forever():
            await asyncio.sleep(999)

        watcher._tail_tasks["dummy.jsonl"] = asyncio.create_task(forever())
        await watcher.stop()
        assert len(watcher._tail_tasks) == 0

    @pytest.mark.asyncio
    async def test_stop_closes_http_client(self, tmp_path):
        watcher = ChronicleWatcher(
            session_id="test",
            watch_dir=tmp_path,
            api_base_url="http://localhost:8081",
            http_headers={},
        )
        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()
        watcher._http_client = mock_client

        await watcher.stop()
        mock_client.aclose.assert_awaited_once()
        assert watcher._http_client is None


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


class TestGetHttpClient:
    @pytest.mark.asyncio
    async def test_lazy_creates_client(self, tmp_path):
        watcher = ChronicleWatcher(
            session_id="test",
            watch_dir=tmp_path,
            api_base_url="http://localhost:9999",
            http_headers={"x-auth-user-id": "svc"},
        )
        assert watcher._http_client is None
        client = await watcher._get_http_client()
        assert client is not None
        assert watcher._http_client is client
        # Second call returns same instance
        assert await watcher._get_http_client() is client
        await client.aclose()

    @pytest.mark.asyncio
    async def test_client_has_correct_headers(self, tmp_path):
        watcher = ChronicleWatcher(
            session_id="test",
            watch_dir=tmp_path,
            api_base_url="http://localhost:9999",
            http_headers={"x-auth-user-id": "watcher", "x-auth-roles": "volundr:service"},
        )
        client = await watcher._get_http_client()
        assert client.headers["x-auth-user-id"] == "watcher"
        assert client.headers["x-auth-roles"] == "volundr:service"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_update_headers_replaces_headers(self, tmp_path):
        watcher = ChronicleWatcher(
            session_id="test",
            watch_dir=tmp_path,
            api_base_url="http://localhost:9999",
            http_headers={"x-auth-user-id": "old"},
        )
        # Warm up the client
        client_before = await watcher._get_http_client()
        assert client_before.headers["x-auth-user-id"] == "old"

        # Update headers (simulates JWT arrival)
        watcher.update_headers({"Authorization": "Bearer new-token"})
        assert watcher._http_client is None  # Old client cleared

        # New client picks up new headers
        client_after = await watcher._get_http_client()
        assert client_after.headers["authorization"] == "Bearer new-token"
        assert client_after is not client_before

        await client_after.aclose()

    @pytest.mark.asyncio
    async def test_update_headers_without_existing_client(self, tmp_path):
        watcher = ChronicleWatcher(
            session_id="test",
            watch_dir=tmp_path,
            api_base_url="http://localhost:9999",
            http_headers={"x-auth-user-id": "old"},
        )
        # No client created yet — should not raise
        watcher.update_headers({"Authorization": "Bearer tok"})

        client = await watcher._get_http_client()
        assert client.headers["authorization"] == "Bearer tok"
        await client.aclose()


# ---------------------------------------------------------------------------
# Polling fallback
# ---------------------------------------------------------------------------


class TestPollDirectory:
    @pytest.mark.asyncio
    async def test_poll_detects_new_file(self, tmp_path):
        watcher = ChronicleWatcher(
            session_id="test",
            watch_dir=tmp_path,
            api_base_url="http://localhost:8081",
            http_headers={},
            debounce_ms=50,
        )

        reported: list[dict] = []
        mock_response = MagicMock()
        mock_response.status_code = 201

        async def mock_post(url, json=None):
            reported.append(json)
            return mock_response

        mock_client = AsyncMock()
        mock_client.post = mock_post
        mock_client.aclose = AsyncMock()
        watcher._http_client = mock_client

        # Start polling
        poll_task = asyncio.create_task(watcher._poll_directory())

        # Wait for first poll cycle, then create file
        await asyncio.sleep(0.5)
        jsonl = tmp_path / "new.jsonl"
        line = json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-03-15T03:00:00.000Z",
                "uuid": "p1",
                "message": {
                    "stop_reason": None,
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Write",
                            "input": {"file_path": "/tmp/polled.py"},
                        }
                    ],
                    "usage": {},
                },
            }
        )
        jsonl.write_text(line + "\n", encoding="utf-8")

        # Wait for poll to pick it up + tail to process
        await asyncio.sleep(3.5)
        watcher._shutting_down = True
        poll_task.cancel()
        try:
            await poll_task
        except asyncio.CancelledError:
            pass  # Expected: task cancelled during test teardown

        # Cancel tail tasks too
        for t in watcher._tail_tasks.values():
            t.cancel()
        await asyncio.gather(*watcher._tail_tasks.values(), return_exceptions=True)

        labels = [e.get("label") for e in reported]
        assert "/tmp/polled.py" in labels

    @pytest.mark.asyncio
    async def test_poll_handles_missing_dir(self, tmp_path):
        """Poll should not crash if watch_dir doesn't exist yet."""
        missing = tmp_path / "nonexistent"
        watcher = ChronicleWatcher(
            session_id="test",
            watch_dir=missing,
            api_base_url="http://localhost:8081",
            http_headers={},
        )

        poll_task = asyncio.create_task(watcher._poll_directory())
        await asyncio.sleep(0.5)
        watcher._shutting_down = True
        poll_task.cancel()
        try:
            await poll_task
        except asyncio.CancelledError:
            pass  # Expected: task cancelled during test teardown


# ---------------------------------------------------------------------------
# Tail edge cases
# ---------------------------------------------------------------------------


class TestTailEdgeCases:
    @pytest.mark.asyncio
    async def test_tail_handles_deleted_file(self, tmp_path):
        """Tail should exit gracefully if the file disappears."""
        jsonl_file = tmp_path / "vanishing.jsonl"
        jsonl_file.write_text(
            json.dumps({"type": "user", "uuid": "x", "timestamp": "2026-03-15T00:00:00Z"}) + "\n",
            encoding="utf-8",
        )

        watcher = ChronicleWatcher(
            session_id="test",
            watch_dir=tmp_path,
            api_base_url="http://localhost:8081",
            http_headers={},
            debounce_ms=50,
        )

        task = asyncio.create_task(watcher._tail_file(jsonl_file))
        await asyncio.sleep(0.2)
        # Delete the file mid-tail
        jsonl_file.unlink()
        await asyncio.sleep(1.0)
        # Task should exit on its own (FileNotFoundError breaks loop)
        assert task.done()

    @pytest.mark.asyncio
    async def test_tail_skips_malformed_json(self, tmp_path):
        """Bad JSON lines should be skipped without crashing."""
        jsonl_file = tmp_path / "bad.jsonl"
        lines = [
            "not valid json\n",
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-03-15T00:00:00Z",
                    "uuid": "ok",
                    "message": {
                        "stop_reason": None,
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Write",
                                "input": {"file_path": "/tmp/good.py"},
                            }
                        ],
                        "usage": {},
                    },
                }
            )
            + "\n",
            "{truncated\n",
        ]
        jsonl_file.write_text("".join(lines), encoding="utf-8")

        reported: list[dict] = []
        mock_response = MagicMock()
        mock_response.status_code = 201

        async def mock_post(url, json=None):
            reported.append(json)
            return mock_response

        watcher = ChronicleWatcher(
            session_id="test",
            watch_dir=tmp_path,
            api_base_url="http://localhost:8081",
            http_headers={},
            debounce_ms=50,
        )
        mock_client = AsyncMock()
        mock_client.post = mock_post
        watcher._http_client = mock_client

        task = asyncio.create_task(watcher._tail_file(jsonl_file))
        await asyncio.sleep(0.3)
        watcher._shutting_down = True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass  # Expected: task cancelled during test teardown

        # Should have processed the valid line
        assert len(reported) >= 1
        assert reported[0]["label"] == "/tmp/good.py"

    @pytest.mark.asyncio
    async def test_tail_tracks_uuid(self, tmp_path):
        """UUID from events should be stored in state."""
        jsonl_file = tmp_path / "uuid_track.jsonl"
        line = json.dumps(
            {
                "type": "user",
                "timestamp": "2026-03-15T00:00:00Z",
                "uuid": "unique-id-123",
                "message": {"content": "hi"},
            }
        )
        jsonl_file.write_text(line + "\n", encoding="utf-8")

        watcher = ChronicleWatcher(
            session_id="test",
            watch_dir=tmp_path,
            api_base_url="http://localhost:8081",
            http_headers={},
            debounce_ms=50,
        )

        task = asyncio.create_task(watcher._tail_file(jsonl_file))
        await asyncio.sleep(0.3)
        watcher._shutting_down = True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass  # Expected: task cancelled during test teardown

        assert watcher._state.get("uuid_track.jsonl", {}).get("last_uuid") == "unique-id-123"

    @pytest.mark.asyncio
    async def test_ensure_tail_replaces_done_task(self, tmp_path):
        """If a previous tail task finished, ensure_tail should start a new one."""
        watcher = ChronicleWatcher(
            session_id="test",
            watch_dir=tmp_path,
            api_base_url="http://localhost:8081",
            http_headers={},
        )

        # Simulate a completed task
        done_task = MagicMock()
        done_task.done.return_value = True
        watcher._tail_tasks["session.jsonl"] = done_task

        jsonl = tmp_path / "session.jsonl"
        jsonl.write_text("{}\n", encoding="utf-8")

        watcher._ensure_tail(jsonl)
        new_task = watcher._tail_tasks["session.jsonl"]
        assert new_task is not done_task
        new_task.cancel()
        try:
            await new_task
        except asyncio.CancelledError:
            pass  # Expected: task cancelled during test teardown
