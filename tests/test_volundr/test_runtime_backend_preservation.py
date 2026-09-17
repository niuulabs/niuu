"""Composition-to-reconciliation proof: local gateways are not Kubernetes orphans."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tests.conftest import InMemorySessionRepository
from volundr.adapters.outbound.local_process import LocalProcessPodManager
from volundr.composition_builders import _runtime_backend
from volundr.config import Settings
from volundr.domain.models import GitSource, Session, SessionStatus
from volundr.domain.services.session import SessionService


@pytest.fixture
def local(tmp_path):
    settings = Settings(
        pod_manager={"adapter": "volundr.adapters.outbound.local_process.LocalProcessPodManager"},
        local_mounts={"enabled": True, "mini_mode": True},
    )
    manager = LocalProcessPodManager(state_file=str(tmp_path / "process-state.json"))
    manager.stop = AsyncMock(side_effect=AssertionError("Must not stop a preserved local gateway"))
    manager.status = AsyncMock(return_value=SessionStatus.RUNNING)
    return settings, manager


def test_actual_local_adapter_declares_process_backend(local):
    settings, manager = local
    assert _runtime_backend(settings, manager) == "process"


@pytest.mark.parametrize(
    "status", [SessionStatus.STOPPED, SessionStatus.ARCHIVED, SessionStatus.FAILED]
)
async def test_composed_local_service_never_reaps_terminal_rows(local, status):
    settings, manager = local
    repo = InMemorySessionRepository()
    session = Session(
        id=uuid4(),
        name="preserved",
        model="gpt-6-astra",
        status=status,
        source=GitSource(type="git", repo_url="https://example.test/r.git", branch="test"),
        created_at=datetime.now(UTC),
        last_active=datetime.now(UTC),
    )
    await repo.create(session)
    service = SessionService(
        repository=repo,
        pod_manager=manager,
        validate_repos=False,
        runtime_backend=_runtime_backend(settings, manager),
    )
    assert await service.reconcile_active_sessions() == 0
    manager.stop.assert_not_awaited()
    manager.status.assert_not_awaited()
    assert (await repo.get(session.id)).status == status


async def test_composed_local_service_keeps_reconciling_running_rows(local):
    settings, manager = local
    repo = InMemorySessionRepository()
    session = Session(
        id=uuid4(),
        name="running",
        model="gpt-6-astra",
        status=SessionStatus.RUNNING,
        source=GitSource(type="git", repo_url="https://example.test/r.git", branch="test"),
        created_at=datetime.now(UTC),
        last_active=datetime.now(UTC),
    )
    await repo.create(session)
    service = SessionService(
        repository=repo,
        pod_manager=manager,
        validate_repos=False,
        runtime_backend=_runtime_backend(settings, manager),
    )
    assert await service.reconcile_active_sessions() == 0
    manager.status.assert_awaited_once_with(session)
    manager.stop.assert_not_awaited()


def test_custom_adapter_declares_backend_without_class_name_routing(local):
    settings, manager = local

    class Custom(LocalProcessPodManager):
        @property
        def runtime_backend(self):
            return "custom-process"

    manager.__class__ = Custom
    assert _runtime_backend(settings, manager) == "custom-process"


def test_legacy_kubernetes_and_openshell_keep_existing_semantics(pod_manager):
    settings = Settings()
    assert _runtime_backend(settings, pod_manager) == "kubernetes"
    settings.pod_manager.adapter = "test.OpenShellPodManager"
    assert _runtime_backend(settings, pod_manager) == "openshell"


def test_explicit_backend_overrides_adapter_identity(local):
    settings, manager = local
    settings.pod_manager.runtime_backend = "vm"
    assert _runtime_backend(settings, manager) == "vm"


def test_vm_adapter_declares_its_backend():
    from volundr.adapters.outbound.vm_pod_manager import VmPodManager

    assert VmPodManager.__new__(VmPodManager).runtime_backend == "vm"
