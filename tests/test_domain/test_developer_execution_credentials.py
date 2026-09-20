from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest

from niuu.ports.workload_identity import IssuedWorkloadToken
from volundr.composition_builders import _create_developer_execution_credential_service
from volundr.config import Settings
from volundr.domain.models import PodSpecAdditions, Session, SessionStatus
from volundr.domain.services.developer_execution_credentials import (
    _ACTIVE_STATUSES,
    DeveloperExecutionCredentialBinding,
    DeveloperExecutionCredentialError,
    DeveloperExecutionCredentialService,
)
from volundr.ports.developer_execution_credentials import DeveloperCredentialProjection


class Repository:
    def __init__(self, sessions: list[Session]) -> None:
        self.sessions = sessions
        self.updated: list[Session] = []

    async def list(self, **_kwargs):
        return self.sessions

    async def update(self, item: Session):
        self.updated.append(item)
        return item


class Issuer:
    enabled = True

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def issue_token(self, **kwargs):
        self.calls.append(kwargs)
        return IssuedWorkloadToken(token=f"token-{len(self.calls)}", expires_at=9999999999)


class Projection:
    def __init__(self, backend: str = "docker") -> None:
        self.backend = backend
        self.projected: list[tuple[UUID, str, str]] = []
        self.removed: list[UUID] = []

    def supports(self, runtime_backend: str) -> bool:
        return runtime_backend == self.backend

    async def project(self, *, session_id, token, runtime_backend):
        self.projected.append((session_id, token, runtime_backend))
        return DeveloperCredentialProjection(
            token_file="/runtime/token", pod_spec=PodSpecAdditions()
        )

    async def remove(self, session_id):
        self.removed.append(session_id)


def session(*, child: bool = False, status: SessionStatus = SessionStatus.RUNNING) -> Session:
    execution_id = uuid4()
    provenance = {
        "execution_id": str(execution_id),
        "parent_node_id": "delivery-workstreams",
        "parent_session_key": (
            f"workflow:a2a-{uuid4().hex}" if child else f"workflow:developer-{execution_id.hex}"
        ),
        "coordinator_id": "developer-coordinator",
    }
    if child:
        provenance.update(
            {
                "child_attempt_id": str(uuid4()),
                "child_intent_id": str(uuid4()),
                "child_task_id": "task-child-1",
            }
        )
    return Session(
        name="developer",
        status=status,
        owner_id="owner-1",
        tenant_id="tenant-1",
        workload_type="ravn_flock",
        workload_config={"provenance": {"developer_execution": provenance}},
    )


def service(repository: Repository, issuer: Issuer, projection: Projection, **kwargs):
    return DeveloperExecutionCredentialService(
        repository=repository,  # type: ignore[arg-type]
        token_issuer=issuer,  # type: ignore[arg-type]
        projection=projection,  # type: ignore[arg-type]
        runtime_backend="docker",
        refresh_interval_seconds=0.01,
        trusted_signing_configured=True,
        **kwargs,
    )


def test_binding_requires_complete_child_lineage() -> None:
    item = session(child=True)
    raw = item.workload_config["provenance"]["developer_execution"]
    raw.pop("child_task_id")
    with pytest.raises(DeveloperExecutionCredentialError, match="attempt, intent, and task"):
        DeveloperExecutionCredentialBinding.from_session(item)


@pytest.mark.asyncio
@pytest.mark.parametrize("child", [False, True])
async def test_project_mints_exact_scope_from_durable_session(child: bool) -> None:
    item = session(child=child)
    issuer = Issuer()
    projection = Projection()
    result = await service(Repository([item]), issuer, projection).project(item)

    assert result is not None
    issued = issuer.calls[0]
    assert issued["principal"].user_id == "owner-1"
    assert issued["principal"].tenant_id == "tenant-1"
    assert issued["principal"].roles == ["volundr:developer"]
    assert issued["claims"]["scopes"] == ["ting:developer:coordinate"]
    assert issued["claims"]["forge_session_id"] == str(item.id)
    assert "owner_id" not in issued["claims"]
    assert "tenant_id" not in issued["claims"]
    assert bool(issued["claims"].get("child_attempt_id")) is child
    assert projection.projected == [(item.id, "token-1", "docker")]


