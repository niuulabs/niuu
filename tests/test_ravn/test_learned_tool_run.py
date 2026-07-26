"""Tests for on-demand learned-tool resolution and dispatch (NIU-1118)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ravn.adapters.permission.allow_deny import AllowAllPermission, DenyAllPermission
from ravn.adapters.skill.file_registry import FileSkillRegistry
from ravn.adapters.tools.learned_tool_run import LearnedToolRunTool
from ravn.skills.management import SkillManagementRegistry
from ravn.valkyrie_evolution.learned_tools import (
    ContainedLearnedToolRunner,
    LearnedToolError,
    LearnedToolResolver,
    learned_tool_storage,
    write_learned_tool,
    write_learned_tool_artifact,
)
from ravn.valkyrie_evolution.models import LearnedToolArtifact, LearnedToolManifest


def _install_tool(
    state_dir: Path,
    name: str,
    *,
    tool_code: str = "def run(input):\n    return {'ok': True, 'echo': input}\n",
    required_permission: str = "mimir:read",
    write_code: bool = True,
) -> LearnedToolArtifact:
    artifact = LearnedToolArtifact(
        artifact_id=f"learned-tool:{name}",
        manifest=LearnedToolManifest(
            name=name,
            description=f"Test tool {name}.",
            input_schema={"type": "object"},
            required_permission=required_permission,
            declared_reach=[],
        ),
        tool_code=tool_code,
    )
    code_dir, artifacts_dir = learned_tool_storage(state_dir)
    if write_code:
        write_learned_tool(tools_dir=code_dir, artifact=artifact)
    write_learned_tool_artifact(artifacts_dir=artifacts_dir, artifact=artifact)
    return artifact


class TestLearnedToolResolver:
    def test_rejects_unknown_execution_backend(self, tmp_path: Path) -> None:
        with pytest.raises(LearnedToolError, match="unknown learned tool execution backend"):
            LearnedToolResolver(state_dir=tmp_path, execution_backend="qemu")

    def test_container_backend_resolves_fail_closed_runner(self, tmp_path: Path) -> None:
        _install_tool(tmp_path, "contained_tool")
        resolver = LearnedToolResolver(
            state_dir=tmp_path,
            execution_backend="container",
            workspace_root=tmp_path,
        )

        tool = resolver.load("contained_tool")

        assert isinstance(tool._runner, ContainedLearnedToolRunner)

    def test_list_artifacts_empty_when_nothing_installed(self, tmp_path: Path) -> None:
        resolver = LearnedToolResolver(state_dir=tmp_path)
        assert resolver.list_artifacts() == []

    def test_list_artifacts_skips_broken_and_codeless_envelopes(self, tmp_path: Path) -> None:
        _install_tool(tmp_path, "good_tool")
        _install_tool(tmp_path, "codeless_tool", write_code=False)
        _, artifacts_dir = learned_tool_storage(tmp_path)
        (artifacts_dir / "broken.json").write_text("{not json", encoding="utf-8")

        resolver = LearnedToolResolver(state_dir=tmp_path)

        names = [artifact.manifest.name for artifact in resolver.list_artifacts()]
        assert names == ["good_tool"]

    def test_load_unknown_tool_raises(self, tmp_path: Path) -> None:
        resolver = LearnedToolResolver(state_dir=tmp_path)
        with pytest.raises(LearnedToolError, match="no learned tool named"):
            resolver.load("missing_tool")

    def test_load_codeless_tool_raises(self, tmp_path: Path) -> None:
        _install_tool(tmp_path, "codeless_tool", write_code=False)
        resolver = LearnedToolResolver(state_dir=tmp_path)
        with pytest.raises(LearnedToolError, match="no code file"):
            resolver.load("codeless_tool")

    def test_load_rejects_non_tool_names(self, tmp_path: Path) -> None:
        resolver = LearnedToolResolver(state_dir=tmp_path)
        with pytest.raises(LearnedToolError, match="invalid learned tool name"):
            resolver.load("/etc/passwd")

    async def test_load_returns_executable_tool(self, tmp_path: Path) -> None:
        _install_tool(tmp_path, "echo_window")
        resolver = LearnedToolResolver(state_dir=tmp_path)

        tool = resolver.load("echo_window")
        result = await tool.execute({"value": 7})

        assert not result.is_error
        assert '"ok": true' in result.content


class TestLearnedToolRunTool:
    def _dispatch(
        self,
        tmp_path: Path,
        permission=None,
        skill_manager: SkillManagementRegistry | None = None,
    ) -> LearnedToolRunTool:
        return LearnedToolRunTool(
            resolver=LearnedToolResolver(state_dir=tmp_path),
            permission=permission or AllowAllPermission(),
            skill_manager=skill_manager,
        )

    async def _manager(
        self,
        tmp_path: Path,
        name: str,
    ) -> SkillManagementRegistry:
        skills_dir = tmp_path / "skills"
        manager = SkillManagementRegistry(
            FileSkillRegistry(
                skill_dirs=[str(skills_dir)],
                write_dir=skills_dir,
                include_builtin=False,
                cwd=tmp_path,
            ),
            metadata_path=tmp_path / "skill_management.json",
        )
        await manager.create(
            name=name,
            content=f"capability: tool.{name}",
            description=f"Managed learned tool {name}",
        )
        return manager

    async def test_executes_installed_tool_by_name(self, tmp_path: Path) -> None:
        _install_tool(tmp_path, "metric_window")
        dispatch = self._dispatch(tmp_path)

        result = await dispatch.execute({"name": "metric_window", "input": {"pod": "api-0"}})

        assert not result.is_error
        assert '"ok": true' in result.content
        assert '"pod": "api-0"' in result.content

    async def test_unknown_tool_points_at_capability_list(self, tmp_path: Path) -> None:
        dispatch = self._dispatch(tmp_path)

        result = await dispatch.execute({"name": "missing_tool"})

        assert result.is_error
        assert "capability_list" in result.content

    async def test_empty_name_is_an_error(self, tmp_path: Path) -> None:
        dispatch = self._dispatch(tmp_path)

        result = await dispatch.execute({"name": "  "})

        assert result.is_error
        assert "name must not be empty" in result.content

    async def test_non_object_input_is_an_error(self, tmp_path: Path) -> None:
        _install_tool(tmp_path, "metric_window")
        dispatch = self._dispatch(tmp_path)

        result = await dispatch.execute({"name": "metric_window", "input": "not-an-object"})

        assert result.is_error
        assert "input must be an object" in result.content

    async def test_manifest_permission_is_enforced(self, tmp_path: Path) -> None:
        _install_tool(tmp_path, "guarded_tool", required_permission="k8s:write")
        dispatch = self._dispatch(tmp_path, permission=DenyAllPermission())

        result = await dispatch.execute({"name": "guarded_tool", "input": {}})

        assert result.is_error
        assert "Permission 'k8s:write' denied" in result.content

    async def test_manifest_permission_string_is_what_gets_checked(self, tmp_path: Path) -> None:
        _install_tool(tmp_path, "scoped_tool", required_permission="mimir:read")
        checked: list[str] = []

        class RecordingPermission:
            async def check(self, permission: str) -> bool:
                checked.append(permission)
                return True

        dispatch = self._dispatch(tmp_path, permission=RecordingPermission())
        result = await dispatch.execute({"name": "scoped_tool", "input": {}})

        assert not result.is_error
        assert checked == ["mimir:read"]

    async def test_tool_runtime_failure_is_reported_not_raised(self, tmp_path: Path) -> None:
        _install_tool(
            tmp_path,
            "broken_tool",
            tool_code="def run(input):\n    raise RuntimeError('boom')\n",
        )
        dispatch = self._dispatch(tmp_path)

        result = await dispatch.execute({"name": "broken_tool", "input": {}})

        assert result.is_error

    async def test_records_real_execution_outcome_in_shared_lifecycle(
        self,
        tmp_path: Path,
    ) -> None:
        _install_tool(tmp_path, "metric_window")
        manager = await self._manager(tmp_path, "metric_window")
        dispatch = self._dispatch(tmp_path, skill_manager=manager)

        result = await dispatch.execute({"name": "metric_window", "input": {}})

        assert not result.is_error
        shown = await manager.show("metric_window")
        assert shown["metadata"]["run_count"] == 1
        assert shown["metadata"]["success_count"] == 1

    async def test_archived_learned_tool_cannot_run(self, tmp_path: Path) -> None:
        _install_tool(tmp_path, "obsolete_probe")
        manager = await self._manager(tmp_path, "obsolete_probe")
        await manager.archive("obsolete_probe")
        dispatch = self._dispatch(tmp_path, skill_manager=manager)

        result = await dispatch.execute({"name": "obsolete_probe", "input": {}})

        assert result.is_error
        assert "archived" in result.content
