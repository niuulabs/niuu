"""Forge MCP toolbox: tool list, grants, validation, bounds and Forge error mapping."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from niuu.domain.notifications import NotificationDraft
from niuu.forge_mcp import (
    FLOCK_TOOL_SPECS,
    TOOL_NAMES,
    FlockError,
    ForgeApiError,
    ForgeClient,
    ForgeMcpFlock,
    ForgeMcpGrant,
    ForgeMcpHost,
    ForgeMcpLimits,
    ForgeMcpNotifyMode,
    ForgeMcpToolbox,
    ToolResult,
    UnknownToolError,
)

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
        self.fail: ForgeApiError | None = None

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

    async def send_message(self, session_id, *, content, request_id, instance_id):
        self._record("send_message", (session_id, content, request_id, instance_id))
        return {"status": "delivered", "delivery": "delivered", "request_id": request_id}

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


class FakeHost(ForgeMcpHost):
    def __init__(self) -> None:
        self.drafts: list[NotificationDraft] = []

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
            "list_notifications",
            "list_sessions",
            "get_session",
            "session_transcript",
            "send_message",
            "message_status",
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
        client.fail = ForgeApiError("down", status=503)
        with_unknown_caller = await toolbox._launch_coordination(
            toolbox._specs["create_session"].args_model.model_validate({"name": "w"})
        )
        assert with_unknown_caller is None
        invalid = await toolbox.call("create_session", {"name": "w", "role": "Lead"})
        assert invalid.is_error

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
        assert [tool["name"] for tool in toolbox.list_tools()] == list(TOOL_NAMES)

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


class FakeFlock(ForgeMcpFlock):
    def __init__(self) -> None:
        self.delegated: list[tuple[str, str]] = []
        self.waits: list[float] = []
        self.activity_result: dict[str, Any] = {"cursor": 0, "activity": []}
        self.refuse: FlockError | None = None

    @property
    def max_wait_s(self) -> float:
        return 30.0

    async def members(self) -> list[dict[str, Any]]:
        return [{"peer_id": "flock-coder", "persona": "coder", "status": "idle"}]

    async def delegate(self, peer_id: str, content: str) -> dict[str, Any]:
        if self.refuse is not None:
            raise self.refuse
        self.delegated.append((peer_id, content))
        return {"message_id": "m1", "peer_id": peer_id, "persona": "coder", "cursor": 4}

    async def activity(self, *, after: int, peer_id: str, wait_s: float) -> dict[str, Any]:
        self.waits.append(wait_s)
        return self.activity_result


def _flock_toolbox() -> tuple[ForgeMcpToolbox, FakeFlock]:
    flock = FakeFlock()
    toolbox = ForgeMcpToolbox(
        client=FakeClient(), host=FakeHost(), grants=(), limits=LIMITS, flock=flock
    )
    return toolbox, flock


class TestFlockTools:
    def test_flock_tools_exist_only_for_a_flock_broker(self) -> None:
        plain, _, _ = _toolbox()
        assert not {spec.name for spec in FLOCK_TOOL_SPECS} & {
            tool["name"] for tool in plain.list_tools()
        }
        toolbox, _ = _flock_toolbox()
        names = [tool["name"] for tool in toolbox.list_tools()]
        assert names[-3:] == ["flock_members", "flock_delegate", "flock_outcomes"]

    async def test_flock_tool_on_a_non_broker_is_refused(self) -> None:
        plain, _, _ = _toolbox()
        with pytest.raises(UnknownToolError):
            await plain.call("flock_delegate", {"peer_id": "flock-coder", "content": "x"})

    async def test_members(self) -> None:
        toolbox, _ = _flock_toolbox()
        result = await toolbox.call("flock_members", {})
        assert result.structured == {
            "members": [{"peer_id": "flock-coder", "persona": "coder", "status": "idle"}]
        }

    async def test_delegate_hands_over_and_returns_a_cursor(self) -> None:
        toolbox, flock = _flock_toolbox()
        result = await toolbox.call(
            "flock_delegate", {"peer_id": "flock-coder", "content": "Write add.py"}
        )
        assert not result.is_error
        assert result.structured["cursor"] == 4
        assert flock.delegated == [("flock-coder", "Write add.py")]

    async def test_refused_delegation_is_a_tool_error(self) -> None:
        toolbox, flock = _flock_toolbox()
        flock.refuse = FlockError("the hand-off to flock-coder was not accepted: offline")
        result = await toolbox.call(
            "flock_delegate", {"peer_id": "flock-coder", "content": "Write add.py"}
        )
        assert result.is_error and "not accepted: offline" in result.text

    async def test_blank_delegation_is_rejected(self) -> None:
        toolbox, flock = _flock_toolbox()
        result = await toolbox.call("flock_delegate", {"peer_id": "flock-coder", "content": " "})
        assert result.is_error and flock.delegated == []

    async def test_outcomes_returns_member_results(self) -> None:
        toolbox, flock = _flock_toolbox()
        flock.activity_result = {
            "cursor": 6,
            "activity": [{"seq": 6, "persona": "reviewer", "verdict": "pass"}],
        }
        result = await toolbox.call("flock_outcomes", {"after": 4})
        assert not result.is_error
        assert result.structured["activity"][0]["verdict"] == "pass"
        assert flock.waits == [30.0]

    async def test_outcomes_wait_is_bounded_by_the_host(self) -> None:
        toolbox, flock = _flock_toolbox()
        flock.activity_result = {"cursor": 5, "activity": [{"seq": 5}]}
        await toolbox.call("flock_outcomes", {"after": 4, "wait_s": 900})
        await toolbox.call("flock_outcomes", {"after": 4, "wait_s": 2})
        assert flock.waits == [30.0, 2.0]

    async def test_no_outcome_is_a_failure_not_a_result(self) -> None:
        toolbox, _ = _flock_toolbox()
        result = await toolbox.call("flock_outcomes", {"after": 4, "wait_s": 1})
        assert result.is_error
        assert "do not report a verdict" in result.text
