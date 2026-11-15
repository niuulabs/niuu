"""A persona reaches the transport the broker builds, identically on every start.

Each test builds the transport through ``Broker._create_transport`` (the real composition path)
and reads the engine-facing contract: argv, SDK options, thread params, files. A persona the
harness cannot enforce stops the session from starting.
"""

from __future__ import annotations

import asyncio
import stat
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personas.domain.persona_document import PortablePersonaDefinition
from skuld.broker import Broker
from skuld.config import SkuldSettings
from skuld.personas import registry
from skuld.personas.projection import PersonaProjectionError
from skuld.transports.claude_policy import ClaudeToolPolicy

SESSION_ID = "0b6c1d7e-3f0a-4a8e-9a57-6a9c2f1d0e11"
ADAPTERS = {
    "sdk": "skuld.transports.sdk.SDKTransport",
    "subprocess": "skuld.transports.subprocess.SubprocessTransport",
    "persistent": "skuld.transports.persistent_subprocess.PersistentSubprocessTransport",
    "sdk_websocket": "skuld.transports.sdk_websocket.SdkWebSocketTransport",
    "tmux": "skuld.transports.tmux_interactive.TmuxInteractiveTransport",
    "codex_ws": "skuld.transports.codex_ws.CodexWebSocketTransport",
    "codex": "skuld.transports.codex.CodexSubprocessTransport",
    "opencode": "skuld.transports.opencode.OpenCodeHttpTransport",
    "grok": "skuld.transports.grok.GrokACPTransport",
    "pi": "skuld.transports.pi.PiRpcTransport",
    "dsh": "skuld.transports.dsh.DshJsonRpcTransport",
    "muse": "skuld.transports.muse.MuseMSPTransport",
}

REVIEWER = {
    "name": "reviewer",
    "system_prompt_template": "You review code and change nothing.",
    "permission_mode": "read_only",
}
READ_ONLY_TOOLS = (
    "Read,Glob,Grep,WebFetch,WebSearch,TodoWrite,TaskCreate,TaskUpdate,TaskGet,TaskList"
)
OPEN = {"name": "open", "system_prompt_template": "You help."}


def document(definition: dict[str, Any]) -> dict[str, Any]:
    return PortablePersonaDefinition(
        id=definition["name"], revision="content-0123456789abcdef", definition=definition
    ).to_dict()


def broker(
    tmp_path: Path,
    adapter: str,
    definition: dict[str, Any] | None,
    *,
    request_prompt: str = "",
    resume: str = "",
    **settings: Any,
) -> Broker:
    session: dict[str, Any] = {
        "id": SESSION_ID,
        "workspace_dir": str(tmp_path / "ws"),
        "model": "m",
        "system_prompt": request_prompt,
        "resume_session_id": resume,
    }
    if definition is not None:
        session["persona"] = document(definition)
    config = SkuldSettings(
        session=session,
        transport_adapter=ADAPTERS[adapter],
        forge_mcp={"runtime_dir": str(tmp_path / "rt")},
        port=18765,
        host="0.0.0.0",
        **settings,
    )
    (tmp_path / "ws").mkdir(exist_ok=True)
    return Broker(settings=config)


def exec_process() -> MagicMock:
    stdout = AsyncMock()
    stdout.readline = AsyncMock(side_effect=[b'{"type": "result", "result": "ok"}\n', b""])
    stdout.read = AsyncMock(return_value=b"")
    stdin = MagicMock()
    stdin.drain = AsyncMock()
    stdin.wait_closed = AsyncMock()
    process = MagicMock(stdout=stdout, stderr=None, stdin=stdin, pid=1, returncode=0)
    process.wait = AsyncMock(return_value=0)
    return process


def flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


# ---------------------------------------------------------------------- claude


