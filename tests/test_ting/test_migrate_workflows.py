"""Coverage for the operator migration CLI: source resolution, _run, and main()."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from ravn.adapters.personas.loader import FilesystemPersonaAdapter
from ting.domain.models import WorkflowDefinition, WorkflowScope
from ting.migrate_workflows import _registry_source_for_workflow, _run, main


def _workflow(**overrides) -> WorkflowDefinition:
    created_at = datetime(2025, 1, 2, 3, 4, tzinfo=UTC)
    base = WorkflowDefinition(
        id=uuid4(),
        name="Migrated workflow",
        description="preserved",
        version="1.2.3",
        scope=WorkflowScope.USER,
        owner_id=None,
        graph={"nodes": [{"id": "stage", "stageMembers": [{"personaId": "reviewer"}]}]},
        created_at=created_at,
        updated_at=created_at,
    )
    return replace(base, **overrides)


class TestRegistrySourceForWorkflow:
    async def test_alias_already_pinned_in_definitions_is_skipped(self):
        registry = SimpleNamespace(get_current_portable_persona=AsyncMock())
        builtins = SimpleNamespace(load_current_portable=lambda _id: None)
        workflow = _workflow(persona_definitions={"reviewer": {"id": "reviewer"}})

        source = await _registry_source_for_workflow(workflow, registry, builtins)

        registry.get_current_portable_persona.assert_not_awaited()
        assert source.load_current_portable("reviewer") is None

    async def test_owner_id_falsy_uses_builtin_loader(self):
        portable = FilesystemPersonaAdapter(
            persona_dirs=[], include_builtin=True
        ).load_current_portable("reviewer")
        assert portable is not None
        registry = SimpleNamespace(get_current_portable_persona=AsyncMock())
        builtins = FilesystemPersonaAdapter(persona_dirs=[], include_builtin=True)
        workflow = _workflow(owner_id=None)

        source = await _registry_source_for_workflow(workflow, registry, builtins)

        registry.get_current_portable_persona.assert_not_awaited()
        assert source.load_current_portable("reviewer") == portable

    async def test_missing_document_is_not_collected(self):
        registry = SimpleNamespace(get_current_portable_persona=AsyncMock(return_value=None))
        builtins = SimpleNamespace(load_current_portable=lambda _id: None)
        workflow = _workflow(owner_id="alice")

        source = await _registry_source_for_workflow(workflow, registry, builtins)

        registry.get_current_portable_persona.assert_awaited_once_with("alice", "reviewer")
        assert source.load_current_portable("reviewer") is None


def _pool_cm(label, calls):
    @asynccontextmanager
    async def _cm(config):
        calls.append((label, config))
        yield f"{label}-pool"

    return _cm


class TestRun:
    async def test_persona_dirs_branch_builds_filesystem_source_and_applies(self, tmp_path):
        pool_calls: list[tuple[str, object]] = []
        report = SimpleNamespace(errors=[], to_dict=lambda: {"errors": []})
        catalog_calls = []

        async def fake_catalog(**kwargs):
            catalog_calls.append(kwargs)
            # Exercise the closure passed in as persona_source_for_workflow.
            resolved = await kwargs["persona_source_for_workflow"](_workflow())
            assert isinstance(resolved, FilesystemPersonaAdapter)
            return report

        with (
            patch("ting.migrate_workflows.database_pool", _pool_cm("main", pool_calls)),
            patch("ting.migrate_workflows.migrate_workflow_catalog", fake_catalog),
            patch("ting.migrate_workflows.load_system_workflows", return_value=[]),
            patch("ting.migrate_workflows.PostgresWorkflowRepository", lambda pool: f"src:{pool}"),
        ):
            result = await _run(
                catalog_path=str(tmp_path),
                apply=True,
                persona_dirs=[str(tmp_path)],
                persona_database="volundr",
                replace_divergent_bundled=False,
            )

        assert result == 0
        assert len(pool_calls) == 1
        assert pool_calls[0][0] == "main"
        assert len(catalog_calls) == 1
        assert catalog_calls[0]["apply"] is True
        assert catalog_calls[0]["replace_divergent_bundled"] is False
        assert catalog_calls[0]["source"] == "src:main-pool"

    async def test_registry_branch_opens_second_pool_and_reports_errors(self, tmp_path):
        pool_calls: list[tuple[str, object]] = []
        report = SimpleNamespace(
            errors=["Database references missing workflow row(s): x"],
            to_dict=lambda: {"errors": ["boom"]},
        )
        catalog_calls = []
        registry_instances = []

        class _FakeRegistry:
            def __init__(self, pool, *, builtin_loader):
                self.pool = pool
                self.builtin_loader = builtin_loader
                registry_instances.append(self)
                self.get_current_portable_persona = AsyncMock(return_value=None)

        workflow = _workflow(owner_id="alice")

        async def fake_catalog(**kwargs):
            catalog_calls.append(kwargs)
            resolved = await kwargs["persona_source_for_workflow"](workflow)
            registry_instances[0].get_current_portable_persona.assert_awaited_once_with(
                "alice", "reviewer"
            )
            assert resolved.load_current_portable("reviewer") is None
            return report

        # database_pool is invoked twice (main db, persona db); alternate labels
        # via a side-effecting wrapper so both calls are distinguishable.
        call_count = {"n": 0}

        def dispatcher(config):
            call_count["n"] += 1
            label = "main" if call_count["n"] == 1 else "persona"
            return _pool_cm(label, pool_calls)(config)

        with (
            patch("ting.migrate_workflows.database_pool", dispatcher),
            patch("ting.migrate_workflows.migrate_workflow_catalog", fake_catalog),
            patch("ting.migrate_workflows.load_system_workflows", return_value=[]),
            patch("ting.migrate_workflows.PostgresWorkflowRepository", lambda pool: f"src:{pool}"),
            patch("ting.migrate_workflows.PostgresPersonaRegistry", _FakeRegistry),
        ):
            result = await _run(
                catalog_path=str(tmp_path),
                apply=False,
                persona_dirs=[],
                persona_database="custom_personas",
                replace_divergent_bundled=True,
            )

        assert result == 2
        assert [label for label, _ in pool_calls] == ["main", "persona"]
        assert pool_calls[1][1].name == "custom_personas"
        assert len(catalog_calls) == 1
        assert catalog_calls[0]["apply"] is False
        assert catalog_calls[0]["replace_divergent_bundled"] is True


class TestMain:
    def test_main_parses_args_and_exits_with_run_result(self, tmp_path, monkeypatch):
        catalog_dir = tmp_path / "catalog"
        catalog_dir.mkdir()
        monkeypatch.setattr(
            "sys.argv",
            [
                "migrate_workflows",
                "--catalog-path",
                str(catalog_dir),
                "--apply",
                "--persona-dir",
                str(tmp_path),
                "--persona-database",
                "otherdb",
                "--replace-divergent-bundled",
            ],
        )
        run_mock = AsyncMock(return_value=0)
        with patch("ting.migrate_workflows._run", run_mock):
            with pytest.raises(SystemExit) as exc:
                main()

        assert exc.value.code == 0
        run_mock.assert_awaited_once_with(
            catalog_path=str(catalog_dir.resolve()),
            apply=True,
            persona_dirs=[str(tmp_path)],
            persona_database="otherdb",
            replace_divergent_bundled=True,
        )

    def test_main_defaults_and_nonzero_exit_on_errors(self, tmp_path, monkeypatch):
        catalog_dir = tmp_path / "catalog2"
        catalog_dir.mkdir()
        monkeypatch.setattr(
            "sys.argv",
            ["migrate_workflows", "--catalog-path", str(catalog_dir)],
        )
        run_mock = AsyncMock(return_value=2)
        with patch("ting.migrate_workflows._run", run_mock):
            with pytest.raises(SystemExit) as exc:
                main()

        assert exc.value.code == 2
        run_mock.assert_awaited_once_with(
            catalog_path=str(catalog_dir.resolve()),
            apply=False,
            persona_dirs=[],
            persona_database="volundr",
            replace_divergent_bundled=False,
        )
