"""Tests for domain models."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from volundr.domain.models import (
    GitProviderType,
    GitSource,
    Model,
    ModelProvider,
    ModelTier,
    RepoInfo,
    Session,
    SessionStatus,
    Stats,
)


class TestSessionStatus:
    """Tests for SessionStatus enum."""

    def test_status_values(self):
        """Verify all expected status values exist."""
        assert SessionStatus.CREATED.value == "created"
        assert SessionStatus.STARTING.value == "starting"
        assert SessionStatus.RUNNING.value == "running"
        assert SessionStatus.STOPPING.value == "stopping"
        assert SessionStatus.STOPPED.value == "stopped"
        assert SessionStatus.FAILED.value == "failed"

    def test_status_is_string_enum(self):
        """Status values should be usable as strings."""
        assert SessionStatus.RUNNING.value == "running"
        # Can be compared directly to string
        assert SessionStatus.RUNNING == "running"


class TestSession:
    """Tests for Session model."""

    def test_create_session_with_required_fields(self):
        """Session can be created with required fields."""
        session = Session(
            name="test-session",
            model="claude-3-opus",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )

        assert session.name == "test-session"
        assert session.model == "claude-3-opus"
        assert session.repo == "https://github.com/org/repo"
        assert session.branch == "main"
        assert session.status == SessionStatus.CREATED
        assert session.chat_endpoint is None
        assert session.code_endpoint is None
        assert session.id is not None
        assert session.created_at is not None
        assert session.updated_at is not None
        assert session.last_active == session.created_at
        assert session.message_count == 0
        assert session.tokens_used == 0
        assert session.pod_name is None
        assert session.error is None

    def test_create_session_with_all_fields(self):
        """Session can be created with all fields specified."""
        session_id = uuid4()
        now = datetime.now(UTC)

        session = Session(
            id=session_id,
            name="full-session",
            model="claude-3-sonnet",
            source=GitSource(repo="https://github.com/org/repo", branch="feature/test"),
            status=SessionStatus.RUNNING,
            chat_endpoint="wss://chat.example.com",
            code_endpoint="https://code.example.com",
            created_at=now,
            updated_at=now,
            last_active=now,
            message_count=10,
            tokens_used=5000,
            pod_name="volundr-abc123",
            error=None,
        )

        assert session.id == session_id
        assert session.status == SessionStatus.RUNNING
        assert session.chat_endpoint == "wss://chat.example.com"
        assert session.code_endpoint == "https://code.example.com"
        assert session.message_count == 10
        assert session.tokens_used == 5000
        assert session.pod_name == "volundr-abc123"

    def test_name_validation_empty(self):
        """Name cannot be empty."""
        with pytest.raises(ValidationError) as exc_info:
            Session(
                name="",
                model="claude-3-opus",
                source=GitSource(repo="https://github.com/org/repo", branch="main"),
            )

        assert "String should have at least 1 character" in str(exc_info.value)

    def test_name_validation_too_long(self):
        """Name cannot exceed 255 characters."""
        with pytest.raises(ValidationError) as exc_info:
            Session(
                name="x" * 256,
                model="claude-3-opus",
                source=GitSource(repo="https://github.com/org/repo", branch="main"),
            )

        assert "String should have at most 255 characters" in str(exc_info.value)

    def test_model_allows_empty(self):
        """Model can be empty (for non-SESSION workloads like OVAS streams)."""
        session = Session(
            name="test",
            model="",
            source=GitSource(
                repo="https://github.com/org/repo",
                branch="main",
            ),
        )
        assert session.model == ""

    def test_model_validation_too_long(self):
        """Model cannot exceed 100 characters."""
        with pytest.raises(ValidationError) as exc_info:
            Session(
                name="test",
                model="x" * 101,
                source=GitSource(
                    repo="https://github.com/org/repo",
                    branch="main",
                ),
            )

        assert "String should have at most 100 characters" in str(exc_info.value)

    def test_repo_allows_empty(self):
        """Repo can be empty (for workloads that don't need a git repo)."""
        session = Session(
            name="test",
            model="claude-3-opus",
            source=GitSource(repo="", branch="main"),
        )
        assert session.repo == ""

    def test_branch_defaults_to_main(self):
        """Branch defaults to main when not provided."""
        session = Session(
            name="test", model="claude-3-opus", source=GitSource(repo="https://github.com/org/repo")
        )
        assert session.branch == "main"


class TestSessionCanStart:
    """Tests for Session.can_start method."""

    def test_can_start_from_created(self):
        """Session can start from CREATED status."""
        session = Session(
            name="test",
            model="claude-3-opus",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
            status=SessionStatus.CREATED,
        )
        assert session.can_start() is True

    def test_can_start_from_stopped(self):
        """Session can start from STOPPED status."""
        session = Session(
            name="test",
            model="claude-3-opus",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
            status=SessionStatus.STOPPED,
        )
        assert session.can_start() is True

    def test_can_start_from_failed(self):
        """Session can start from FAILED status."""
        session = Session(
            name="test",
            model="claude-3-opus",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
            status=SessionStatus.FAILED,
        )
        assert session.can_start() is True

    def test_cannot_start_from_starting(self):
        """Session cannot start from STARTING status."""
        session = Session(
            name="test",
            model="claude-3-opus",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
            status=SessionStatus.STARTING,
        )
        assert session.can_start() is False

    def test_cannot_start_from_running(self):
        """Session cannot start from RUNNING status."""
        session = Session(
            name="test",
            model="claude-3-opus",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
            status=SessionStatus.RUNNING,
        )
        assert session.can_start() is False

    def test_cannot_start_from_stopping(self):
        """Session cannot start from STOPPING status."""
        session = Session(
            name="test",
            model="claude-3-opus",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
            status=SessionStatus.STOPPING,
        )
        assert session.can_start() is False


class TestSessionCanStop:
    """Tests for Session.can_stop method."""

    def test_can_stop_from_running(self):
        """Session can stop from RUNNING status."""
        session = Session(
            name="test",
            model="claude-3-opus",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
            status=SessionStatus.RUNNING,
        )
        assert session.can_stop() is True

    def test_cannot_stop_from_created(self):
        """Session cannot stop from CREATED status."""
        session = Session(
            name="test",
            model="claude-3-opus",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
            status=SessionStatus.CREATED,
        )
        assert session.can_stop() is False

    def test_cannot_stop_from_stopped(self):
        """Session cannot stop from STOPPED status."""
        session = Session(
            name="test",
            model="claude-3-opus",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
            status=SessionStatus.STOPPED,
        )
        assert session.can_stop() is False


class TestSessionCopyMethods:
    """Tests for Session copy/update methods."""

    def test_with_status(self):
        """with_status returns a new session with updated status."""
        original = Session(
            name="test",
            model="claude-3-opus",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        original_updated_at = original.updated_at

        updated = original.with_status(SessionStatus.RUNNING)

        assert updated.status == SessionStatus.RUNNING
        assert updated.id == original.id
        assert updated.name == original.name
        assert original.status == SessionStatus.CREATED  # Original unchanged
        assert updated.updated_at >= original_updated_at

    def test_with_endpoints(self):
        """with_endpoints returns a new session with endpoints set."""
        original = Session(
            name="test",
            model="claude-3-opus",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )

        updated = original.with_endpoints("wss://chat.example.com", "https://code.example.com")

        assert updated.chat_endpoint == "wss://chat.example.com"
        assert updated.code_endpoint == "https://code.example.com"
        assert original.chat_endpoint is None  # Original unchanged

    def test_with_cleared_endpoints(self):
        """with_cleared_endpoints returns a new session with endpoints cleared."""
        original = Session(
            name="test",
            model="claude-3-opus",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
            chat_endpoint="wss://chat.example.com",
            code_endpoint="https://code.example.com",
        )

        updated = original.with_cleared_endpoints()

        assert updated.chat_endpoint is None
        assert updated.code_endpoint is None
        assert original.chat_endpoint == "wss://chat.example.com"  # Original unchanged

    def test_with_pod_name(self):
        """with_pod_name returns a new session with pod_name set."""
        original = Session(
            name="test",
            model="claude-3-opus",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )

        updated = original.with_pod_name("volundr-abc123")

        assert updated.pod_name == "volundr-abc123"
        assert original.pod_name is None  # Original unchanged

    def test_with_error(self):
        """with_error returns a new session with error message set."""
        original = Session(
            name="test",
            model="claude-3-opus",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )

        updated = original.with_error("Something went wrong")

        assert updated.error == "Something went wrong"
        assert original.error is None  # Original unchanged

    def test_with_activity(self):
        """with_activity returns a new session with updated activity metrics."""
        original = Session(
            name="test",
            model="claude-3-opus",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        original_last_active = original.last_active

        updated = original.with_activity(message_count=5, tokens=1000)

        assert updated.message_count == 5
        assert updated.tokens_used == 1000
        assert updated.last_active >= original_last_active
        assert original.message_count == 0  # Original unchanged
        assert original.tokens_used == 0  # Original unchanged


class TestModelProvider:
    """Tests for ModelProvider enum."""

    def test_provider_values(self):
        """Verify all expected provider values exist."""
        assert ModelProvider.CLOUD.value == "cloud"
        assert ModelProvider.LOCAL.value == "local"

    def test_provider_is_string_enum(self):
        """Provider values should be usable as strings."""
        assert ModelProvider.CLOUD == "cloud"
        assert ModelProvider.LOCAL == "local"

    def test_provider_from_string(self):
        """Provider can be created from string value."""
        assert ModelProvider("cloud") == ModelProvider.CLOUD
        assert ModelProvider("local") == ModelProvider.LOCAL


class TestStats:
    """Tests for Stats dataclass."""

    def test_create_stats(self):
        """Stats can be created with all fields."""
        stats = Stats(
            active_sessions=5,
            total_sessions=20,
            tokens_today=100000,
            local_tokens=40000,
            cloud_tokens=60000,
            cost_today=Decimal("3.75"),
        )

        assert stats.active_sessions == 5
        assert stats.total_sessions == 20
        assert stats.tokens_today == 100000
        assert stats.local_tokens == 40000
        assert stats.cloud_tokens == 60000
        assert stats.cost_today == Decimal("3.75")

    def test_stats_is_frozen(self):
        """Stats should be immutable (frozen)."""
        stats = Stats(
            active_sessions=5,
            total_sessions=20,
            tokens_today=100000,
            local_tokens=40000,
            cloud_tokens=60000,
            cost_today=Decimal("3.75"),
        )

        # Attempting to modify should raise an error
        with pytest.raises(AttributeError):
            stats.active_sessions = 10

    def test_stats_zero_values(self):
        """Stats can be created with zero values."""
        stats = Stats(
            active_sessions=0,
            total_sessions=0,
            tokens_today=0,
            local_tokens=0,
            cloud_tokens=0,
            cost_today=Decimal("0"),
        )

        assert stats.active_sessions == 0
        assert stats.total_sessions == 0
        assert stats.tokens_today == 0
        assert stats.local_tokens == 0
        assert stats.cloud_tokens == 0
        assert stats.cost_today == Decimal("0")

    def test_stats_equality(self):
        """Stats with same values should be equal."""
        stats1 = Stats(
            active_sessions=5,
            total_sessions=20,
            tokens_today=100000,
            local_tokens=40000,
            cloud_tokens=60000,
            cost_today=Decimal("3.75"),
        )
        stats2 = Stats(
            active_sessions=5,
            total_sessions=20,
            tokens_today=100000,
            local_tokens=40000,
            cloud_tokens=60000,
            cost_today=Decimal("3.75"),
        )

        assert stats1 == stats2


class TestModelTier:
    """Tests for ModelTier enum."""

    def test_tier_values(self):
        """Verify all expected tier values exist."""
        assert ModelTier.FRONTIER.value == "frontier"
        assert ModelTier.BALANCED.value == "balanced"
        assert ModelTier.EXECUTION.value == "execution"
        assert ModelTier.REASONING.value == "reasoning"

    def test_tier_is_string_enum(self):
        """Tier values should be usable as strings."""
        assert ModelTier.FRONTIER == "frontier"
        assert ModelTier.BALANCED == "balanced"

    def test_tier_from_string(self):
        """Tier can be created from string value."""
        assert ModelTier("frontier") == ModelTier.FRONTIER
        assert ModelTier("balanced") == ModelTier.BALANCED
        assert ModelTier("execution") == ModelTier.EXECUTION
        assert ModelTier("reasoning") == ModelTier.REASONING


class TestModel:
    """Tests for Model dataclass."""

    def test_create_cloud_model(self):
        """Model can be created for cloud provider."""
        model = Model(
            id="claude-sonnet-4-20250514",
            name="Claude Sonnet 4",
            description="Fast, intelligent model",
            provider=ModelProvider.CLOUD,
            tier=ModelTier.BALANCED,
            color="#2563EB",
            cost_per_million_tokens=3.00,
        )

        assert model.id == "claude-sonnet-4-20250514"
        assert model.name == "Claude Sonnet 4"
        assert model.description == "Fast, intelligent model"
        assert model.provider == ModelProvider.CLOUD
        assert model.tier == ModelTier.BALANCED
        assert model.color == "#2563EB"
        assert model.cost_per_million_tokens == 3.00
        assert model.vram_required is None

    def test_create_local_model(self):
        """Model can be created for local provider with VRAM."""
        model = Model(
            id="llama3.2:latest",
            name="Llama 3.2",
            description="Open source local model",
            provider=ModelProvider.LOCAL,
            tier=ModelTier.BALANCED,
            color="#F59E0B",
            cost_per_million_tokens=None,
            vram_required="8GB",
        )

        assert model.id == "llama3.2:latest"
        assert model.provider == ModelProvider.LOCAL
        assert model.cost_per_million_tokens is None
        assert model.vram_required == "8GB"

    def test_model_is_frozen(self):
        """Model should be immutable (frozen)."""
        model = Model(
            id="test-model",
            name="Test",
            description="Test model",
            provider=ModelProvider.CLOUD,
            tier=ModelTier.BALANCED,
            color="#000000",
        )

        with pytest.raises(AttributeError):
            model.name = "Changed"

    def test_model_equality(self):
        """Models with same values should be equal."""
        model1 = Model(
            id="test-model",
            name="Test",
            description="Test model",
            provider=ModelProvider.CLOUD,
            tier=ModelTier.BALANCED,
            color="#000000",
            cost_per_million_tokens=1.00,
        )
        model2 = Model(
            id="test-model",
            name="Test",
            description="Test model",
            provider=ModelProvider.CLOUD,
            tier=ModelTier.BALANCED,
            color="#000000",
            cost_per_million_tokens=1.00,
        )

        assert model1 == model2

    def test_model_defaults(self):
        """Model has correct defaults for optional fields."""
        model = Model(
            id="test-model",
            name="Test",
            description="Test",
            provider=ModelProvider.CLOUD,
            tier=ModelTier.BALANCED,
            color="#000000",
        )

        assert model.cost_per_million_tokens is None
        assert model.vram_required is None


class TestGitProviderType:
    """Tests for GitProviderType enum."""

    def test_provider_type_values(self):
        """Verify all expected provider type values exist."""
        assert GitProviderType.GITHUB.value == "github"
        assert GitProviderType.GITLAB.value == "gitlab"
        assert GitProviderType.BITBUCKET.value == "bitbucket"
        assert GitProviderType.GENERIC.value == "generic"

    def test_provider_type_is_string_enum(self):
        """Provider type values should be usable as strings."""
        assert GitProviderType.GITHUB == "github"
        assert GitProviderType.GITLAB == "gitlab"

    def test_provider_type_from_string(self):
        """Provider type can be created from string value."""
        assert GitProviderType("github") == GitProviderType.GITHUB
        assert GitProviderType("gitlab") == GitProviderType.GITLAB
        assert GitProviderType("bitbucket") == GitProviderType.BITBUCKET
        assert GitProviderType("generic") == GitProviderType.GENERIC


class TestRepoInfo:
    """Tests for RepoInfo dataclass."""

    def test_create_repo_info(self):
        """RepoInfo can be created with all fields."""
        info = RepoInfo(
            provider=GitProviderType.GITHUB,
            org="anthropics",
            name="claude-code",
            clone_url="https://github.com/anthropics/claude-code.git",
            url="https://github.com/anthropics/claude-code",
        )

        assert info.provider == GitProviderType.GITHUB
        assert info.org == "anthropics"
        assert info.name == "claude-code"
        assert info.clone_url == "https://github.com/anthropics/claude-code.git"
        assert info.url == "https://github.com/anthropics/claude-code"

    def test_repo_info_is_frozen(self):
        """RepoInfo should be immutable (frozen)."""
        info = RepoInfo(
            provider=GitProviderType.GITHUB,
            org="org",
            name="repo",
            clone_url="https://github.com/org/repo.git",
            url="https://github.com/org/repo",
        )

        with pytest.raises(AttributeError):
            info.name = "changed"

    def test_repo_info_equality(self):
        """RepoInfo with same values should be equal."""
        info1 = RepoInfo(
            provider=GitProviderType.GITHUB,
            org="org",
            name="repo",
            clone_url="https://github.com/org/repo.git",
            url="https://github.com/org/repo",
        )
        info2 = RepoInfo(
            provider=GitProviderType.GITHUB,
            org="org",
            name="repo",
            clone_url="https://github.com/org/repo.git",
            url="https://github.com/org/repo",
        )

        assert info1 == info2

    def test_repo_info_gitlab(self):
        """RepoInfo can be created for GitLab repos."""
        info = RepoInfo(
            provider=GitProviderType.GITLAB,
            org="mygroup",
            name="myproject",
            clone_url="https://gitlab.com/mygroup/myproject.git",
            url="https://gitlab.com/mygroup/myproject",
        )

        assert info.provider == GitProviderType.GITLAB
        assert info.org == "mygroup"
        assert info.name == "myproject"