@pytest.mark.asyncio
async def test_restart_reconciles_active_and_cleans_terminal() -> None:
    active = session(status=SessionStatus.RUNNING)
    stopped = session(status=SessionStatus.STOPPED)
    issuer = Issuer()
    projection = Projection()
    credential_service = service(Repository([active, stopped]), issuer, projection)

    await credential_service.reconcile_once()
    await credential_service.reconcile_once()

    assert [entry[0] for entry in projection.projected] == [active.id, active.id]
    assert projection.projected[0][1] != projection.projected[1][1]
    assert projection.removed == [stopped.id, stopped.id]


@pytest.mark.asyncio
async def test_restart_isolates_malformed_binding_and_rotates_valid_session() -> None:
    malformed = session()
    malformed.workload_config["provenance"]["developer_execution"]["execution_id"] = "bad"
    valid = session()
    repository = Repository([malformed, valid])
    issuer = Issuer()
    projection = Projection()

    await service(repository, issuer, projection).reconcile_once()

    assert projection.projected == [(valid.id, "token-1", "docker")]
    assert repository.updated[0].id == malformed.id
    assert repository.updated[0].status is SessionStatus.FAILED
    assert "must be a UUID" in (repository.updated[0].error or "")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [SessionStatus.STOPPED, SessionStatus.FAILED])
async def test_terminal_legacy_binding_is_cleaned_without_rewriting_history(status) -> None:
    historical = session(status=status)
    historical.workload_config["provenance"]["developer_execution"]["execution_id"] = "legacy"
    active = session()
    repository = Repository([historical, active])
    issuer = Issuer()
    projection = Projection()

    await service(repository, issuer, projection).reconcile_once()

    assert repository.updated == []
    assert projection.removed == [historical.id]
    assert projection.projected == [(active.id, "token-1", "docker")]


@pytest.mark.asyncio
async def test_legacy_cleanup_failure_does_not_prevent_other_token_rotation() -> None:
    historical = session(status=SessionStatus.STOPPED)
    historical.workload_config["provenance"]["developer_execution"]["execution_id"] = "legacy"
    active = session()

    class FailingCleanup(Projection):
        async def remove(self, session_id):
            raise OSError("projection cannot be removed")

    repository = Repository([historical, active])
    projection = FailingCleanup()
    with pytest.raises(DeveloperExecutionCredentialError, match="reconciliation failed"):
        await service(repository, Issuer(), projection).reconcile_once()

    assert repository.updated == []
    assert projection.projected == [(active.id, "token-1", "docker")]


@pytest.mark.asyncio
async def test_rotation_loop_survives_failed_cycle() -> None:
    item = session()
    repository = Repository([item])
    issuer = Issuer()
    projection = Projection()
    credential_service = service(repository, issuer, projection)
    original = credential_service._reconcile_active_cycle
    calls = 0

    async def flaky() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise DeveloperExecutionCredentialError("transient")
        await original()

    credential_service._reconcile_active_cycle = flaky  # type: ignore[method-assign]
    await credential_service.start()
    await asyncio.sleep(0.035)
    await credential_service.stop()

    assert calls >= 3
    assert len(projection.projected) >= 2


@pytest.mark.asyncio
async def test_rotation_loop_survives_a_bare_repository_exception() -> None:
    """A transient repository fault (e.g. a dropped asyncpg connection) must not

    kill the rotation loop, even though it is not a
    `DeveloperExecutionCredentialError` — previously only that narrower type was
    caught at the cycle boundary, so a bare connection error would propagate
    out of `_run()` and silently end rotation for good.
    """
    item = session()
    repository = Repository([item])
    issuer = Issuer()
    projection = Projection()
    credential_service = service(repository, issuer, projection)
    original = credential_service._reconcile_active_cycle
    calls = 0

    async def flaky() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ConnectionError("connection to server was lost")
        await original()

    credential_service._reconcile_active_cycle = flaky  # type: ignore[method-assign]
    await credential_service.start()
    await asyncio.sleep(0.035)
    await credential_service.stop()

    assert calls >= 3
    assert len(projection.projected) >= 2


