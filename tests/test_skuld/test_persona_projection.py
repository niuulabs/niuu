"""A persona projected onto each engine family, and what each harness refuses.

The projectors are pure functions of the pinned persona, the request's ad-hoc prompt and the
node's configuration, so these tests build a persona and a context and read the result.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from personas.adapters.loader import FilesystemPersonaAdapter
from personas.domain.persona_document import PersonaDocumentError, PortablePersonaDefinition
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
READ_ONLY_TOOLS = (
    "Read",
    "Glob",
    "Grep",
    "WebFetch",
    "WebSearch",
    "TodoWrite",
    "TaskCreate",
    "TaskUpdate",
    "TaskGet",
    "TaskList",
)


def persona(**definition: Any) -> PortablePersonaDefinition:
    return PortablePersonaDefinition(
        id="p", revision="content-0123456789abcdef", definition={"name": "p", **definition}
    )


def context(tmp_path: Path, transport: str = SDK, **fields: Any) -> ProjectionContext:
    defaults: dict[str, Any] = {
        "transport": transport,
        "runtime_dir": tmp_path,
        "mcp_servers": (MIMIR, LINEAR, FORGE),
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
    projection = claude(tmp_path, system_prompt_template="Hello.", permission_mode="read_only")

    [recorded] = projection.files
    assert recorded.name == PERSONA_FILE
    assert '"claude_tool_policy"' in recorded.content
    assert "Hello." in recorded.content


@pytest.mark.parametrize("name", ["../escape.json", "sub/dir.json", ".hidden", ""])
def test_a_projected_file_can_only_be_a_plain_name_in_the_runtime_dir(name: str) -> None:
    with pytest.raises(ValueError, match="plain file name"):
        ProjectedFile(name, "x")


@pytest.mark.parametrize("key", ["allowed_tools", "forbidden_tools"])
def test_a_persona_with_a_tool_restriction_cannot_reach_a_projector(key: str) -> None:
    """Skuld builds the pinned document before projecting it, and the document refuses."""
    with pytest.raises(PersonaDocumentError, match=f"persona p: {key} is not supported"):
        persona(**{key: ["file_read"]})


# ---------------------------------------------------------------------- claude


def test_a_read_only_persona_is_a_closed_boundary_that_can_only_read(tmp_path: Path) -> None:
    projection = claude(
        tmp_path, TMUX, system_prompt_template="Review.", permission_mode="read_only"
    )

    policy = projection.kwargs["claude_tool_policy"]
    assert isinstance(policy, ClaudeToolPolicy)
    assert policy.tools == (*READ_ONLY_TOOLS, "Bash")
    assert policy.permission_mode == CLOSED_PERMISSION_MODE == "dontAsk"
    assert {"Read", "Glob", "Grep"} <= set(policy.allowed)
    assert {"mcp__mimir-wiki", "mcp__linear", "mcp__forge"} <= set(policy.allowed)
    assert "Bash(git diff *)" in policy.allowed
    assert "Bash(git log)" in policy.allowed
    assert "Bash(present-file *)" in policy.allowed
    assert "Bash(mimir_search *)" in policy.allowed
    assert "Bash(tracker_issue)" in policy.allowed
    assert "Bash" not in policy.allowed
    assert not {"Write", "Edit", "NotebookEdit"} & set(policy.allowed)
    assert not {"Write", "Edit", "NotebookEdit"} & set(policy.tools or ())
    assert not any(rule.startswith("Bash(git commit") for rule in policy.allowed)
    assert not projection.refusals
    assert not projection.notes


def test_a_read_only_persona_is_run_deny_by_default_on_the_cli_and_in_the_sdk(
    tmp_path: Path,
) -> None:
    policy = claude(tmp_path, permission_mode="read_only").kwargs["claude_tool_policy"]

    args = policy.cli_args()
    assert args[:4] == [
        "--tools",
        ",".join((*READ_ONLY_TOOLS, "Bash")),
        "--strict-mcp-config",
        "--permission-mode",
    ]
    assert args[4] == "dontAsk"
    assert args[5] == "--allowedTools"
    options = policy.sdk_options()
    assert options["tools"] == [*READ_ONLY_TOOLS, "Bash"]
    assert options["strict_mcp_config"] is True
    assert options["permission_mode"] == "dontAsk"
    assert "Read" in options["allowed_tools"]


def test_read_only_keeps_only_the_session_servers_it_can_name(tmp_path: Path) -> None:
    projection = ClaudeProjector().project(
        persona(permission_mode="read_only"),
        context(tmp_path, SDK, mcp_servers=(FORGE,)),
    )

    policy = projection.kwargs["claude_tool_policy"]
    assert "mcp__forge" in policy.allowed
    assert not any(rule.startswith("mcp__linear") for rule in policy.allowed)


def test_workspace_write_adds_no_boundary_and_the_workspace_limit_is_stated(
    tmp_path: Path,
) -> None:
    projection = claude(tmp_path, permission_mode="workspace_write")

    assert set(projection.kwargs) == {"system_prompt"}
    assert [gap.subject for gap in projection.notes] == ["permission_mode:workspace_write"]
    assert not projection.refusals


def test_deny_all_offers_nothing_and_approves_nothing(tmp_path: Path) -> None:
    projection = claude(tmp_path, TMUX, permission_mode="deny_all")

    policy = projection.kwargs["claude_tool_policy"]
    assert policy.tools == ()
    assert policy.allowed == ()
    assert policy.permission_mode == "dontAsk"
    assert projection.kwargs["mcp_servers"] == []
    assert policy.cli_args()[:2] == ["--tools", ""]


def test_claude_refuses_the_prompt_permission_mode(tmp_path: Path) -> None:
    projection = claude(tmp_path, permission_mode="prompt")

    assert refusals(projection) == ["permission_mode:prompt"]
    with pytest.raises(PersonaProjectionError, match="permission_mode:prompt"):
        projection.require_playable("p", SDK)


def test_a_refusal_names_the_permission_mode_as_the_thing_to_change(tmp_path: Path) -> None:
    projection = claude(tmp_path, permission_mode="prompt")

    with pytest.raises(PersonaProjectionError) as raised:
        projection.require_playable("p", SDK)

    assert "change the persona's permission_mode" in str(raised.value)
    assert "allowed_tools" not in str(raised.value)


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
    projection = codex(tmp_path, permission_mode="read_only")

    assert projection.kwargs["sandbox"] == "read-only"
    assert projection.kwargs["approval_policy"] == "never"
    assert projection.kwargs["skip_permissions"] is False
    assert not projection.gaps


def test_a_workspace_write_persona_runs_in_codex_workspace_sandbox(tmp_path: Path) -> None:
    projection = codex(tmp_path, permission_mode="workspace_write")

    assert projection.kwargs["sandbox"] == "workspace-write"
    assert not projection.refusals


def test_a_persona_without_a_mode_leaves_codex_as_the_node_configured_it(tmp_path: Path) -> None:
    projection = codex(tmp_path)

    assert projection.kwargs == {"system_prompt": ""}
    assert not projection.gaps


def test_codex_sandbox_is_only_ever_narrowed_against_the_node(tmp_path: Path) -> None:
    wide = CodexProjector().project(
        persona(permission_mode="workspace_write"),
        context(tmp_path, CODEX_WS, node_skip_permissions=True),
    )
    narrow = CodexProjector().project(
        persona(permission_mode="workspace_write"),
        context(tmp_path, CODEX_WS, node_sandbox="read-only", node_approval_policy="untrusted"),
    )

    assert wide.kwargs["sandbox"] == "workspace-write"
    assert narrow.kwargs["sandbox"] == "read-only"
    assert narrow.kwargs["approval_policy"] == "untrusted"
    assert narrower_sandbox("", "workspace-write") == "workspace-write"
    assert narrower_sandbox("danger-full-access", "workspace-write") == "workspace-write"
    assert narrower_sandbox("read-only", "workspace-write") == "read-only"


@pytest.mark.parametrize(
    ("transport", "definition", "subject"),
    [
        (CODEX_WS, {"permission_mode": "prompt"}, "permission_mode:prompt"),
        (CODEX_WS, {"permission_mode": "deny_all"}, "permission_mode:deny_all"),
        (CODEX_EXEC, {"permission_mode": "prompt"}, "permission_mode:prompt"),
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
        ({"permission_mode": "read_only"}, "permission_mode:read_only"),
        ({"permission_mode": "prompt"}, "permission_mode:prompt"),
        ({"permission_mode": "deny_all"}, "permission_mode:deny_all"),
    ],
)
def test_harnesses_that_enforce_nothing_refuse_every_restricting_mode(
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
    source = FilesystemPersonaAdapter(persona_dirs=[], include_builtin=True)
    documents = [source.load_current_portable(name) for name in source.list_builtin_names()]
    return [document for document in documents if document is not None]


def test_every_builtin_persona_projects_onto_claude_without_a_refusal(tmp_path: Path) -> None:
    for document in _builtins():
        for transport in (SDK, TMUX):
            projection = ClaudeProjector().project(document, context(tmp_path, transport))
            assert not projection.refusals, (document.id, transport, projection.refusals)


def test_every_builtin_persona_projects_onto_codex_without_a_refusal(tmp_path: Path) -> None:
    for document in _builtins():
        for transport in (CODEX_WS, CODEX_EXEC):
            projection = CodexProjector().project(document, context(tmp_path, transport))
            assert not projection.refusals, (document.id, transport, projection.refusals)


def test_no_read_only_builtin_gets_a_write_tool_on_claude(tmp_path: Path) -> None:
    read_only = [d for d in _builtins() if d.definition.get("permission_mode") == "read_only"]
    assert read_only
    for document in read_only:
        projection = ClaudeProjector().project(document, context(tmp_path, TMUX))
        policy = projection.kwargs["claude_tool_policy"]
        assert not {"Write", "Edit", "NotebookEdit"} & set(policy.tools or ()), document.id
        assert "Bash" not in policy.allowed, document.id
        assert policy.permission_mode == "dontAsk", document.id


def test_a_read_only_builtin_is_never_run_on_a_harness_that_could_write(tmp_path: Path) -> None:
    auditor = next(d for d in _builtins() if d.id == "security-auditor")

    for engine in ("opencode", "grok", "pi", "dsh", "muse"):
        projection = PROJECTORS[engine].project(auditor, context(tmp_path))
        assert refusals(projection), engine
    codex_projection = CodexProjector().project(auditor, context(tmp_path, CODEX_WS))
    assert codex_projection.kwargs["sandbox"] == "read-only"
    assert not codex_projection.refusals
