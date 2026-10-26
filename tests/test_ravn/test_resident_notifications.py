"""A resident's notification outbox, its `notify` tool and the shared entry contract."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from niuu.domain.notifications import NotificationDraft, NotificationKind
from niuu.domain.resident_notifications import (
    MAX_RESOLVED_REQUESTS,
    AttentionInputKind,
    ResidentNotificationEntry,
    ResidentNotificationOp,
)
from ravn.adapters.resident_notifications import SqliteResidentNotificationOutbox
from ravn.adapters.tools.notify import NOTIFY_TOOL_NAME, ResidentNotifyTool
from ravn.domain.notification_channel import current_notification_channel

NOW = datetime(2026, 10, 16, tzinfo=UTC)


def _outbox(tmp_path, *, max_pending: int = 100) -> SqliteResidentNotificationOutbox:
    return SqliteResidentNotificationOutbox(tmp_path / "state.db", max_pending=max_pending)


class TestEntryContract:
    def test_attention_needs_its_request_and_kind(self):
        with pytest.raises(ValidationError, match="request_id and input_kind"):
            ResidentNotificationEntry(seq=1, op="attention", request_id="q1", created_at=NOW)

    def test_resolved_names_the_requests_it_settles(self):
        with pytest.raises(ValidationError, match="names the request ids"):
            ResidentNotificationEntry(seq=1, op="resolved", created_at=NOW)
        with pytest.raises(ValidationError, match="at most"):
            ResidentNotificationEntry(
                seq=1, op="resolved", resolved_request_ids=["x" * 201], created_at=NOW
            )

    def test_notify_needs_a_draft_and_a_key(self):
        with pytest.raises(ValidationError, match="draft and an idempotency_key"):
            ResidentNotificationEntry(seq=1, op="notify", idempotency_key="k", created_at=NOW)

    def test_round_trips_as_json(self):
        entry = ResidentNotificationEntry(
            seq=3,
            op="notify",
            channel="agent:main:niuu-1",
            draft=NotificationDraft(kind=NotificationKind.INFO, title="FYI"),
            idempotency_key="k",
            created_at=NOW,
        )
        assert ResidentNotificationEntry.model_validate(entry.model_dump(mode="json")) == entry


class TestOutbox:
    async def test_a_question_is_raised_once_and_its_answer_settles_it(self, tmp_path):
        outbox = _outbox(tmp_path)
        assert await outbox.attention(
            request_id="q1", group="case-1", input_kind=AttentionInputKind.QUESTION, prompt="A?"
        )
        # Re-published after a restart: the same request is not raised again.
        assert not await outbox.attention(
            request_id="q1", group="case-1", input_kind=AttentionInputKind.QUESTION, prompt="A?"
        )
        await outbox.attention(
            request_id="q2",
            group="case-1",
            input_kind=AttentionInputKind.PERMISSION,
            prompt="B?",
            channel="agent:main:niuu-x",
        )
        await outbox.attention(
            request_id="q3", group="case-2", input_kind=AttentionInputKind.QUESTION, prompt="C?"
        )

        assert await outbox.resolve(group="case-1") == 2
        assert await outbox.resolve(group="case-1") == 0, "settling twice settles nothing"
        # A settled request stays settled.
        assert not await outbox.attention(
            request_id="q1", group="case-1", input_kind=AttentionInputKind.QUESTION, prompt="A?"
        )

        entries = await outbox.pending(limit=10)
        assert [(e.op, e.channel, e.request_id or e.resolved_request_ids) for e in entries] == [
            (ResidentNotificationOp.ATTENTION, None, "q1"),
            (ResidentNotificationOp.ATTENTION, "agent:main:niuu-x", "q2"),
            (ResidentNotificationOp.ATTENTION, None, "q3"),
            (ResidentNotificationOp.RESOLVED, None, ["q1"]),
            (ResidentNotificationOp.RESOLVED, "agent:main:niuu-x", ["q2"]),
        ]
        assert [e.seq for e in entries] == sorted(e.seq for e in entries)
        assert entries[1].input_kind is AttentionInputKind.PERMISSION

    async def test_acknowledged_entries_are_dropped_and_it_survives_a_restart(self, tmp_path):
        outbox = _outbox(tmp_path)
        draft = NotificationDraft(kind=NotificationKind.MILESTONE, title="Done")
        first = await outbox.notify(draft=draft, idempotency_key="k1")
        second = await outbox.notify(draft=draft, idempotency_key="k2", channel="c")
        assert await outbox.acknowledge(through_seq=first.seq) == 1
        outbox.close()

        reopened = _outbox(tmp_path)
        [left] = await reopened.pending(limit=10)
        assert left == second
        assert left.draft == draft and left.channel == "c"

    async def test_a_long_unread_outbox_keeps_only_the_newest(self, tmp_path, caplog):
        outbox = _outbox(tmp_path, max_pending=2)
        draft = NotificationDraft(kind=NotificationKind.INFO, title="n")
        for key in ("a", "b", "c"):
            await outbox.notify(draft=draft, idempotency_key=key)
        with caplog.at_level(logging.WARNING):
            await outbox.notify(draft=draft, idempotency_key="d")
        assert [e.idempotency_key for e in await outbox.pending(limit=10)] == ["c", "d"]
        assert "dropped the 1 oldest" in caplog.text

    async def test_many_open_requests_are_settled_in_bounded_entries(self, tmp_path):
        outbox = _outbox(tmp_path, max_pending=1000)
        for index in range(MAX_RESOLVED_REQUESTS + 1):
            await outbox.attention(
                request_id=f"q{index}",
                group="case",
                input_kind=AttentionInputKind.QUESTION,
                prompt="",
            )
        assert await outbox.resolve(group="case") == MAX_RESOLVED_REQUESTS + 1
        resolved = [
            e for e in await outbox.pending(limit=1000) if e.op is ResidentNotificationOp.RESOLVED
        ]
        assert [len(e.resolved_request_ids) for e in resolved] == [MAX_RESOLVED_REQUESTS, 1]

    async def test_listeners_hear_about_each_new_entry(self, tmp_path):
        outbox = _outbox(tmp_path)
        calls: list[int] = []
        unsubscribe = outbox.subscribe(lambda: calls.append(1))
        await outbox.attention(
            request_id="q1", group="g", input_kind=AttentionInputKind.QUESTION, prompt=""
        )
        await outbox.attention(
            request_id="q1", group="g", input_kind=AttentionInputKind.QUESTION, prompt=""
        )
        unsubscribe()
        await outbox.resolve(group="g")
        assert calls == [1], "a duplicate adds nothing; an unsubscribed listener hears nothing"

    async def test_invalid_input_is_refused(self, tmp_path):
        outbox = _outbox(tmp_path)
        with pytest.raises(ValueError, match="request_id and a group"):
            await outbox.attention(
                request_id="", group="g", input_kind=AttentionInputKind.QUESTION, prompt=""
            )
        with pytest.raises(ValueError, match="group"):
            await outbox.resolve(group="")
        with pytest.raises(ValueError, match="limit"):
            await outbox.pending(limit=0)
        with pytest.raises(ValueError, match="max_pending"):
            SqliteResidentNotificationOutbox(tmp_path / "x.db", max_pending=0)
        with pytest.raises(ValidationError):
            # A malformed entry is never stored where it would stop Forge's drain.
            await outbox.attention(
                request_id="x" * 201, group="g", input_kind=AttentionInputKind.QUESTION, prompt=""
            )
        assert await outbox.pending(limit=10) == []


class TestNotifyTool:
    async def test_queues_the_notification_for_the_turns_channel(self, tmp_path):
        outbox = _outbox(tmp_path)
        tool = ResidentNotifyTool(outbox)
        token = current_notification_channel.set("agent:main:niuu-abc")
        try:
            result = await tool.execute(
                {
                    "kind": "milestone",
                    "severity": "success",
                    "title": "Report ready",
                    "links": [{"label": "PR", "url": "https://example.com/pr/1"}],
                    "idempotency_key": "k1",
                }
            )
        finally:
            current_notification_channel.reset(token)

        assert not result.is_error
        assert json.loads(result.content) == {"state": "queued", "idempotency_key": "k1", "seq": 1}
        [entry] = await outbox.pending(limit=10)
        assert entry.op is ResidentNotificationOp.NOTIFY
        assert entry.channel == "agent:main:niuu-abc"
        assert entry.draft.title == "Report ready" and entry.draft.links[0].label == "PR"

    async def test_a_turn_outside_a_channel_notifies_from_the_main_room(self, tmp_path):
        outbox = _outbox(tmp_path)
        await ResidentNotifyTool(outbox).execute({"kind": "info", "title": "FYI"})
        [entry] = await outbox.pending(limit=10)
        assert entry.channel is None and entry.idempotency_key

    async def test_the_channel_follows_into_the_turns_own_task(self, tmp_path):
        outbox = _outbox(tmp_path)
        tool = ResidentNotifyTool(outbox)

        async def turn() -> None:
            await tool.execute({"kind": "info", "title": "from the turn"})

        async def drive() -> None:
            current_notification_channel.set("agent:main:niuu-xyz")
            await asyncio.create_task(turn())

        await asyncio.create_task(drive())
        [entry] = await outbox.pending(limit=10)
        assert entry.channel == "agent:main:niuu-xyz"

    @pytest.mark.parametrize(
        ("payload", "message"),
        [
            ({"kind": "reply_ready", "title": "Done"}, "derived by Forge"),
            ({"kind": "info", "title": " "}, "Invalid notification"),
            ({"kind": "info", "title": "x", "idempotency_key": "k" * 201}, "at most"),
        ],
    )
    async def test_refuses_what_forge_would_refuse(self, tmp_path, payload, message):
        outbox = _outbox(tmp_path)
        result = await ResidentNotifyTool(outbox).execute(payload)
        assert result.is_error and message in result.content
        assert await outbox.pending(limit=10) == []

    def test_describes_itself_like_the_forge_notify_tool(self, tmp_path):
        tool = ResidentNotifyTool(_outbox(tmp_path))
        assert tool.name == NOTIFY_TOOL_NAME == "notify"
        assert tool.required_permission == "notify:send"
        assert not tool.parallelisable
        assert "reply_ready" not in tool.input_schema["properties"]["kind"]["enum"]
        assert "Notify only when" in tool.description


class TestDaemonWiring:
    def _settings(self, tmp_path, **openclaw):
        from ravn.config import Settings

        return Settings.model_validate(
            {
                "gateway": {
                    "channels": {"openclaw": {"store_path": str(tmp_path / "state.db"), **openclaw}}
                }
            }
        )

    def test_the_outbox_follows_the_shim(self, tmp_path):
        from ravn.cli.resident_runtime_wiring import build_resident_notification_outbox

        assert build_resident_notification_outbox(self._settings(tmp_path)) is None
        off = self._settings(tmp_path, enabled=True, notifications_enabled=False)
        assert build_resident_notification_outbox(off) is None
        outbox = build_resident_notification_outbox(self._settings(tmp_path, enabled=True))
        assert isinstance(outbox, SqliteResidentNotificationOutbox)
        assert (tmp_path / "state.db").exists(), "it shares the shim's own database"

    def test_notify_is_offered_unless_disabled_or_forbidden(self, tmp_path):
        from types import SimpleNamespace

        from ravn.cli.daemon_runtime import _notify_tool_allowed

        settings = self._settings(tmp_path)
        assert _notify_tool_allowed(settings, None)
        assert _notify_tool_allowed(settings, SimpleNamespace(forbidden_tools=["bash"]))
        assert not _notify_tool_allowed(settings, SimpleNamespace(forbidden_tools=["notify"]))
        settings.tools.disabled = ["notify"]
        assert not _notify_tool_allowed(settings, None)


class TestCliAgentNotify:
    """A CLI-backed persona (Codex) reaches ``notify`` through its ravn-tools MCP server."""

    def _settings(self, tmp_path, **openclaw):
        from ravn.config import Settings

        return Settings.model_validate(
            {
                "gateway": {
                    "channels": {"openclaw": {"store_path": str(tmp_path / "state.db"), **openclaw}}
                }
            }
        )

    def test_the_server_gets_the_outbox_on_the_daemons_terms(self, tmp_path):
        from types import SimpleNamespace

        from ravn.cli.commands import _tool_mcp_notification_outbox

        assert _tool_mcp_notification_outbox(self._settings(tmp_path), None) is None
        on = self._settings(tmp_path, enabled=True)
        outbox = _tool_mcp_notification_outbox(on, None)
        assert isinstance(outbox, SqliteResidentNotificationOutbox)
        outbox.close()
        forbidden = SimpleNamespace(forbidden_tools=[NOTIFY_TOOL_NAME])
        assert _tool_mcp_notification_outbox(on, forbidden) is None

    def test_tool_mcp_serves_notify_in_the_turns_channel(self, tmp_path):
        from unittest.mock import MagicMock, patch

        from ravn.cli import commands as commands_mod
        from ravn.config import Settings

        outbox = MagicMock()
        served: dict = {}

        def server_for(tools, **_kwargs):
            served["tools"] = tools
            server = MagicMock()

            async def run_stdio() -> None:
                served["channel"] = current_notification_channel.get()

            server.run_stdio = run_stdio
            return server

        with (
            patch.object(commands_mod, "Settings", return_value=Settings()),
            patch.object(commands_mod, "_configure_logging"),
            patch.object(commands_mod.ProjectConfig, "discover", return_value=MagicMock()),
            patch.object(commands_mod, "_resolve_profile", return_value=None),
            patch.object(
                commands_mod, "_resolve_persona", return_value=MagicMock(allowed_tools=[])
            ),
            patch.object(commands_mod, "_build_tool_mcp_tools", return_value=[]),
            patch.object(commands_mod, "_tool_mcp_notification_outbox", return_value=outbox),
            patch.object(commands_mod, "_resolve_workspace", return_value=tmp_path),
            patch.object(commands_mod, "_attach_agent_build_tool"),
            patch("ravn.adapters.mcp.tool_port_server.ToolPortMcpServer", side_effect=server_for),
        ):
            commands_mod.tool_mcp(
                config="", persona="resident-codex", profile="", notification_channel="room-7"
            )

        assert [tool.name for tool in served["tools"]] == [NOTIFY_TOOL_NAME]
        assert served["channel"] == "room-7"
        assert current_notification_channel.get() is None, "the channel ends with the server"
        outbox.close.assert_called_once_with()

    def test_the_servers_notification_reaches_the_residents_outbox(self, tmp_path):
        # The server is its own process: it writes the file the daemon's shim serves.
        daemon = _outbox(tmp_path)
        server = _outbox(tmp_path)
        token = current_notification_channel.set("room-7")
        try:
            result = asyncio.run(
                ResidentNotifyTool(server).execute(
                    {"kind": "milestone", "severity": "success", "title": "Report published"}
                )
            )
        finally:
            current_notification_channel.reset(token)
            server.close()

        assert not result.is_error
        (entry,) = asyncio.run(daemon.pending(limit=10))
        assert entry.draft is not None and entry.draft.title == "Report published"
        assert entry.channel == "room-7"
        daemon.close()
