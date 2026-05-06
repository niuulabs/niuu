"""Tests for DispatchService.create_saga_from_template."""

from __future__ import annotations

import textwrap
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.test_tyr.stubs import InMemorySagaRepository, StubVolundrFactory, StubVolundrPort
from tyr.adapters.memory_event_bus import InMemoryEventBus
from tyr.domain.models import SagaStatus
from tyr.domain.services.dispatch_service import DispatchConfig, DispatchService
from tyr.ports.event_bus import TyrEvent

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TS = datetime(2026, 4, 12, 10, 0, 0, tzinfo=UTC)
_OWNER = "test-owner"


class StubTrackerFactory:
    async def for_owner(self, owner_id: str):
        return []


class StubDispatcherRepo:
    async def get_or_create(self, owner_id: str):
        return None

    async def update(self, owner_id: str, **kwargs):
        pass


def _make_service(
    saga_repo: InMemorySagaRepository | None = None,
    volundr: StubVolundrPort | None = None,
    event_bus: InMemoryEventBus | None = None,
    templates_dir: Path | None = None,
) -> DispatchService:
    from tyr.domain.templates import BUNDLED_TEMPLATES_DIR

    _volundr = volundr or StubVolundrPort()
    config = DispatchConfig(
        templates_dir=templates_dir or BUNDLED_TEMPLATES_DIR,
    )
    return DispatchService(
        tracker_factory=StubTrackerFactory(),
        volundr_factory=StubVolundrFactory(_volundr),
        saga_repo=saga_repo or InMemorySagaRepository(),
        dispatcher_repo=StubDispatcherRepo(),
        config=config,
        event_bus=event_bus,
    )


def _write_minimal_template(tmp_path: Path, name: str) -> None:
    (tmp_path / f"{name}.yaml").write_text(
        textwrap.dedent("""\
            name: "Template saga for {event.repo}"
            feature_branch: "{event.branch}"
            base_branch: main
            repos:
              - "{event.repo}"
            phases:
              - name: Execute
                raids:
                  - name: "Execute task in {event.repo}"
                    description: "Do the work"
                    acceptance_criteria:
                      - "Task completed"
                    declared_files: []
                    estimate_hours: 1.0
                    persona: executor
                    prompt: "Execute task in {event.repo} on {event.branch}"
        """),
        encoding="utf-8",
    )


