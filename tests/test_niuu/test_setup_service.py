"""Tests for the setup service, its state model and the file store."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from niuu.adapters.file_setup_state import FileSetupStateStore
from niuu.domain.services.setup import SetupService
from niuu.domain.setup import KNOWN_SETUP_STEPS, SetupState, SetupStepRecord, SystemReport

MOD = "niuu.domain.services.setup"


class TestStateModel:
    def test_round_trip(self) -> None:
        now = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)
        state = SetupState().with_step(SetupStepRecord("system", now, {"ok": True}))
        state = state.mark_completed(now)
        restored = SetupState.from_dict(json.loads(json.dumps(state.to_dict())))
        assert restored == state

    def test_from_dict_tolerates_missing_fields(self) -> None:
        state = SetupState.from_dict({})
        assert state.completed is False
        assert state.steps == {}

    def test_from_dict_ignores_non_dict_steps_and_data(self) -> None:
        raw = {
            "steps": {
                "git": {"step": "git", "completed_at": "2026-09-12T10:00:00+00:00", "data": 5},
                "bad": "nope",
            }
        }
        state = SetupState.from_dict(raw)
        assert list(state.steps) == ["git"]
        assert state.steps["git"].data == {}

    def test_report_healthy(self) -> None:
        from niuu.domain.setup import SystemCheck

        report = SystemReport(
            host=None,
            checks=[
                SystemCheck("a", True, "ok"),
                SystemCheck("b", False, "warn", warn_only=True),
            ],
        )
        assert report.healthy is True
        report = SystemReport(host=None, checks=[SystemCheck("a", False, "bad")])
        assert report.healthy is False


class TestFileStore:
    @pytest.mark.asyncio
    async def test_missing_file_is_empty_state(self, tmp_path: Path) -> None:
        store = FileSetupStateStore(path=str(tmp_path / "nested" / "state.json"))
        assert await store.load() == SetupState()

    @pytest.mark.asyncio
    async def test_save_and_load(self, tmp_path: Path) -> None:
        store = FileSetupStateStore(path=str(tmp_path / "nested" / "state.json"))
        state = SetupState().with_step(
            SetupStepRecord("welcome", datetime(2026, 1, 1, tzinfo=UTC), {})
        )
        await store.save(state)
        assert store.path.exists()
        assert await store.load() == state

    @pytest.mark.asyncio
    async def test_rejects_non_object(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        path.write_text("[1, 2]")
        store = FileSetupStateStore(path=str(path))
        with pytest.raises(ValueError, match="JSON object"):
            await store.load()


@pytest.fixture
def store(tmp_path: Path) -> FileSetupStateStore:
    return FileSetupStateStore(path=str(tmp_path / "state.json"))


class TestService:
    @pytest.mark.asyncio
    async def test_step_progress_and_completion(self, store: FileSetupStateStore) -> None:
        service = SetupService(store, enabled=True, mode="docker")
        assert service.enabled is True
        assert service.mode == "docker"
        state = await service.complete_step("providers", {"anthropic": True})
        assert state.steps["providers"].data == {"anthropic": True}
        assert (await service.state()).completed is False
        done = await service.complete()
        assert done.completed is True
        assert done.completed_at is not None
        assert "providers" in done.steps
        reset = await service.reset()
        assert reset == SetupState()

    @pytest.mark.asyncio
    async def test_unknown_step_rejected(self, store: FileSetupStateStore) -> None:
        service = SetupService(store, enabled=True, mode="docker")
        with pytest.raises(ValueError, match="Unknown setup step"):
            await service.complete_step("nope")
        assert list(KNOWN_SETUP_STEPS)[0] == "welcome"

    def test_host_facts_absent(self, store: FileSetupStateStore, tmp_path: Path) -> None:
        assert SetupService(store, enabled=False, mode="mini").host_facts() is None
        missing = SetupService(
            store, enabled=True, mode="docker", host_facts_file=str(tmp_path / "nope.json")
        )
        assert missing.host_facts() is None

    def test_host_facts_present(self, store: FileSetupStateStore, tmp_path: Path) -> None:
        facts = tmp_path / "host-facts.json"
        facts.write_text(json.dumps({"hostname": "spark", "gpus": []}))
        service = SetupService(store, enabled=True, mode="docker", host_facts_file=str(facts))
        assert service.host_facts() == {"hostname": "spark", "gpus": []}

    def test_host_facts_invalid(self, store: FileSetupStateStore, tmp_path: Path) -> None:
        facts = tmp_path / "host-facts.json"
        facts.write_text("[]")
        service = SetupService(store, enabled=True, mode="docker", host_facts_file=str(facts))
        with pytest.raises(ValueError, match="JSON object"):
            service.host_facts()

    @pytest.mark.asyncio
    async def test_system_report_all_good(self, store: FileSetupStateStore, tmp_path: Path) -> None:
        facts = tmp_path / "host-facts.json"
        facts.write_text(json.dumps({"hostname": "spark"}))
        sock = tmp_path / "docker.sock"
        sock.write_text("")
        service = SetupService(
            store,
            enabled=True,
            mode="docker",
            host_facts_file=str(facts),
            docker_socket_path=str(sock),
            database_probe=AsyncMock(return_value=True),
        )
        with patch(f"{MOD}.shutil.which", return_value="/usr/bin/git"):
            report = await service.system()
        assert report.host == {"hostname": "spark"}
        assert report.healthy is True
        by_name = {c.name: c for c in report.checks}
        assert by_name["database"].passed is True
        assert by_name["docker socket"].passed is True
        assert by_name["git"].message.endswith("/usr/bin/git")

    @pytest.mark.asyncio
    async def test_system_report_degraded(self, store: FileSetupStateStore, tmp_path: Path) -> None:
        service = SetupService(
            store,
            enabled=True,
            mode="docker",
            docker_socket_path=str(tmp_path / "missing.sock"),
            database_probe=AsyncMock(side_effect=OSError("refused")),
        )
        with patch(f"{MOD}.shutil.which", return_value=None):
            report = await service.system()
        by_name = {c.name: c for c in report.checks}
        assert by_name["host facts"].passed is False
        assert by_name["host facts"].warn_only is True
        assert by_name["database"].passed is False
        assert "refused" in by_name["database"].message
        assert by_name["docker socket"].warn_only is True
        assert by_name["git"].passed is False
        assert report.healthy is False

    @pytest.mark.asyncio
    async def test_system_report_probe_false_and_unconfigured(
        self, store: FileSetupStateStore
    ) -> None:
        service = SetupService(
            store, enabled=True, mode="docker", database_probe=AsyncMock(return_value=False)
        )
        with patch(f"{MOD}.shutil.which", return_value="/usr/bin/git"):
            report = await service.system()
        assert {c.name: c.passed for c in report.checks}["database"] is False

        unconfigured = SetupService(store, enabled=True, mode="docker")
        with patch(f"{MOD}.shutil.which", return_value="/usr/bin/git"):
            report = await unconfigured.system()
        db = {c.name: c for c in report.checks}["database"]
        assert db.passed is True
        assert db.warn_only is True