def test_persistent_claude_gets_the_boundary_and_the_instructions_on_every_spawn(
    tmp_path: Path,
) -> None:
    fresh = broker(tmp_path, "persistent", REVIEWER, request_prompt="Mind the migrations.")
    resumed = broker(
        tmp_path, "persistent", REVIEWER, request_prompt="Mind the migrations.", resume="abc"
    )

    cold = fresh._create_transport()._build_command()
    warm = resumed._create_transport()._build_command()

    assert "--resume" not in cold
    assert flag(warm, "--resume") == "abc"
    for argv in (cold, warm):
        assert flag(argv, "--tools") == f"{READ_ONLY_TOOLS},Bash"
        assert flag(argv, "--permission-mode") == "dontAsk"
        assert "--strict-mcp-config" in argv
        assert "Bash(git diff *)" in flag(argv, "--allowedTools")
        appended = flag(argv, "--append-system-prompt")
        assert appended.index("You review code and change nothing.") < appended.index(
            "Mind the migrations."
        )
    assert flag(cold, "--append-system-prompt") == flag(warm, "--append-system-prompt")
    assert {a for a in cold if a.startswith("--") and a != "--resume"} == {
        a for a in warm if a.startswith("--") and a != "--resume"
    }


async def test_a_node_in_bypass_mode_does_not_widen_a_read_only_persona(tmp_path: Path) -> None:
    node = broker(
        tmp_path,
        "persistent",
        REVIEWER,
        skip_permissions=True,
        claude_permission_mode="bypassPermissions",
    )

    argv = node._create_transport()._build_command()

    assert argv.count("--permission-mode") == 1
    assert flag(argv, "--permission-mode") == "dontAsk"
    assert "bypassPermissions" not in argv


def test_a_persona_without_a_restricting_mode_leaves_the_nodes_own_mode(tmp_path: Path) -> None:
    node = broker(
        tmp_path,
        "persistent",
        {**OPEN, "permission_mode": "workspace_write"},
        skip_permissions=True,
        claude_permission_mode="acceptEdits",
    )

    argv = node._create_transport()._build_command()

    assert flag(argv, "--permission-mode") == "acceptEdits"
    assert "--tools" not in argv
    assert "--allowedTools" not in argv
    assert "--disallowedTools" not in argv


async def test_subprocess_claude_resends_the_persona_on_resume(tmp_path: Path) -> None:
    transport = broker(tmp_path, "subprocess", REVIEWER)._create_transport()
    transport._session_id = "sess-existing"
    transport.on_event(AsyncMock())

    with patch(
        "skuld.transports.subprocess.asyncio.create_subprocess_exec", new_callable=AsyncMock
    ) as spawn:
        spawn.return_value = exec_process()
        await transport.send_message("go")

    argv = list(spawn.call_args[0])
    assert flag(argv, "--resume") == "sess-existing"
    assert "You review code and change nothing." in flag(argv, "--append-system-prompt")
    assert flag(argv, "--tools") == f"{READ_ONLY_TOOLS},Bash"
    assert flag(argv, "--permission-mode") == "dontAsk"


async def test_sdk_claude_appends_to_its_own_prompt_and_runs_deny_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, options: Any) -> None:
            captured["options"] = options

        async def __aenter__(self) -> FakeClient:
            return self

    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", FakeClient)
    transport = broker(
        tmp_path,
        "sdk",
        REVIEWER,
        request_prompt="Mind the migrations.",
        skip_permissions=True,
        agent_teams=True,
    )._create_transport()

    await transport._connect_client()

    options = captured["options"]
    assert options.system_prompt["type"] == "preset"
    assert options.system_prompt["preset"] == "claude_code"
    appended = options.system_prompt["append"]
    assert appended.index("You review code and change nothing.") < appended.index(
        "Mind the migrations."
    )
    assert options.tools == [*READ_ONLY_TOOLS.split(","), "Bash"]
    assert options.permission_mode == "dontAsk"
    assert "Bash(git status *)" in options.allowed_tools
    assert options.strict_mcp_config is True
    assert options.can_use_tool is None
    assert "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS" not in options.env