@pytest.mark.asyncio
async def test_periodic_cycle_only_lists_active_statuses_not_full_history() -> None:
    """The periodic cycle must not repeat reconcile_once()'s unbounded list()

    scan: doing that every tick would make each interval's DB read grow with
    total historical session count instead of active-session count.
    """
    item = session()
    requested_statuses: list[SessionStatus | None] = []

    class TrackingRepository(Repository):
        async def list(self, status: SessionStatus | None = None, **_kwargs):
            requested_statuses.append(status)
            return await super().list(status=status, **_kwargs)

    repository = TrackingRepository([item])
    credential_service = service(repository, Issuer(), Projection())

    await credential_service._reconcile_active_cycle()

    assert None not in requested_statuses
    assert set(requested_statuses) == set(_ACTIVE_STATUSES)


@pytest.mark.asyncio
async def test_periodic_cycle_removes_projection_for_session_that_left_active_set() -> None:
    """Cleanup on the transition: once a tracked session is no longer among the

    active rows the cycle fetches, its projection is removed — without waiting
    for (or repeating) a full-history scan.
    """
    item = session()
    repository = Repository([item])
    projection = Projection()
    credential_service = service(repository, Issuer(), projection)

    await credential_service._reconcile_active_cycle()
    assert projection.projected == [(item.id, "token-1", "docker")]

    repository.sessions = []
    await credential_service._reconcile_active_cycle()

    assert projection.removed == [item.id]
    assert item.id not in credential_service._locations


@pytest.mark.asyncio
async def test_rotation_loop_death_is_logged_critical(caplog) -> None:
    """If the cycle-boundary catch-all is ever bypassed, the loop's own death

    must still be loud — no-fallbacks forbids a background loop dying with
    only a debug-level trace, since tokens would then expire silently.
    """
    item = session()
    repository = Repository([item])
    credential_service = service(repository, Issuer(), Projection())

    async def explode() -> None:
        raise RuntimeError("unexpected escape from the cycle catch-all")

    credential_service._run = explode  # type: ignore[method-assign]
    with caplog.at_level("CRITICAL"):
        await credential_service.start()
        await asyncio.sleep(0.02)

    assert any(
        record.levelname == "CRITICAL" and "rotation loop exited unexpectedly" in record.message
        for record in caplog.records
    )
    credential_service._task = None


def test_configured_backend_and_trusted_signer_are_required() -> None:
    issuer = Issuer()
    with pytest.raises(DeveloperExecutionCredentialError, match="does not support"):
        service(Repository([]), issuer, Projection("kubernetes"))
    with pytest.raises(DeveloperExecutionCredentialError, match="trusted by Ting"):
        DeveloperExecutionCredentialService(
            repository=Repository([]),  # type: ignore[arg-type]
            token_issuer=issuer,  # type: ignore[arg-type]
            projection=Projection(),  # type: ignore[arg-type]
            runtime_backend="docker",
            refresh_interval_seconds=1,
            trusted_signing_configured=False,
        )


def test_composition_rejects_refresh_interval_at_or_above_token_ttl() -> None:
    settings = Settings()
    settings.workload_identity.token_ttl_seconds = 300
    settings.developer_execution_credentials.enabled = True
    settings.developer_execution_credentials.projection_adapter = "invalid.Adapter"
    settings.developer_execution_credentials.refresh_interval_seconds = 300

    with pytest.raises(ValueError, match="must be less than"):
        _create_developer_execution_credential_service(
            settings,
            repository=Repository([]),
            token_issuer=Issuer(),
            runtime_backend="docker",
        )
