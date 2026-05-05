"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from volundr.domain.models import (
    Chronicle,
    EventType,
    GitProviderType,
    Model,
    ModelProvider,
    ModelTier,
    PodSpecAdditions,
    RealtimeEvent,
    RepoInfo,
    Session,
    SessionSpec,
    SessionStatus,
    Stats,
    TimelineEvent,
    TimelineResponse,
    TokenUsageRecord,
)
from volundr.domain.ports import (
    ChronicleRepository,
    EventBroadcaster,
    GitProvider,
    PodManager,
    PodStartResult,
    PricingProvider,
    SessionRepository,
    StatsRepository,
    TimelineRepository,
    TokenTracker,
)


class InMemorySessionRepository(SessionRepository):
    """In-memory session repository for testing."""

    def __init__(self):
        self._sessions: dict[UUID, Session] = {}

    async def create(self, session: Session) -> Session:
        self._sessions[session.id] = session
        return session

    async def get(self, session_id: UUID) -> Session | None:
        return self._sessions.get(session_id)

    async def get_many(self, session_ids: list[UUID]) -> dict[UUID, Session]:
        return {sid: self._sessions[sid] for sid in session_ids if sid in self._sessions}

    async def list(
        self,
        status: SessionStatus | None = None,
        tenant_id: str | None = None,
        owner_id: str | None = None,
    ) -> list[Session]:
        sessions = list(self._sessions.values())
        if status is not None:
            sessions = [s for s in sessions if s.status == status]
        if tenant_id is not None:
            sessions = [s for s in sessions if s.tenant_id == tenant_id]
        if owner_id is not None:
            sessions = [s for s in sessions if s.owner_id == owner_id]
        return sessions

    async def update(self, session: Session) -> Session:
        self._sessions[session.id] = session
        return session

    async def delete(self, session_id: UUID) -> bool:
        if session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False


class InMemoryChronicleRepository(ChronicleRepository):
    """In-memory chronicle repository for testing."""

    def __init__(self):
        self._chronicles: dict[UUID, Chronicle] = {}

    async def create(self, chronicle: Chronicle) -> Chronicle:
        self._chronicles[chronicle.id] = chronicle
        return chronicle

    async def get(self, chronicle_id: UUID) -> Chronicle | None:
        return self._chronicles.get(chronicle_id)

    async def get_by_session(self, session_id: UUID) -> Chronicle | None:
        matching = [c for c in self._chronicles.values() if c.session_id == session_id]
        if not matching:
            return None
        return sorted(matching, key=lambda c: c.created_at, reverse=True)[0]

    async def list(
        self,
        project: str | None = None,
        repo: str | None = None,
        model: str | None = None,
        tags: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Chronicle]:
        results = list(self._chronicles.values())

        if project is not None:
            results = [c for c in results if c.project == project]
        if repo is not None:
            results = [c for c in results if c.repo == repo]
        if model is not None:
            results = [c for c in results if c.model == model]
        if tags is not None:
            results = [c for c in results if all(t in c.tags for t in tags)]

        results.sort(key=lambda c: c.created_at, reverse=True)
        return results[offset : offset + limit]

    async def update(self, chronicle: Chronicle) -> Chronicle:
        self._chronicles[chronicle.id] = chronicle
        return chronicle

    async def delete(self, chronicle_id: UUID) -> bool:
        if chronicle_id in self._chronicles:
            del self._chronicles[chronicle_id]
            return True
        return False

    async def get_chain(self, chronicle_id: UUID) -> list[Chronicle]:
        chain: list[Chronicle] = []
        current = self._chronicles.get(chronicle_id)
        if current is None:
            return chain
        chain.append(current)
        while current.parent_chronicle_id is not None:
            parent = self._chronicles.get(current.parent_chronicle_id)
            if parent is None:
                break
            chain.append(parent)
            current = parent
        chain.reverse()
        return chain


@pytest.fixture
def chronicle_repository() -> InMemoryChronicleRepository:
    """Create an in-memory chronicle repository."""
    return InMemoryChronicleRepository()


