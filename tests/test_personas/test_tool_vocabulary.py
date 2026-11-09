"""The neutral tool vocabulary: what a persona may name, and the refusals around it."""

from __future__ import annotations

from pathlib import Path

import pytest

from personas.adapters.loader import FilesystemPersonaAdapter
from personas.domain.permission_mode import PermissionMode, parse_permission_mode
from personas.domain.persona import parse_persona
from personas.domain.persona_document import (
    PersonaDocumentError,
    PortablePersonaDefinition,
    persona_definition_digest,
)
from personas.domain.tool_vocabulary import (
    BUILTIN_TOOLS,
    LEGACY_TOOL_HINTS,
    MIMIR_TOOLS,
    PersonaToolError,
    ToolRef,
    parse_tool,
    validate_tool_names,
)


@pytest.mark.parametrize("name", BUILTIN_TOOLS)
def test_every_builtin_class_parses(name: str) -> None:
    assert parse_tool(name) == ToolRef("builtin", name)


@pytest.mark.parametrize("name", ["present_file", "tracker", "mimir"])
def test_capability_tools_parse(name: str) -> None:
    assert parse_tool(name) == ToolRef("capability", name)


@pytest.mark.parametrize("tool", MIMIR_TOOLS)
def test_a_single_mimir_tool_parses(tool: str) -> None:
    ref = parse_tool(f"mimir:{tool}")
    assert ref == ToolRef("capability", "mimir", tool)
    assert ref.text == f"mimir:{tool}"


def test_mcp_server_and_mcp_tool_parse() -> None:
    assert parse_tool("mcp:linear") == ToolRef("mcp", "linear")
    assert parse_tool("mcp:linear:create_issue") == ToolRef("mcp", "linear", "create_issue")
    assert parse_tool("mcp:linear:create_issue").text == "mcp:linear:create_issue"


@pytest.mark.parametrize(
    "name", ["", "Bash", "mimir:nope", "mcp:", "mcp:bad server", "mcp:a:b:c", "file", 7, None]
)
def test_unknown_names_are_refused_with_the_valid_set(name: object) -> None:
    with pytest.raises(PersonaToolError) as caught:
        parse_tool(name)  # type: ignore[arg-type]
    message = str(caught.value)
    for valid in ("file_read", "shell", "mimir:<", "mcp:<server>:<tool>"):
        assert valid in message


def test_a_retired_runtime_tool_names_its_replacement() -> None:
    with pytest.raises(PersonaToolError, match="use shell"):
        parse_tool("terminal")


def test_a_retired_tool_without_a_counterpart_says_to_remove_it() -> None:
    with pytest.raises(PersonaToolError, match="remove it"):
        parse_tool("a2a_task")


def test_every_hint_points_at_a_name_the_vocabulary_accepts() -> None:
    for retired, replacement in LEGACY_TOOL_HINTS.items():
        with pytest.raises(PersonaToolError):
            parse_tool(retired)
        if replacement is None:
            continue
        for candidate in replacement.replace(" and/or ", ",").split(","):
            parse_tool(candidate.strip())


def test_a_tool_list_error_names_the_field() -> None:
    with pytest.raises(PersonaToolError, match="forbidden_tools"):
        validate_tool_names("forbidden_tools", ["shell", "terminal"])


def test_a_tool_list_must_be_a_list() -> None:
    with pytest.raises(PersonaToolError, match="must be a list"):
        validate_tool_names("allowed_tools", "shell")


def test_the_persona_loader_refuses_an_unknown_tool() -> None:
    with pytest.raises(ValueError, match="Persona 'x'.*Unknown persona tool 'terminal'"):
        parse_persona("name: x\nallowed_tools: [file_read, terminal]\n")


def test_the_persona_loader_refuses_an_unknown_forbidden_tool() -> None:
    with pytest.raises(ValueError, match="forbidden_tools"):
        parse_persona("name: x\nforbidden_tools: [cascade]\n")


def test_the_persona_loader_accepts_the_vocabulary() -> None:
    config = parse_persona(
        "name: x\nallowed_tools: [file_read, 'mimir:search', 'mcp:linear:create_issue']\n"
    )
    assert config is not None
    assert config.allowed_tools == ["file_read", "mimir:search", "mcp:linear:create_issue"]


def _definition(**fields: object) -> dict[str, object]:
    return {"name": "x", **fields}


def test_a_portable_document_refuses_an_unknown_tool() -> None:
    with pytest.raises(PersonaDocumentError, match="Unknown persona tool 'file'"):
        PortablePersonaDefinition(
            id="x", revision="r1", definition=_definition(allowed_tools=["file"])
        )


def test_a_portable_document_refuses_an_unknown_permission_mode() -> None:
    with pytest.raises(PersonaDocumentError, match="Unknown permission_mode"):
        PortablePersonaDefinition(
            id="x", revision="r1", definition=_definition(permission_mode="sudo")
        )


@pytest.mark.parametrize("spelling", ["read-only", "read_only"])
def test_both_permission_spellings_are_one_mode(spelling: str) -> None:
    PortablePersonaDefinition(
        id="x", revision="r1", definition=_definition(permission_mode=spelling)
    )
    assert parse_permission_mode(spelling) is PermissionMode.READ_ONLY


def test_the_digest_follows_the_authored_spelling_so_existing_pins_stay_valid() -> None:
    hyphen = persona_definition_digest(_definition(permission_mode="read-only"))
    underscore = persona_definition_digest(_definition(permission_mode="read_only"))
    assert hyphen != underscore


def test_every_builtin_persona_speaks_only_the_vocabulary_and_one_mode_spelling() -> None:
    source = FilesystemPersonaAdapter(persona_dirs=[], include_builtin=True)
    names = source.list_builtin_names()
    assert len(names) == 5
    for name in names:
        config = source.load(name)
        assert config is not None
        validate_tool_names("allowed_tools", config.allowed_tools)
        validate_tool_names("forbidden_tools", config.forbidden_tools)
        assert "-" not in config.permission_mode, name
        document = source.load_current_portable(name)
        assert document is not None


def test_a_builtin_that_could_only_use_retired_tools_is_closed_not_opened() -> None:
    # These three are archived, not shipped; their documents still follow the rule.
    source = FilesystemPersonaAdapter(
        persona_dirs=[str(Path(__file__).resolve().parents[2] / "archive" / "personas")],
        include_builtin=False,
    )
    for name in ("developer-coordinator", "developer-integration-verifier", "research-coordinator"):
        config = source.load(name)
        assert config is not None
        assert config.allowed_tools == ["plan"], name
        assert "shell" in config.forbidden_tools or "file_write" in config.forbidden_tools


def test_a_read_only_builtin_cannot_write_or_run_a_shell() -> None:
    source = FilesystemPersonaAdapter(persona_dirs=[], include_builtin=True)
    for name in source.list_builtin_names():
        config = source.load(name)
        assert config is not None
        if config.parsed_permission_mode is not PermissionMode.READ_ONLY:
            continue
        assert "file_write" not in config.allowed_tools, name
        assert "shell" not in config.allowed_tools, name
        assert "git" not in config.allowed_tools, name
