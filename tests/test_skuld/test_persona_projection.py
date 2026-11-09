"""A persona projected onto each engine family, and what each harness refuses.

The projectors are pure functions of the pinned persona, the request's ad-hoc prompt and the
node's configuration, so these tests build a persona and a context and read the result.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from personas.adapters.loader import FilesystemPersonaAdapter
from personas.domain.persona_document import PortablePersonaDefinition
from skuld.personas.claude import CLOSED_PERMISSION_MODE, ClaudeProjector
from skuld.personas.codex import CodexProjector, narrower_sandbox
from skuld.personas.instruction_only import InstructionOnlyProjector
from skuld.personas.projection import (
    PERSONA_FILE,
    PersonaProjectionError,
    ProjectedFile,
    ProjectionContext,
    compose_instructions,
)
from skuld.personas.registry import ENGINE_BY_ADAPTER, PROJECTORS, projector_for
from skuld.transports.claude_policy import ClaudeToolPolicy

SDK = "skuld.transports.sdk.SDKTransport"
TMUX = "skuld.transports.tmux_interactive.TmuxInteractiveTransport"
CODEX_WS = "skuld.transports.codex_ws.CodexWebSocketTransport"
CODEX_EXEC = "skuld.transports.codex.CodexSubprocessTransport"

MIMIR = {"name": "mimir-wiki", "type": "stdio", "command": "mimir"}
LINEAR = {"name": "linear", "type": "http", "url": "https://mcp.example/linear"}
FORGE = {"name": "forge", "type": "stdio", "command": "forge"}


def persona(**definition: Any) -> PortablePersonaDefinition:
    return PortablePersonaDefinition(
        id="p", revision="content-0123456789abcdef", definition={"name": "p", **definition}
    )


def context(tmp_path: Path, transport: str = SDK, **fields: Any) -> ProjectionContext:
    defaults: dict[str, Any] = {
        "transport": transport,
        "runtime_dir": tmp_path,
        "mcp_servers": (MIMIR, LINEAR, FORGE),
        "platform_mcp_servers": ("forge",),
        "supports_read_only_mcp_boundary": transport in (SDK, CODEX_WS),
    }
    return ProjectionContext(**{**defaults, **fields})


def claude(tmp_path: Path, transport: str = SDK, **definition: Any):
    return ClaudeProjector().project(persona(**definition), context(tmp_path, transport))


def codex(tmp_path: Path, transport: str = CODEX_WS, **definition: Any):
    return CodexProjector().project(persona(**definition), context(tmp_path, transport))


def refusals(projection) -> list[str]:
    return [gap.subject for gap in projection.refusals]


# ---------------------------------------------------------------- instructions


def test_the_persona_is_the_base_and_the_request_prompt_is_appended() -> None:
    definition = {"system_prompt_template": "You review code."}

    composed = compose_instructions(definition, "Focus on the migrations.")

    assert composed == "You review code.\n\nFocus on the migrations."
    assert compose_instructions(definition, "") == "You review code."
    assert compose_instructions({}, "Only the request.") == "Only the request."


def test_the_outcome_block_the_persona_implies_is_part_of_its_instructions() -> None:
    definition = {
        "system_prompt_template": "Review.",
        "produces": {
            "event_type": "review.completed",
            "schema": {"verdict": {"type": "enum", "values": ["pass", "fail"]}},
        },
    }

    composed = compose_instructions(definition, "Be brief.")

    assert composed.startswith("Review.\n\n")
    assert "---outcome---" in composed
    assert composed.endswith("Be brief.")
    assert composed.count("---outcome---") == compose_instructions(
        {**definition, "system_prompt_template": composed}, ""
    ).count("---outcome---")


def test_a_persona_without_limits_projects_only_its_instructions(tmp_path: Path) -> None:
    projection = claude(tmp_path, system_prompt_template="Hello.")

    assert projection.kwargs == {"system_prompt": "Hello."}
    assert not projection.gaps


def test_the_projection_records_itself_in_a_plain_file_for_the_runtime_dir(tmp_path: Path) -> None:
    projection = claude(tmp_path, system_prompt_template="Hello.", allowed_tools=["file_read"])

    [recorded] = projection.files
    assert recorded.name == PERSONA_FILE
    assert '"claude_tool_policy"' in recorded.content
    assert "Hello." in recorded.content


@pytest.mark.parametrize("name", ["../escape.json", "sub/dir.json", ".hidden", ""])
def test_a_projected_file_can_only_be_a_plain_name_in_the_runtime_dir(name: str) -> None:
    with pytest.raises(ValueError, match="plain file name"):
        ProjectedFile(name, "x")


# ---------------------------------------------------------------------- claude


def test_a_read_only_reviewer_is_a_closed_boundary_that_can_only_read(tmp_path: Path) -> None:
    projection = claude(
        tmp_path,
        TMUX,
        system_prompt_template="Review.",
        allowed_tools=["file_read", "git_read"],
        forbidden_tools=["file_write", "shell", "git"],
        permission_mode="read_only",
    )

    policy = projection.kwargs["claude_tool_policy"]
    assert isinstance(policy, ClaudeToolPolicy)
    assert policy.tools == ("Read", "Glob", "Grep", "Bash")
    assert policy.permission_mode == CLOSED_PERMISSION_MODE == "dontAsk"
    assert {"Read", "Glob", "Grep", "mcp__forge"} <= set(policy.allowed)
    assert "Bash(git diff *)" in policy.allowed
    assert "Bash(git log)" in policy.allowed
    assert "Bash" not in policy.allowed
    assert not {"Write", "Edit"} & set(policy.allowed)
    assert not projection.refusals
    # Only the platform server stays: the persona grants no other.
    assert [server["name"] for server in projection.kwargs["mcp_servers"]] == ["forge"]


def test_a_closed_persona_is_run_deny_by_default_on_the_cli_and_in_the_sdk(tmp_path: Path) -> None:
    policy = claude(tmp_path, allowed_tools=["file_read"]).kwargs["claude_tool_policy"]

    assert policy.cli_args() == [
        "--tools",
        "Read,Glob,Grep",
        "--strict-mcp-config",
        "--permission-mode",
        "dontAsk",
        "--allowedTools",
        "Read,Glob,Grep,mcp__forge",
    ]
    assert policy.sdk_options() == {
        "tools": ["Read", "Glob", "Grep"],
        "strict_mcp_config": True,
        "permission_mode": "dontAsk",
        "allowed_tools": ["Read", "Glob", "Grep", "mcp__forge"],
    }


def test_read_only_takes_write_tools_away_even_when_the_persona_lists_them(
    tmp_path: Path,
) -> None:
    projection = claude(
        tmp_path,
        allowed_tools=["file_read", "file_write", "shell", "git"],
        permission_mode="read_only",
    )

    policy = projection.kwargs["claude_tool_policy"]
    assert policy.tools == ("Read", "Glob", "Grep")
    assert not {"Write", "Edit", "Bash"} & set(policy.allowed)
    stated = {gap.subject for gap in projection.notes}
    assert {"allowed_tools:file_write", "allowed_tools:shell", "allowed_tools:git"} <= stated


def test_a_coder_gets_a_shell_and_edit_tools_and_the_workspace_boundary_is_stated(
    tmp_path: Path,
) -> None:
    projection = claude(
        tmp_path,
        allowed_tools=["file_read", "file_write", "shell", "git", "web_fetch", "plan"],
        permission_mode="workspace_write",
    )

    policy = projection.kwargs["claude_tool_policy"]
    assert {"Read", "Write", "Edit", "Bash", "WebFetch", "TodoWrite"} <= set(policy.tools or ())
    assert "Bash" in policy.allowed
    assert [gap.subject for gap in projection.notes] == ["permission_mode:workspace_write"]


def test_command_capabilities_without_a_shell_are_exact_command_rules(tmp_path: Path) -> None:
    projection = claude(
        tmp_path, allowed_tools=["present_file", "mimir:search", "tracker"], permission_mode=""
    )

    policy = projection.kwargs["claude_tool_policy"]
    assert policy.tools == ("Bash",)
    assert "Bash(present-file *)" in policy.allowed
    assert "Bash(mimir_search *)" in policy.allowed
    assert "Bash(tracker_issue)" in policy.allowed
    assert "Bash(mimir_write *)" not in policy.allowed
    assert "Bash" not in policy.allowed


def test_a_persona_with_only_mcp_tools_uses_the_mcp_only_boundary_where_it_exists(
    tmp_path: Path,
) -> None:
    projection = claude(
        tmp_path,
        SDK,
        allowed_tools=["mcp:linear:create_issue"],
        forbidden_tools=["shell", "file_read", "file_write", "git", "web_fetch"],
        permission_mode="workspace_write",
    )

    assert projection.kwargs["read_only_mcp_only"] is True
    assert set(projection.kwargs["allowed_mcp_tools"]) == {
        "mcp__linear__create_issue",
        "mcp__forge",
    }
    assert "claude_tool_policy" not in projection.kwargs
    assert sorted(server["name"] for server in projection.kwargs["mcp_servers"]) == [
        "forge",
        "linear",
    ]


def test_the_same_persona_elsewhere_is_a_policy_with_no_built_in_tools(tmp_path: Path) -> None:
    projection = claude(tmp_path, TMUX, allowed_tools=["mimir:search"])

    policy = projection.kwargs["claude_tool_policy"]
    assert policy.tools == ("Bash",)
    assert "mcp__mimir-wiki__mimir_search" in policy.allowed
    assert "read_only_mcp_only" not in projection.kwargs

    nothing = claude(tmp_path, TMUX, allowed_tools=["mcp:linear:create_issue"])
    assert nothing.kwargs["claude_tool_policy"].tools == ()
    assert nothing.kwargs["claude_tool_policy"].cli_args()[:2] == ["--tools", ""]


def test_a_forbidden_mcp_tool_inside_a_granted_server_is_denied(tmp_path: Path) -> None:
    projection = claude(
        tmp_path,
        TMUX,
        allowed_tools=["mcp:linear"],
        forbidden_tools=["mcp:linear:delete_issue"],
    )

    policy = projection.kwargs["claude_tool_policy"]
    assert "mcp__linear" in policy.allowed
    assert policy.disallowed == ("mcp__linear__delete_issue",)


def test_an_mcp_server_the_session_does_not_have_is_stated_not_invented(tmp_path: Path) -> None:
    projection = claude(tmp_path, TMUX, allowed_tools=["file_read", "mcp:jira"])

    assert [gap.subject for gap in projection.notes] == ["allowed_tools:mcp:jira"]
    assert "mcp__jira" not in projection.kwargs["claude_tool_policy"].allowed


def test_a_persona_that_only_forbids_tools_keeps_the_nodes_mode(tmp_path: Path) -> None:
    projection = claude(tmp_path, forbidden_tools=["web_search", "file_write", "mcp:linear"])

    policy = projection.kwargs["claude_tool_policy"]
    assert policy.permission_mode is None
    assert policy.tools is None
    assert not policy.sets_permission_mode
    assert set(policy.disallowed) == {
        "WebSearch",
        "Write",
        "Edit",
        "NotebookEdit",
        "mcp__linear",
    }
    assert [server["name"] for server in projection.kwargs["mcp_servers"]] == [
        "mimir-wiki",
        "forge",
    ]
    assert policy.cli_args() == [
        "--strict-mcp-config",
        "--disallowedTools",
        "WebSearch,Write,Edit,NotebookEdit,mcp__linear",
    ]


def test_deny_all_offers_nothing_and_approves_nothing(tmp_path: Path) -> None:
    projection = claude(tmp_path, TMUX, permission_mode="deny_all")

    policy = projection.kwargs["claude_tool_policy"]
    assert policy.tools == ()
    assert policy.allowed == ()
    assert projection.kwargs["mcp_servers"] == []


@pytest.mark.parametrize(
    ("definition", "subject"),
    [
        ({"permission_mode": "prompt"}, "permission_mode:prompt"),
        (
            {"allowed_tools": ["shell", "file_read"], "forbidden_tools": ["git"]},
            "forbidden_tools:git",
        ),
        ({"forbidden_tools": ["mimir:ingest"]}, "forbidden_tools:mimir:ingest"),
    ],
)
def test_claude_refuses_what_it_cannot_enforce(
    tmp_path: Path, definition: dict[str, Any], subject: str
) -> None:
    projection = claude(tmp_path, **definition)

    assert subject in refusals(projection)
    with pytest.raises(PersonaProjectionError, match=subject):
        projection.require_playable("p", SDK)


def test_forbidding_git_is_fine_when_no_shell_could_run_it(tmp_path: Path) -> None:
    projection = claude(
        tmp_path, allowed_tools=["file_read", "git_read"], forbidden_tools=["git", "shell"]
    )

    assert not projection.refusals


# ----------------------------------------------------------------------- codex


def test_codex_instructions_are_the_persona_then_the_request(tmp_path: Path) -> None:
    projection = CodexProjector().project(
        persona(system_prompt_template="Review."),
        context(tmp_path, CODEX_WS, request_prompt="Be brief."),
    )

    assert projection.kwargs == {"system_prompt": "Review.\n\nBe brief."}


def test_a_read_only_persona_runs_in_codex_read_only_sandbox_with_approvals_off(
    tmp_path: Path,
) -> None:
    projection = codex(
        tmp_path,
        allowed_tools=["file_read", "shell", "git_read", "web_search"],
        permission_mode="read_only",
    )

    assert projection.kwargs["sandbox"] == "read-only"
    assert projection.kwargs["approval_policy"] == "never"
    assert projection.kwargs["skip_permissions"] is False
    assert "shell_tool_enabled" not in projection.kwargs
    assert not projection.refusals


def test_codex_sandbox_is_only_ever_narrowed_against_the_node(tmp_path: Path) -> None:
    wide = CodexProjector().project(
        persona(allowed_tools=["shell"], permission_mode="workspace_write"),
        context(tmp_path, CODEX_WS, node_skip_permissions=True),
    )
    narrow = CodexProjector().project(
        persona(allowed_tools=["shell", "file_write"], permission_mode="workspace_write"),
        context(tmp_path, CODEX_WS, node_sandbox="read-only", node_approval_policy="untrusted"),
    )

    assert wide.kwargs["sandbox"] == "read-only"  # no file_write: narrower than the persona's mode
    assert narrow.kwargs["sandbox"] == "read-only"
    assert narrow.kwargs["approval_policy"] == "untrusted"
    assert narrower_sandbox("", "workspace-write") == "workspace-write"
    assert narrower_sandbox("danger-full-access", "workspace-write") == "workspace-write"
    assert narrower_sandbox("read-only", "workspace-write") == "read-only"


def test_a_persona_with_no_file_write_cannot_change_files_through_codex(tmp_path: Path) -> None:
    projection = codex(tmp_path, allowed_tools=["shell", "file_read"], permission_mode="")

    assert projection.kwargs["sandbox"] == "read-only"
    assert [gap.subject for gap in projection.notes] == ["allowed_tools:file_write"]


def test_an_mcp_only_persona_has_no_shell_no_web_and_only_its_servers(tmp_path: Path) -> None:
    projection = codex(
        tmp_path,
        allowed_tools=["mimir:search", "mimir:read"],
        forbidden_tools=["shell", "file_read", "file_write", "web_search", "web_fetch"],
        permission_mode="workspace_write",
    )

    assert projection.kwargs["shell_tool_enabled"] is False
    assert projection.kwargs["web_search_enabled"] is False
    assert projection.kwargs["read_only_mcp_only"] is True
    assert sorted(server["name"] for server in projection.kwargs["mcp_servers"]) == [
        "forge",
        "mimir-wiki",
    ]
    assert projection.kwargs["sandbox"] == "read-only"
    assert not projection.refusals


def test_codex_has_one_web_tool_and_says_so(tmp_path: Path) -> None:
    projection = codex(tmp_path, allowed_tools=["shell", "web_fetch"])

    assert "web_search_enabled" not in projection.kwargs
    assert "allowed_tools:web_fetch,web_search" in [gap.subject for gap in projection.notes]


@pytest.mark.parametrize(
    ("transport", "definition", "subject"),
    [
        (CODEX_WS, {"permission_mode": "prompt"}, "permission_mode:prompt"),
        (CODEX_WS, {"permission_mode": "deny_all"}, "permission_mode:deny_all"),
        (
            CODEX_WS,
            {"allowed_tools": ["file_read"], "forbidden_tools": ["shell"]},
            "allowed_tools:file_read",
        ),
        (CODEX_WS, {"allowed_tools": ["git_read"]}, "allowed_tools:git_read"),
        (
            CODEX_WS,
            {"allowed_tools": ["shell"], "forbidden_tools": ["git"]},
            "forbidden_tools:git",
        ),
        (CODEX_EXEC, {"allowed_tools": ["mimir"]}, "allowed_tools:mcp"),
    ],
)
def test_codex_refuses_what_it_cannot_enforce(
    tmp_path: Path, transport: str, definition: dict[str, Any], subject: str
) -> None:
    projection = codex(tmp_path, transport, **definition)

    assert subject in refusals(projection)
    with pytest.raises(PersonaProjectionError, match="cannot run on codex"):
        projection.require_playable("p", transport)


def test_codex_refuses_a_node_sandbox_it_does_not_know(tmp_path: Path) -> None:
    projection = CodexProjector().project(
        persona(permission_mode="read_only"), context(tmp_path, CODEX_WS, node_sandbox="chroot")
    )

    assert refusals(projection) == ["node:sandbox"]


# ------------------------------------------------------ instruction-only engines


@pytest.mark.parametrize("engine", ["opencode", "pi", "dsh"])
def test_instruction_only_engines_append_the_instructions(tmp_path: Path, engine: str) -> None:
    projection = PROJECTORS[engine].project(
        persona(system_prompt_template="Be a poet."),
        context(tmp_path, request_prompt="In haiku."),
    )

    assert projection.kwargs == {"system_prompt": "Be a poet.\n\nIn haiku."}
    assert not projection.gaps


def test_grok_states_that_its_instructions_are_unverified(tmp_path: Path) -> None:
    projection = PROJECTORS["grok"].project(
        persona(system_prompt_template="Be a poet."), context(tmp_path)
    )

    assert projection.kwargs == {"system_prompt": "Be a poet."}
    assert [gap.subject for gap in projection.notes] == ["system_prompt_template"]
    assert not projection.refusals


@pytest.mark.parametrize("engine", ["opencode", "grok", "pi", "dsh", "muse"])
@pytest.mark.parametrize(
    ("definition", "subject"),
    [
        ({"allowed_tools": ["file_read"]}, "allowed_tools:file_read"),
        ({"forbidden_tools": ["shell"]}, "forbidden_tools:shell"),
        ({"permission_mode": "read_only"}, "permission_mode:read_only"),
        ({"permission_mode": "prompt"}, "permission_mode:prompt"),
        ({"permission_mode": "deny_all"}, "permission_mode:deny_all"),
    ],
)
def test_harnesses_that_enforce_nothing_refuse_every_restriction(
    tmp_path: Path, engine: str, definition: dict[str, Any], subject: str
) -> None:
    projection = PROJECTORS[engine].project(persona(**definition), context(tmp_path))

    assert subject in refusals(projection)


@pytest.mark.parametrize("engine", ["opencode", "grok", "pi", "dsh"])
def test_unrestricted_personas_are_playable_where_instructions_are_taken(
    tmp_path: Path, engine: str
) -> None:
    projection = PROJECTORS[engine].project(
        persona(system_prompt_template="Hi.", permission_mode="workspace_write"),
        context(tmp_path),
    )

    assert not projection.refusals
    assert "permission_mode:workspace_write" in [gap.subject for gap in projection.notes]


@pytest.mark.parametrize("engine", ["muse", "claude-remote-control", "codex-remote-control"])
def test_harnesses_without_instructions_refuse_a_persona_that_has_some(
    tmp_path: Path, engine: str
) -> None:
    projection = PROJECTORS[engine].project(
        persona(system_prompt_template="Hi."), context(tmp_path)
    )

    assert refusals(projection) == ["system_prompt_template"]
    assert "system_prompt" not in projection.kwargs
    assert PROJECTORS[engine].project(persona(), context(tmp_path)).refusals == ()


def test_an_instruction_only_projector_is_a_plain_value() -> None:
    projector = InstructionOnlyProjector("x", delivery="append")

    assert projector.engine == "x"


# -------------------------------------------------------------------- registry


def test_every_transport_adapter_has_exactly_one_projector() -> None:
    for adapter, engine in ENGINE_BY_ADAPTER.items():
        assert projector_for(adapter) is PROJECTORS[engine]
    assert set(ENGINE_BY_ADAPTER.values()) <= set(PROJECTORS)


def test_every_registered_adapter_is_a_real_transport() -> None:
    from niuu.utils import import_class

    for adapter in ENGINE_BY_ADAPTER:
        import_class(adapter)


def test_an_unknown_transport_cannot_play_a_persona() -> None:
    with pytest.raises(PersonaProjectionError, match="No persona projector.*custom.Transport"):
        projector_for("custom.Transport")


# ------------------------------------------------------------- built-in personas


def _builtins() -> list[PortablePersonaDefinition]:
    # The shipped personas and the archived ones: the archived documents must stay projectable.
    source = FilesystemPersonaAdapter(
        persona_dirs=[str(Path(__file__).resolve().parents[2] / "archive" / "personas")],
        include_builtin=True,
    )
    names = sorted(
        {
            *source.list_builtin_names(),
            *(p.stem for p in Path(source._persona_dirs[0]).glob("*.yaml")),
        }
    )
    documents = [source.load_current_portable(name) for name in names]
    return [document for document in documents if document is not None]


def test_every_builtin_persona_projects_onto_claude_without_a_refusal(tmp_path: Path) -> None:
    for document in _builtins():
        for transport in (SDK, TMUX):
            projection = ClaudeProjector().project(document, context(tmp_path, transport))
            assert not projection.refusals, (document.id, transport, projection.refusals)


def test_no_read_only_builtin_gets_a_write_tool_on_claude(tmp_path: Path) -> None:
    for document in _builtins():
        if document.definition.get("permission_mode") != "read_only":
            continue
        projection = ClaudeProjector().project(document, context(tmp_path, TMUX))
        policy = projection.kwargs["claude_tool_policy"]
        assert not {"Write", "Edit", "NotebookEdit"} & set(policy.tools or ()), document.id
        assert "Bash" not in policy.allowed, document.id
        assert policy.permission_mode == "dontAsk", document.id


def test_a_read_only_builtin_is_never_run_on_a_harness_that_could_write(tmp_path: Path) -> None:
    reviewer = next(d for d in _builtins() if d.id == "developer-code-reviewer")

    for engine in ("opencode", "grok", "pi", "dsh", "muse"):
        projection = PROJECTORS[engine].project(reviewer, context(tmp_path))
        assert refusals(projection), engine
    codex_projection = CodexProjector().project(reviewer, context(tmp_path, CODEX_WS))
    assert codex_projection.kwargs["sandbox"] == "read-only"
    # It forbids the shell it would need to read files on Codex: refused, not widened.
    assert "allowed_tools:file_read,git_read" in refusals(codex_projection)