class InMemoryTimelineRepository(TimelineRepository):
    """In-memory timeline repository for testing."""

    def __init__(self):
        self._events: list[TimelineEvent] = []

    async def add_event(self, event: TimelineEvent) -> TimelineEvent:
        self._events.append(event)
        return event

    async def get_events(self, chronicle_id: UUID) -> list[TimelineEvent]:
        return sorted(
            [e for e in self._events if e.chronicle_id == chronicle_id],
            key=lambda e: e.t,
        )

    async def get_events_by_session(self, session_id: UUID) -> list[TimelineEvent]:
        return sorted(
            [e for e in self._events if e.session_id == session_id],
            key=lambda e: e.t,
        )

    async def delete_by_chronicle(self, chronicle_id: UUID) -> int:
        before = len(self._events)
        self._events = [e for e in self._events if e.chronicle_id != chronicle_id]
        return before - len(self._events)


@pytest.fixture
def timeline_repository() -> InMemoryTimelineRepository:
    """Create an in-memory timeline repository."""
    return InMemoryTimelineRepository()


def make_spec(**values) -> SessionSpec:
    """Build a SessionSpec with given values and empty pod_spec."""
    return SessionSpec(values=values, pod_spec=PodSpecAdditions())


class MockPodManager(PodManager):
    """Mock pod manager for testing."""

    def __init__(
        self,
        start_success: bool = True,
        stop_success: bool = True,
        chat_endpoint: str = "wss://chat.example.com/session",
        code_endpoint: str = "https://code.example.com/session",
        pod_name: str = "volundr-test-pod",
        wait_for_ready_result: SessionStatus = SessionStatus.RUNNING,
    ):
        self.start_success = start_success
        self.stop_success = stop_success
        self.chat_endpoint = chat_endpoint
        self.code_endpoint = code_endpoint
        self.pod_name = pod_name
        self.wait_for_ready_result = wait_for_ready_result
        self.start_calls: list[tuple] = []
        self.stop_calls: list[Session] = []
        self.wait_for_ready_calls: list[tuple] = []

    async def start(
        self,
        session: Session,
        spec: SessionSpec | None = None,
    ) -> PodStartResult:
        self.start_calls.append((session, spec))
        if not self.start_success:
            raise RuntimeError("Pod start failed")
        return PodStartResult(
            chat_endpoint=self.chat_endpoint,
            code_endpoint=self.code_endpoint,
            pod_name=self.pod_name,
        )

    async def stop(self, session: Session) -> bool:
        self.stop_calls.append(session)
        if not self.stop_success:
            raise RuntimeError("Pod stop failed")
        return True

    async def status(self, session: Session) -> SessionStatus:
        return session.status

    async def wait_for_ready(self, session: Session, timeout: float) -> SessionStatus:
        self.wait_for_ready_calls.append((session, timeout))
        return self.wait_for_ready_result


@pytest.fixture
def repository() -> InMemorySessionRepository:
    """Create an in-memory repository."""
    return InMemorySessionRepository()


@pytest.fixture
def pod_manager() -> MockPodManager:
    """Create a mock pod manager."""
    return MockPodManager()


@pytest.fixture
def failing_pod_manager() -> MockPodManager:
    """Create a mock pod manager that fails."""
    return MockPodManager(start_success=False, stop_success=False)


