"""Tests for TokenService."""

from uuid import uuid4

import pytest

from bifrost.config import ManagedModelConfig
from tests.conftest import InMemorySessionRepository, InMemoryTokenTracker
from volundr.adapters.outbound.pricing import HardcodedPricingProvider
from volundr.domain.models import GitSource, ModelProvider, Session, SessionStatus
from volundr.domain.services import (
    SessionNotFoundError,
    SessionNotRunningError,
    TokenService,
)


@pytest.fixture
def session_repository() -> InMemorySessionRepository:
    """Create an in-memory session repository."""
    return InMemorySessionRepository()


@pytest.fixture
def token_tracker() -> InMemoryTokenTracker:
    """Create an in-memory token tracker."""
    return InMemoryTokenTracker()


@pytest.fixture
def pricing_provider() -> HardcodedPricingProvider:
    """Create a pricing provider with test models."""
    return HardcodedPricingProvider(
        [
            ManagedModelConfig(
                id="claude-sonnet-4-20250514", name="Sonnet", cost_per_million_tokens=3.0
            ),
            ManagedModelConfig(
                id="claude-opus-4-20250514",
                name="Opus",
                cost_per_million_tokens=15.0,
            ),
        ]
    )


@pytest.fixture
def token_service(
    token_tracker: InMemoryTokenTracker,
    session_repository: InMemorySessionRepository,
    pricing_provider: HardcodedPricingProvider,
) -> TokenService:
    """Create a token service with mock dependencies."""
    return TokenService(token_tracker, session_repository, pricing_provider)


@pytest.fixture
def running_session() -> Session:
    """Create a running session for testing."""
    return Session(
        name="Test Session",
        model="claude-sonnet-4-20250514",
        source=GitSource(repo="https://github.com/test/repo", branch="main"),
        status=SessionStatus.RUNNING,
    )


class TestTokenServiceRecordUsage:
    """Tests for TokenService.record_usage."""

    async def test_record_usage_success(
        self,
        token_service: TokenService,
        session_repository: InMemorySessionRepository,
        running_session: Session,
    ) -> None:
        """Test recording usage for a running session."""
        await session_repository.create(running_session)

        record = await token_service.record_usage(
            session_id=running_session.id,
            tokens=1000,
            provider=ModelProvider.CLOUD,
            model="claude-sonnet-4-20250514",
            message_count=1,
        )

        assert record.session_id == running_session.id
        assert record.tokens == 1000
        assert record.provider == ModelProvider.CLOUD
        assert record.model == "claude-sonnet-4-20250514"

    async def test_record_usage_updates_session(
        self,
        token_service: TokenService,
        session_repository: InMemorySessionRepository,
        running_session: Session,
    ) -> None:
        """Test that recording usage updates session metrics."""
        await session_repository.create(running_session)

        await token_service.record_usage(
            session_id=running_session.id,
            tokens=1000,
            provider=ModelProvider.CLOUD,
            model="claude-sonnet-4-20250514",
            message_count=2,
        )

        updated = await session_repository.get(running_session.id)
        assert updated is not None
        assert updated.tokens_used == 1000
        assert updated.message_count == 2

    async def test_record_usage_accumulates(
        self,
        token_service: TokenService,
        session_repository: InMemorySessionRepository,
        running_session: Session,
    ) -> None:
        """Test that multiple usage records accumulate."""
        await session_repository.create(running_session)

        await token_service.record_usage(
            session_id=running_session.id,
            tokens=1000,
            provider=ModelProvider.CLOUD,
            model="claude-sonnet-4-20250514",
        )
        await token_service.record_usage(
            session_id=running_session.id,
            tokens=500,
            provider=ModelProvider.LOCAL,
            model="llama-3",
        )

        updated = await session_repository.get(running_session.id)
        assert updated is not None
        assert updated.tokens_used == 1500
        assert updated.message_count == 2

    async def test_record_usage_session_not_found(
        self,
        token_service: TokenService,
    ) -> None:
        """Test that recording usage for non-existent session raises error."""
        with pytest.raises(SessionNotFoundError):
            await token_service.record_usage(
                session_id=uuid4(),
                tokens=1000,
                provider=ModelProvider.CLOUD,
                model="claude-sonnet-4-20250514",
            )

    async def test_record_usage_session_not_running(
        self,
        token_service: TokenService,
        session_repository: InMemorySessionRepository,
    ) -> None:
        """Test that recording usage for non-running session raises error."""
        stopped_session = Session(
            name="Stopped Session",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
            status=SessionStatus.STOPPED,
        )
        await session_repository.create(stopped_session)

        with pytest.raises(SessionNotRunningError) as exc_info:
            await token_service.record_usage(
                session_id=stopped_session.id,
                tokens=1000,
                provider=ModelProvider.CLOUD,
                model="claude-sonnet-4-20250514",
            )

        assert exc_info.value.current_status == SessionStatus.STOPPED


