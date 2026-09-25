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
    LearnedTool,
    LearnedToolError,
    LearnedToolInfrastructureError,
    LearnedToolResolver,
    _ContainerProcessResult,
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
    verified: bool = True,
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
        # An installed tool carries the verification the build path performs;
        # loading refuses anything else, so a fixture without it is not a tool
        # this resident would ever have.
        provenance={"verification": {"ok": True, "logs": "fixture"}} if verified else {},
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

    async def test_regression_beyond_threshold_archives_the_tool(self, tmp_path: Path) -> None:
        """The YOLO invariant applies to the path every learned tool actually
        runs through, not only the autonomous install loop: a tool that keeps
        failing is automatically rolled back, not left to fail forever."""
        _install_tool(
            tmp_path,
            "flaky_tool",
            tool_code="def run(input):\n    raise RuntimeError('boom')\n",
        )
        manager = await self._manager(tmp_path, "flaky_tool")
        dispatch = LearnedToolRunTool(
            resolver=LearnedToolResolver(state_dir=tmp_path),
            permission=AllowAllPermission(),
            skill_manager=manager,
            rollback_consecutive_failures=2,
        )

        first = await dispatch.execute({"name": "flaky_tool", "input": {}})
        assert first.is_error
        assert "archived" not in first.content

        second = await dispatch.execute({"name": "flaky_tool", "input": {}})
        assert second.is_error
        assert "archived after 2 consecutive failures" in second.content
        shown = await manager.show("flaky_tool", include_archived=True)
        assert shown["metadata"]["status"] == "archived"

        third = await dispatch.execute({"name": "flaky_tool", "input": {}})
        assert third.is_error
        assert "archived" in third.content

    async def test_container_backend_infrastructure_failure_is_not_counted_toward_rollback(
        self,
        tmp_path: Path,
    ) -> None:
        """The bug a reviewer's script confirmed: on the default 'container'
        backend, every dispatch used to be handed a host_call the runner
        refuses outright, so a perfectly healthy tool was archived after a
        handful of calls. Fixed two ways, both exercised here: (1) the
        container runner is never offered a host_call it cannot honor, and
        (2) a genuine backend outage (docker unreachable) is classified as
        infrastructure and never reaches record_usage/rollback at all."""

        async def docker_daemon_unreachable(argv, stdin, timeout_seconds, name):
            return _ContainerProcessResult(
                returncode=127,
                stderr=(
                    b"docker: failed to connect to the docker API at "
                    b"unix:///var/run/docker.sock: connect: no such file or directory\n"
                ),
            )

        async def unused_host_call(_name: str, _arguments: dict) -> object:
            return None  # never invoked; presence alone used to trip the old bug

        artifact = _install_tool(tmp_path, "good_tool")
        code_dir, _artifacts_dir = learned_tool_storage(tmp_path)
        runner = ContainedLearnedToolRunner(
            workspace_root=tmp_path,
            command_runner=docker_daemon_unreachable,
        )
        tool = LearnedTool(
            manifest=artifact.manifest,
            tool_path=code_dir / "good_tool.py",
            runner=runner,
            host_call=unused_host_call,
        )

        class _FakeResolver:
            def load(self, name: str, *, host_call=None) -> LearnedTool:
                assert name == "good_tool"
                return tool

        manager = await self._manager(tmp_path, "good_tool")
        dispatch = LearnedToolRunTool(
            resolver=_FakeResolver(),  # type: ignore[arg-type]
            permission=AllowAllPermission(),
            skill_manager=manager,
            rollback_consecutive_failures=2,
        )

        for _ in range(4):
            result = await dispatch.execute({"name": "good_tool", "input": {}})
            assert result.is_error
            assert "backend error, not a tool failure" in result.content
            # Proves host_call was withheld from the container runner (it was
            # actually reached and ran the fake docker command), not that the
            # runner refused up front with "cannot provide the host SDK".
            assert "docker: failed to connect" in result.content
            assert "cannot provide the host SDK" not in result.content
            assert "archived" not in result.content

        shown = await manager.show("good_tool")
        assert shown["metadata"]["status"] == "active"
        assert shown["metadata"]["run_count"] == 0
        assert shown["metadata"]["consecutive_failures"] == 0

    async def test_archived_learned_tool_cannot_run(self, tmp_path: Path) -> None:
        _install_tool(tmp_path, "obsolete_probe")
        manager = await self._manager(tmp_path, "obsolete_probe")
        await manager.archive("obsolete_probe")
        dispatch = self._dispatch(tmp_path, skill_manager=manager)

        result = await dispatch.execute({"name": "obsolete_probe", "input": {}})

        assert result.is_error
        assert "archived" in result.content


