"""Forge MCP toolbox: tool list, grants, validation, bounds and Forge error mapping."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from niuu.domain.notifications import NotificationDraft
from niuu.domain.peer_message import (
    PeerAddress,
    PeerKind,
    PeerMessage,
    PeerMessageError,
    ReplyKind,
    ReplyRoute,
)
from niuu.domain.workflow_step import WorkflowStepCloseArgs, WorkflowStepCloseError
from niuu.forge_mcp import (
    TOOL_NAMES,
    ForgeApiError,
    ForgeClient,
    ForgeMcpGrant,
    ForgeMcpHost,
    ForgeMcpLimits,
    ForgeMcpNotifyMode,
    ForgeMcpToolbox,
    ToolResult,
    UnknownToolError,
)
from niuu.forge_mcp.tools import TRANSCRIPT_ONLY_TOOLS

SELF = str(uuid.uuid4())
PEER = str(uuid.uuid4())
LIMITS = ForgeMcpLimits(
    list_default_limit=2,
    list_max_limit=3,
    transcript_default_turns=2,
    transcript_max_turns=4,
    transcript_turn_max_chars=10,
    output_max_chars=4000,
)


def _session(session_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": session_id,
        "instance_id": "node-a",
        "instance_name": "Node A",
        "name": "peer",
        "status": "running",
        "model": "claude-opus-5",
        "chat_endpoint": "wss://secret-endpoint?access_token=leak",
        "source": {"type": "git", "repo": "github.com/acme/app", "branch": "main"},
        "coordination": {"project_id": "proj-1", "role": "builder"},
        **extra,
    }


class FakeClient(ForgeClient):
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.envelope_args: list[dict[str, Any]] = []
        self.fail: ForgeApiError | None = None
        self.sessions: list[dict[str, Any]] | None = None

    def _record(self, name: str, payload: Any) -> None:
        self.calls.append((name, payload))
        if self.fail is not None:
            raise self.fail

    async def list_notifications(self, params):
        self._record("list_notifications", params)
        items = [
            {"id": f"n{i}", "seq": i, "kind": "milestone", "title": f"t{i}", "body": "x" * 50}
            for i in range(5)
        ]
        return {"items": items, "next_before": 1, "head_seq": 9, "unread_count": 4}

    async def list_sessions(self, params):
        self._record("list_sessions", params)
        if self.sessions is not None:
            return self.sessions
        return [_session(str(uuid.uuid4())) for _ in range(5)]

    async def get_session(self, session_id, *, instance_id):
        self._record("get_session", (session_id, instance_id))
        return _session(session_id)

    async def get_conversation(self, session_id, *, turns, instance_id):
        self._record("get_conversation", (session_id, turns, instance_id))
        rows = [
            {
                "id": f"t{i}",
                "role": "assistant",
                "content": "a long answer " * 5,
                "parts": [{"type": "tool_use", "name": "Bash"}],
            }
            for i in range(6)
        ]
        rows.append({"id": "in-progress", "role": "assistant", "in_progress": True})
        return {"turns": rows, "total_turns": 40}

    async def send_message(
        self, session_id, *, content, request_id, instance_id, in_reply_to="", urgent=False
    ):
        self._record("send_message", (session_id, content, request_id, instance_id))
        self.envelope_args.append({"in_reply_to": in_reply_to, "urgent": urgent})
        return {"status": "delivered", "delivery": "delivered", "request_id": request_id}

    async def post_room_message(self, host_session_id, *, room_id, content):
        self._record("post_room_message", (host_session_id, room_id, content))
        return {"status": "sent", "message_id": "room-msg-1"}

    async def get_message_delivery(self, session_id, *, request_id, instance_id):
        self._record("get_message_delivery", (session_id, request_id, instance_id))
        return {"status": "delivered"}

    async def create_session(self, body):
        self._record("create_session", body)
        return _session(str(uuid.uuid4()), status="starting")

    async def start_session(self, session_id, *, instance_id):
        self._record("start_session", (session_id, instance_id))
        return _session(session_id, status="starting")

    async def stop_session(self, session_id, *, instance_id):
        self._record("stop_session", (session_id, instance_id))
        return _session(session_id, status="stopped")

    async def submit_notification(self, session_id, draft, *, idempotency_key, instance_id):
        self._record("submit_notification", (session_id, draft, idempotency_key, instance_id))
        return {"id": "nid-feed", "seq": 12, "session_id": session_id, "source": "agent"}

    async def create_secret_request(self, session_id, body):
        self._record("create_secret_request", (session_id, body))
        return {"id": "sr-1", "session_id": session_id, "status": "pending", **body}

    async def get_secret_request(self, session_id, request_id):
        self._record("get_secret_request", (session_id, request_id))
        return {"id": request_id, "session_id": session_id, "status": "pending"}

    async def claim_secret_request(self, session_id, request_id):
        self._record("claim_secret_request", (session_id, request_id))
        return {"name": "TOKEN", "value": "not-a-real-secret"}

    async def cancel_secret_request(self, session_id, request_id):
        self._record("cancel_secret_request", (session_id, request_id))
        return {"id": request_id, "session_id": session_id, "status": "cancelled"}

    async def update_description(self, session_id, changes):
        self._record("update_description", (session_id, changes))
        stamp = "2026-10-16T12:00:00+00:00"
        return _session(
            session_id,
            short_description=changes.get("short_description"),
            description=changes.get("description"),
            short_description_updated_at=stamp if "short_description" in changes else None,
            description_updated_at=stamp if "description" in changes else None,
        )


class FakeHost(ForgeMcpHost):
    def __init__(self) -> None:
        self.drafts: list[NotificationDraft] = []
        self.step_host = True
        self.step_open = False
        self.closes: list[WorkflowStepCloseArgs] = []
        self.close_error: WorkflowStepCloseError | None = None
        self.mesh = True
        self.mesh_sends: list[dict[str, Any]] = []
        self.mesh_publishes: list[dict[str, Any]] = []
        self.mesh_peer_queries: list[dict[str, str]] = []
        self.inbox: dict[str, PeerMessage] = {}
        self.mesh_peer_ids: dict[str, str] = {}
        self.mesh_down = False
        self.mesh_send_error: ForgeApiError | None = None
        self.ask_max_s = 50.0
        self.asks: list[dict[str, Any]] = []
        self.ask_result: dict[str, Any] = {"outcome": "answered", "answer": "yes", "from": "bob"}

    def ask_wait_max_s(self) -> float:
        return self.ask_max_s

    async def ask_for_answer(self, **kwargs):
        self.asks.append(kwargs)
        return self.ask_result

    def workflow_step_host(self) -> bool:
        return self.step_host

    def mesh_host(self) -> bool:
        return self.mesh

    async def mesh_peer_for(self, *, session_id, name):
        if self.mesh_down:
            raise ForgeApiError("this session's mesh is not running", status=503)
        return self.mesh_peer_ids.get(session_id or name)

    async def send_mesh_message(
        self, peer, content, *, expect_reply, request_id, in_reply_to, urgent=False
    ):
        self.mesh_sends.append(
            {
                "peer": peer,
                "content": content,
                "expect_reply": expect_reply,
                "request_id": request_id,
                "in_reply_to": in_reply_to,
                "urgent": urgent,
            }
        )
        if self.mesh_send_error is not None:
            raise self.mesh_send_error
        return {"message_id": "mesh-msg-1", "status": "accepted"}

    async def mesh_peers(self, *, role, capability, consumes, produces):
        self.mesh_peer_queries.append(
            {"role": role, "capability": capability, "consumes": consumes, "produces": produces}
        )
        return {"peers": [{"peer_id": "p-1", "role": "reviewer"}], "count": 1, "as_of": "now"}

    async def publish_mesh_event(self, topic, content, *, request_id, correlation_id, in_reply_to):
        self.mesh_publishes.append(
            {
                "topic": topic,
                "content": content,
                "request_id": request_id,
                "correlation_id": correlation_id,
                "in_reply_to": in_reply_to,
            }
        )
        return {
            "event_id": "evt-1",
            "topic": topic,
            "correlation_id": correlation_id or "evt-1",
            "status": "published",
        }

    async def received_peer_message(self, message_id: str) -> PeerMessage:
        if message_id not in self.inbox:
            raise PeerMessageError(f"this session received no message {message_id!r}")
        return self.inbox[message_id]

    async def close_workflow_step(self, args: WorkflowStepCloseArgs) -> dict[str, Any]:
        if not self.step_open:
            raise WorkflowStepCloseError("no workflow step is open on this session")
        if self.close_error is not None:
            raise self.close_error
        self.closes.append(args)
        return {"activation_id": args.activation_id, "state": "closed"}

    @property
    def session_id(self) -> str | None:
        return SELF

    async def environment(self) -> dict[str, Any]:
        return {"session": {"id": SELF}}

    async def notify(self, draft: NotificationDraft) -> dict[str, Any]:
        self.drafts.append(draft)
        return {"notification_id": "nid", "turn_id": "nt_x", "session_seq": 7, "state": "pending"}


def _toolbox(*grants: ForgeMcpGrant) -> tuple[ForgeMcpToolbox, FakeClient, FakeHost]:
    client, host = FakeClient(), FakeHost()
    return ForgeMcpToolbox(client=client, host=host, grants=grants, limits=LIMITS), client, host


class TestToolList:
    def test_every_tool_is_listed_with_a_schema(self) -> None:
        toolbox, _, _ = _toolbox()
        tools = toolbox.list_tools()
        assert [tool["name"] for tool in tools] == list(TOOL_NAMES)
        assert set(TOOL_NAMES) == {
            "environment",
            "notify",
            "report_artifact",
            "request_secret",
            "workflow_step_close",
            "describe_session",
            "list_notifications",
            "list_sessions",
            "get_session",
            "session_transcript",
            "send_message",
            "ask",
            "message_status",
            "reply",
            "room_post",
            "mesh_send",
            "mesh_publish",
            "mesh_peers",
            "create_session",
            "start_session",
            "stop_session",
        }
        for tool in tools:
            assert tool["inputSchema"]["type"] == "object"
            assert tool["inputSchema"]["additionalProperties"] is False

    def test_granted_tools_say_so_and_notify_teaches_when(self) -> None:
        toolbox, _, _ = _toolbox()
        tools = {tool["name"]: tool for tool in toolbox.list_tools()}
        assert "'message' grant" in tools["send_message"]["description"]
        assert "'lifecycle' grant" in tools["stop_session"]["description"]
        notify = tools["notify"]["description"]
        # Forge no longer notifies on turn end, so the tool must say the agent owns "done".
        assert "Forge sends nothing when a turn ends" in notify and "progress" in notify
        assert "reply ready" not in notify
        kinds = tools["notify"]["inputSchema"]["properties"]["kind"]["enum"]
        assert "reply_ready" not in kinds and "milestone" in kinds

    def test_notify_and_generated_skills_share_compact_event_guidance(self, tmp_path) -> None:
        from niuu.forge_mcp.guidance import (
            COMPACT_NOTIFICATION_GUIDANCE,
            NOTIFICATION_TRIGGER_GUIDANCE,
        )
        from niuu.forge_mcp.tools import _FEED_NOTIFY_DESCRIPTION, MAX_BODY_CHARS, MAX_TITLE_CHARS
        from skuld.forge_mcp.skill import (
            forge_notify_skill_markdown,
            materialize_claude_plugin,
            materialize_codex_skills_root,
        )

        toolbox, _, _ = _toolbox()
        notify = next(tool for tool in toolbox.list_tools() if tool["name"] == "notify")
        assert COMPACT_NOTIFICATION_GUIDANCE in notify["description"]
        assert COMPACT_NOTIFICATION_GUIDANCE in _FEED_NOTIFY_DESCRIPTION
        # One definition of *when* to notify, shared by every agent-facing surface.
        assert NOTIFICATION_TRIGGER_GUIDANCE in notify["description"]
        assert NOTIFICATION_TRIGGER_GUIDANCE in _FEED_NOTIFY_DESCRIPTION
        skill = forge_notify_skill_markdown("forge")
        assert COMPACT_NOTIFICATION_GUIDANCE in skill
        assert NOTIFICATION_TRIGGER_GUIDANCE in skill
        assert "reply ready" not in skill.lower()
        assert "`forge` MCP server" in skill
        claude = materialize_claude_plugin(tmp_path, "forge")
        codex = materialize_codex_skills_root(tmp_path, "forge")
        assert (claude / "skills/forge-notify/SKILL.md").read_text() == skill
        assert (codex / "forge-notify/SKILL.md").read_text() == skill
        properties = notify["inputSchema"]["properties"]
        assert "72 characters" in properties["title"]["description"]
        assert "160 characters" in properties["body"]["description"]
        # Guidance must not reject legitimate older records or alter the wire limits.
        assert properties["title"]["maxLength"] == MAX_TITLE_CHARS
        assert properties["body"]["maxLength"] == MAX_BODY_CHARS

    def test_schema_properties_match_argument_models(self) -> None:
        from niuu.forge_mcp.tools import TOOL_SPECS

        for spec in TOOL_SPECS:
            fields = set(spec.args_model.model_fields)
            assert set(spec.input_schema["properties"]) == fields, spec.name


class TestOwnSession:
    async def test_environment_adds_grants_and_available_tools(self) -> None:
        toolbox, _, _ = _toolbox(ForgeMcpGrant.MESSAGE)
        result = await toolbox.call("environment", {})
        assert not result.is_error
        assert result.structured["grants"] == ["message"]
        assert "send_message" in result.structured["tools"]
        assert "stop_session" not in result.structured["tools"]

    async def test_notify_passes_a_validated_draft(self) -> None:
        toolbox, _, host = _toolbox()
        result = await toolbox.call(
            "notify",
            {
                "kind": "milestone",
                "severity": "success",
                "title": "  PR   opened ",
                "links": [{"label": "PR", "url": "https://git/pr/1", "kind": "pr"}],
            },
        )
        assert not result.is_error
        assert result.structured["state"] == "pending"
        assert host.drafts[0].title == "PR opened"
        assert host.drafts[0].links[0].kind.value == "pr"

    @pytest.mark.parametrize(
        ("arguments", "message"),
        [
            ({"kind": "reply_ready", "title": "done"}, "reply_ready is reserved for Forge"),
            ({"kind": "milestone", "title": "   "}, "title"),
            ({"kind": "milestone"}, "title"),
            ({"kind": "milestone", "title": "x", "extra": 1}, "extra"),
            ({"kind": "milestone", "title": "x" * 201}, "title"),
        ],
    )
    async def test_notify_rejects_bad_drafts(self, arguments, message) -> None:
        toolbox, _, host = _toolbox()
        result = await toolbox.call("notify", arguments)
        assert result.is_error
        assert message in result.text
        assert host.drafts == []


class TestReads:
    async def test_list_notifications_encodes_filters_and_bounds(self) -> None:
        toolbox, client, _ = _toolbox()
        result = await toolbox.call(
            "list_notifications",
            {
                "limit": 99,
                "kinds": ["milestone", "attention"],
                "sources": ["agent"],
                "min_severity": "warning",
                "unread": True,
                "before": 5,
                "after": 1,
                "session_id": PEER,
                "project_id": "p",
            },
        )
        params = client.calls[0][1]
        assert params == {
            "limit": "3",
            "before": "5",
            "after": "1",
            "kind": "milestone,attention",
            "min_severity": "warning",
            "source": "agent",
            "session_id": PEER,
            "project_id": "p",
            "unread": "true",
            "unresolved": "true",
        }
        items = result.structured["items"]
        assert len(items) == 3
        assert items[0]["body"].endswith("…") and len(items[0]["body"]) == 10
        assert result.structured["unread_count"] == 4

    async def test_list_sessions_summarizes_and_drops_endpoints(self) -> None:
        toolbox, client, _ = _toolbox()
        result = await toolbox.call("list_sessions", {"status": "running"})
        assert client.calls[0][1] == {"scope": "guild", "status": "running"}
        assert result.structured["total"] == 5 and result.structured["returned"] == 2
        session = result.structured["sessions"][0]
        assert session["instance_id"] == "node-a"
        assert session["instance_name"] == "Node A"
        assert session["project_id"] == "proj-1"
        assert "chat_endpoint" not in session
        assert "access_token" not in json.dumps(result.structured)

    async def test_get_session_validates_the_id(self) -> None:
        toolbox, client, _ = _toolbox()
        bad = await toolbox.call("get_session", {"session_id": "../../admin"})
        assert bad.is_error and "session UUID" in bad.text
        ok = await toolbox.call("get_session", {"session_id": PEER, "instance_id": "node-b"})
        assert ok.structured["session"]["id"] == PEER
        assert client.calls == [("get_session", (PEER, "node-b"))]

    async def test_transcript_tail_is_bounded(self) -> None:
        toolbox, client, _ = _toolbox()
        result = await toolbox.call("session_transcript", {"session_id": PEER, "turns": 50})
        assert client.calls[0][1] == (PEER, 4, None)
        turns = result.structured["turns"]
        assert len(turns) == 3  # the in-progress sentinel is dropped from the 4-turn tail
        assert all(len(turn["content"]) <= 10 for turn in turns)
        assert turns[0]["tools"] == ["Bash"]


class TestGrants:
    @pytest.mark.parametrize(
        ("tool", "arguments", "grant"),
        [
            ("send_message", {"session_id": PEER, "content": "hi"}, "message"),
            ("message_status", {"session_id": PEER, "request_id": "r1"}, "message"),
            ("create_session", {"name": "x"}, "lifecycle"),
            ("start_session", {"session_id": PEER}, "lifecycle"),
            ("stop_session", {"session_id": PEER}, "lifecycle"),
        ],
    )
    async def test_missing_grant_is_an_explained_error(self, tool, arguments, grant) -> None:
        toolbox, client, _ = _toolbox()
        result = await toolbox.call(tool, arguments)
        assert result.is_error
        assert f"'{grant}' grant" in result.text and "forge_mcp.grants" in result.text
        assert client.calls == []

    async def test_send_message_generates_a_stable_request_id(self) -> None:
        toolbox, client, _ = _toolbox(ForgeMcpGrant.MESSAGE)
        result = await toolbox.call("send_message", {"session_id": PEER, "content": "rebase"})
        request_id = result.structured["request_id"]
        assert request_id.startswith("fmcp-")
        assert client.calls[0][1] == (PEER, "rebase", request_id, None)
        again = await toolbox.call(
            "send_message", {"session_id": PEER, "content": "rebase", "request_id": request_id}
        )
        assert again.structured["request_id"] == request_id

    async def test_send_message_and_stop_refuse_the_own_session(self) -> None:
        toolbox, client, _ = _toolbox(ForgeMcpGrant.MESSAGE, ForgeMcpGrant.LIFECYCLE)
        for tool, arguments in (
            ("send_message", {"session_id": SELF, "content": "x"}),
            ("stop_session", {"session_id": SELF}),
        ):
            result = await toolbox.call(tool, arguments)
            assert result.is_error and "own session" in result.text
        assert client.calls == []

    async def test_message_status(self) -> None:
        toolbox, client, _ = _toolbox("message")
        result = await toolbox.call("message_status", {"session_id": PEER, "request_id": "r-1"})
        assert result.structured["delivery"] == {"status": "delivered"}
        assert client.calls[0] == ("get_message_delivery", (PEER, "r-1", None))

    async def test_lifecycle_tools(self) -> None:
        toolbox, client, _ = _toolbox(ForgeMcpGrant.LIFECYCLE)
        created = await toolbox.call(
            "create_session",
            {"name": "helper", "repo": "github.com/acme/app", "branch": "fix", "model": "m"},
        )
        assert created.structured["session"]["status"] == "starting"
        creates = [payload for name, payload in client.calls if name == "create_session"]
        # The caller belongs to proj-1, so the new session becomes its worker.
        assert creates[0] == {
            "name": "helper",
            "model": "m",
            "source": {"type": "git", "repo": "github.com/acme/app", "branch": "fix"},
            "coordination": {
                "project_id": "proj-1",
                "role": "worker",
                "parent": {"instance_id": "node-a", "session_id": SELF},
            },
        }
        local = await toolbox.call("create_session", {"name": "l", "local_path": "/srv/app"})
        assert not local.is_error
        assert client.calls[-1][1]["source"] == {"type": "local_mount", "local_path": "/srv/app"}
        both = await toolbox.call("create_session", {"name": "b", "repo": "r", "local_path": "/x"})
        assert both.is_error
        relative = await toolbox.call("create_session", {"name": "b", "local_path": "x"})
        assert relative.is_error and "absolute" in relative.text
        started = await toolbox.call("start_session", {"session_id": PEER})
        stopped = await toolbox.call("stop_session", {"session_id": PEER})
        assert started.structured["session"]["status"] == "starting"
        assert stopped.structured["session"]["status"] == "stopped"

    async def test_create_session_project_membership_choices(self) -> None:
        toolbox, client, _ = _toolbox(ForgeMcpGrant.LIFECYCLE)

        async def created(arguments: dict[str, Any]) -> dict[str, Any]:
            result = await toolbox.call("create_session", {"name": "w", **arguments})
            assert not result.is_error, result.text
            return [payload for name, payload in client.calls if name == "create_session"][-1]

        assert "coordination" not in await created({"standalone": True})
        other = await created({"project_id": "proj-2", "role": "coordinator", "objective": "Lead"})
        assert other["coordination"] == {
            "project_id": "proj-2",
            "role": "coordinator",
            "objective": "Lead",
        }
        explicit = await created({"parent_session_id": PEER, "parent_instance_id": "node-b"})
        assert explicit["coordination"]["parent"] == {"instance_id": "node-b", "session_id": PEER}
        invalid = await toolbox.call("create_session", {"name": "w", "role": "Lead"})
        assert invalid.is_error

    async def test_create_session_refuses_when_its_own_membership_cannot_be_read(self) -> None:
        toolbox, client, _ = _toolbox(ForgeMcpGrant.LIFECYCLE)
        client.fail = ForgeApiError("down", status=503)

        refused = await toolbox.call("create_session", {"name": "w"})

        assert refused.is_error
        assert not [name for name, _ in client.calls if name == "create_session"]

    async def test_list_sessions_filters_by_role_and_parent(self) -> None:
        toolbox, client, _ = _toolbox()
        listed = await toolbox.call(
            "list_sessions", {"role": "worker", "parent_session_id": SELF, "project_id": "p"}
        )
        assert listed.structured["sessions"][0].get("parent") is None
        assert client.calls[-1][1] == {
            "scope": "guild",
            "project_id": "p",
            "role": "worker",
            "parent_session_id": SELF,
        }


class TestErrors:
    async def test_unknown_tool_is_a_protocol_error(self) -> None:
        toolbox, _, _ = _toolbox()
        with pytest.raises(UnknownToolError):
            await toolbox.call("delete_session", {})

    @pytest.mark.parametrize(
        ("error", "text"),
        [
            (ForgeApiError("connect refused"), "Forge is unreachable"),
            (ForgeApiError("not yours", status=403), "Forge answered 403: not yours"),
        ],
    )
    async def test_forge_errors_become_tool_errors(self, error, text) -> None:
        toolbox, client, _ = _toolbox()
        client.fail = error
        result = await toolbox.call("list_sessions", {})
        assert result.is_error and text in result.text

    async def test_invalid_arguments(self) -> None:
        toolbox, _, _ = _toolbox()
        result = await toolbox.call("list_sessions", {"scope": "everywhere"})
        assert result.is_error and "scope" in result.text
        result = await toolbox.call("list_sessions", None)
        assert not result.is_error


class TestResultEncoding:
    def test_to_mcp(self) -> None:
        ok = ToolResult.of({"a": 1}, max_chars=100).to_mcp()
        assert ok["isError"] is False and ok["structuredContent"] == {"a": 1}
        assert json.loads(ok["content"][0]["text"]) == {"a": 1}
        err = ToolResult.error("nope").to_mcp()
        assert err == {"content": [{"type": "text", "text": "nope"}], "isError": True}

    def test_text_is_bounded(self) -> None:
        result = ToolResult.of({"blob": "x" * 500}, max_chars=100)
        assert len(result.text) < 200 and "truncated" in result.text
        assert result.structured == {"blob": "x" * 500}

    def test_limits_clamp(self) -> None:
        assert LIMITS.clamp_list(None) == 2
        assert LIMITS.clamp_list(0) == 1
        assert LIMITS.clamp_turns(None) == 2
        assert LIMITS.clamp_turns(9) == 4


class _SessionlessHost(FakeHost):
    @property
    def session_id(self) -> str | None:
        return None


def _feed_toolbox(host: ForgeMcpHost | None = None) -> tuple[ForgeMcpToolbox, FakeClient]:
    client = FakeClient()
    toolbox = ForgeMcpToolbox(
        client=client,
        host=host or FakeHost(),
        grants=(),
        limits=LIMITS,
        notify_mode=ForgeMcpNotifyMode.FEED,
    )
    return toolbox, client


class TestFeedNotify:
    def test_feed_notify_schema_requires_an_idempotency_key(self) -> None:
        toolbox, _ = _feed_toolbox()
        notify = next(tool for tool in toolbox.list_tools() if tool["name"] == "notify")
        schema = notify["inputSchema"]
        assert schema["required"] == ["kind", "title", "idempotency_key"]
        assert {"session_id", "instance_id", "idempotency_key"} <= set(schema["properties"])
        assert "feed-only" in notify["description"]
        assert [tool["name"] for tool in toolbox.list_tools()] == [
            name for name in TOOL_NAMES if name not in TRANSCRIPT_ONLY_TOOLS
        ]

    def test_transcript_mode_keeps_the_session_schema(self) -> None:
        toolbox, _, _ = _toolbox()
        notify = next(tool for tool in toolbox.list_tools() if tool["name"] == "notify")
        assert "idempotency_key" not in notify["inputSchema"]["properties"]

    async def test_defaults_to_the_callers_session(self) -> None:
        toolbox, client = _feed_toolbox()
        result = await toolbox.call(
            "notify", {"kind": "milestone", "title": "Done", "idempotency_key": "k-1"}
        )
        assert not result.is_error
        name, (session_id, draft, key, instance_id) = client.calls[-1]
        assert name == "submit_notification"
        assert (session_id, key, instance_id) == (SELF, "k-1", None)
        assert draft.title == "Done" and draft.kind.value == "milestone"
        assert result.structured == {
            "notification_id": "nid-feed",
            "seq": 12,
            "session_id": SELF,
            "source": "agent",
            "state": "committed",
        }

    async def test_explicit_session_and_instance(self) -> None:
        toolbox, client = _feed_toolbox(_SessionlessHost())
        result = await toolbox.call(
            "notify",
            {
                "kind": "attention",
                "title": "Look",
                "idempotency_key": "k-2",
                "session_id": PEER,
                "instance_id": "node-b",
            },
        )
        assert not result.is_error
        assert client.calls[-1][1][0] == PEER and client.calls[-1][1][3] == "node-b"

    async def test_without_a_session_the_call_explains(self) -> None:
        toolbox, client = _feed_toolbox(_SessionlessHost())
        result = await toolbox.call(
            "notify", {"kind": "info", "title": "x", "idempotency_key": "k-3"}
        )
        assert result.is_error and "needs session_id" in result.text
        assert client.calls == []

    async def test_missing_idempotency_key_is_invalid(self) -> None:
        toolbox, client = _feed_toolbox()
        result = await toolbox.call("notify", {"kind": "info", "title": "x"})
        assert result.is_error and "idempotency_key" in result.text
        assert client.calls == []

    async def test_reply_ready_cannot_be_raised(self) -> None:
        toolbox, _ = _feed_toolbox()
        result = await toolbox.call(
            "notify", {"kind": "reply_ready", "title": "x", "idempotency_key": "k"}
        )
        assert result.is_error and "reply_ready is reserved for Forge" in result.text

    async def test_environment_lists_the_granted_tools(self) -> None:
        toolbox, _ = _feed_toolbox()
        result = await toolbox.call("environment", {})
        assert result.structured["grants"] == []
        assert "send_message" not in result.structured["tools"]
        assert "notify" in result.structured["tools"]


class TestDescribeSession:
    async def test_writes_only_the_sent_keys_of_the_own_session(self) -> None:
        toolbox, client, _ = _toolbox()
        result = await toolbox.call("describe_session", {"short_description": "Fixing CI"})
        assert not result.is_error
        assert client.calls == [("update_description", (SELF, {"short_description": "Fixing CI"}))]
        assert result.structured["session_id"] == SELF
        assert result.structured["short_description"] == "Fixing CI"
        assert result.structured["short_description_updated_at"] is not None
        assert result.structured["description"] is None

    async def test_null_clears_and_both_keys_travel_together(self) -> None:
        toolbox, client, _ = _toolbox()
        await toolbox.call("describe_session", {"short_description": None, "description": "## s"})
        assert client.calls[0][1] == (SELF, {"short_description": None, "description": "## s"})

    async def test_needs_no_grant_and_is_offered_to_every_session(self) -> None:
        toolbox, _, _ = _toolbox()
        spec = next(t for t in toolbox.list_tools() if t["name"] == "describe_session")
        assert "grant" not in spec["description"]
        assert "Blocked on" in spec["description"] and "Done:" in spec["description"]
        environment = await toolbox.call("environment", {})
        assert "describe_session" in environment.structured["tools"]

    async def test_an_empty_call_is_refused_without_a_write(self) -> None:
        toolbox, client, _ = _toolbox()
        result = await toolbox.call("describe_session", {})
        assert result.is_error and "short_description" in result.text
        assert client.calls == []

    async def test_unknown_keys_are_invalid(self) -> None:
        toolbox, client, _ = _toolbox()
        result = await toolbox.call("describe_session", {"summary": "x"})
        assert result.is_error and "Invalid arguments" in result.text
        assert client.calls == []

    async def test_outside_a_session_the_call_explains(self) -> None:
        toolbox, client = _feed_toolbox(_SessionlessHost())
        result = await toolbox.call("describe_session", {"short_description": "x"})
        assert result.is_error and "own Forge session" in result.text
        assert client.calls == []

    async def test_forge_refusal_is_a_tool_error(self) -> None:
        toolbox, client, _ = _toolbox()
        client.fail = ForgeApiError("not yours", status=403)
        result = await toolbox.call("describe_session", {"description": "x"})
        assert result.is_error and "Forge answered 403" in result.text

    async def test_session_summaries_carry_the_descriptions(self) -> None:
        toolbox, client, _ = _toolbox()
        stored = await client.update_description(PEER, {"short_description": "Reviewing PR"})
        client.calls.clear()

        async def get_session(session_id, *, instance_id):
            return stored

        client.get_session = get_session
        result = await toolbox.call("get_session", {"session_id": PEER})
        session = result.structured["session"]
        assert session["short_description"] == "Reviewing PR"
        assert session["short_description_updated_at"] == "2026-10-16T12:00:00+00:00"
        assert "description" not in session  # unset fields are omitted from summaries


CLOSE_ARGS = {
    "activation_id": "3f2b8c1e-5a47-4d0e-9b61-7c1e2a9d4f10",
    "ending": "verdict",
    "verdict": "implemented",
    "outcome": {"summary": "done"},
    "note": "all green",
    "evidence": [{"kind": "commit", "ref": "abc123", "summary": "the change"}],
}


class TestWorkflowStepClose:
    def test_listed_for_the_whole_session_of_a_step_host(self) -> None:
        toolbox, _, host = _toolbox()
        host.step_open = False
        assert "workflow_step_close" in [tool["name"] for tool in toolbox.list_tools()]
        host.step_open = True
        assert "workflow_step_close" in [tool["name"] for tool in toolbox.list_tools()]

    def test_not_listed_on_a_session_that_is_not_a_step_host(self) -> None:
        toolbox, _, host = _toolbox()
        host.step_host = False
        assert "workflow_step_close" not in [tool["name"] for tool in toolbox.list_tools()]

    async def test_environment_lists_the_tool_on_a_step_host_only(self) -> None:
        toolbox, _, host = _toolbox()
        hosted = await toolbox.call("environment", {})
        host.step_host = False
        plain = await toolbox.call("environment", {})
        assert "workflow_step_close" in hosted.structured["tools"]
        assert "workflow_step_close" not in plain.structured["tools"]

    async def test_a_close_reaches_the_host_with_validated_arguments(self) -> None:
        toolbox, _, host = _toolbox()
        host.step_open = True
        result = await toolbox.call("workflow_step_close", CLOSE_ARGS)
        assert not result.is_error
        assert host.closes[0].verdict == "implemented"
        assert host.closes[0].evidence[0].ref == "abc123"

    async def test_a_call_with_no_open_step_is_a_tool_error_that_records_nothing(self) -> None:
        toolbox, _, host = _toolbox()
        result = await toolbox.call("workflow_step_close", CLOSE_ARGS)
        assert result.is_error
        assert "no workflow step is open on this session" in result.text
        assert not host.closes

    async def test_a_call_on_a_session_that_is_not_a_step_host_is_refused(self) -> None:
        toolbox, _, host = _toolbox()
        host.step_host = False
        host.step_open = True
        result = await toolbox.call("workflow_step_close", CLOSE_ARGS)
        assert result.is_error
        assert "not a workflow step host" in result.text
        assert not host.closes

    async def test_the_hosts_rejection_reaches_the_agent_as_a_tool_error(self) -> None:
        toolbox, _, host = _toolbox()
        host.step_open = True
        host.close_error = WorkflowStepCloseError("verdict 'x' is not valid; use implemented")
        result = await toolbox.call("workflow_step_close", CLOSE_ARGS)
        assert result.is_error
        assert "verdict 'x' is not valid; use implemented" in result.text

    async def test_malformed_arguments_are_refused_before_the_host(self) -> None:
        toolbox, _, host = _toolbox()
        host.step_open = True
        result = await toolbox.call(
            "workflow_step_close", {"activation_id": "a", "ending": "someday"}
        )
        assert result.is_error
        assert "Invalid arguments for workflow_step_close" in result.text
        assert not host.closes

    async def test_the_feed_endpoint_does_not_serve_it(self) -> None:
        toolbox, _ = _feed_toolbox()
        with pytest.raises(UnknownToolError):
            await toolbox.call("workflow_step_close", CLOSE_ARGS)


def _named(name: str, session_id: str, node: str = "node-a", **extra: Any) -> dict[str, Any]:
    return _session(session_id, name=name, instance_id=node, instance_name=node.upper(), **extra)


def _envelope(**fields: Any) -> PeerMessage:
    base: dict[str, Any] = {
        "message_id": "m-1",
        "sender": PeerAddress(kind=PeerKind.SESSION, id=PEER, name="peer", node="thor"),
        "recipient": PeerAddress(kind=PeerKind.SESSION, id=SELF, name="me"),
        "sent_at": datetime(2026, 11, 8, tzinfo=UTC),
        "content": "can you check the build?",
        "reply": ReplyRoute(kind=ReplyKind.SESSION, target=PEER, node="thor"),
    }
    return PeerMessage(**{**base, **fields})


class TestAddressByName:
    async def test_a_unique_name_resolves_to_its_id_and_node(self) -> None:
        toolbox, client, _ = _toolbox(ForgeMcpGrant.MESSAGE)
        client.sessions = [_named("alpha", PEER, "node-b"), _named("beta", str(uuid.uuid4()))]

        result = await toolbox.call("send_message", {"to": "alpha", "content": "hello"})

        assert not result.is_error
        assert client.calls[0] == ("list_sessions", {"scope": "guild"})
        sent = next(args for name, args in client.calls if name == "send_message")
        assert (sent[0], sent[3]) == (PEER, "node-b")
        assert result.structured["session_id"] == PEER

    async def test_an_unknown_name_is_an_error_naming_it(self) -> None:
        toolbox, client, _ = _toolbox(ForgeMcpGrant.MESSAGE)
        client.sessions = [_named("alpha", PEER)]

        result = await toolbox.call("send_message", {"to": "ghost", "content": "hello"})

        assert result.is_error and "'ghost'" in result.text
        assert not any(name == "send_message" for name, _ in client.calls)

    async def test_an_ambiguous_name_lists_the_candidates_and_picks_none(self) -> None:
        toolbox, client, _ = _toolbox(ForgeMcpGrant.MESSAGE)
        other = str(uuid.uuid4())
        client.sessions = [
            _named("alpha", PEER, "node-a", status="running"),
            _named("alpha", other, "node-b", status="stopped"),
        ]

        result = await toolbox.call("send_message", {"to": "alpha", "content": "hello"})

        assert result.is_error
        assert "2 sessions are named 'alpha'" in result.text
        assert f"id {PEER}" in result.text and f"id {other}" in result.text
        assert "NODE-A" in result.text and "NODE-B" in result.text
        assert not any(name == "send_message" for name, _ in client.calls)

    async def test_instance_id_narrows_an_ambiguous_name(self) -> None:
        toolbox, client, _ = _toolbox(ForgeMcpGrant.MESSAGE)
        other = str(uuid.uuid4())
        client.sessions = [_named("alpha", PEER, "node-a"), _named("alpha", other, "node-b")]

        result = await toolbox.call(
            "send_message", {"to": "alpha", "instance_id": "node-b", "content": "hello"}
        )

        assert not result.is_error and result.structured["session_id"] == other

    async def test_a_name_that_is_the_callers_own_session_is_refused(self) -> None:
        toolbox, client, _ = _toolbox(ForgeMcpGrant.MESSAGE)
        client.sessions = [_named("me", SELF)]

        result = await toolbox.call("send_message", {"to": "me", "content": "hello"})

        assert result.is_error and "own session" in result.text

    @pytest.mark.parametrize(
        "arguments",
        [{"content": "x"}, {"to": "a", "session_id": PEER, "content": "x"}],
    )
    async def test_exactly_one_address_is_required(self, arguments) -> None:
        toolbox, client, _ = _toolbox(ForgeMcpGrant.MESSAGE)

        result = await toolbox.call("send_message", arguments)

        assert result.is_error and "exactly one of `to`" in result.text
        assert client.calls == []

    async def test_urgent_reaches_forge(self) -> None:
        toolbox, client, _ = _toolbox(ForgeMcpGrant.MESSAGE)

        await toolbox.call("send_message", {"session_id": PEER, "content": "now", "urgent": True})

        assert client.envelope_args == [{"in_reply_to": "", "urgent": True}]

    def test_names_show_in_get_and_list_summaries(self) -> None:
        from niuu.forge_mcp.tools import _session_summary

        assert _session_summary(_named("alpha", PEER))["name"] == "alpha"


class TestAsk:
    async def test_it_sends_then_returns_the_answer_the_host_waited_for(self) -> None:
        toolbox, client, host = _toolbox(ForgeMcpGrant.MESSAGE)

        result = await toolbox.call(
            "ask", {"session_id": PEER, "content": "ship it?", "request_id": "q-1"}
        )

        assert not result.is_error
        assert result.structured["outcome"] == "answered" and result.structured["answer"] == "yes"
        assert result.structured["request_id"] == "q-1"
        name, (session_id, content, request_id, _) = client.calls[0]
        assert (name, session_id, content, request_id) == ("send_message", PEER, "ship it?", "q-1")
        wait = host.asks[0]
        assert wait["recipient_id"] == PEER and wait["deadline_s"] == 50.0
        assert wait["delivery_confirmed"] is True
        from niuu.domain.peer_envelope import derive_message_id
        from niuu.domain.peer_message import PeerAddress, PeerKind

        expected = derive_message_id(
            PeerAddress(PeerKind.SESSION, SELF), PeerAddress(PeerKind.SESSION, PEER), "q-1"
        )
        assert wait["message_id"] == expected == result.structured["message_id"]

    @pytest.mark.parametrize(
        "outcome",
        ["blocked", "gone", "not_started", "delivery_unknown", "deadline"],
    )
    async def test_every_other_ending_is_its_own_named_error_never_an_answer(self, outcome) -> None:
        toolbox, _, host = _toolbox(ForgeMcpGrant.MESSAGE)
        host.ask_result = {"outcome": outcome, "detail": "why"}

        result = await toolbox.call("ask", {"session_id": PEER, "content": "q"})

        assert result.is_error is True
        assert result.structured["outcome"] == outcome and "answer" not in result.structured
        assert result.structured["request_id"].startswith("fmcp-")

    async def test_a_pending_send_is_passed_on_as_unconfirmed(self) -> None:
        toolbox, client, host = _toolbox(ForgeMcpGrant.MESSAGE)

        async def pending(*_args, **_kwargs):
            return {"status": "pending", "delivery": "pending"}

        client.send_message = pending  # type: ignore[method-assign]
        await toolbox.call("ask", {"session_id": PEER, "content": "q"})

        assert host.asks[0]["delivery_confirmed"] is False

    async def test_a_deadline_above_the_maximum_is_refused_naming_the_knob_and_sends_nothing(
        self,
    ) -> None:
        from niuu import cap_telemetry

        cap_telemetry.reset_cap_hits()
        toolbox, client, host = _toolbox(ForgeMcpGrant.MESSAGE)
        host.ask_max_s = 50.0

        result = await toolbox.call("ask", {"session_id": PEER, "content": "q", "deadline_s": 51})

        assert result.is_error and "ask.wait_max_s=50" in result.text
        assert client.calls == [] and host.asks == []
        assert cap_telemetry.cap_hit_counts()["ask.wait_max_s"] == 1

    async def test_a_deadline_at_the_maximum_is_accepted(self) -> None:
        toolbox, _, host = _toolbox(ForgeMcpGrant.MESSAGE)

        await toolbox.call("ask", {"session_id": PEER, "content": "q", "deadline_s": 50})

        assert host.asks[0]["deadline_s"] == 50.0

    async def test_it_needs_the_message_grant_and_never_asks_itself(self) -> None:
        toolbox, client, _ = _toolbox()
        denied = await toolbox.call("ask", {"session_id": PEER, "content": "q"})
        assert denied.is_error and "'message' grant" in denied.text

        toolbox, client, host = _toolbox(ForgeMcpGrant.MESSAGE)
        own = await toolbox.call("ask", {"session_id": SELF, "content": "q"})
        assert own.is_error and "your own session" in own.text
        assert client.calls == [] and host.asks == []

    async def test_it_resolves_a_name_like_send_message(self) -> None:
        toolbox, client, host = _toolbox(ForgeMcpGrant.MESSAGE)
        client.sessions = [_named("alpha", PEER)]

        await toolbox.call("ask", {"to": "alpha", "content": "q"})

        assert host.asks[0]["recipient_id"] == PEER

    async def test_an_unknown_name_is_an_error_and_nothing_is_awaited(self) -> None:
        toolbox, client, host = _toolbox(ForgeMcpGrant.MESSAGE)
        client.sessions = []

        result = await toolbox.call("ask", {"to": "nobody", "content": "q"})

        assert result.is_error and "no session named 'nobody'" in result.text
        assert host.asks == []

    def test_only_a_sessions_own_broker_serves_it(self) -> None:
        assert "ask" in TRANSCRIPT_ONLY_TOOLS

    async def test_a_host_that_holds_no_inbox_refuses(self) -> None:
        client = FakeClient()
        toolbox = ForgeMcpToolbox(
            client=client,
            host=_NoInboxHost(),
            grants=(ForgeMcpGrant.MESSAGE,),
            limits=LIMITS,
        )

        result = await toolbox.call("ask", {"session_id": PEER, "content": "q"})

        assert result.is_error and "own MCP" in result.text and client.calls == []


class TestMeshRouting:
    async def test_a_message_to_a_mesh_peer_goes_over_the_mesh_not_through_forge(self) -> None:
        toolbox, client, host = _toolbox(ForgeMcpGrant.MESSAGE)
        host.mesh_peer_ids = {PEER: "peer-9"}

        result = await toolbox.call(
            "send_message", {"session_id": PEER, "content": "hi", "request_id": "r-1"}
        )

        assert not result.is_error
        assert result.structured["path"] == "mesh" and result.structured["status"] == "delivered"
        assert client.calls == []
        assert host.mesh_sends[0]["peer"] == "peer-9" and host.mesh_sends[0]["expect_reply"]

    async def test_a_name_that_is_a_mesh_peer_is_routed_over_the_mesh(self) -> None:
        toolbox, client, host = _toolbox(ForgeMcpGrant.MESSAGE)
        host.mesh_peer_ids = {"alpha": "peer-9"}

        result = await toolbox.call(
            "send_message", {"to": "alpha", "content": "hi", "urgent": True}
        )

        assert result.structured["path"] == "mesh" and host.mesh_sends[0]["urgent"] is True
        assert client.calls == []

    async def test_a_recipient_that_is_not_a_peer_goes_through_forge(self) -> None:
        toolbox, client, host = _toolbox(ForgeMcpGrant.MESSAGE)

        result = await toolbox.call("send_message", {"session_id": PEER, "content": "hi"})

        assert result.structured["path"] == "forge"
        assert client.calls[0][0] == "send_message" and host.mesh_sends == []

    async def test_a_mesh_that_is_down_is_an_error_and_never_a_forge_send(self) -> None:
        toolbox, client, host = _toolbox(ForgeMcpGrant.MESSAGE)
        host.mesh_down = True

        for tool, args in (
            ("send_message", {"session_id": PEER, "content": "hi"}),
            ("ask", {"session_id": PEER, "content": "hi"}),
        ):
            result = await toolbox.call(tool, args)
            assert result.is_error and "mesh is not running" in result.text
        assert client.calls == [] and host.mesh_sends == [] and host.asks == []

    async def test_a_peer_that_refuses_the_message_is_an_error(self) -> None:
        toolbox, client, host = _toolbox(ForgeMcpGrant.MESSAGE)
        host.mesh_peer_ids = {PEER: "peer-9"}
        host.mesh_send_error = ForgeApiError("mesh peer refused the message: inbox off", status=502)

        result = await toolbox.call("send_message", {"session_id": PEER, "content": "hi"})

        assert result.is_error and "inbox off" in result.text and client.calls == []

    async def test_a_session_reply_to_a_mesh_peer_goes_over_the_mesh(self) -> None:
        toolbox, client, host = _toolbox(ForgeMcpGrant.MESSAGE)
        host.mesh_peer_ids = {PEER: "peer-9"}
        host.inbox["m-1"] = _envelope()

        result = await toolbox.call("reply", {"message_id": "m-1", "content": "done"})

        assert result.structured["path"] == "mesh" and client.calls == []
        assert host.mesh_sends[0]["peer"] == "peer-9"
        assert host.mesh_sends[0]["in_reply_to"] == "m-1"

    async def test_a_session_reply_to_a_non_peer_goes_through_forge(self) -> None:
        toolbox, client, host = _toolbox(ForgeMcpGrant.MESSAGE)
        host.inbox["m-1"] = _envelope()

        result = await toolbox.call("reply", {"message_id": "m-1", "content": "done"})

        assert result.structured["path"] == "forge" and host.mesh_sends == []

    async def test_ask_over_the_mesh_waits_on_the_peer_not_on_forge(self) -> None:
        toolbox, client, host = _toolbox(ForgeMcpGrant.MESSAGE)
        host.mesh_peer_ids = {PEER: "peer-9"}

        result = await toolbox.call(
            "ask", {"session_id": PEER, "content": "q", "request_id": "q-1"}
        )

        assert not result.is_error and result.structured["path"] == "mesh"
        wait = host.asks[0]
        assert wait["mesh_peer"] == "peer-9" and wait["delivery_confirmed"] is True
        assert client.calls == []
        from niuu.domain.peer_envelope import derive_message_id
        from niuu.domain.peer_message import PeerAddress, PeerKind

        assert wait["message_id"] == derive_message_id(
            PeerAddress(PeerKind.SESSION, SELF), PeerAddress(PeerKind.MESH_PEER, "peer-9"), "q-1"
        )

    async def test_an_ask_whose_send_gets_no_answer_waits_as_unconfirmed(self) -> None:
        toolbox, _, host = _toolbox(ForgeMcpGrant.MESSAGE)
        host.mesh_peer_ids = {PEER: "peer-9"}
        host.mesh_send_error = ForgeApiError("did not answer", status=504)
        host.ask_result = {"outcome": "delivery_unknown", "detail": "x"}

        result = await toolbox.call("ask", {"session_id": PEER, "content": "q"})

        assert host.asks[0]["delivery_confirmed"] is False
        assert result.is_error and result.structured["outcome"] == "delivery_unknown"

    async def test_an_ask_refused_by_the_peer_is_an_error_not_a_wait(self) -> None:
        toolbox, _, host = _toolbox(ForgeMcpGrant.MESSAGE)
        host.mesh_peer_ids = {PEER: "peer-9"}
        host.mesh_send_error = ForgeApiError("refused: inbox off", status=502)

        result = await toolbox.call("ask", {"session_id": PEER, "content": "q"})

        assert result.is_error and "refused" in result.text and host.asks == []

    async def test_a_peer_that_is_this_session_is_refused(self) -> None:
        toolbox, _, host = _toolbox(ForgeMcpGrant.MESSAGE)
        host.mesh_peer_ids = {PEER: SELF}

        for tool in ("send_message", "ask"):
            result = await toolbox.call(tool, {"session_id": PEER, "content": "q"})
            assert result.is_error and "your own session" in result.text
        assert host.mesh_sends == []


class _NoInboxHost(ForgeMcpHost):
    @property
    def session_id(self) -> str | None:
        return SELF

    async def environment(self) -> dict[str, Any]:
        return {}

    async def notify(self, draft: NotificationDraft) -> dict[str, Any]:
        return {}


class TestReply:
    async def test_a_session_reply_goes_back_with_in_reply_to(self) -> None:
        toolbox, client, host = _toolbox(ForgeMcpGrant.MESSAGE)
        host.inbox["m-1"] = _envelope()

        result = await toolbox.call("reply", {"message_id": "m-1", "content": "build is green"})

        assert not result.is_error and result.structured["via"] == "session"
        name, (session_id, content, _request, instance_id) = client.calls[0]
        assert (name, session_id, content, instance_id) == (
            "send_message",
            PEER,
            "build is green",
            None,
        )
        assert client.envelope_args == [{"in_reply_to": "m-1", "urgent": False}]

    async def test_a_mesh_reply_goes_back_over_the_mesh(self) -> None:
        toolbox, client, host = _toolbox()
        host.inbox["m-2"] = _envelope(
            message_id="m-2", reply=ReplyRoute(kind=ReplyKind.MESH, target="peer-7")
        )

        result = await toolbox.call("reply", {"message_id": "m-2", "content": "done"})

        assert not result.is_error and result.structured["via"] == "mesh"
        assert host.mesh_sends[0]["peer"] == "peer-7"
        assert host.mesh_sends[0]["in_reply_to"] == "m-2" and not host.mesh_sends[0]["expect_reply"]
        assert client.calls == []

    async def test_a_room_reply_is_posted_to_the_hosts_room(self) -> None:
        toolbox, client, host = _toolbox(ForgeMcpGrant.MESSAGE)
        host.inbox["m-3"] = _envelope(
            message_id="m-3",
            room_id="huddle-1",
            reply=ReplyRoute(kind=ReplyKind.ROOM, target=PEER, node="thor"),
        )

        result = await toolbox.call("reply", {"message_id": "m-3", "content": "agreed"})

        assert not result.is_error and result.structured["via"] == "room"
        assert client.calls == [("post_room_message", (PEER, "huddle-1", "agreed"))]

    async def test_a_message_with_no_reply_route_is_refused_with_the_reason(self) -> None:
        toolbox, client, host = _toolbox(ForgeMcpGrant.MESSAGE)
        host.inbox["m-4"] = _envelope(message_id="m-4", reply=None)

        result = await toolbox.call("reply", {"message_id": "m-4", "content": "hi"})

        assert result.is_error and "takes no reply" in result.text
        assert client.calls == [] and host.mesh_sends == []

    async def test_an_unknown_message_id_is_an_error(self) -> None:
        toolbox, client, _ = _toolbox(ForgeMcpGrant.MESSAGE)

        result = await toolbox.call("reply", {"message_id": "nope", "content": "hi"})

        assert result.is_error and "received no message 'nope'" in result.text
        assert client.calls == []

    @pytest.mark.parametrize("kind", [ReplyKind.SESSION, ReplyKind.ROOM])
    async def test_session_and_room_replies_need_the_message_grant(self, kind) -> None:
        toolbox, client, host = _toolbox()
        host.inbox["m-5"] = _envelope(
            message_id="m-5", room_id="r", reply=ReplyRoute(kind=kind, target=PEER)
        )

        result = await toolbox.call("reply", {"message_id": "m-5", "content": "hi"})

        assert result.is_error and "'message' grant" in result.text
        assert client.calls == []

    async def test_a_room_route_without_a_room_is_refused(self) -> None:
        toolbox, client, host = _toolbox(ForgeMcpGrant.MESSAGE)
        host.inbox["m-6"] = _envelope(
            message_id="m-6", reply=ReplyRoute(kind=ReplyKind.ROOM, target=PEER)
        )

        result = await toolbox.call("reply", {"message_id": "m-6", "content": "hi"})

        assert result.is_error and "no room" in result.text
        assert client.calls == []

    async def test_forges_refusal_reaches_the_agent(self) -> None:
        toolbox, client, host = _toolbox(ForgeMcpGrant.MESSAGE)
        host.inbox["m-1"] = _envelope()
        client.fail = ForgeApiError("Session gone", status=404)

        result = await toolbox.call("reply", {"message_id": "m-1", "content": "x"})

        assert result.is_error and "404" in result.text

    def test_reply_is_a_session_broker_tool_only(self) -> None:
        assert {"reply", "room_post", "mesh_send"} <= TRANSCRIPT_ONLY_TOOLS


class TestRoomPost:
    HOST = str(uuid.uuid4())

    async def test_a_post_goes_to_the_hosting_session(self) -> None:
        toolbox, client, _ = _toolbox(ForgeMcpGrant.MESSAGE)

        result = await toolbox.call(
            "room_post", {"room_id": "huddle-1", "host_session_id": self.HOST, "content": "hi all"}
        )

        assert not result.is_error
        assert client.calls == [("post_room_message", (self.HOST, "huddle-1", "hi all"))]
        assert result.structured["message_id"] == "room-msg-1"

    async def test_a_member_refusal_carries_the_hosts_reason(self) -> None:
        toolbox, client, _ = _toolbox(ForgeMcpGrant.MESSAGE)
        client.fail = ForgeApiError("Session x is not a member of room huddle-1", status=403)

        result = await toolbox.call(
            "room_post", {"room_id": "huddle-1", "host_session_id": self.HOST, "content": "hi"}
        )

        assert result.is_error and "not a member of room huddle-1" in result.text

    async def test_it_needs_the_message_grant(self) -> None:
        toolbox, client, _ = _toolbox()

        result = await toolbox.call(
            "room_post", {"room_id": "r", "host_session_id": self.HOST, "content": "hi"}
        )

        assert result.is_error and "'message' grant" in result.text and client.calls == []

    async def test_a_session_does_not_post_into_its_own_room_this_way(self) -> None:
        toolbox, client, _ = _toolbox(ForgeMcpGrant.MESSAGE)

        result = await toolbox.call(
            "room_post", {"room_id": "r", "host_session_id": SELF, "content": "hi"}
        )

        assert result.is_error and "own session" in result.text and client.calls == []


class TestMeshSend:
    async def test_it_sends_through_the_host_with_a_request_id(self) -> None:
        toolbox, _, host = _toolbox()

        result = await toolbox.call(
            "mesh_send", {"peer": "peer-7", "content": "hello", "expect_reply": True}
        )

        assert not result.is_error
        sent = host.mesh_sends[0]
        assert (sent["peer"], sent["content"], sent["expect_reply"]) == ("peer-7", "hello", True)
        assert sent["request_id"].startswith("fmcp-")
        assert result.structured["request_id"] == sent["request_id"]

    def test_it_is_listed_only_when_the_mesh_is_enabled(self) -> None:
        toolbox, _, host = _toolbox()
        assert "mesh_send" in [tool["name"] for tool in toolbox.list_tools()]
        host.mesh = False
        assert "mesh_send" not in [tool["name"] for tool in toolbox.list_tools()]

    async def test_a_call_without_a_mesh_is_refused_with_the_reason(self) -> None:
        toolbox, _, host = _toolbox()
        host.mesh = False

        result = await toolbox.call("mesh_send", {"peer": "p", "content": "hi"})

        assert result.is_error and "no mesh" in result.text
        assert host.mesh_sends == []

    async def test_the_hosts_refusal_reaches_the_agent(self) -> None:
        toolbox, _, host = _toolbox()

        async def refuse(*_args, **_kwargs):
            raise ForgeApiError("mesh peer 'p' refused the message", status=502)

        host.send_mesh_message = refuse  # type: ignore[method-assign]

        result = await toolbox.call("mesh_send", {"peer": "p", "content": "hi"})

        assert result.is_error and "refused the message" in result.text

    async def test_the_default_host_has_no_mesh_or_inbox(self) -> None:
        class Bare(ForgeMcpHost):
            session_id = None

            async def environment(self):
                return {}

            async def notify(self, draft):
                return {}

        bare = Bare()
        assert bare.mesh_host() is False
        with pytest.raises(ForgeApiError, match="mesh_send"):
            await bare.send_mesh_message(
                "p", "x", expect_reply=False, request_id="r", in_reply_to=""
            )


class TestMeshPublish:
    async def test_it_publishes_through_the_host_with_a_request_id(self) -> None:
        toolbox, _, host = _toolbox()

        result = await toolbox.call(
            "mesh_publish",
            {
                "topic": " review.requested ",
                "content": "please review PR 12",
                "correlation_id": "pr-12",
                "in_reply_to": "evt-0",
            },
        )

        assert not result.is_error
        published = host.mesh_publishes[0]
        assert published["topic"] == "review.requested"
        assert (published["content"], published["correlation_id"]) == (
            "please review PR 12",
            "pr-12",
        )
        assert published["in_reply_to"] == "evt-0"
        assert published["request_id"].startswith("fmcp-")
        assert result.structured["request_id"] == published["request_id"]
        assert result.structured["event_id"] == "evt-1"

    async def test_it_names_no_recipient_and_takes_no_sender(self) -> None:
        toolbox, _, host = _toolbox()
        spec = {tool["name"]: tool for tool in toolbox.list_tools()}["mesh_publish"]

        assert set(spec["inputSchema"]["properties"]) == {
            "topic",
            "content",
            "correlation_id",
            "in_reply_to",
            "request_id",
        }
        forged = await toolbox.call(
            "mesh_publish", {"topic": "t", "content": "x", "sender": "the CEO"}
        )
        assert forged.is_error and host.mesh_publishes == []

    @pytest.mark.parametrize(
        "arguments",
        [{"topic": "  ", "content": "x"}, {"topic": "t", "content": "  "}, {"content": "x"}],
    )
    async def test_a_blank_or_missing_argument_is_refused(self, arguments) -> None:
        toolbox, _, host = _toolbox()

        result = await toolbox.call("mesh_publish", arguments)

        assert result.is_error and host.mesh_publishes == []

    def test_it_is_listed_only_when_the_mesh_is_enabled(self) -> None:
        toolbox, _, host = _toolbox()
        assert "mesh_publish" in [tool["name"] for tool in toolbox.list_tools()]
        host.mesh = False
        assert "mesh_publish" not in [tool["name"] for tool in toolbox.list_tools()]

    async def test_a_call_without_a_mesh_is_refused_with_the_reason(self) -> None:
        toolbox, _, host = _toolbox()
        host.mesh = False

        result = await toolbox.call("mesh_publish", {"topic": "t", "content": "hi"})

        assert result.is_error and "no mesh" in result.text
        assert host.mesh_publishes == []

    async def test_the_hosts_refusal_reaches_the_agent(self) -> None:
        toolbox, _, host = _toolbox()

        async def refuse(*_args, **_kwargs):
            raise ForgeApiError("'t' is not a topic this session declares it publishes", status=403)

        host.publish_mesh_event = refuse  # type: ignore[method-assign]

        result = await toolbox.call("mesh_publish", {"topic": "t", "content": "hi"})

        assert result.is_error and "declares it publishes" in result.text

    async def test_the_default_host_has_no_mesh(self) -> None:
        class Bare(ForgeMcpHost):
            session_id = None

            async def environment(self):
                return {}

            async def notify(self, draft):
                return {}

        with pytest.raises(ForgeApiError, match="mesh_publish"):
            await Bare().publish_mesh_event(
                "t", "x", request_id="r", correlation_id="", in_reply_to=""
            )


class TestMeshPeers:
    async def test_it_returns_the_hosts_table_unranked_with_the_filters_it_was_given(self) -> None:
        toolbox, _, host = _toolbox()

        result = await toolbox.call("mesh_peers", {"role": "reviewer", "consumes": "x"})

        assert not result.is_error
        assert host.mesh_peer_queries == [
            {"role": "reviewer", "capability": "", "consumes": "x", "produces": ""}
        ]
        assert result.structured["peers"] == [{"peer_id": "p-1", "role": "reviewer"}]

    async def test_it_takes_no_filter_it_does_not_name(self) -> None:
        toolbox, _, host = _toolbox()
        spec = {tool["name"]: tool for tool in toolbox.list_tools()}["mesh_peers"]

        assert set(spec["inputSchema"]["properties"]) == {
            "role",
            "capability",
            "consumes",
            "produces",
        }
        assert (await toolbox.call("mesh_peers", {"best": True})).is_error
        assert host.mesh_peer_queries == []

    def test_it_is_listed_only_when_the_mesh_is_enabled(self) -> None:
        toolbox, _, host = _toolbox()
        assert "mesh_peers" in [tool["name"] for tool in toolbox.list_tools()]
        host.mesh = False
        assert "mesh_peers" not in [tool["name"] for tool in toolbox.list_tools()]

    async def test_a_call_without_a_mesh_is_refused_with_the_reason(self) -> None:
        toolbox, _, host = _toolbox()
        host.mesh = False

        result = await toolbox.call("mesh_peers", {})

        assert result.is_error and "no mesh" in result.text and host.mesh_peer_queries == []

    async def test_the_hosts_refusal_reaches_the_agent(self) -> None:
        toolbox, _, host = _toolbox()

        async def refuse(**_kwargs):
            raise ForgeApiError("this session has no peer discovery", status=503)

        host.mesh_peers = refuse  # type: ignore[method-assign]

        result = await toolbox.call("mesh_peers", {})

        assert result.is_error and "no peer discovery" in result.text

    async def test_the_default_host_has_no_mesh(self) -> None:
        class Bare(ForgeMcpHost):
            session_id = None

            async def environment(self):
                return {}

            async def notify(self, draft):
                return {}

        with pytest.raises(ForgeApiError, match="mesh_peers"):
            await Bare().mesh_peers(role="", capability="", consumes="", produces="")


class TestPeerToolArguments:
    @pytest.mark.parametrize(
        ("tool", "arguments"),
        [
            ("send_message", {"to": "alpha", "content": "   "}),
            ("reply", {"message_id": "m-1", "content": "   "}),
            ("room_post", {"room_id": "r", "host_session_id": PEER, "content": "   "}),
            ("mesh_send", {"peer": "p", "content": "   "}),
        ],
    )
    async def test_blank_content_is_refused_before_anything_is_sent(self, tool, arguments) -> None:
        toolbox, client, host = _toolbox(ForgeMcpGrant.MESSAGE)

        result = await toolbox.call(tool, arguments)

        assert result.is_error and "content must not be blank" in result.text
        assert client.calls == [] and host.mesh_sends == []

    async def test_a_host_session_id_must_be_a_session_uuid(self) -> None:
        toolbox, client, _ = _toolbox(ForgeMcpGrant.MESSAGE)

        result = await toolbox.call(
            "room_post", {"room_id": "r", "host_session_id": "not-a-uuid", "content": "hi"}
        )

        assert result.is_error and "Forge session UUID" in result.text
        assert client.calls == []