class InMemoryStatsRepository(StatsRepository):
    """In-memory stats repository for testing."""

    def __init__(
        self,
        active_sessions: int = 0,
        total_sessions: int = 0,
        tokens_today: int = 0,
        local_tokens: int = 0,
        cloud_tokens: int = 0,
        cost_today: Decimal = Decimal("0"),
    ):
        self._stats = Stats(
            active_sessions=active_sessions,
            total_sessions=total_sessions,
            tokens_today=tokens_today,
            local_tokens=local_tokens,
            cloud_tokens=cloud_tokens,
            cost_today=cost_today,
        )

    async def get_stats(self) -> Stats:
        return self._stats

    def set_stats(
        self,
        active_sessions: int | None = None,
        total_sessions: int | None = None,
        tokens_today: int | None = None,
        local_tokens: int | None = None,
        cloud_tokens: int | None = None,
        cost_today: Decimal | None = None,
    ) -> None:
        """Update stats for testing different scenarios."""
        current = self._stats
        self._stats = Stats(
            active_sessions=(
                active_sessions if active_sessions is not None else current.active_sessions
            ),
            total_sessions=(
                total_sessions if total_sessions is not None else current.total_sessions
            ),
            tokens_today=tokens_today if tokens_today is not None else current.tokens_today,
            local_tokens=local_tokens if local_tokens is not None else current.local_tokens,
            cloud_tokens=cloud_tokens if cloud_tokens is not None else current.cloud_tokens,
            cost_today=cost_today if cost_today is not None else current.cost_today,
        )


@pytest.fixture
def stats_repository() -> InMemoryStatsRepository:
    """Create an in-memory stats repository."""
    return InMemoryStatsRepository()


class InMemoryTokenTracker(TokenTracker):
    """In-memory token tracker for testing."""

    def __init__(self):
        self._records: list[TokenUsageRecord] = []

    async def record_usage(
        self,
        session_id: UUID,
        tokens: int,
        provider: ModelProvider,
        model: str,
        cost: float | None = None,
    ) -> TokenUsageRecord:
        from datetime import datetime

        record = TokenUsageRecord(
            id=uuid4(),
            session_id=session_id,
            recorded_at=datetime.now(UTC),
            tokens=tokens,
            provider=provider,
            model=model,
            cost=Decimal(str(cost)) if cost is not None else None,
        )
        self._records.append(record)
        return record

    async def get_session_usage(self, session_id: UUID) -> int:
        return sum(r.tokens for r in self._records if r.session_id == session_id)


@pytest.fixture
def token_tracker() -> InMemoryTokenTracker:
    """Create an in-memory token tracker."""
    return InMemoryTokenTracker()


class InMemoryPricingProvider(PricingProvider):
    """In-memory pricing provider for testing."""

    def __init__(self):
        self._models: list[Model] = [
            Model(
                id="claude-sonnet-4-20250514",
                name="Claude Sonnet 4",
                description="Fast, intelligent model for everyday tasks",
                provider=ModelProvider.CLOUD,
                tier=ModelTier.BALANCED,
                color="#2563EB",
                cost_per_million_tokens=3.00,
            ),
            Model(
                id="claude-opus-4-20250514",
                name="Claude Opus 4",
                description="Most capable model for complex tasks",
                provider=ModelProvider.CLOUD,
                tier=ModelTier.FRONTIER,
                color="#7C3AED",
                cost_per_million_tokens=15.00,
            ),
            Model(
                id="llama3.2:latest",
                name="Llama 3.2",
                description="Open source local model",
                provider=ModelProvider.LOCAL,
                tier=ModelTier.BALANCED,
                color="#F59E0B",
                cost_per_million_tokens=None,
                vram_required="8GB",
            ),
        ]
        self._pricing: dict[str, float] = {
            "claude-sonnet-4-20250514": 3.00,
            "claude-opus-4-20250514": 15.00,
        }

    def get_price(self, model_id: str) -> float | None:
        return self._pricing.get(model_id)

    def list_models(self) -> list[Model]:
        return self._models.copy()


@pytest.fixture
def pricing_provider() -> InMemoryPricingProvider:
    """Create an in-memory pricing provider."""
    return InMemoryPricingProvider()