def _write_two_phase_template(tmp_path: Path, name: str) -> None:
    (tmp_path / f"{name}.yaml").write_text(
        textwrap.dedent("""\
            name: "Two-phase"
            feature_branch: main
            base_branch: main
            repos: []
            phases:
              - name: Phase 1
                raids:
                  - name: "Phase 1 raid"
                    description: "First"
                    acceptance_criteria: []
                    declared_files: []
                    estimate_hours: 1.0
                    persona: worker
                    prompt: "Do phase 1"
              - name: Phase 2
                raids:
                  - name: "Phase 2 raid"
                    description: "Second"
                    acceptance_criteria: []
                    declared_files: []
                    estimate_hours: 1.0
                    persona: worker
                    prompt: "Do phase 2"
        """),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCreateSagaFromTemplate:
    async def test_creates_saga_in_repository(self, tmp_path):
        _write_minimal_template(tmp_path, "simple")
        saga_repo = InMemorySagaRepository()
        svc = _make_service(saga_repo=saga_repo, templates_dir=tmp_path)

        saga_id = await svc.create_saga_from_template(
            "simple", {"repo": "myrepo", "branch": "feat/x"}, _OWNER
        )

        assert saga_id
        assert len(saga_repo.sagas) == 1
        saga = list(saga_repo.sagas.values())[0]
        assert saga.owner_id == _OWNER
        assert saga.status == SagaStatus.ACTIVE
        assert "myrepo" in saga.name

    async def test_creates_phases_and_raids_in_repository(self, tmp_path):
        _write_minimal_template(tmp_path, "simple")
        saga_repo = InMemorySagaRepository()
        svc = _make_service(saga_repo=saga_repo, templates_dir=tmp_path)

        await svc.create_saga_from_template(
            "simple", {"repo": "myrepo", "branch": "feat/x"}, _OWNER
        )

        assert len(saga_repo.phases) == 1
        assert len(saga_repo.raids) == 1

    async def test_auto_start_spawns_phase_1_sessions(self, tmp_path):
        _write_minimal_template(tmp_path, "simple")
        volundr = StubVolundrPort()
        saga_repo = InMemorySagaRepository()
        svc = _make_service(saga_repo=saga_repo, volundr=volundr, templates_dir=tmp_path)

        await svc.create_saga_from_template(
            "simple", {"repo": "myrepo", "branch": "feat/x"}, _OWNER, auto_start=True
        )

        assert len(volundr.spawned) == 1
        assert volundr.spawned[0].profile == "executor"

    async def test_auto_start_false_does_not_spawn(self, tmp_path):
        _write_minimal_template(tmp_path, "simple")
        volundr = StubVolundrPort()
        svc = _make_service(volundr=volundr, templates_dir=tmp_path)

        await svc.create_saga_from_template(
            "simple", {"repo": "myrepo", "branch": "feat/x"}, _OWNER, auto_start=False
        )

        assert len(volundr.spawned) == 0

    async def test_multi_phase_only_phase_1_spawned(self, tmp_path):
        _write_two_phase_template(tmp_path, "two")
        volundr = StubVolundrPort()
        saga_repo = InMemorySagaRepository()
        svc = _make_service(saga_repo=saga_repo, volundr=volundr, templates_dir=tmp_path)

        await svc.create_saga_from_template("two", {}, _OWNER, auto_start=True)

        assert len(volundr.spawned) == 1
        assert volundr.spawned[0].name == "phase-1-raid"

    async def test_interpolation_applied_to_payload(self, tmp_path):
        _write_minimal_template(tmp_path, "simple")
        saga_repo = InMemorySagaRepository()
        svc = _make_service(saga_repo=saga_repo, templates_dir=tmp_path)

        await svc.create_saga_from_template(
            "simple", {"repo": "custom-repo", "branch": "my-branch"}, _OWNER
        )

        saga = list(saga_repo.sagas.values())[0]
        assert "custom-repo" in saga.name
        raid = list(saga_repo.raids.values())[0]
        assert "custom-repo" in raid.description or "custom-repo" in raid.name

    async def test_missing_template_raises(self, tmp_path):
        svc = _make_service(templates_dir=tmp_path)
        with pytest.raises(FileNotFoundError):
            await svc.create_saga_from_template("nonexistent", {}, _OWNER)

    async def test_invalid_template_raises_value_error(self, tmp_path):
        (tmp_path / "bad.yaml").write_text(
            textwrap.dedent("""\
                name: "Bad"
                feature_branch: main
                base_branch: main
                repos: []
                phases:
                  - name: P
                    raids:
                      - name: "R"
                        persona: ""
                        prompt: "x"
                        acceptance_criteria: []
                        declared_files: []
                        estimate_hours: 1.0
            """),
            encoding="utf-8",
        )
        svc = _make_service(templates_dir=tmp_path)
        with pytest.raises(ValueError, match="persona"):
            await svc.create_saga_from_template("bad", {}, _OWNER)

    async def test_emits_saga_created_event_when_event_bus_provided(self, tmp_path):
        _write_minimal_template(tmp_path, "simple")
        event_bus = InMemoryEventBus()
        q = event_bus.subscribe()
        svc = _make_service(event_bus=event_bus, templates_dir=tmp_path)

        await svc.create_saga_from_template("simple", {"repo": "r", "branch": "main"}, _OWNER)

        events: list[TyrEvent] = []
        while not q.empty():
            events.append(q.get_nowait())

        assert any(e.event == "saga.created" for e in events)

    async def test_no_event_bus_does_not_raise(self, tmp_path):
        _write_minimal_template(tmp_path, "simple")
        svc = _make_service(event_bus=None, templates_dir=tmp_path)

        # Must not raise even without an event bus
        saga_id = await svc.create_saga_from_template(
            "simple", {"repo": "r", "branch": "main"}, _OWNER
        )
        assert saga_id

    async def test_returns_saga_id_string(self, tmp_path):
        _write_minimal_template(tmp_path, "simple")
        svc = _make_service(templates_dir=tmp_path)

        saga_id = await svc.create_saga_from_template(
            "simple", {"repo": "r", "branch": "main"}, _OWNER
        )

        assert isinstance(saga_id, str)
        # Must be a valid UUID string
        import uuid

        uuid.UUID(saga_id)  # raises if invalid


# ---------------------------------------------------------------------------
# Bundled templates through DispatchService
# ---------------------------------------------------------------------------


class TestDispatchServiceBundledTemplates:
    """Smoke-load each bundled template through DispatchService."""

    async def test_ship_template_via_dispatch_service(self):
        saga_repo = InMemorySagaRepository()
        svc = _make_service(saga_repo=saga_repo)
        payload = {"repo": "niuulabs/volundr", "branch": "feat/release", "base_branch": "main"}

        saga_id = await svc.create_saga_from_template("ship", payload, _OWNER, auto_start=False)

        assert saga_id
        assert len(saga_repo.phases) == 4

    async def test_retro_template_via_dispatch_service(self):
        saga_repo = InMemorySagaRepository()
        svc = _make_service(saga_repo=saga_repo)
        payload = {"week": "2026-W15"}

        saga_id = await svc.create_saga_from_template("retro", payload, _OWNER, auto_start=False)

        assert saga_id
        assert len(saga_repo.phases) == 2