def test_tmux_claude_gets_the_boundary(tmp_path: Path) -> None:
    transport = broker(tmp_path, "tmux", REVIEWER, skip_permissions=True)._create_transport()

    argv = transport._interactive_argv()

    assert flag(argv, "--tools") == f"{READ_ONLY_TOOLS},Bash"
    assert argv.count("--permission-mode") == 1
    assert flag(argv, "--permission-mode") == "dontAsk"
    assert "You review code and change nothing." in flag(argv, "--append-system-prompt")


async def test_sdk_websocket_claude_gets_the_boundary_and_follows_the_nodes_mode(
    tmp_path: Path,
) -> None:
    closed = broker(tmp_path, "sdk_websocket", REVIEWER, skip_permissions=True)
    open_ = broker(
        tmp_path,
        "sdk_websocket",
        {**OPEN, "permission_mode": "workspace_write"},
        skip_permissions=True,
        claude_permission_mode="acceptEdits",
    )

    argvs = []
    for node in (closed, open_):
        transport = node._create_transport()
        with patch(
            "skuld.transports.sdk_websocket.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as spawn:
            spawn.return_value = MagicMock(stdout=None, stderr=None, pid=1)
            await transport.start()
        argvs.append(list(spawn.call_args[0]))

    assert argvs[0].count("--permission-mode") == 1
    assert flag(argvs[0], "--permission-mode") == "dontAsk"
    assert flag(argvs[0], "--tools") == f"{READ_ONLY_TOOLS},Bash"
    assert flag(argvs[1], "--permission-mode") == "acceptEdits"
    assert "--tools" not in argvs[1]


# ----------------------------------------------------------------------- codex


async def test_codex_app_server_sends_developer_instructions_on_start_and_on_resume(
    tmp_path: Path,
) -> None:
    for resume in ("", "thread-9"):
        transport = broker(
            tmp_path,
            "codex_ws",
            {**OPEN, "permission_mode": "read_only"},
            request_prompt="Be brief.",
            resume=resume,
        )._create_transport()
        transport._ws = MagicMock()
        transport._alive = True
        sent: list[tuple[str, dict]] = []

        async def rpc(method: str, params: dict | None = None, *, sent=sent, resume=resume):
            sent.append((method, params or {}))
            if method == "thread/resume":
                return {"thread": {"id": resume}}
            return {"thread": {"id": "t-1"}}

        transport._send_rpc = rpc
        transport._send_notification = AsyncMock()
        transport._authenticate_codex = AsyncMock()
        transport._register_session_skills = AsyncMock()
        transport.on_event(AsyncMock())

        await transport._handshake()

        method, params = sent[-1]
        assert method == ("thread/resume" if resume else "thread/start")
        instructions = params["developerInstructions"]
        assert instructions.index("You help.") < instructions.index("Be brief.")
        assert "baseInstructions" not in params
        assert params["sandbox"] == "read-only"
        assert params["approvalPolicy"] == "never"


async def test_codex_exec_carries_the_persona_in_every_invocation(tmp_path: Path) -> None:
    transport = broker(
        tmp_path,
        "codex",
        {
            "name": "ro",
            "system_prompt_template": "You inspect.",
            "permission_mode": "read_only",
        },
        request_prompt="Be brief.",
    )._create_transport()
    transport.on_event(AsyncMock())

    with (
        patch(
            "skuld.transports.codex.asyncio.create_subprocess_exec", new_callable=AsyncMock
        ) as spawn,
        patch("skuld.transports.codex.resolve_codex_cli", return_value="/bin/codex"),
        patch("skuld.transports.codex.ensure_codex_tool_shims", return_value=(None, {})),
    ):
        spawn.return_value = exec_process()
        await transport.send_message("go")

    argv = list(spawn.call_args[0])
    assert flag(argv, "--sandbox") == "read-only"
    assert "approval_policy='never'" in argv
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv
    developer = next(a for a in argv if a.startswith("developer_instructions="))
    assert developer.index("You inspect.") < developer.index("Be brief.")


def test_codex_refuses_the_modes_it_cannot_enforce(tmp_path: Path) -> None:
    node = broker(tmp_path, "codex_ws", {"name": "c", "permission_mode": "deny_all"})

    with pytest.raises(PersonaProjectionError, match="permission_mode:deny_all"):
        node._create_transport()


def test_codex_app_server_runs_a_workspace_write_persona_in_the_workspace_sandbox(
    tmp_path: Path,
) -> None:
    transport = broker(
        tmp_path, "codex_ws", {"name": "w", "permission_mode": "workspace_write"}
    )._create_transport()

    assert transport._permission_thread_params() == {
        "approvalPolicy": "never",
        "sandbox": "workspace-write",
    }


# ----------------------------------------------------------- instruction-only


@pytest.mark.parametrize(
    ("adapter", "attribute"),
    [
        ("opencode", "_system_prompt"),
        ("grok", "_system_prompt"),
        ("pi", "_system"),
        ("dsh", "_system_prompt"),
    ],
)
def test_instruction_only_harnesses_get_the_persona_then_the_request(
    tmp_path: Path, adapter: str, attribute: str
) -> None:
    transport = broker(tmp_path, adapter, OPEN, request_prompt="Be brief.")._create_transport()

    assert getattr(transport, attribute) == "You help.\n\nBe brief."


@pytest.mark.parametrize("adapter", ["opencode", "grok", "pi", "dsh", "muse"])
def test_a_read_only_persona_never_reaches_a_harness_that_could_write(
    tmp_path: Path, adapter: str
) -> None:
    node = broker(tmp_path, adapter, REVIEWER)

    with pytest.raises(PersonaProjectionError, match="cannot run on"):
        node._create_transport()


def test_muse_cannot_take_a_persona_with_instructions(tmp_path: Path) -> None:
    with pytest.raises(PersonaProjectionError, match="Muse MSP cannot apply system instructions"):
        broker(tmp_path, "muse", OPEN)._create_transport()


# ------------------------------------------------------- composition and files


def test_a_session_without_a_persona_is_built_exactly_as_before(tmp_path: Path) -> None:
    node = broker(tmp_path, "persistent", None, request_prompt="Plain.")

    transport = node._create_transport()

    assert transport._tool_policy is None
    assert transport._system_prompt == "Plain."
    assert not (node._session_runtime.root / "persona.json").exists()
    assert node.startup_status().get("persona") is None


def test_persona_files_exist_only_in_the_per_start_runtime_dir(tmp_path: Path) -> None:
    node = broker(tmp_path, "persistent", REVIEWER)

    node._create_transport()

    recorded = node._session_runtime.root / "persona.json"
    assert recorded.is_file()
    assert stat.S_IMODE(recorded.stat().st_mode) == 0o600
    assert recorded.parent == tmp_path / "rt"
    assert "dontAsk" in recorded.read_text()
    everything = {path.name for path in tmp_path.rglob("persona*")}
    assert everything == {"persona.json"}
    assert not list((tmp_path / "ws").rglob("persona*"))


def test_the_status_says_what_was_projected_and_what_was_only_stated(tmp_path: Path) -> None:
    node = broker(tmp_path, "persistent", {**OPEN, "permission_mode": "workspace_write"})

    node._create_transport()

    status = node.startup_status()["persona"]
    assert status["id"] == "open"
    assert status["revision"] == "content-0123456789abcdef"
    assert status["engine"] == "claude"
    assert status["projected"] == ["system_prompt"]
    assert status["notes"] == [
        "permission_mode:workspace_write: Claude cannot confine a shell or an edit tool to the "
        "workspace; the session runtime is the boundary"
    ]


def test_the_projection_is_the_same_on_every_start(tmp_path: Path) -> None:
    first = broker(tmp_path, "persistent", REVIEWER, request_prompt="x")
    second = broker(tmp_path, "persistent", REVIEWER, request_prompt="x")

    a = first._create_transport()
    b = second._create_transport()

    assert a._tool_policy == b._tool_policy
    assert a._system_prompt == b._system_prompt
    assert a._raw_mcp_servers == b._raw_mcp_servers


class Narrow:
    """A transport whose constructor takes a prompt but none of a persona's limits."""

    def __init__(self, workspace_dir: str, system_prompt: str = "") -> None:
        self.workspace_dir = workspace_dir


def test_a_projected_argument_the_transport_does_not_accept_stops_the_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = f"{__name__}.Narrow"
    monkeypatch.setattr(
        registry, "ENGINE_BY_ADAPTER", {**registry.ENGINE_BY_ADAPTER, path: "claude"}
    )
    monkeypatch.setitem(ADAPTERS, "narrow", path)
    node = broker(tmp_path, "narrow", REVIEWER)

    with pytest.raises(PersonaProjectionError, match="claude_tool_policy.*does not accept"):
        node._create_transport()


def test_a_transport_with_no_projector_cannot_play_a_persona(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(ADAPTERS, "unregistered", ADAPTERS["persistent"])
    monkeypatch.setattr(registry, "ENGINE_BY_ADAPTER", {})

    with pytest.raises(PersonaProjectionError, match="No persona projector"):
        broker(tmp_path, "unregistered", OPEN)._create_transport()


async def test_a_refused_persona_fails_startup_and_leaves_the_diagnostics_up(
    tmp_path: Path,
) -> None:
    node = broker(tmp_path, "persistent", {**OPEN, "permission_mode": "prompt"})

    await node.startup()

    status = node.startup_status()
    assert status["ready"] is False
    assert status["startup_state"] == "failed"
    assert "permission_mode:prompt" in status["error"]
    assert "PersonaProjectionError" in status["error"]
    assert node._transport is None


async def test_a_refused_persona_is_reported_to_forge_as_a_failed_start(
    tmp_path: Path,
) -> None:
    node = broker(tmp_path, "persistent", {**OPEN, "permission_mode": "prompt"})
    node.volundr_api_url = "http://forge.test"
    node._post_activity_payload = AsyncMock(return_value=True)  # type: ignore[method-assign]

    await node.startup()
    for _ in range(5):
        await asyncio.sleep(0)

    reports = [call.args[0] for call in node._post_activity_payload.await_args_list]
    (failed,) = [report for report in reports if report["state"] == "error"]
    assert failed["metadata"]["startup_failed"] is True
    assert failed["metadata"]["error"] == node.startup_status()["error"]
    assert "permission_mode:prompt" in failed["metadata"]["error"]


async def test_a_started_broker_reports_no_failed_start(tmp_path: Path) -> None:
    node = broker(tmp_path, "persistent", OPEN)
    node.volundr_api_url = "http://forge.test"
    node._post_activity_payload = AsyncMock(return_value=True)  # type: ignore[method-assign]

    with patch.object(node, "_auto_start_transport", AsyncMock()):
        await node.startup()
    for _ in range(5):
        await asyncio.sleep(0)

    reports = [call.args[0] for call in node._post_activity_payload.await_args_list]
    assert not [r for r in reports if r["metadata"].get("startup_failed")]


async def test_a_tampered_persona_document_fails_startup(tmp_path: Path) -> None:
    node = broker(tmp_path, "persistent", OPEN)
    node._settings.session.persona["definition"]["allowed_tools"] = ["file_read"]

    await node.startup()

    status = node.startup_status()
    assert status["ready"] is False
    assert "persona open: allowed_tools is not supported" in status["error"]
    assert "per-harness tool restrictions are not available yet" in status["error"]


def test_the_class_the_policy_travels_in_is_a_plain_value() -> None:
    policy = ClaudeToolPolicy(tools=("Read",), allowed=("Read",))

    assert policy == ClaudeToolPolicy(tools=("Read",), allowed=("Read",))
    assert policy.permission_mode == "dontAsk"