class MockGitProvider(GitProvider):
    """Mock git provider for testing."""

    def __init__(
        self,
        name: str = "MockGit",
        provider_type: GitProviderType = GitProviderType.GITHUB,
        supported_hosts: list[str] | None = None,
        validate_success: bool = True,
        repos: list[RepoInfo] | None = None,
        orgs: tuple[str, ...] = (),
    ):
        self._name = name
        self._provider_type = provider_type
        self._supported_hosts = supported_hosts or ["github.com"]
        self._validate_success = validate_success
        self._repos = repos or []
        self._orgs = orgs
        self.validate_calls: list[str] = []
        self.list_repos_calls: list[str] = []

    @property
    def provider_type(self) -> GitProviderType:
        return self._provider_type

    @property
    def name(self) -> str:
        return self._name

    @property
    def base_url(self) -> str:
        return f"https://{self._supported_hosts[0]}"

    @property
    def orgs(self) -> tuple[str, ...]:
        return self._orgs

    def supports(self, repo_url: str) -> bool:
        return any(host in repo_url for host in self._supported_hosts)

    async def validate_repo(self, repo_url: str) -> bool:
        self.validate_calls.append(repo_url)
        return self._validate_success

    def parse_repo(self, repo_url: str) -> RepoInfo | None:
        if not self.supports(repo_url):
            return None
        # Simple parsing for tests
        parts = repo_url.replace("https://", "").replace("http://", "").split("/")
        if len(parts) >= 3:
            return RepoInfo(
                provider=self._provider_type,
                org=parts[1],
                name=parts[2].replace(".git", ""),
                clone_url=f"https://{parts[0]}/{parts[1]}/{parts[2]}.git",
                url=f"https://{parts[0]}/{parts[1]}/{parts[2]}",
            )
        return None

    def get_clone_url(self, repo_url: str) -> str | None:
        info = self.parse_repo(repo_url)
        return info.clone_url if info else None

    async def list_repos(self, org: str) -> list[RepoInfo]:
        self.list_repos_calls.append(org)
        return [r for r in self._repos if r.org == org]

    async def list_branches(self, repo_url: str) -> list[str]:
        if not self.supports(repo_url):
            from volundr.domain.ports import GitRepoNotFoundError

            raise GitRepoNotFoundError(f"Repository not found: {repo_url}")
        return ["main", "develop", "feature/test"]


class MockGitRegistry:
    """Mock git registry for testing."""

    def __init__(self, providers: list[GitProvider] | None = None):
        self._providers = providers or []
        self._url_to_provider: dict[str, GitProvider] = {}

    def register(self, provider: GitProvider) -> None:
        self._providers.append(provider)

    @property
    def providers(self) -> list[GitProvider]:
        return list(self._providers)

    def get_provider(self, repo_url: str) -> GitProvider | None:
        cached = self._url_to_provider.get(repo_url)
        if cached is not None:
            return cached
        for provider in self._providers:
            if provider.supports(repo_url):
                return provider
        return None

    def get_clone_url(self, repo_url: str) -> str | None:
        provider = self.get_provider(repo_url)
        if provider is None:
            return None
        return provider.get_clone_url(repo_url)

    async def validate_repo(self, repo_url: str) -> bool:
        provider = self.get_provider(repo_url)
        if provider is None:
            return False
        return await provider.validate_repo(repo_url)

    async def list_repos(
        self,
        org: str,
        provider_type: GitProviderType | None = None,
    ) -> list[RepoInfo]:
        repos: list[RepoInfo] = []
        for provider in self._providers:
            if provider_type is not None and provider.provider_type != provider_type:
                continue
            provider_repos = await provider.list_repos(org)
            repos.extend(provider_repos)
        return repos

    async def list_configured_repos(self) -> dict[str, list[RepoInfo]]:
        result: dict[str, list[RepoInfo]] = {}
        for provider in self._providers:
            if not provider.orgs:
                continue
            provider_repos: list[RepoInfo] = []
            for org in provider.orgs:
                repos = await provider.list_repos(org)
                provider_repos.extend(repos)
            for repo in provider_repos:
                self._url_to_provider[repo.url] = provider
            if provider_repos:
                result[provider.name] = provider_repos
        return result

    async def list_branches(self, repo_url: str) -> list[str]:
        provider = self.get_provider(repo_url)
        if provider is None:
            raise ValueError(f"No git provider found for: {repo_url}")
        return await provider.list_branches(repo_url)

    async def close(self) -> None:
        pass