class TestTokenServiceGetSessionUsage:
    """Tests for TokenService.get_session_usage."""

    async def test_get_session_usage(
        self,
        token_service: TokenService,
        session_repository: InMemorySessionRepository,
        token_tracker: InMemoryTokenTracker,
        running_session: Session,
    ) -> None:
        """Test getting total usage for a session."""
        await session_repository.create(running_session)

        await token_service.record_usage(
            session_id=running_session.id,
            tokens=1000,
            provider=ModelProvider.CLOUD,
            model="claude-sonnet-4-20250514",
        )
        await token_service.record_usage(
            session_id=running_session.id,
            tokens=500,
            provider=ModelProvider.LOCAL,
            model="llama-3",
        )

        total = await token_service.get_session_usage(running_session.id)
        assert total == 1500

    async def test_get_session_usage_not_found(
        self,
        token_service: TokenService,
    ) -> None:
        """Test that getting usage for non-existent session raises error."""
        with pytest.raises(SessionNotFoundError):
            await token_service.get_session_usage(uuid4())

    async def test_get_session_usage_zero(
        self,
        token_service: TokenService,
        session_repository: InMemorySessionRepository,
        running_session: Session,
    ) -> None:
        """Test getting usage for session with no records."""
        await session_repository.create(running_session)

        total = await token_service.get_session_usage(running_session.id)
        assert total == 0


class TestTokenServiceCostCalculation:
    """Tests for TokenService automatic cost calculation."""

    async def test_cost_calculated_for_cloud_model(
        self,
        token_service: TokenService,
        session_repository: InMemorySessionRepository,
        token_tracker: InMemoryTokenTracker,
        running_session: Session,
    ) -> None:
        """Test that cost is automatically calculated for cloud models."""
        await session_repository.create(running_session)

        record = await token_service.record_usage(
            session_id=running_session.id,
            tokens=1_000_000,  # 1M tokens
            provider=ModelProvider.CLOUD,
            model="claude-sonnet-4-20250514",
        )

        # claude-sonnet-4-20250514 costs $3.00 per million tokens
        assert record.cost == pytest.approx(3.00)

    async def test_cost_none_for_local_model(
        self,
        token_service: TokenService,
        session_repository: InMemorySessionRepository,
        token_tracker: InMemoryTokenTracker,
        running_session: Session,
    ) -> None:
        """Test that cost is None for local models."""
        await session_repository.create(running_session)

        record = await token_service.record_usage(
            session_id=running_session.id,
            tokens=1_000_000,
            provider=ModelProvider.LOCAL,
            model="llama3.2:latest",
        )

        assert record.cost is None

    async def test_cost_none_for_unknown_model(
        self,
        token_service: TokenService,
        session_repository: InMemorySessionRepository,
        token_tracker: InMemoryTokenTracker,
        running_session: Session,
    ) -> None:
        """Test that cost is None for unknown cloud models."""
        await session_repository.create(running_session)

        record = await token_service.record_usage(
            session_id=running_session.id,
            tokens=1_000_000,
            provider=ModelProvider.CLOUD,
            model="unknown-model",
        )

        assert record.cost is None

    async def test_cost_without_pricing_provider(
        self,
        token_tracker: InMemoryTokenTracker,
        session_repository: InMemorySessionRepository,
        running_session: Session,
    ) -> None:
        """Test that cost is None when no pricing provider is configured."""
        service = TokenService(token_tracker, session_repository, None)
        await session_repository.create(running_session)

        record = await service.record_usage(
            session_id=running_session.id,
            tokens=1_000_000,
            provider=ModelProvider.CLOUD,
            model="claude-sonnet-4-20250514",
        )

        assert record.cost is None

    async def test_cost_scales_with_tokens(
        self,
        token_service: TokenService,
        session_repository: InMemorySessionRepository,
        token_tracker: InMemoryTokenTracker,
        running_session: Session,
    ) -> None:
        """Test that cost scales correctly with token count."""
        await session_repository.create(running_session)

        # Use 500k tokens of Claude Opus 4 ($15/million)
        record = await token_service.record_usage(
            session_id=running_session.id,
            tokens=500_000,
            provider=ModelProvider.CLOUD,
            model="claude-opus-4-20250514",
        )

        # 500k tokens at $15/million = $7.50
        assert record.cost == pytest.approx(7.50)

    async def test_precalculated_cost_overrides_pricing_table(
        self,
        token_service: TokenService,
        session_repository: InMemorySessionRepository,
        token_tracker: InMemoryTokenTracker,
        running_session: Session,
    ) -> None:
        """Test that a pre-calculated cost is used instead of the pricing table."""
        await session_repository.create(running_session)

        # Pass pre-calculated cost (e.g. from Claude CLI costUSD field)
        record = await token_service.record_usage(
            session_id=running_session.id,
            tokens=20_000,
            provider=ModelProvider.CLOUD,
            model="claude-opus-4-5-20251101",
            cost=0.0527,
        )

        # Should use the provided cost, not calculate from pricing table
        assert float(record.cost) == pytest.approx(0.0527)

    async def test_precalculated_cost_none_falls_back(
        self,
        token_service: TokenService,
        session_repository: InMemorySessionRepository,
        token_tracker: InMemoryTokenTracker,
        running_session: Session,
    ) -> None:
        """Test that cost=None falls back to pricing table calculation."""
        await session_repository.create(running_session)

        record = await token_service.record_usage(
            session_id=running_session.id,
            tokens=1_000_000,
            provider=ModelProvider.CLOUD,
            model="claude-sonnet-4-20250514",
            cost=None,
        )

        # Should fall back to pricing table: $3.00/M
        assert record.cost == pytest.approx(3.00)