class TestOrphanedArtifactReaping:
    """An artifact with no code file is removed, not warned about forever.

    A manifest whose .py never landed can never execute, but it kept the
    capability it claims looking installable. The resident saw the same gap on
    every sweep and commissioned the same build again — 34 rebuilds of one
    tool over five days, and 26 such orphans across the fleet.
    """

    def test_orphaned_artifact_is_removed_from_the_registry(self, tmp_path: Path) -> None:
        _install_tool(tmp_path, "good_tool")
        _install_tool(tmp_path, "orphan_tool", write_code=False)
        code_dir, artifacts_dir = learned_tool_storage(tmp_path)
        resolver = LearnedToolResolver(state_dir=tmp_path)

        listed = resolver.list_artifacts()

        assert [a.manifest.name for a in listed] == ["good_tool"]
        # Reaped from disk, so the next sweep does not see it again.
        assert not (artifacts_dir / "orphan_tool.json").exists()
        assert (artifacts_dir / "good_tool.json").exists()
        assert (code_dir / "good_tool.py").exists()

    def test_a_second_sweep_is_quiet(self, tmp_path: Path) -> None:
        _install_tool(tmp_path, "orphan_tool", write_code=False)
        resolver = LearnedToolResolver(state_dir=tmp_path)

        assert resolver.list_artifacts() == []
        # Nothing left to warn about — this is what stops the rebuild loop.
        assert resolver.list_artifacts() == []

    def test_a_healthy_catalog_is_untouched(self, tmp_path: Path) -> None:
        _install_tool(tmp_path, "one")
        _install_tool(tmp_path, "two")
        resolver = LearnedToolResolver(state_dir=tmp_path)

        assert sorted(a.manifest.name for a in resolver.list_artifacts()) == ["one", "two"]


class TestCapabilityKeyDedup:
    """One capability must not fork into several tools over a naming whim.

    Observed in production: `inspect_k8s_node_disk_pressure` and
    `inspect_kubernetes_node_disk_pressure` are the same capability, but
    matching was exact-name only and every artifact carried an empty
    capability_id — so neither answered to the other's name, and a resident
    that could not find what it built asked for it again. One resident held 101
    tools covering 92 capabilities; one capability was built 34 times.
    """

    def test_spelling_variants_fold_to_one_key(self) -> None:
        from ravn.valkyrie_evolution.learned_tools import capability_key

        assert capability_key("inspect_k8s_node_disk_pressure") == capability_key(
            "inspect_kubernetes_node_disk_pressure"
        )
        assert capability_key("inspect.k8s.node.disk.pressure") == capability_key(
            "inspect_kubernetes_node_disk_pressure"
        )
        assert capability_key("inspect_daemonset_faileddaemonpod") == capability_key(
            "inspect_daemonset_failed_daemon_pod"
        )

    def test_different_capabilities_stay_distinct(self) -> None:
        """It folds spelling, never meaning."""
        from ravn.valkyrie_evolution.learned_tools import capability_key

        assert capability_key("inspect_node_disk_pressure") != capability_key(
            "inspect_node_memory_pressure"
        )
        assert capability_key("get_pod") != capability_key("get_pod_logs")

    def test_an_existing_tool_is_found_under_a_different_spelling(self, tmp_path: Path) -> None:
        from ravn.valkyrie_evolution.learned_tools import find_installed_capability

        _install_tool(tmp_path, "inspect_kubernetes_node_disk_pressure")
        code_dir, artifacts_dir = learned_tool_storage(tmp_path)

        found = find_installed_capability(
            artifacts_dir=artifacts_dir,
            tools_dir=code_dir,
            capability_id="",
            name="inspect_k8s_node_disk_pressure",
        )

        assert found is not None
        assert found.manifest.name == "inspect_kubernetes_node_disk_pressure"

    def test_exact_name_still_wins_over_a_fold(self, tmp_path: Path) -> None:
        from ravn.valkyrie_evolution.learned_tools import find_installed_capability

        _install_tool(tmp_path, "inspect_k8s_node_disk_pressure")
        _install_tool(tmp_path, "inspect_kubernetes_node_disk_pressure")
        code_dir, artifacts_dir = learned_tool_storage(tmp_path)

        found = find_installed_capability(
            artifacts_dir=artifacts_dir,
            tools_dir=code_dir,
            capability_id="",
            name="inspect_k8s_node_disk_pressure",
        )

        assert found is not None
        assert found.manifest.name == "inspect_k8s_node_disk_pressure"

    def test_an_unrelated_tool_is_not_matched(self, tmp_path: Path) -> None:
        from ravn.valkyrie_evolution.learned_tools import find_installed_capability

        _install_tool(tmp_path, "inspect_node_memory_pressure")
        code_dir, artifacts_dir = learned_tool_storage(tmp_path)

        assert (
            find_installed_capability(
                artifacts_dir=artifacts_dir,
                tools_dir=code_dir,
                capability_id="",
                name="inspect_node_disk_pressure",
            )
            is None
        )


