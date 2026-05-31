"""Tests for ChronicleService."""

from uuid import uuid4

import pytest

from tests.conftest import (
    InMemoryChronicleRepository,
    InMemorySessionRepository,
    MockPodManager,
)
from volundr.domain.models import ChronicleStatus, GitSource, SessionStatus
from volundr.domain.services import (
    ChronicleNotFoundError,
    ChronicleService,
    SessionNotFoundError,
    SessionService,
)

# Type aliases for shorter signatures
ChronRepo = InMemoryChronicleRepository
SessRepo = InMemorySessionRepository
Pods = MockPodManager


class TestChronicleServiceCreate:
    """Tests for ChronicleService.create_chronicle."""

    async def test_create_chronicle_from_session(
        self,
        chronicle_repository: ChronRepo,
        repository: SessRepo,
        pod_manager: Pods,
    ):
        """Creating a chronicle captures session metadata."""
        session_service = SessionService(repository, pod_manager)
        chronicle_service = ChronicleService(chronicle_repository, session_service)

        session = await session_service.create_session(
            name="my-session",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )

        chronicle = await chronicle_service.create_chronicle(session.id)

        assert chronicle.session_id == session.id
        assert chronicle.project == "repo"
        assert chronicle.repo == session.repo
        assert chronicle.branch == session.branch
        assert chronicle.model == session.model
        assert chronicle.status == ChronicleStatus.DRAFT
        assert chronicle.config_snapshot["name"] == "my-session"
        assert chronicle.config_snapshot["model"] == "claude-sonnet-4-20250514"

        # Verify it's in the repository
        stored = await chronicle_repository.get(chronicle.id)
        assert stored is not None
        assert stored.id == chronicle.id

    async def test_create_chronicle_nonexistent_session(
        self,
        chronicle_repository: ChronRepo,
        repository: SessRepo,
        pod_manager: Pods,
    ):
        """Creating a chronicle for a nonexistent session raises SessionNotFoundError."""
        session_service = SessionService(repository, pod_manager)
        chronicle_service = ChronicleService(chronicle_repository, session_service)

        with pytest.raises(SessionNotFoundError):
            await chronicle_service.create_chronicle(uuid4())

    async def test_create_chronicle_derives_project_from_repo(
        self,
        chronicle_repository: ChronRepo,
        repository: SessRepo,
        pod_manager: Pods,
    ):
        """Project name is derived from various repo URL formats."""
        session_service = SessionService(repository, pod_manager)
        chronicle_service = ChronicleService(chronicle_repository, session_service)

        test_cases = [
            ("https://github.com/org/my-project", "my-project"),
            ("https://github.com/org/my-project.git", "my-project"),
            ("https://github.com/org/my-project/", "my-project"),
        ]

        for repo_url, expected_project in test_cases:
            session = await session_service.create_session(
                name="test",
                model="claude-sonnet-4-20250514",
                source=GitSource(repo=repo_url, branch="main"),
            )
            chronicle = await chronicle_service.create_chronicle(session.id)
            assert chronicle.project == expected_project, (
                f"Expected project '{expected_project}' for repo '{repo_url}', "
                f"got '{chronicle.project}'"
            )