@pytest.fixture
def git_provider() -> MockGitProvider:
    """Create a mock git provider."""
    return MockGitProvider()


@pytest.fixture
def failing_git_provider() -> MockGitProvider:
    """Create a mock git provider that fails validation."""
    return MockGitProvider(validate_success=False)


@pytest.fixture
def git_registry(git_provider: MockGitProvider) -> MockGitRegistry:
    """Create a mock git registry with a GitHub provider."""
    registry = MockGitRegistry()
    registry.register(git_provider)
    return registry


class MockEventBroadcaster(EventBroadcaster):
    """Mock event broadcaster for testing."""

    def __init__(self):
        self._events: list[RealtimeEvent] = []
        self._session_created_events: list[Session] = []
        self._session_updated_events: list[Session] = []
        self._session_deleted_events: list[UUID] = []
        self._stats_events: list[Stats] = []
        self._heartbeat_count: int = 0

    async def publish(self, event: RealtimeEvent) -> None:
        """Record a published event."""
        self._events.append(event)

    async def subscribe(self) -> AsyncGenerator[RealtimeEvent, None]:
        """Return events that have been published."""
        for event in self._events:
            yield event

    async def publish_session_created(self, session: Session) -> None:
        """Record a session created event."""
        self._session_created_events.append(session)
        await self.publish(
            RealtimeEvent(
                type=EventType.SESSION_CREATED,
                data={"id": str(session.id)},
                timestamp=datetime.now(UTC),
            )
        )

    async def publish_session_updated(self, session: Session) -> None:
        """Record a session updated event."""
        self._session_updated_events.append(session)
        await self.publish(
            RealtimeEvent(
                type=EventType.SESSION_UPDATED,
                data={"id": str(session.id)},
                timestamp=datetime.now(UTC),
            )
        )

    async def publish_session_deleted(self, session_id: UUID) -> None:
        """Record a session deleted event."""
        self._session_deleted_events.append(session_id)
        await self.publish(
            RealtimeEvent(
                type=EventType.SESSION_DELETED,
                data={"id": str(session_id)},
                timestamp=datetime.now(UTC),
            )
        )

    async def publish_stats(self, stats: Stats) -> None:
        """Record a stats event."""
        self._stats_events.append(stats)
        await self.publish(
            RealtimeEvent(
                type=EventType.STATS_UPDATED,
                data={"active_sessions": stats.active_sessions},
                timestamp=datetime.now(UTC),
            )
        )

    async def publish_heartbeat(self) -> None:
        """Record a heartbeat event."""
        self._heartbeat_count += 1
        await self.publish(
            RealtimeEvent(
                type=EventType.HEARTBEAT,
                data={},
                timestamp=datetime.now(UTC),
            )
        )

    async def publish_chronicle_event(
        self,
        session_id: UUID,
        event: TimelineEvent,
        timeline: TimelineResponse,
    ) -> None:
        """Record a chronicle event."""
        await self.publish(
            RealtimeEvent(
                type=EventType.CHRONICLE_EVENT,
                data={
                    "session_id": str(session_id),
                    "event": {"t": event.t, "type": event.type.value},
                },
                timestamp=datetime.now(UTC),
            )
        )

    @property
    def events(self) -> list[RealtimeEvent]:
        """Return all published events."""
        return list(self._events)

    @property
    def session_created_events(self) -> list[Session]:
        """Return all session created events."""
        return list(self._session_created_events)

    @property
    def session_updated_events(self) -> list[Session]:
        """Return all session updated events."""
        return list(self._session_updated_events)

    @property
    def session_deleted_events(self) -> list[UUID]:
        """Return all session deleted events."""
        return list(self._session_deleted_events)


@pytest.fixture
def broadcaster() -> MockEventBroadcaster:
    """Create a mock event broadcaster."""
    return MockEventBroadcaster()
