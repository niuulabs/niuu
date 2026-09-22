"""Execution selection precedes and survives the session contributor pipeline."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from tests.conftest import InMemorySessionRepository, MockPodManager
from volundr.adapters.outbound.contributors.storage import StorageContributor
from volundr.domain.models import LaunchSpec, SessionStatus
from volundr.domain.ports import SessionContext, SessionContribution
from volundr.domain.services.session import SessionService


class Resolver:
    def __init__(self):
        self.selected = []
        self.reference = {
            "execution_id": "openshell",
            "revision": "1",
            "plan_digest": "test-digest",
        }
        self.plan = SimpleNamespace(
            runtime=SimpleNamespace(
                contributor_backend="openshell",
                storage_mode="vm",
                capabilities=("native_credentials",),
            )
        )

    async def has_legacy_allocation(self, session):
        return False

    async def resolve_execution(self, session):
        self.selected.append(session.workload_config.copy())
        return self.plan

    async def execution_for(self, session):
        assert session.workload_config["_compute_execution"] == self.reference
        return self.plan

    def execution_reference(self, plan):
        return self.reference.copy()


class Contributor:
    name = "recording"

    def __init__(self, repository):
        self.repository = repository
        self.contexts = []

    async def contribute(self, session, context):
        persisted = await self.repository.get(session.id)
        assert (
            persisted.workload_config["_compute_execution"]
            == (session.workload_config["_compute_execution"])
        )
        self.contexts.append(context)
        return SessionContribution()

    async def cleanup(self, session, context):
        self.contexts.append(context)


async def start_without_polling(service, session, **kwargs):
    service._poll_readiness = AsyncMock()
    result = await service.start_session(session.id, **kwargs)
    pending = list(service._provisioning_tasks.values())
    await asyncio.gather(*pending)
    return result


async def test_resolves_before_contributors_and_keeps_vm_storage_with_native_credentials():
    repository, manager, resolver = InMemorySessionRepository(), MockPodManager(), Resolver()
    contributor = Contributor(repository)
    service = SessionService(
        repository,
        manager,
        contributors=[contributor],
        execution_resolver=resolver,
        runtime_backend="vm",
    )
    session = await service.create_session(name="test", model="test")
    await start_without_polling(
        service, session, workload_config={"execution_profile": "openshell@1"}
    )
    context = contributor.contexts[0]
    assert resolver.selected[0]["execution_profile"] == "openshell@1"
    assert context.runtime_backend == "openshell"
    assert context.storage_backend == "vm"
    assert context.runtime_capabilities == ("native_credentials",)
    assert manager.start_calls[0][0].workload_config["_compute_execution"] == resolver.reference
    await service._run_cleanup(await repository.get(session.id), None)
    assert contributor.contexts[-1].runtime_backend == "openshell"
    assert contributor.contexts[-1].storage_backend == "vm"


@pytest.mark.parametrize("explicit,expected", [(None, "launch@1"), ("request@2", "request@2")])
async def test_launch_spec_selection_is_below_explicit_request(explicit, expected):
    repository, resolver = InMemorySessionRepository(), Resolver()
    catalog = Mock()
    catalog.get.return_value = LaunchSpec(
        name="selected", workload_config={"execution_profile": "launch@1"}
    )
    service = SessionService(
        repository, MockPodManager(), execution_resolver=resolver, launch_spec_provider=catalog
    )
    session = await service.create_session(name="test", model="test")
    config = {"execution_profile": explicit} if explicit else None
    await start_without_polling(service, session, launch_spec="selected", workload_config=config)
    assert resolver.selected[0]["execution_profile"] == expected


async def test_restart_preserves_pin_when_other_workload_config_changes():
    repository, resolver = InMemorySessionRepository(), Resolver()
    service = SessionService(repository, MockPodManager(), execution_resolver=resolver)
    session = await service.create_session(name="test", model="test")
    session = session.model_copy(
        update={
            "workload_config": {
                "_compute_execution": resolver.reference,
                "execution_profile": "openshell@1",
            },
            "status": SessionStatus.STOPPED,
        }
    )
    await repository.update(session)
    await start_without_polling(service, session, workload_config={"persona": "new-persona"})
    assert resolver.selected[0]["_compute_execution"] == resolver.reference
    assert resolver.selected[0]["persona"] == "new-persona"


async def test_caller_cannot_supply_internal_execution_reference():
    repository, manager, resolver = InMemorySessionRepository(), MockPodManager(), Resolver()
    service = SessionService(repository, manager, execution_resolver=resolver)
    session = await service.create_session(name="test", model="test")
    with pytest.raises(ValueError, match="managed by the session service"):
        await service.start_session(session.id, workload_config={"_compute_execution": {}})
    assert (await repository.get(session.id)).status == session.status
    assert not resolver.selected
    assert not manager.start_calls


async def test_failed_resolution_does_not_start_or_reserve_session():
    repository, manager, resolver = InMemorySessionRepository(), MockPodManager(), Resolver()
    resolver.resolve_execution = AsyncMock(side_effect=ValueError("Unknown execution"))
    service = SessionService(repository, manager, execution_resolver=resolver)
    session = await service.create_session(name="test", model="test")
    with pytest.raises(ValueError, match="Unknown execution"):
        await service.start_session(session.id)
    assert (await repository.get(session.id)).status == session.status
    assert not manager.start_calls


async def test_execution_selection_without_catalog_fails_before_start():
    repository, manager = InMemorySessionRepository(), MockPodManager()
    service = SessionService(repository, manager)
    session = await service.create_session(name="test", model="test")
    with pytest.raises(ValueError, match="requires a configured catalog"):
        await service.start_session(session.id, workload_config={"execution_profile": "selected@1"})
    assert (await repository.get(session.id)).status == session.status
    assert not manager.start_calls


async def test_vm_storage_is_independent_of_openshell_credential_backend():
    storage = AsyncMock()
    contributor = StorageContributor(storage=storage)
    context = SessionContext(runtime_backend="openshell", storage_backend="vm")
    repository = InMemorySessionRepository()
    session = await SessionService(repository, MockPodManager()).create_session(
        name="test", model="test"
    )
    assert (await contributor.contribute(session, context)).values == {}
    await contributor.cleanup(session, context)
    storage.create_session_workspace.assert_not_called()
    storage.archive_session_workspace.assert_not_called()


async def test_catalog_default_does_not_retrofit_active_legacy_allocation():
    repository, resolver = InMemorySessionRepository(), Resolver()
    resolver.has_legacy_allocation = AsyncMock(return_value=True)
    resolver.resolve_execution = AsyncMock(return_value=None)
    catalog = Mock()
    catalog.get_default.return_value = LaunchSpec(
        name="new-default", workload_config={"execution_profile": "new@1"}
    )
    service = SessionService(
        repository, MockPodManager(), execution_resolver=resolver, launch_spec_provider=catalog
    )
    session = await service.create_session(name="test", model="test")
    await start_without_polling(service, session)
    selected = resolver.resolve_execution.call_args.args[0]
    assert "execution_profile" not in selected.workload_config
    assert "_compute_execution" not in (await repository.get(session.id)).workload_config
    catalog.get_default.assert_not_called()


@pytest.mark.parametrize("stop_result", [False, RuntimeError("archive failed")])
@pytest.mark.parametrize("catalog_configured", [False, True])
async def test_durable_compute_delete_retains_session_until_deletion_is_confirmed(
    stop_result, catalog_configured
):
    repository, manager = InMemorySessionRepository(), MockPodManager()
    if isinstance(stop_result, Exception):
        manager.stop = AsyncMock(side_effect=stop_result)
        expected = "archive failed"
    else:
        manager.stop = AsyncMock(return_value=stop_result)
        expected = "deletion was not confirmed"
    service = SessionService(
        repository,
        manager,
        runtime_backend="kubernetes" if catalog_configured else "vm",
        execution_resolver=Resolver() if catalog_configured else None,
    )
    session = await service.create_session(name="test", model="test")

    with pytest.raises(RuntimeError, match=expected):
        await service.delete_session(session.id)

    assert await repository.get(session.id) == session


@pytest.mark.parametrize("operation", ["stop", "delete"])
async def test_cleanup_execution_context_is_resolved_before_infrastructure_release(operation):
    repository, manager, resolver = InMemorySessionRepository(), MockPodManager(), Resolver()
    contributor = Contributor(repository)
    released = False

    async def execution_for(session):
        assert not released
        return resolver.plan

    async def stop(session):
        nonlocal released
        released = True
        return True

    resolver.execution_for = execution_for
    manager.stop = AsyncMock(side_effect=stop)
    service = SessionService(
        repository,
        manager,
        contributors=[contributor],
        execution_resolver=resolver,
    )
    session = await service.create_session(name="test", model="test")
    session = session.model_copy(
        update={
            "workload_config": {"_compute_execution": resolver.reference},
            "status": SessionStatus.RUNNING,
        }
    )
    await repository.update(session)

    if operation == "stop":
        await service.stop_session(session.id)
        assert (await repository.get(session.id)).status == SessionStatus.STOPPED
    else:
        assert await service.delete_session(session.id)
        assert await repository.get(session.id) is None

    assert released
    assert contributor.contexts[-1].runtime_backend == "openshell"


async def test_durable_compute_stop_does_not_cleanup_or_mark_stopped_when_unconfirmed():
    repository, manager = InMemorySessionRepository(), MockPodManager()
    contributor = Contributor(repository)
    manager.stop = AsyncMock(return_value=False)
    service = SessionService(
        repository,
        manager,
        contributors=[contributor],
        runtime_backend="vm",
    )
    session = await service.create_session(name="test", model="test")
    await repository.update(session.with_status(SessionStatus.RUNNING))

    with pytest.raises(RuntimeError, match="stop was not confirmed"):
        await service.stop_session(session.id)

    retained = await repository.get(session.id)
    assert retained.status == SessionStatus.FAILED
    assert contributor.contexts == []