class TestContainedLearnedToolRunnerVerify:
    """verify() runs in the SAME execution boundary as run() — the fix for
    peer test_code/tool_code and pip-installed requirements running on the
    resident's own host (its own network, its own filesystem access) during
    re-verification, regardless of the configured execution backend."""

    async def test_verify_runs_the_test_suite_inside_a_container(self, tmp_path: Path) -> None:
        calls: list[list[str]] = []

        async def fake_docker(argv, stdin, timeout_seconds, name):
            calls.append(list(argv))
            if "install" in argv:
                return _ContainerProcessResult(returncode=0)
            return _ContainerProcessResult(returncode=0, stdout=b"verify: ran 1 test callable(s)")

        runner = ContainedLearnedToolRunner(
            workspace_root=tmp_path,
            command_runner=fake_docker,
        )

        result = await runner.verify(
            tool_name="echo_tool",
            tool_code="def run(input):\n    return {'ok': True}\n",
            test_code=(
                "import _verify_tool\n\n"
                "def test_ok():\n"
                "    assert _verify_tool.run({}) == {'ok': True}\n"
            ),
            requirements=["requests"],
        )

        assert result.ok
        # Three container runs: venv creation, dependency install, then the
        # test itself — never a bare host subprocess for any of them.
        assert len(calls) == 3
        assert any("install" in argv for argv in calls)
        assert any("_verify_runner.py" in " ".join(argv) for argv in calls)
        # The verify scratch dir is cleaned up, not left behind.
        assert not (tmp_path / ".ravn" / "verify_runs").exists() or not list(
            (tmp_path / ".ravn" / "verify_runs").iterdir()
        )

    async def test_verify_reports_a_failing_test_without_raising(self, tmp_path: Path) -> None:
        async def fake_docker(argv, stdin, timeout_seconds, name):
            if "install" in argv:
                return _ContainerProcessResult(returncode=0)
            return _ContainerProcessResult(
                returncode=1,
                stderr=b"AssertionError: expected True, got False",
            )

        runner = ContainedLearnedToolRunner(
            workspace_root=tmp_path,
            command_runner=fake_docker,
        )

        result = await runner.verify(
            tool_name="broken_tool",
            tool_code="def run(input):\n    return {'ok': False}\n",
            test_code=(
                "import _verify_tool\n\n"
                "def test_ok():\n"
                "    assert _verify_tool.run({}) == {'ok': True}\n"
            ),
            requirements=[],
        )

        assert not result.ok
        assert "AssertionError" in result.logs

    async def test_verify_backend_outage_raises_infrastructure_error(self, tmp_path: Path) -> None:
        async def docker_daemon_unreachable(argv, stdin, timeout_seconds, name):
            return _ContainerProcessResult(
                returncode=1,
                error="docker: failed to connect to the docker API",
            )

        runner = ContainedLearnedToolRunner(
            workspace_root=tmp_path,
            command_runner=docker_daemon_unreachable,
        )

        with pytest.raises(LearnedToolInfrastructureError):
            await runner.verify(
                tool_name="echo_tool",
                tool_code="def run(input):\n    return {'ok': True}\n",
                test_code="import _verify_tool\n\ndef test_ok():\n    pass\n",
                requirements=[],
            )

    async def test_verify_skips_execution_for_static_defects(self, tmp_path: Path) -> None:
        async def unreachable_docker(argv, stdin, timeout_seconds, name):
            raise AssertionError("must never reach docker: static analysis should reject first")

        runner = ContainedLearnedToolRunner(
            workspace_root=tmp_path,
            command_runner=unreachable_docker,
        )

        result = await runner.verify(
            tool_name="undeclared_import_tool",
            tool_code="import requests\n\ndef run(input):\n    return {}\n",
            test_code="import _verify_tool\n\ndef test_ok():\n    pass\n",
            requirements=[],  # 'requests' is used but never declared
        )

        assert not result.ok
        assert result.missing_module == "requests"
