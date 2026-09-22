"""Tests for PostgresWorkflowRepository with mocked asyncpg pool."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from ting.adapters.postgres_workflows import PostgresWorkflowRepository
from ting.domain.models import WorkflowDefinition, WorkflowDependency, WorkflowScope


@pytest.fixture
def mock_pool() -> MagicMock:
    pool = MagicMock()
    pool.execute = AsyncMock(return_value="INSERT 0 1")
    pool.acquire.return_value.__aenter__.return_value = pool
    pool.transaction.return_value.__aenter__.return_value = None
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    return pool


@pytest.fixture
def repo(mock_pool: MagicMock) -> PostgresWorkflowRepository:
    return PostgresWorkflowRepository(mock_pool)


@pytest.fixture
def workflow() -> WorkflowDefinition:
    now = datetime.now(UTC)
    return WorkflowDefinition(
        id=uuid4(),
        name="Review Workflow",
        description="Code review path",
        version="1.0.0",
        scope=WorkflowScope.USER,
        owner_id="user-1",
        graph={"nodes": [{"id": "n1"}], "edges": []},
        created_at=now,
        updated_at=now,
    )


class TestSaveWorkflow:
    async def test_preserves_schema_two_dependency_closure(self, repo, workflow, mock_pool):
        pin = WorkflowDependency(uuid4(), "sha256:" + "a" * 64, "sha256:" + "a" * 64)
        scoped = {"worker": {"document": {"name": "Child"}, "persona_definitions": {}}}
        extended = replace(
            workflow,
            schema_version=2,
            workflow_dependencies={"worker": pin},
            workflow_definitions=scoped,
        )
        await repo.save_workflow(extended)
        args = next(
            call.args
            for call in mock_pool.execute.call_args_list
            if "INSERT INTO workflows\n" in call.args[0]
        )
        assert args[15] == 2
        assert json.loads(args[16]) == {"worker": pin.to_dict()}
        assert json.loads(args[17]) == scoped
        row = {
            "tenant_id": workflow.tenant_id,
            "id": workflow.id,
            "name": workflow.name,
            "description": workflow.description,
            "version": workflow.version,
            "scope": "user",
            "owner_id": workflow.owner_id,
            "graph_json": workflow.graph,
            "created_at": workflow.created_at,
            "updated_at": workflow.updated_at,
            "schema_version": 2,
            "workflow_dependencies_json": args[16],
            "workflow_definitions_json": args[17],
        }
        mock_pool.fetchrow.return_value = row
        loaded = await repo.get_workflow(workflow.id)
        assert loaded.schema_version == 2
        assert loaded.workflow_dependencies == {"worker": pin}
        assert loaded.workflow_definitions == scoped

    @pytest.mark.asyncio
    async def test_upserts_workflow(
        self,
        repo: PostgresWorkflowRepository,
        workflow: WorkflowDefinition,
        mock_pool: MagicMock,
    ) -> None:
        await repo.save_workflow(workflow)

        assert mock_pool.execute.call_count == 3
        call_args = mock_pool.execute.call_args_list[1]
        assert "INSERT INTO workflows" in call_args[0][0]
        assert call_args[0][1] == workflow.id
        assert call_args[0][5] == workflow.scope.value
        assert call_args[0][7] == json.dumps(workflow.graph)


class TestListWorkflows:
    @pytest.mark.asyncio
    async def test_lists_system_and_owned_user_workflows(
        self,
        repo: PostgresWorkflowRepository,
        workflow: WorkflowDefinition,
        mock_pool: MagicMock,
    ) -> None:
        mock_pool.fetch.return_value = [
            {
                "tenant_id": workflow.tenant_id,
                "id": workflow.id,
                "name": workflow.name,
                "description": workflow.description,
                "version": workflow.version,
                "scope": workflow.scope.value,
                "owner_id": workflow.owner_id,
                "graph_json": workflow.graph,
                "created_at": workflow.created_at,
                "updated_at": workflow.updated_at,
            }
        ]

        result = await repo.list_workflows(owner_id="user-1")

        assert len(result) == 1
        assert result[0].name == workflow.name
        call_args = mock_pool.fetch.call_args
        assert "scope = 'system'" in call_args[0][0]
        assert call_args[0][1] == "user-1"

    @pytest.mark.asyncio
    async def test_lists_user_scope_only_for_owner(
        self,
        repo: PostgresWorkflowRepository,
        mock_pool: MagicMock,
    ) -> None:
        await repo.list_workflows(owner_id="user-1", scope=WorkflowScope.USER)

        call_args = mock_pool.fetch.call_args
        assert "scope = 'user'" in call_args[0][0]
        assert "owner_id = $1" in call_args[0][0]
        assert call_args[0][1] == "user-1"

    @pytest.mark.asyncio
    async def test_lists_system_scope_without_owner_parameter(
        self,
        repo: PostgresWorkflowRepository,
        mock_pool: MagicMock,
    ) -> None:
        await repo.list_workflows(owner_id="user-1", scope=WorkflowScope.SYSTEM)

        call_args = mock_pool.fetch.call_args
        assert "scope = 'system'" in call_args[0][0]
        assert len(call_args[0]) == 1


class TestGetWorkflow:
    @pytest.mark.asyncio
    async def test_returns_none_when_missing(self, repo: PostgresWorkflowRepository) -> None:
        result = await repo.get_workflow(uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_parses_json_string_graph(
        self,
        repo: PostgresWorkflowRepository,
        workflow: WorkflowDefinition,
        mock_pool: MagicMock,
    ) -> None:
        mock_pool.fetchrow.return_value = {
            "tenant_id": workflow.tenant_id,
            "id": workflow.id,
            "name": workflow.name,
            "description": workflow.description,
            "version": workflow.version,
            "scope": WorkflowScope.SYSTEM.value,
            "owner_id": None,
            "graph_json": json.dumps(workflow.graph),
            "created_at": workflow.created_at,
            "updated_at": workflow.updated_at,
        }

        result = await repo.get_workflow(workflow.id)

        assert result is not None
        assert result.scope == WorkflowScope.SYSTEM
        assert result.graph == workflow.graph


class TestDeleteWorkflow:
    @pytest.mark.asyncio
    async def test_returns_true_when_deleted(
        self,
        repo: PostgresWorkflowRepository,
        mock_pool: MagicMock,
    ) -> None:
        mock_pool.execute.return_value = "DELETE 1"

        result = await repo.delete_workflow(uuid4())

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_missing(
        self,
        repo: PostgresWorkflowRepository,
        mock_pool: MagicMock,
    ) -> None:
        mock_pool.execute.return_value = "DELETE 0"

        result = await repo.delete_workflow(uuid4())

        assert result is False


def workflow_row(workflow):
    return {
        "id": workflow.id,
        "tenant_id": workflow.tenant_id,
        "name": workflow.name,
        "description": workflow.description,
        "version": workflow.version,
        "scope": workflow.scope.value,
        "owner_id": workflow.owner_id,
        "graph_json": workflow.graph,
        "created_at": workflow.created_at,
        "updated_at": workflow.updated_at,
        "persona_definitions_json": workflow.persona_definitions,
        "requirements_json": workflow.requirements,
    }


@pytest.fixture
def versioned(workflow):
    return replace(workflow, graph={"nodes": [], "edges": []})


class TestVersions:
    async def test_advances_version_and_archives_complete_pin_closure(
        self, repo, mock_pool, versioned
    ):
        from ting.domain.workflow_document import workflow_document_revision

        head = replace(versioned, persona_definitions={"worker": {"model": "old"}})
        mock_pool.fetchrow.return_value = workflow_row(head)
        current = await repo.get_workflow(head.id)
        proposed = replace(
            current, name="Updated", persona_definitions={"worker": {"model": "new"}}
        )
        saved = await repo.save_workflow_version(
            proposed,
            expected_revision=current.revision,
            base_revision=workflow_document_revision(current),
        )
        assert saved.id == current.id
        assert saved.version == "1.0.1"
        assert saved.based_on_revision == workflow_document_revision(current)
        assert saved.document_revision != current.document_revision
        assert saved.revision != current.revision
        archives = [
            call.args
            for call in mock_pool.execute.call_args_list
            if "INSERT INTO workflow_versions" in call.args[0]
        ]
        assert [args[2] for args in archives] == ["1.0.0", "1.0.1"]
        assert json.loads(archives[0][4])["persona_definitions"]["worker"]["model"] == "old"
        assert json.loads(archives[1][4])["persona_definitions"]["worker"]["model"] == "new"
        mock_pool.transaction.assert_called_once()

    async def test_editing_old_base_still_advances_latest(self, repo, mock_pool, versioned):
        from ting.domain.workflow_versioning import serialize_workflow_version

        current = replace(versioned, version="1.0.3")
        old = serialize_workflow_version(versioned)
        mock_pool.fetchrow.side_effect = [workflow_row(current), {"snapshot": old}]
        saved = await repo.save_workflow_version(
            versioned,
            expected_revision=repo._database_revision(current.updated_at),
            base_revision=old["document_revision"],
        )
        assert saved.version == "1.0.4"
        assert saved.based_on_revision == old["document_revision"]

    @pytest.mark.parametrize("head_exists", [True, False])
    async def test_rejects_stale_head_without_writes(self, repo, mock_pool, versioned, head_exists):
        from ting.domain.exceptions import WorkflowConflictError

        mock_pool.fetchrow.return_value = workflow_row(versioned) if head_exists else None
        with pytest.raises(WorkflowConflictError):
            await repo.save_workflow_version(
                versioned, expected_revision="stale", base_revision="old"
            )
        assert mock_pool.execute.call_count == 1  # Advisory lock only.
        assert (
            mock_pool.transaction.return_value.__aexit__.call_args.args[0] is WorkflowConflictError
        )

    async def test_rejects_missing_base_without_archiving(self, repo, mock_pool, versioned):
        from ting.domain.exceptions import WorkflowConflictError

        mock_pool.fetchrow.side_effect = [workflow_row(versioned), None]
        with pytest.raises(WorkflowConflictError, match="base version"):
            await repo.save_workflow_version(
                versioned,
                expected_revision=repo._database_revision(versioned.updated_at),
                base_revision="unknown",
            )
        assert mock_pool.execute.call_count == 1

    async def test_rejects_changed_ownership(self, repo, mock_pool, versioned):
        from identity.ports import AuthorizationDeniedError

        mock_pool.fetchrow.return_value = workflow_row(versioned)
        with pytest.raises(AuthorizationDeniedError):
            await repo.save_workflow_version(
                replace(versioned, owner_id="other"),
                expected_revision=repo._database_revision(versioned.updated_at),
                base_revision="old",
            )

    async def test_history_reads_keep_current_cas_and_immutable_content(
        self, repo, mock_pool, versioned
    ):
        from ting.domain.workflow_versioning import serialize_workflow_version

        head = replace(versioned, name="Latest", version="1.0.2")
        payload = serialize_workflow_version(versioned)
        mock_pool.fetchrow.side_effect = [workflow_row(head), {"snapshot": json.dumps(payload)}]
        old = await repo.get_workflow_version(head.id, version="1.0.0")
        assert old.name == versioned.name
        assert old.version == "1.0.0"
        assert not old.is_head
        assert old.read_only
        assert old.revision == repo._database_revision(head.updated_at)
        assert old.document_revision == payload["document_revision"]

    async def test_history_list_includes_legacy_head_before_first_edit(
        self, repo, mock_pool, versioned
    ):
        from ting.domain.workflow_versioning import serialize_workflow_version

        head = replace(versioned, version="1.0.2")
        mock_pool.fetchrow.return_value = workflow_row(head)
        mock_pool.fetch.return_value = [{"snapshot": serialize_workflow_version(versioned)}]
        versions = await repo.list_workflow_versions(head.id)
        assert [item.version for item in versions] == ["1.0.2", "1.0.0"]
        assert [item.is_head for item in versions] == [True, False]

    async def test_history_list_does_not_duplicate_archived_head(self, repo, mock_pool, versioned):
        from ting.domain.workflow_versioning import serialize_workflow_version

        mock_pool.fetchrow.return_value = workflow_row(versioned)
        mock_pool.fetch.return_value = [{"snapshot": serialize_workflow_version(versioned)}]
        assert len(await repo.list_workflow_versions(versioned.id)) == 1

    async def test_missing_history_and_head(self, repo, mock_pool, versioned):
        assert await repo.list_workflow_versions(versioned.id) == []
        assert await repo.get_workflow_version(versioned.id, version="1.0.0") is None
        mock_pool.fetchrow.side_effect = [workflow_row(versioned), None]
        assert await repo.get_workflow_version(versioned.id, version="0.0.1") is None

    async def test_head_lookup_by_digest(self, repo, mock_pool, versioned):
        from ting.domain.workflow_document import workflow_document_revision

        mock_pool.fetchrow.return_value = workflow_row(versioned)
        selected = await repo.get_workflow_version(
            versioned.id, document_revision=workflow_document_revision(versioned)
        )
        assert selected.is_head
        assert selected.version == versioned.version
        assert mock_pool.fetchrow.call_count == 1

    async def test_legacy_save_also_preserves_history(self, repo, mock_pool, versioned):
        mock_pool.fetchrow.return_value = workflow_row(versioned)
        saved = await repo.save_workflow(
            replace(
                versioned,
                name="Imported change",
                revision=repo._database_revision(versioned.updated_at),
            )
        )
        assert saved.version == "1.0.1"
        assert (
            len(
                [
                    call
                    for call in mock_pool.execute.call_args_list
                    if "INSERT INTO workflow_versions" in call.args[0]
                ]
            )
            == 2
        )

    async def test_legacy_save_conflicts_if_revision_is_stale_or_deleted(
        self, repo, mock_pool, versioned
    ):
        from ting.domain.exceptions import WorkflowConflictError

        with pytest.raises(WorkflowConflictError, match="no longer exists"):
            await repo.save_workflow(replace(versioned, revision="old"))
        mock_pool.fetchrow.return_value = workflow_row(versioned)
        with pytest.raises(WorkflowConflictError, match="changed"):
            await repo.save_workflow(replace(versioned, revision="old"))

    async def test_archive_existing_immutable_version_is_idempotent(
        self, repo, mock_pool, versioned
    ):
        from ting.domain.workflow_versioning import serialize_workflow_version

        mock_pool.execute.return_value = "INSERT 0 0"
        mock_pool.fetchrow.return_value = {
            "snapshot": json.dumps(serialize_workflow_version(versioned))
        }
        await repo._archive(mock_pool, versioned)

    @pytest.mark.parametrize("snapshot", [None, {"snapshot": {"document_revision": "different"}}])
    async def test_archive_refuses_to_overwrite_existing_version(
        self, repo, mock_pool, versioned, snapshot
    ):
        from ting.domain.exceptions import WorkflowConflictError

        mock_pool.execute.return_value = "INSERT 0 0"
        mock_pool.fetchrow.return_value = snapshot
        with pytest.raises(WorkflowConflictError, match="immutable"):
            await repo._archive(mock_pool, versioned)

    async def test_explicit_bundled_upgrade_preserves_package_version(
        self, repo, mock_pool, versioned
    ):
        mock_pool.fetchrow.return_value = workflow_row(versioned)
        saved = await repo.save_workflow(
            replace(
                versioned,
                origin="bundled",
                version="2.0.0",
                revision=repo._database_revision(versioned.updated_at),
            )
        )
        assert saved.version == "2.0.0"
        assert saved.origin == "bundled"
        assert saved.read_only
        mock_pool.fetchrow.return_value = {**workflow_row(saved), "version_origin": "bundled"}
        loaded = await repo.get_workflow(saved.id)
        assert loaded.read_only
        assert loaded.origin == "bundled"

    async def test_rejects_blind_update_without_head_token(self, repo, mock_pool, versioned):
        from ting.domain.exceptions import WorkflowConflictError

        mock_pool.fetchrow.return_value = workflow_row(versioned)
        with pytest.raises(WorkflowConflictError):
            await repo.save_workflow(versioned)
        assert mock_pool.execute.call_count == 1

    async def test_cannot_delete_bundled_ancestry_and_resurrect_it(
        self, repo, mock_pool, versioned
    ):
        from ting.domain.exceptions import WorkflowReadOnlyError

        mock_pool.execute.return_value = "DELETE 0"
        mock_pool.fetchrow.return_value = workflow_row(versioned)
        with pytest.raises(WorkflowReadOnlyError, match="bundled version history"):
            await repo.delete_workflow(versioned.id)
        assert "snapshot->>'origin' = 'bundled'" in mock_pool.execute.call_args.args[0]

    async def test_rejects_ambiguous_version_selectors(self, repo, versioned):
        with pytest.raises(ValueError, match="not both"):
            await repo.get_workflow_version(versioned.id, version="1.0.0", document_revision="hash")