class TestChronicleServiceGet:
    """Tests for ChronicleService.get_chronicle."""

    async def test_get_existing_chronicle(
        self,
        chronicle_repository: ChronRepo,
        repository: SessRepo,
        pod_manager: Pods,
    ):
        """Getting an existing chronicle returns it."""
        session_service = SessionService(repository, pod_manager)
        chronicle_service = ChronicleService(chronicle_repository, session_service)

        session = await session_service.create_session(
            name="test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        created = await chronicle_service.create_chronicle(session.id)

        result = await chronicle_service.get_chronicle(created.id)

        assert result is not None
        assert result.id == created.id
        assert result.project == "repo"

    async def test_get_nonexistent_chronicle(
        self,
        chronicle_repository: ChronRepo,
        repository: SessRepo,
        pod_manager: Pods,
    ):
        """Getting a nonexistent chronicle returns None."""
        session_service = SessionService(repository, pod_manager)
        chronicle_service = ChronicleService(chronicle_repository, session_service)

        result = await chronicle_service.get_chronicle(uuid4())

        assert result is None


class TestChronicleServiceList:
    """Tests for ChronicleService.list_chronicles."""

    async def test_list_empty(
        self,
        chronicle_repository: ChronRepo,
        repository: SessRepo,
        pod_manager: Pods,
    ):
        """Listing chronicles when none exist returns empty list."""
        session_service = SessionService(repository, pod_manager)
        chronicle_service = ChronicleService(chronicle_repository, session_service)

        result = await chronicle_service.list_chronicles()

        assert result == []

    async def test_list_multiple(
        self,
        chronicle_repository: ChronRepo,
        repository: SessRepo,
        pod_manager: Pods,
    ):
        """Listing chronicles returns all chronicles."""
        session_service = SessionService(repository, pod_manager)
        chronicle_service = ChronicleService(chronicle_repository, session_service)

        s1 = await session_service.create_session(
            name="session-1",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/org/repo1", branch="main"),
        )
        s2 = await session_service.create_session(
            name="session-2",
            model="claude-opus-4-20250514",
            source=GitSource(repo="https://github.com/org/repo2", branch="dev"),
        )
        await chronicle_service.create_chronicle(s1.id)
        await chronicle_service.create_chronicle(s2.id)

        result = await chronicle_service.list_chronicles()

        assert len(result) == 2

    async def test_list_filter_by_project(
        self,
        chronicle_repository: ChronRepo,
        repository: SessRepo,
        pod_manager: Pods,
    ):
        """Filtering by project returns only matching chronicles."""
        session_service = SessionService(repository, pod_manager)
        chronicle_service = ChronicleService(chronicle_repository, session_service)

        s1 = await session_service.create_session(
            name="session-1",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/org/alpha", branch="main"),
        )
        s2 = await session_service.create_session(
            name="session-2",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/org/beta", branch="main"),
        )
        await chronicle_service.create_chronicle(s1.id)
        await chronicle_service.create_chronicle(s2.id)

        result = await chronicle_service.list_chronicles(project="alpha")

        assert len(result) == 1
        assert result[0].project == "alpha"

    async def test_list_filter_by_tags(
        self,
        chronicle_repository: ChronRepo,
        repository: SessRepo,
        pod_manager: Pods,
    ):
        """Filtering by tags returns only chronicles with all requested tags."""
        session_service = SessionService(repository, pod_manager)
        chronicle_service = ChronicleService(chronicle_repository, session_service)

        s1 = await session_service.create_session(
            name="session-1",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/org/repo1", branch="main"),
        )
        s2 = await session_service.create_session(
            name="session-2",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/org/repo2", branch="main"),
        )
        c1 = await chronicle_service.create_chronicle(s1.id)
        c2 = await chronicle_service.create_chronicle(s2.id)

        await chronicle_service.update_chronicle(c1.id, tags=["python", "testing"])
        await chronicle_service.update_chronicle(c2.id, tags=["rust"])

        result = await chronicle_service.list_chronicles(tags=["python"])

        assert len(result) == 1
        assert "python" in result[0].tags


class TestChronicleServiceUpdate:
    """Tests for ChronicleService.update_chronicle."""

    async def test_update_summary(
        self,
        chronicle_repository: ChronRepo,
        repository: SessRepo,
        pod_manager: Pods,
    ):
        """Updating summary field works."""
        session_service = SessionService(repository, pod_manager)
        chronicle_service = ChronicleService(chronicle_repository, session_service)

        session = await session_service.create_session(
            name="test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        created = await chronicle_service.create_chronicle(session.id)

        updated = await chronicle_service.update_chronicle(created.id, summary="Added new feature")

        assert updated.summary == "Added new feature"
        assert updated.id == created.id

    async def test_update_tags(
        self,
        chronicle_repository: ChronRepo,
        repository: SessRepo,
        pod_manager: Pods,
    ):
        """Updating tags works."""
        session_service = SessionService(repository, pod_manager)
        chronicle_service = ChronicleService(chronicle_repository, session_service)

        session = await session_service.create_session(
            name="test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        created = await chronicle_service.create_chronicle(session.id)

        updated = await chronicle_service.update_chronicle(created.id, tags=["python", "refactor"])

        assert updated.tags == ["python", "refactor"]

    async def test_update_status(
        self,
        chronicle_repository: ChronRepo,
        repository: SessRepo,
        pod_manager: Pods,
    ):
        """Changing status to COMPLETE works."""
        session_service = SessionService(repository, pod_manager)
        chronicle_service = ChronicleService(chronicle_repository, session_service)

        session = await session_service.create_session(
            name="test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        created = await chronicle_service.create_chronicle(session.id)
        assert created.status == ChronicleStatus.DRAFT

        updated = await chronicle_service.update_chronicle(
            created.id, status=ChronicleStatus.COMPLETE
        )

        assert updated.status == ChronicleStatus.COMPLETE

    async def test_update_nonexistent(
        self,
        chronicle_repository: ChronRepo,
        repository: SessRepo,
        pod_manager: Pods,
    ):
        """Updating a nonexistent chronicle raises ChronicleNotFoundError."""
        session_service = SessionService(repository, pod_manager)
        chronicle_service = ChronicleService(chronicle_repository, session_service)
        fake_id = uuid4()

        with pytest.raises(ChronicleNotFoundError) as exc_info:
            await chronicle_service.update_chronicle(fake_id, summary="test")

        assert exc_info.value.chronicle_id == fake_id


class TestChronicleServiceDelete:
    """Tests for ChronicleService.delete_chronicle."""

    async def test_delete_existing(
        self,
        chronicle_repository: ChronRepo,
        repository: SessRepo,
        pod_manager: Pods,
    ):
        """Deleting an existing chronicle returns True."""
        session_service = SessionService(repository, pod_manager)
        chronicle_service = ChronicleService(chronicle_repository, session_service)

        session = await session_service.create_session(
            name="test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        created = await chronicle_service.create_chronicle(session.id)

        result = await chronicle_service.delete_chronicle(created.id)

        assert result is True
        assert await chronicle_repository.get(created.id) is None

    async def test_delete_nonexistent(
        self,
        chronicle_repository: ChronRepo,
        repository: SessRepo,
        pod_manager: Pods,
    ):
        """Deleting a nonexistent chronicle returns False."""
        session_service = SessionService(repository, pod_manager)
        chronicle_service = ChronicleService(chronicle_repository, session_service)

        result = await chronicle_service.delete_chronicle(uuid4())

        assert result is False


class TestChronicleServiceCreateOrUpdateFromBroker:
    """Tests for ChronicleService.create_or_update_from_broker."""

    async def test_creates_chronicle_when_none_exists(
        self,
        chronicle_repository: ChronRepo,
        repository: SessRepo,
        pod_manager: Pods,
    ):
        """Creates a new DRAFT chronicle from session when none exists."""
        session_service = SessionService(repository, pod_manager)
        chronicle_service = ChronicleService(chronicle_repository, session_service)

        session = await session_service.create_session(
            name="broker-session",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )

        chronicle = await chronicle_service.create_or_update_from_broker(
            session_id=session.id,
            summary="Did some work",
            key_changes=["main.py: added feature"],
            duration_seconds=120,
        )

        assert chronicle.session_id == session.id
        assert chronicle.status == ChronicleStatus.COMPLETE
        assert chronicle.summary == "Did some work"
        assert chronicle.key_changes == ["main.py: added feature"]
        assert chronicle.duration_seconds == 120
        assert chronicle.project == "repo"

    async def test_enriches_existing_draft(
        self,
        chronicle_repository: ChronRepo,
        repository: SessRepo,
        pod_manager: Pods,
    ):
        """Enriches an existing DRAFT chronicle with broker data."""
        session_service = SessionService(repository, pod_manager)
        chronicle_service = ChronicleService(chronicle_repository, session_service)

        session = await session_service.create_session(
            name="test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        existing = await chronicle_service.create_chronicle(session.id)
        assert existing.summary is None

        updated = await chronicle_service.create_or_update_from_broker(
            session_id=session.id,
            summary="Session summary from broker",
            key_changes=["app.py: refactored"],
            unfinished_work="Tests still needed",
            duration_seconds=300,
        )

        assert updated.id == existing.id  # same chronicle, updated
        assert updated.status == ChronicleStatus.COMPLETE
        assert updated.summary == "Session summary from broker"
        assert updated.key_changes == ["app.py: refactored"]
        assert updated.unfinished_work == "Tests still needed"
        assert updated.duration_seconds == 300

    async def test_reuses_existing_complete_chronicle(
        self,
        chronicle_repository: ChronRepo,
        repository: SessRepo,
        pod_manager: Pods,
    ):
        """Reuses the same chronicle when the broker reports twice for one session."""
        session_service = SessionService(repository, pod_manager)
        chronicle_service = ChronicleService(chronicle_repository, session_service)

        session = await session_service.create_session(
            name="test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        existing = await chronicle_service.create_chronicle(session.id)
        await chronicle_service.update_chronicle(existing.id, status=ChronicleStatus.COMPLETE)

        updated = await chronicle_service.create_or_update_from_broker(
            session_id=session.id,
            summary="New session work",
        )

        assert updated.id == existing.id
        assert updated.status == ChronicleStatus.COMPLETE
        assert updated.summary == "New session work"

    async def test_nonexistent_session_raises(
        self,
        chronicle_repository: ChronRepo,
        repository: SessRepo,
        pod_manager: Pods,
    ):
        """Raises SessionNotFoundError for nonexistent session."""
        session_service = SessionService(repository, pod_manager)
        chronicle_service = ChronicleService(chronicle_repository, session_service)

        with pytest.raises(SessionNotFoundError):
            await chronicle_service.create_or_update_from_broker(
                session_id=uuid4(),
                summary="test",
            )

    async def test_partial_update_preserves_existing_fields(
        self,
        chronicle_repository: ChronRepo,
        repository: SessRepo,
        pod_manager: Pods,
    ):
        """Only updates fields that are provided, preserving others."""
        session_service = SessionService(repository, pod_manager)
        chronicle_service = ChronicleService(chronicle_repository, session_service)

        session = await session_service.create_session(
            name="test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        existing = await chronicle_service.create_chronicle(session.id)
        await chronicle_service.update_chronicle(
            existing.id, tags=["python"], summary="Manual summary"
        )

        updated = await chronicle_service.create_or_update_from_broker(
            session_id=session.id,
            duration_seconds=60,
        )

        assert updated.id == existing.id
        assert updated.status == ChronicleStatus.COMPLETE
        assert updated.duration_seconds == 60
        # Existing fields should be preserved
        assert updated.tags == ["python"]
        assert updated.summary == "Manual summary"


class TestChronicleServiceGetBySession:
    """Tests for ChronicleService.get_chronicle_by_session."""

    async def test_get_by_session_returns_chronicle(
        self,
        chronicle_repository: ChronRepo,
        repository: SessRepo,
        pod_manager: Pods,
    ):
        """Returns the most recent chronicle for a session."""
        session_service = SessionService(repository, pod_manager)
        chronicle_service = ChronicleService(chronicle_repository, session_service)

        session = await session_service.create_session(
            name="test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        created = await chronicle_service.create_chronicle(session.id)

        result = await chronicle_service.get_chronicle_by_session(session.id)

        assert result is not None
        assert result.id == created.id
        assert result.session_id == session.id

    async def test_get_by_session_returns_none(
        self,
        chronicle_repository: ChronRepo,
        repository: SessRepo,
        pod_manager: Pods,
    ):
        """Returns None when no chronicle exists for session."""
        session_service = SessionService(repository, pod_manager)
        chronicle_service = ChronicleService(chronicle_repository, session_service)

        result = await chronicle_service.get_chronicle_by_session(uuid4())

        assert result is None


class TestChronicleServiceGetChain:
    """Tests for ChronicleService.get_chain."""

    async def test_get_chain_single(
        self,
        chronicle_repository: ChronRepo,
        repository: SessRepo,
        pod_manager: Pods,
    ):
        """Chain of a single chronicle returns just itself."""
        session_service = SessionService(repository, pod_manager)
        chronicle_service = ChronicleService(chronicle_repository, session_service)

        session = await session_service.create_session(
            name="test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        chronicle = await chronicle_service.create_chronicle(session.id)

        chain = await chronicle_service.get_chain(chronicle.id)

        assert len(chain) == 1
        assert chain[0].id == chronicle.id

    async def test_get_chain_empty_for_nonexistent(
        self,
        chronicle_repository: ChronRepo,
        repository: SessRepo,
        pod_manager: Pods,
    ):
        """Chain of a nonexistent chronicle returns empty list."""
        session_service = SessionService(repository, pod_manager)
        chronicle_service = ChronicleService(chronicle_repository, session_service)

        chain = await chronicle_service.get_chain(uuid4())

        assert chain == []


class TestChronicleServiceReforge:
    """Tests for ChronicleService.reforge."""

    async def test_reforge_creates_new_session(
        self,
        chronicle_repository: ChronRepo,
        repository: SessRepo,
        pod_manager: Pods,
    ):
        """Reforging creates a new session with '(reforged)' in the name."""
        session_service = SessionService(repository, pod_manager)
        chronicle_service = ChronicleService(chronicle_repository, session_service)

        session = await session_service.create_session(
            name="original-session",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        chronicle = await chronicle_service.create_chronicle(session.id)

        new_session = await chronicle_service.reforge(chronicle.id)

        assert "(reforged)" in new_session.name
        assert new_session.repo == session.repo
        assert new_session.branch == session.branch
        assert new_session.model == session.model
        assert new_session.status == SessionStatus.CREATED
        assert new_session.id != session.id

    async def test_reforge_uses_config_snapshot(
        self,
        chronicle_repository: ChronRepo,
        repository: SessRepo,
        pod_manager: Pods,
    ):
        """Reforged session uses configuration from the chronicle snapshot."""
        session_service = SessionService(repository, pod_manager)
        chronicle_service = ChronicleService(chronicle_repository, session_service)

        session = await session_service.create_session(
            name="original",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/org/repo", branch="feature/old"),
        )
        chronicle = await chronicle_service.create_chronicle(session.id)

        new_session = await chronicle_service.reforge(chronicle.id)

        assert new_session.model == "claude-sonnet-4-20250514"
        assert new_session.repo == "https://github.com/org/repo"
        assert new_session.branch == "feature/old"

    async def test_reforge_nonexistent(
        self,
        chronicle_repository: ChronRepo,
        repository: SessRepo,
        pod_manager: Pods,
    ):
        """Reforging a nonexistent chronicle raises ChronicleNotFoundError."""
        session_service = SessionService(repository, pod_manager)
        chronicle_service = ChronicleService(chronicle_repository, session_service)
        fake_id = uuid4()

        with pytest.raises(ChronicleNotFoundError) as exc_info:
            await chronicle_service.reforge(fake_id)

        assert exc_info.value.chronicle_id == fake_id
