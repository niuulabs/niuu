"""Project acceptance at the domain/API boundary; ordinary sessions keep their contract."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from tests.conftest import make_session_participant_service
from volundr.adapters.inbound.rest import SessionCreate, SessionResponse, create_router
from volundr.domain.models import Principal, SessionStatus
from volundr.domain.ports import SessionContribution
from volundr.domain.project_ports import ProjectConflictError
from volundr.domain.projects import ProjectReceipt, SessionCoordination, SessionReference
from volundr.domain.services.projects import ProjectNotFoundError, ProjectService


def launch(project, **coordination):
    return SessionCreate(
        name="lexi-task",
        model="test",
        dispatch_id=uuid4(),
        coordination=SessionCoordination(project_id=project.id, **coordination),
    )


async def test_multiple_coordinators_survive_independent_stop(rig):
    service, forge, project, repo, _ = rig
    first = await forge.create_and_start_session(launch(project, role="coordinator"))
    second = await forge.create_and_start_session(launch(project, role="coordinator"))
    assert first.id != second.id
    await repo.update(first.model_copy(update={"status": SessionStatus.STOPPED}))
    assert (await service.get(project.id, None)).id == project.id
    assert (await repo.get(second.id)).coordination.role == "coordinator"


async def test_concurrent_retries_create_one_session_and_one_runtime(rig):
    _, forge, project, repo, pods = rig
    data = launch(project)
    results = await asyncio.gather(*(forge.create_and_start_session(data) for _ in range(20)))
    assert len({s.id for s in results}) == 1
    assert len(await repo.list()) == 1
    await asyncio.gather(*forge._session_service._provisioning_tasks.values())
    assert len(pods.start_calls) == 1


async def test_dispatch_payload_conflicts_and_deleted_session_tombstone(rig):
    _, forge, project, repo, _ = rig
    data = launch(project)
    session = await forge.create_and_start_session(data)
    with pytest.raises(ProjectConflictError):
        await forge.create_and_start_session(
            data.model_copy(update={"initial_prompt": "different"})
        )
    await asyncio.gather(*forge._session_service._provisioning_tasks.values())
    await repo.delete(session.id)
    with pytest.raises(ProjectConflictError, match="deleted"):
        await forge.create_and_start_session(data)


async def test_context_survives_restart_and_raw_config_is_private(rig):
    service, forge, project, repo, pods = rig
    data = launch(project)
    session = await forge.create_and_start_session(data)
    await asyncio.gather(*service.sessions._provisioning_tasks.values())
    stored = await repo.get(session.id)
    assert "Keep project decisions" in stored.workload_config["project_context"]
    assert stored.coordination.context_revision == "revision-a"
    service.workspace.context.return_value = ("NEW context", "revision-b")
    await repo.update(stored.model_copy(update={"status": SessionStatus.STOPPED}))
    await service.sessions.start_session(session.id)
    await asyncio.gather(*service.sessions._provisioning_tasks.values())
    spec = pods.start_calls[-1][1]
    assert "Keep project decisions" in spec.values["session"]["systemPrompt"]
    assert "NEW context" not in spec.values["session"]["systemPrompt"]
    payload = SessionResponse.from_session(stored).model_dump()
    assert payload["coordination"]["project_id"] == project.id
    assert "workload_config" not in payload and "project_context" not in payload


async def test_project_owner_and_tenant_are_enforced(rig):
    service, _, project, _, _ = rig
    outsider = Principal("other", "o@example.com", "other-tenant", [])
    with pytest.raises(ProjectNotFoundError):
        await service.get(project.id, outsider)
    assert await service.list(outsider) == []


async def test_project_context_preserves_resolved_persona_instructions(rig):
    service, forge, project, _, pods = rig
    contributor = AsyncMock()
    contributor.contribute.return_value = SessionContribution(
        values={"session": {"systemPrompt": "Keep the selected review persona."}},
    )
    service.sessions._contributors.insert(0, contributor)
    await forge.create_and_start_session(launch(project))
    await asyncio.gather(*service.sessions._provisioning_tasks.values())
    prompt = pods.start_calls[-1][1].values["session"]["systemPrompt"]
    assert "Keep the selected review persona." in prompt
    assert "Keep project decisions" in prompt


async def test_archival_blocks_new_work_but_preserves_records(rig):
    service, forge, project, _, _ = rig
    original = await forge.create_and_start_session(launch(project))
    await service.update(project.id, {"status": "archived"}, 1, None)
    with pytest.raises(ProjectConflictError, match="archived"):
        await forge.create_and_start_session(launch(project))
    assert (await service.sessions.get_session(original.id)).coordination.project_id == project.id
    with pytest.raises(ProjectConflictError):
        await service.update(project.id, {"status": "active"}, 1, None)
    await service.update(project.id, {"status": "active"}, 2, None)
    assert (await forge.create_and_start_session(launch(project))).id != original.id


async def test_local_parent_validation_and_remote_link(rig):
    _, forge, project, _, _ = rig
    absent = SessionReference(instance_id="thor", session_id=uuid4())
    with pytest.raises(ValueError, match="not a project member"):
        await forge.create_and_start_session(launch(project, parent=absent))
    parent = await forge.create_and_start_session(launch(project, role="coordinator"))
    child = await forge.create_and_start_session(
        launch(
            project,
            parent=SessionReference(instance_id="thor", session_id=parent.id),
        )
    )
    assert child.coordination.parent.session_id == parent.id
    remote = await forge.create_and_start_session(
        launch(
            project,
            parent=SessionReference(instance_id="spark", session_id=uuid4()),
        )
    )
    assert remote.coordination.parent.instance_id == "spark"


async def test_stale_context_revision_and_missing_dispatch_are_rejected(rig):
    _, forge, project, repo, _ = rig
    with pytest.raises(ProjectConflictError, match="context changed"):
        await forge.create_and_start_session(launch(project, context_revision="stale"))
    with pytest.raises(ValueError, match="dispatch_id"):
        await forge.create_and_start_session(
            launch(project).model_copy(update={"dispatch_id": None})
        )
    assert await repo.list() == []


async def test_receipts_survive_service_replacement_and_export_retry(rig):
    service, forge, project, _, _ = rig
    sender = await forge.create_and_start_session(launch(project))
    receipt = ProjectReceipt(
        project_id=project.id,
        sender=SessionReference(instance_id="thor", session_id=sender.id),
        content="Implemented the requested change; review linked test evidence.",
        evidence=["git:abc123"],
    )
    await service.record(receipt, None)
    await service.record(receipt, None)
    with pytest.raises(ProjectConflictError):
        await service.record(receipt.model_copy(update={"content": "different"}), None)
    restored = ProjectService(
        service.repository, service.workspace, service.sessions, instance_id="thor"
    )
    page = await restored.repository.receipts(project.id, 0, 10)
    assert len(page) == 1 and page[0]["acknowledged_at"] is None
    service.workspace.archive_receipt.side_effect = OSError("disk offline")
    with pytest.raises(OSError):
        await restored.export(project.id, None, after=0, limit=10)
    assert len(await restored.repository.receipts(project.id, 0, 10)) == 1
    service.workspace.archive_receipt.side_effect = None
    result = await restored.export(project.id, None, after=0, limit=10)
    assert result == {"exported": 1, "next_cursor": 1}
    assert await restored.repository.acknowledge(project.id, receipt.id)


async def test_public_api_project_filter_and_legacy_sessions(rig):
    service, _, project, _, _ = rig
    app = FastAPI()
    app.include_router(
        create_router(
            service.sessions,
            project_service=service,
            session_participant_service=make_session_participant_service(service.sessions),
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        data = launch(project, role="coordinator").model_dump(mode="json")
        first = await client.post("/api/v1/forge/sessions", json=data)
        assert first.status_code == 201, first.text
        again = await client.post("/api/v1/forge/sessions", json=data)
        assert first.json()["id"] == again.json()["id"]
        legacy = await client.post(
            "/api/v1/forge/sessions", json={"name": "legacy", "model": "test"}
        )
        assert legacy.status_code == 201 and legacy.json()["coordination"] is None
        filtered = await client.get(
            "/api/v1/forge/sessions", params={"project_id": str(project.id)}
        )
        assert [s["id"] for s in filtered.json()] == [first.json()["id"]]
        no_workers = await client.get("/api/v1/forge/sessions", params={"role": "worker"})
        assert no_workers.json() == []
        conflict = await client.post("/api/v1/forge/sessions", json={**data, "name": "changed"})
        assert conflict.status_code == 409


@pytest.mark.parametrize("role", ["coordinator", "worker", "reviewer", "researcher"])
async def test_project_instructions_flow_without_forge_role_policy(rig, role):
    service, forge, project, _, pods = rig
    source = (
        "## AGENTS.md\nPreserve all active objectives.\n## instructions/work.md\nUse our workflow."
    )
    service.workspace.context.return_value = (source, "instruction-revision")
    await forge.create_and_start_session(
        launch(project, role=role, objective="Review project navigation")
    )
    await asyncio.gather(*service.sessions._provisioning_tasks.values())
    prompt = pods.start_calls[-1][1].values["session"]["systemPrompt"]
    assert source in prompt
    assert f"Project role: {role}" in prompt
    assert "Review project navigation" in prompt
    assert "Read the project's coordinator skill" not in prompt
    assert "Tool delivery is not task completion" not in prompt
