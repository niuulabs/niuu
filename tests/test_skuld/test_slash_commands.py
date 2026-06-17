"""Tests for slash-command enumeration + transport plumbing."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from skuld.slash_commands import (
    BUILTIN_COMMANDS,
    build_slash_command_catalog,
    compose_slash_command_text,
    enumerate_filesystem_commands,
)
from skuld.transports.persistent_subprocess import PersistentSubprocessTransport
from skuld.transports.subprocess import SubprocessTransport


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Redirect ~/.claude to an empty temp home so the user's real skills and
    plugins don't leak into deterministic assertions."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".claude" / "commands").mkdir(parents=True)
    (ws / ".claude" / "skills").mkdir(parents=True)
    return ws


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# compose_slash_command_text
# ---------------------------------------------------------------------------


class TestComposeSlashCommandText:
    def test_adds_leading_slash(self):
        assert compose_slash_command_text("compact") == "/compact"

    def test_keeps_existing_slash(self):
        assert compose_slash_command_text("/compact") == "/compact"

    def test_appends_arguments(self):
        assert compose_slash_command_text("/compact", "keep tests") == "/compact keep tests"

    def test_strips_whitespace(self):
        assert compose_slash_command_text("  /model  ", "  opus  ") == "/model opus"

    def test_empty_command_is_empty(self):
        assert compose_slash_command_text("") == ""
        assert compose_slash_command_text("   ") == ""


# ---------------------------------------------------------------------------
# build_slash_command_catalog
# ---------------------------------------------------------------------------


class TestBuildCatalog:
    def test_builtin_names_get_descriptions(self, isolated_home):
        catalog = build_slash_command_catalog(["compact", "clear"], [], None)
        names = {c["name"] for c in catalog}
        assert names == {"/compact", "/clear"}
        compact = next(c for c in catalog if c["name"] == "/compact")
        assert compact["description"] == BUILTIN_COMMANDS["compact"]
        assert compact["source"] == "builtin"

    def test_sorted_and_deduped(self, isolated_home):
        catalog = build_slash_command_catalog(
            ["model", "clear", "clear", "compact"], [], None
        )
        names = [c["name"] for c in catalog]
        assert names == ["/clear", "/compact", "/model"]

    def test_skills_marked_as_skill_source(self, isolated_home):
        catalog = build_slash_command_catalog([], ["lexi"], None)
        lexi = next(c for c in catalog if c["name"] == "/lexi")
        assert lexi["source"] == "skill"

    def test_filesystem_command_enriches_description(self, isolated_home, workspace):
        _write(
            workspace / ".claude" / "commands" / "deploy.md",
            "---\nname: deploy\ndescription: Ship the build\nargument-hint: <env>\n---\nbody",
        )
        # 'deploy' is reported by the CLI init but has no builtin description;
        # the filesystem scan supplies one.
        catalog = build_slash_command_catalog(["deploy"], [], str(workspace))
        deploy = next(c for c in catalog if c["name"] == "/deploy")
        assert deploy["description"] == "Ship the build"
        assert deploy["argument_hint"] == "<env>"
        assert deploy["source"] == "custom"

    def test_filesystem_only_command_is_excluded(self, isolated_home, workspace):
        """A command on disk but NOT reported by the CLI is omitted — it wouldn't
        execute in this session mode, so we must not surface it."""
        _write(
            workspace / ".claude" / "commands" / "secret.md",
            "---\ndescription: Hidden helper\n---\nbody",
        )
        catalog = build_slash_command_catalog([], [], str(workspace))
        assert not any(c["name"] == "/secret" for c in catalog)

    def test_namespaced_command_enriches_only_when_reported(self, isolated_home, workspace):
        _write(
            workspace / ".claude" / "commands" / "frontend" / "component.md",
            "---\ndescription: Scaffold a component\n---\nbody",
        )
        # Not reported by the CLI -> absent.
        assert not any(
            c["name"] == "/frontend:component"
            for c in build_slash_command_catalog([], [], str(workspace))
        )
        # Reported by the CLI (namespaced) -> present and enriched from disk.
        catalog = build_slash_command_catalog(["frontend:component"], [], str(workspace))
        entry = next(c for c in catalog if c["name"] == "/frontend:component")
        assert entry["description"] == "Scaffold a component"

    def test_include_filesystem_false_skips_scan(self, isolated_home, workspace):
        _write(workspace / ".claude" / "commands" / "deploy.md", "---\n---\nbody")
        catalog = build_slash_command_catalog(
            ["compact"], [], str(workspace), include_filesystem=False
        )
        assert {c["name"] for c in catalog} == {"/compact"}

    def test_empty_inputs_no_crash(self, isolated_home):
        assert build_slash_command_catalog(None, None, None) == []


class TestEnumerateFilesystem:
    def test_skill_directory_and_flat_file(self, isolated_home, workspace):
        _write(
            workspace / ".claude" / "skills" / "alpha" / "SKILL.md",
            "---\nname: alpha\ndescription: Alpha skill\n---\nbody",
        )
        _write(
            workspace / ".claude" / "skills" / "beta.md",
            "---\ndescription: Beta skill\n---\nbody",
        )
        found = {c["name"]: c for c in enumerate_filesystem_commands(str(workspace))}
        assert found["/alpha"]["source"] == "skill"
        assert found["/beta"]["description"] == "Beta skill"

    def test_missing_workspace_is_safe(self, isolated_home):
        assert enumerate_filesystem_commands("/nonexistent/path/xyz") == []


# ---------------------------------------------------------------------------
# Transport plumbing — capture, discover, send_control
# ---------------------------------------------------------------------------

_INIT_EVENT = {
    "type": "system",
    "subtype": "init",
    "session_id": "sess-1",
    "slash_commands": ["compact", "clear", "model"],
    "skills": ["lexi"],
}


@pytest.mark.parametrize(
    "factory",
    [
        lambda ws: PersistentSubprocessTransport(ws),
        lambda ws: SubprocessTransport(ws),
    ],
)
class TestTransportSlashCommands:
    def test_capabilities_declare_slash_commands(self, factory, tmp_path):
        transport = factory(str(tmp_path))
        assert transport.capabilities.slash_commands is True
        assert transport.capabilities.skills is True

    async def test_capture_and_discover(self, factory, isolated_home, tmp_path):
        transport = factory(str(tmp_path))
        transport._capture_init_commands(_INIT_EVENT)
        catalog = await transport.discover_slash_commands(refresh=True)
        names = {c["name"] for c in catalog}
        assert {"/compact", "/clear", "/model", "/lexi"} <= names
        lexi = next(c for c in catalog if c["name"] == "/lexi")
        assert lexi["source"] == "skill"

    async def test_discover_before_init_is_empty(self, factory, isolated_home, tmp_path):
        transport = factory(str(tmp_path))
        assert await transport.discover_slash_commands(refresh=True) == []

    async def test_send_control_composes_user_message(self, factory, tmp_path):
        transport = factory(str(tmp_path))
        transport.send_message = AsyncMock()
        await transport.send_control(
            "slash_command", command="compact", arguments="keep tests"
        )
        # send runs as a detached background task — let it complete.
        pending = [t for t in asyncio.all_tasks() if t.get_name() == "claude-slash-command"]
        if pending:
            await asyncio.gather(*pending)
        transport.send_message.assert_awaited_once_with("/compact keep tests")

    async def test_send_control_ignores_other_subtypes(self, factory, tmp_path):
        transport = factory(str(tmp_path))
        transport.send_message = AsyncMock()
        await transport.send_control("set_model", model="opus")
        await asyncio.sleep(0)
        transport.send_message.assert_not_awaited()
