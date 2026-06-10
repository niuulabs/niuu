"""Tests for the external session discovery and import service."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from volundr.domain.models import (
    ExternalSessionRecord,
    LocalMountSource,
    Session,
    SessionSpec,
)
from volundr.domain.ports import ExternalSessionProvider
from volundr.domain.services import SessionService
from volundr.domain.services.external_sessions import (
    ExternalSessionAlreadyImportedError,
    ExternalSessionNotFoundError,
    ExternalSessionPathNotAllowedError,
    ExternalSessionProviderNotFoundError,
    ExternalSessionService,
    ExternalSessionWorkspaceError,
)


class FakeProvider(ExternalSessionProvider):
    """In-memory external session provider for tests."""

    def __init__(self, name: str, harness: str, records: list[ExternalSessionRecord]):
        self._name = name
        self._harness = harness
        self._records = records

    @property
    def name(self) -> str:
        return self._name

    @property
    def harness(self) -> str:
        return self._harness

    async def list_sessions(self) -> list[ExternalSessionRecord]:
        return list(self._records)

    async def get_session(self, external_id: str) -> ExternalSessionRecord | None:
        for record in self._records:
            if record.external_id == external_id:
                return record
        return None


def _record(
    provider: str,
    harness: str,
    external_id: str,
    workspace: Path,
    *,
    updated_at: datetime | None = None,
) -> ExternalSessionRecord:
    return ExternalSessionRecord(
        provider=provider,
        harness=harness,
        external_id=external_id,
        workspace_path=str(workspace),
        title=f"session {external_id}",
        model="claude-sonnet-4-6",
        created_at=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
        updated_at=updated_at or datetime(2026, 6, 1, 11, 0, tzinfo=UTC),
        live=False,
        workspace_exists=workspace.is_dir(),
    )


@pytest.fixture
def session_service(repository, pod_manager) -> SessionService:
    return SessionService(repository=repository, pod_manager=pod_manager)


class TestListExternalSessions:
    async def test_merges_providers_newest_first(
        self, repository, session_service, tmp_path: Path
    ) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        claude = FakeProvider(
            "claude-code",
            "claude",
            [
                _record(
                    "claude-code",
                    "claude",
                    "claude-1",
                    workspace,
                    updated_at=datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
                )
            ],
        )
        codex = FakeProvider(
            "codex",
            "codex",
            [
                _record(
                    "codex",
                    "codex",
                    "codex-1",
                    workspace,
                    updated_at=datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
                )
            ],
        )
        service = ExternalSessionService([claude, codex], repository, session_service)

        records = await service.list_external_sessions()

        assert [r.external_id for r in records] == ["codex-1", "claude-1"]

    async def test_filters_by_provider(self, repository, session_service, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        claude = FakeProvider(
            "claude-code",
            "claude",
            [_record("claude-code", "claude", "claude-1", workspace)],
        )
        codex = FakeProvider("codex", "codex", [])
        service = ExternalSessionService([claude, codex], repository, session_service)

        records = await service.list_external_sessions(provider="claude-code")

        assert [r.external_id for r in records] == ["claude-1"]

    async def test_unknown_provider_raises(self, repository, session_service) -> None:
        service = ExternalSessionService([], repository, session_service)

        with pytest.raises(ExternalSessionProviderNotFoundError):
            await service.list_external_sessions(provider="nope")

    async def test_annotates_already_imported(
        self, repository, session_service, tmp_path: Path
    ) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        provider = FakeProvider(
            "claude-code",
            "claude",
            [_record("claude-code", "claude", "claude-1", workspace)],
        )
        existing = Session(
            name="already-imported",
            origin="claude",
            external_session_id="claude-1",
            source=LocalMountSource(local_path=str(workspace)),
        )
        await repository.create(existing)
        service = ExternalSessionService([provider], repository, session_service)

        records = await service.list_external_sessions()

        assert records[0].imported_session_id == existing.id


class TestImportSession:
    async def test_imports_as_volundr_session(
        self, repository, session_service, tmp_path: Path
    ) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        provider = FakeProvider(
            "claude-code",
            "claude",
            [_record("claude-code", "claude", "claude-1", workspace)],
        )
        service = ExternalSessionService([provider], repository, session_service)

        session = await service.import_session("claude-code", "claude-1")

        assert session.origin == "claude"
        assert session.external_session_id == "claude-1"
        assert isinstance(session.source, LocalMountSource)
        assert session.source.local_path == str(workspace)
        assert session.model == "claude-sonnet-4-6"
        assert session.name == "claude-import-claude"
        assert await repository.get(session.id) is not None

    async def test_custom_name_is_used(self, repository, session_service, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        provider = FakeProvider(
            "claude-code",
            "claude",
            [_record("claude-code", "claude", "claude-1", workspace)],
        )
        service = ExternalSessionService([provider], repository, session_service)

        session = await service.import_session("claude-code", "claude-1", name="my-import")

        assert session.name == "my-import"

    async def test_unknown_provider_raises(self, repository, session_service) -> None:
        service = ExternalSessionService([], repository, session_service)

        with pytest.raises(ExternalSessionProviderNotFoundError):
            await service.import_session("nope", "id-1")

    async def test_unknown_session_raises(self, repository, session_service) -> None:
        provider = FakeProvider("claude-code", "claude", [])
        service = ExternalSessionService([provider], repository, session_service)

        with pytest.raises(ExternalSessionNotFoundError):
            await service.import_session("claude-code", "missing")

    async def test_duplicate_import_raises(
        self, repository, session_service, tmp_path: Path
    ) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        provider = FakeProvider(
            "claude-code",
            "claude",
            [_record("claude-code", "claude", "claude-1", workspace)],
        )
        service = ExternalSessionService([provider], repository, session_service)
        first = await service.import_session("claude-code", "claude-1")

        with pytest.raises(ExternalSessionAlreadyImportedError) as exc_info:
            await service.import_session("claude-code", "claude-1")

        assert exc_info.value.session_id == first.id

    async def test_missing_workspace_raises(
        self, repository, session_service, tmp_path: Path
    ) -> None:
        provider = FakeProvider(
            "claude-code",
            "claude",
            [_record("claude-code", "claude", "claude-1", tmp_path / "gone")],
        )
        service = ExternalSessionService([provider], repository, session_service)

        with pytest.raises(ExternalSessionWorkspaceError):
            await service.import_session("claude-code", "claude-1")


class TestMountPrefixPolicy:
    async def test_import_outside_allowed_prefixes_raises(
        self, repository, session_service, tmp_path: Path
    ) -> None:
        allowed = tmp_path / "allowed"
        outside = tmp_path / "outside"
        allowed.mkdir()
        outside.mkdir()
        provider = FakeProvider(
            "claude-code",
            "claude",
            [_record("claude-code", "claude", "claude-1", outside)],
        )
        service = ExternalSessionService(
            [provider],
            repository,
            session_service,
            allowed_workspace_prefixes=[str(allowed)],
        )

        with pytest.raises(ExternalSessionPathNotAllowedError):
            await service.import_session("claude-code", "claude-1")

    async def test_import_under_allowed_prefix_succeeds(
        self, repository, session_service, tmp_path: Path
    ) -> None:
        workspace = tmp_path / "allowed" / "ws"
        workspace.mkdir(parents=True)
        provider = FakeProvider(
            "claude-code",
            "claude",
            [_record("claude-code", "claude", "claude-1", workspace)],
        )
        service = ExternalSessionService(
            [provider],
            repository,
            session_service,
            allowed_workspace_prefixes=[str(tmp_path / "allowed")],
        )

        session = await service.import_session("claude-code", "claude-1")

        assert session.external_session_id == "claude-1"

    async def test_listing_annotates_workspace_allowed(
        self, repository, session_service, tmp_path: Path
    ) -> None:
        allowed = tmp_path / "allowed"
        outside = tmp_path / "outside"
        allowed.mkdir()
        outside.mkdir()
        provider = FakeProvider(
            "claude-code",
            "claude",
            [
                _record("claude-code", "claude", "claude-ok", allowed),
                _record("claude-code", "claude", "claude-denied", outside),
            ],
        )
        service = ExternalSessionService(
            [provider],
            repository,
            session_service,
            allowed_workspace_prefixes=[str(allowed)],
        )

        records = await service.list_external_sessions()

        allowed_map = {r.external_id: r.workspace_allowed for r in records}
        assert allowed_map == {"claude-ok": True, "claude-denied": False}

    async def test_empty_prefixes_allow_all(
        self, repository, session_service, tmp_path: Path
    ) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        provider = FakeProvider(
            "claude-code",
            "claude",
            [_record("claude-code", "claude", "claude-1", workspace)],
        )
        service = ExternalSessionService([provider], repository, session_service)

        records = await service.list_external_sessions()

        assert records[0].workspace_allowed is True


class TestExternalResumeOverlay:
    def test_overlay_skipped_without_external_id(self) -> None:
        session = Session(name="normal")
        spec = SessionSpec(values={}, pod_spec=None)

        SessionService._overlay_external_resume(session, spec)

        assert "broker" not in spec.values

    def test_claude_session_pins_persistent_transport(self) -> None:
        session = Session(
            name="imported",
            origin="claude",
            external_session_id="claude-1",
        )
        spec = SessionSpec(
            values={"broker": {"cliType": "claude", "transport": "sdk"}},
            pod_spec=None,
        )

        SessionService._overlay_external_resume(session, spec)

        broker = spec.values["broker"]
        assert broker["resumeSessionId"] == "claude-1"
        assert broker["cliType"] == "claude"
        assert broker["transportAdapter"] == (
            "skuld.transports.persistent_subprocess.PersistentSubprocessTransport"
        )

    def test_codex_session_uses_websocket_transport(self) -> None:
        session = Session(
            name="imported",
            origin="codex",
            external_session_id="thread-1",
        )
        spec = SessionSpec(values={}, pod_spec=None)

        SessionService._overlay_external_resume(session, spec)

        broker = spec.values["broker"]
        assert broker["resumeSessionId"] == "thread-1"
        assert broker["cliType"] == "codex-ws"
        assert broker["transportAdapter"] == "skuld.transports.codex_ws.CodexWebSocketTransport"

    async def test_pipeline_start_passes_resume_to_pod_manager(
        self, repository, pod_manager
    ) -> None:
        service = SessionService(repository=repository, pod_manager=pod_manager)
        session = await service.create_session(
            name="imported",
            model="claude-sonnet-4-6",
            source=LocalMountSource(local_path="/tmp/ws"),
            origin="claude",
            external_session_id="claude-1",
        )

        await service._start_with_pipeline(session, None, None, None, False)

        _, spec = pod_manager.start_calls[0]
        assert spec.values["broker"]["resumeSessionId"] == "claude-1"
