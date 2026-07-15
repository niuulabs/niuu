"""Tests for domain services."""

import asyncio

import pytest

from volundr.domain.models import GitSource, ModelProvider, Session, SessionStatus
from volundr.domain.services import SessionService, TokenService


class TestSessionServiceBroadcaster:
    """Tests for SessionService event broadcasting."""

    @pytest.fixture
    def service(self, repository, pod_manager, broadcaster):
        """Create a SessionService with broadcaster."""
        return SessionService(
            repository=repository,
            pod_manager=pod_manager,
            broadcaster=broadcaster,
            provisioning_initial_delay=0,
            provisioning_timeout=1.0,
        )

    @pytest.mark.asyncio
    async def test_create_session_publishes_event(self, service, broadcaster):
        """Creating a session publishes a session_created event."""
        session = await service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )

        assert len(broadcaster.session_created_events) == 1
        assert broadcaster.session_created_events[0].id == session.id

    @pytest.mark.asyncio
    async def test_update_session_publishes_event(self, service, broadcaster):
        """Updating a session publishes a session_updated event."""
        session = await service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )

        # Clear the created event
        broadcaster._session_updated_events.clear()

        await service.update_session(session.id, name="Updated Name")

        assert len(broadcaster.session_updated_events) == 1
        assert broadcaster.session_updated_events[0].name == "Updated Name"

    @pytest.mark.asyncio
    async def test_delete_session_publishes_event(self, service, broadcaster):
        """Deleting a session publishes a session_deleted event."""
        session = await service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )

        deleted = await service.delete_session(session.id)

        assert deleted is True
        assert len(broadcaster.session_deleted_events) == 1
        assert broadcaster.session_deleted_events[0] == session.id

    @pytest.mark.asyncio
    async def test_start_session_publishes_events(self, service, broadcaster):
        """Starting a session publishes session_updated events."""
        session = await service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )

        # Clear previous events
        broadcaster._session_updated_events.clear()

        result = await service.start_session(session.id)

        # start_session returns STARTING immediately (async provisioning)
        assert result.status == SessionStatus.STARTING

        # Should have 1 update so far: starting
        assert len(broadcaster.session_updated_events) >= 1
        assert broadcaster.session_updated_events[0].status == SessionStatus.STARTING

        # Wait for background provisioning + readiness task to complete
        await asyncio.sleep(0.5)

        # After background task, should have provisioning and running updates
        assert len(broadcaster.session_updated_events) == 3
        assert broadcaster.session_updated_events[2].status == SessionStatus.RUNNING

    @pytest.mark.asyncio
    async def test_start_session_persists_and_reuses_definition(self, service, repository):
        """Restart parity: the session definition is persisted on first start and
        reused on later restarts, so the session keeps its transport (e.g. Grok ACP)
        instead of falling back to the platform default."""
        session = await service.create_session(
            name="Grok",
            model="grok-build",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )

        # First start with an explicit definition persists it on the session.
        await service.start_session(session.id, definition="skuldGrok")
        await asyncio.sleep(0.5)  # let background provisioning settle
        stored = await repository.get(session.id)
        assert stored.session_definition == "skuldGrok"

        # Stop, then restart WITHOUT a definition — the stored one must be reused.
        await service.stop_session(session.id)
        await service.start_session(session.id)
        await asyncio.sleep(0.1)
        restored = await repository.get(session.id)
        assert restored.session_definition == "skuldGrok"

    @pytest.mark.asyncio
    async def test_stop_session_publishes_events(self, service, broadcaster):
        """Stopping a session publishes session_updated events."""
        session = await service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )

        await service.start_session(session.id)
        # Wait for background readiness task to transition to RUNNING
        await asyncio.sleep(0.1)

        # Clear previous events
        broadcaster._session_updated_events.clear()

        await service.stop_session(session.id)

        # Should have 2 updates: stopping and stopped
        assert len(broadcaster.session_updated_events) == 2
        assert broadcaster.session_updated_events[0].status == SessionStatus.STOPPING
        assert broadcaster.session_updated_events[1].status == SessionStatus.STOPPED

    @pytest.mark.asyncio
    async def test_start_session_failure_publishes_failed_event(
        self, repository, failing_pod_manager, broadcaster
    ):
        """Failed session start publishes session_updated event with failed status."""
        service = SessionService(
            repository=repository,
            pod_manager=failing_pod_manager,
            broadcaster=broadcaster,
            provisioning_initial_delay=0,
            provisioning_timeout=1.0,
        )

        session = await service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )

        broadcaster._session_updated_events.clear()

        # start_session returns immediately; failure happens in background
        result = await service.start_session(session.id)
        assert result.status == SessionStatus.STARTING

        # Wait for background task to fail
        await asyncio.sleep(0.5)

        # Should have starting + failed updates
        assert broadcaster.session_updated_events[0].status == SessionStatus.STARTING
        assert any(e.status == SessionStatus.FAILED for e in broadcaster.session_updated_events)


class TestSessionServiceWithoutBroadcaster:
    """Tests for SessionService without broadcaster (backward compatibility)."""

    @pytest.fixture
    def service(self, repository, pod_manager):
        """Create a SessionService without broadcaster."""
        return SessionService(
            repository=repository,
            pod_manager=pod_manager,
            broadcaster=None,
            provisioning_initial_delay=0,
            provisioning_timeout=1.0,
        )

    @pytest.mark.asyncio
    async def test_create_session_works_without_broadcaster(self, service):
        """Creating a session works without a broadcaster."""
        session = await service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )

        assert session.name == "Test"

    @pytest.mark.asyncio
    async def test_start_session_works_without_broadcaster(self, service):
        """Starting a session works without a broadcaster."""
        session = await service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )

        started = await service.start_session(session.id)
        assert started.status == SessionStatus.STARTING


class TestTokenServiceBroadcaster:
    """Tests for TokenService event broadcasting."""

    @pytest.fixture
    def service(self, token_tracker, repository, pricing_provider, broadcaster):
        """Create a TokenService with broadcaster."""
        return TokenService(
            token_tracker=token_tracker,
            session_repository=repository,
            pricing_provider=pricing_provider,
            broadcaster=broadcaster,
        )

    @pytest.mark.asyncio
    async def test_record_usage_publishes_event(self, service, repository, broadcaster):
        """Recording usage publishes a session_updated event."""
        # Create a running session
        session = Session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
            status=SessionStatus.RUNNING,
        )
        await repository.create(session)

        await service.record_usage(
            session_id=session.id,
            tokens=100,
            provider=ModelProvider.CLOUD,
            model="claude-sonnet-4-20250514",
        )

        assert len(broadcaster.session_updated_events) == 1
        updated = broadcaster.session_updated_events[0]
        assert updated.tokens_used == 100
        assert updated.message_count == 1


class TestTokenServiceWithoutBroadcaster:
    """Tests for TokenService without broadcaster."""

    @pytest.fixture
    def service(self, token_tracker, repository, pricing_provider):
        """Create a TokenService without broadcaster."""
        return TokenService(
            token_tracker=token_tracker,
            session_repository=repository,
            pricing_provider=pricing_provider,
            broadcaster=None,
        )

    @pytest.mark.asyncio
    async def test_record_usage_works_without_broadcaster(self, service, repository):
        """Recording usage works without a broadcaster."""
        session = Session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
            status=SessionStatus.RUNNING,
        )
        await repository.create(session)

        record = await service.record_usage(
            session_id=session.id,
            tokens=100,
            provider=ModelProvider.CLOUD,
            model="claude-sonnet-4-20250514",
        )

        assert record.tokens == 100


class TestSessionProvisioningState:
    """Tests for the PROVISIONING session state flow."""

    @pytest.fixture
    def service(self, repository, pod_manager, broadcaster):
        """Create a SessionService with fast provisioning config."""
        return SessionService(
            repository=repository,
            pod_manager=pod_manager,
            broadcaster=broadcaster,
            provisioning_initial_delay=0,
            provisioning_timeout=1.0,
        )

    @pytest.mark.asyncio
    async def test_start_session_returns_starting(self, service):
        """start_session returns a session in STARTING state (async provisioning)."""
        session = await service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )

        result = await service.start_session(session.id)
        assert result.status == SessionStatus.STARTING
        assert result.chat_endpoint is not None

    @pytest.mark.asyncio
    async def test_start_session_uses_pod_manager_initial_chat_endpoint(
        self, repository, pod_manager, broadcaster
    ):
        """Flux/Gateway sessions return their public route immediately."""
        service = SessionService(
            repository=repository,
            pod_manager=pod_manager,
            broadcaster=broadcaster,
            provisioning_initial_delay=0,
            provisioning_timeout=1.0,
        )
        session = await service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )
        pod_manager.initial_chat_endpoint = (  # type: ignore[method-assign]
            lambda s: f"wss://sessions.example.com/s/{s.id}/session"
        )

        result = await service.start_session(session.id)

        assert result.chat_endpoint == f"wss://sessions.example.com/s/{session.id}/session"

    @pytest.mark.asyncio
    async def test_start_session_clears_stale_error(self, service, repository):
        """Restart is an explicit "bring it back" — a stale failure verdict
        (e.g. the liveness reaper's "broker presumed dead") must not survive
        onto the relaunched session, or clients banner a healthy session."""
        session = await service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )
        stamped = session.model_copy(
            update={"error": "liveness: no activity heartbeat — broker presumed dead"}
        )
        await repository.update(stamped)

        result = await service.start_session(session.id)
        assert result.error is None

    @pytest.mark.asyncio
    async def test_poll_readiness_transitions_to_running(self, service, repository):
        """Background poller transitions PROVISIONING -> RUNNING on success."""
        session = await service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )

        await service.start_session(session.id)

        # Wait for background readiness poll to complete
        await asyncio.sleep(0.2)

        updated = await repository.get(session.id)
        assert updated.status == SessionStatus.RUNNING

    @pytest.mark.asyncio
    async def test_poll_readiness_transitions_to_failed_on_backend_failure(
        self, repository, broadcaster
    ):
        """Background poller transitions PROVISIONING -> FAILED on backend failure."""
        from tests.conftest import MockPodManager

        pod_manager = MockPodManager(wait_for_ready_result=SessionStatus.FAILED)
        service = SessionService(
            repository=repository,
            pod_manager=pod_manager,
            broadcaster=broadcaster,
            provisioning_initial_delay=0,
            provisioning_timeout=0.1,
        )

        session = await service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )

        await service.start_session(session.id)

        # Wait for background readiness poll to complete
        await asyncio.sleep(0.3)

        updated = await repository.get(session.id)
        assert updated.status == SessionStatus.FAILED
        assert "infrastructure reported failure" in updated.error

    @pytest.mark.asyncio
    async def test_poll_readiness_keeps_progressing_session_provisioning(
        self, repository, broadcaster
    ):
        """A bounded wait ending while the runtime progresses is not a failure."""
        from tests.conftest import MockPodManager

        pod_manager = MockPodManager(wait_for_ready_result=SessionStatus.STARTING)
        service = SessionService(
            repository=repository,
            pod_manager=pod_manager,
            broadcaster=broadcaster,
            provisioning_initial_delay=0,
            provisioning_timeout=0.1,
        )
        session = await service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )

        await service.start_session(session.id)
        await asyncio.sleep(0.2)

        updated = await repository.get(session.id)
        assert updated.status == SessionStatus.PROVISIONING
        assert updated.error is None

    @pytest.mark.asyncio
    async def test_can_stop_provisioning_session(self, service, repository):
        """Stopping a PROVISIONING session works and cancels the task."""
        session = await service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )

        started = await service.start_session(session.id)
        assert started.status == SessionStatus.STARTING

        # The session should be stoppable from PROVISIONING
        assert started.can_stop()

        stopped = await service.stop_session(session.id)
        assert stopped.status == SessionStatus.STOPPED

    @pytest.mark.asyncio
    async def test_can_stop_returns_true_for_provisioning(self):
        """can_stop() returns True for PROVISIONING sessions."""
        session = Session(
            name="Test",
            model="test",
            source=GitSource(repo="test", branch="main"),
            status=SessionStatus.PROVISIONING,
        )
        assert session.can_stop() is True

    @pytest.mark.asyncio
    async def test_can_start_excludes_provisioning(self):
        """can_start() returns False for PROVISIONING sessions."""
        session = Session(
            name="Test",
            model="test",
            source=GitSource(repo="test", branch="main"),
            status=SessionStatus.PROVISIONING,
        )
        assert session.can_start() is False

    @pytest.mark.asyncio
    async def test_delete_provisioning_session(self, service, repository):
        """Deleting a PROVISIONING session cancels the task and deletes."""
        session = await service.create_session(
            name="Test",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
        )

        await service.start_session(session.id)

        deleted = await service.delete_session(session.id)
        assert deleted is True

        result = await repository.get(session.id)
        assert result is None

    @pytest.mark.asyncio
    async def test_reconcile_provisioning_sessions(self, repository, pod_manager, broadcaster):
        """Reconciliation re-launches polling for stuck PROVISIONING sessions."""
        service = SessionService(
            repository=repository,
            pod_manager=pod_manager,
            broadcaster=broadcaster,
            provisioning_initial_delay=0,
            provisioning_timeout=1.0,
        )

        # Create a session already stuck in PROVISIONING
        session = Session(
            name="Stuck",
            model="test",
            source=GitSource(repo="test", branch="main"),
            status=SessionStatus.PROVISIONING,
            pod_name="volundr-test",
        )
        await repository.create(session)

        # Reconcile should re-launch polling
        await service.reconcile_provisioning_sessions()

        # Wait for background task to complete
        await asyncio.sleep(0.2)

        updated = await repository.get(session.id)
        assert updated.status == SessionStatus.RUNNING

    @pytest.mark.asyncio
    async def test_reconcile_active_sessions_marks_stale_running_session_stopped(
        self, repository, pod_manager, broadcaster
    ):
        """Running sessions are downgraded when the runtime is gone after restart."""
        service = SessionService(
            repository=repository,
            pod_manager=pod_manager,
            broadcaster=broadcaster,
        )

        session = Session(
            name="Stale",
            model="test",
            source=GitSource(repo="test", branch="main"),
            status=SessionStatus.RUNNING,
            chat_endpoint="ws://localhost:8080/s/stale/session",
            code_endpoint="file:///tmp/stale",
        )
        created = await repository.create(session)
        broadcaster._session_updated_events.clear()

        async def stale_status(_session):
            return SessionStatus.STOPPED

        pod_manager.status = stale_status  # type: ignore[method-assign]

        await service.reconcile_active_sessions()

        updated = await repository.get(created.id)
        assert updated is not None
        assert updated.status == SessionStatus.STOPPED
        assert updated.chat_endpoint is None
        assert updated.code_endpoint is None
        assert [event.status for event in broadcaster.session_updated_events] == [
            SessionStatus.STOPPED
        ]


class TestFluxWaitForReady:
    """Tests for Flux adapter wait_for_ready with mocked watch."""

    @pytest.mark.asyncio
    async def test_flux_status_missing_release_is_starting_while_session_starts(self):
        """A just-created STARTING session may be polled before its HelmRelease exists."""
        from unittest.mock import AsyncMock, Mock, patch

        from volundr.adapters.outbound.flux import FluxPodManager

        flux = FluxPodManager(namespace="test")
        session = Session(
            name="Test",
            model="test",
            source=GitSource(repo="test", branch="main"),
            status=SessionStatus.STARTING,
        )
        api = Mock()
        api.get_namespaced_custom_object = AsyncMock(side_effect=Exception("404 NotFound"))

        with patch.object(flux, "_get_api", AsyncMock(return_value=api)):
            assert await flux.status(session) == SessionStatus.STARTING

    @pytest.mark.asyncio
    async def test_flux_status_missing_release_is_stopped_after_running(self):
        """Missing HelmRelease remains authoritative death for an already live row."""
        from unittest.mock import AsyncMock, Mock, patch

        from volundr.adapters.outbound.flux import FluxPodManager

        flux = FluxPodManager(namespace="test")
        session = Session(
            name="Test",
            model="test",
            source=GitSource(repo="test", branch="main"),
            status=SessionStatus.RUNNING,
        )
        api = Mock()
        api.get_namespaced_custom_object = AsyncMock(side_effect=Exception("404 NotFound"))

        with patch.object(flux, "_get_api", AsyncMock(return_value=api)):
            assert await flux.status(session) == SessionStatus.STOPPED

    @pytest.mark.asyncio
    async def test_flux_wait_for_ready_returns_running(self):
        """Flux wait_for_ready returns RUNNING when ready condition is True."""
        import sys
        import types
        from unittest.mock import AsyncMock, MagicMock, patch

        # Create a fake kubernetes_asyncio.watch module
        mock_k8s = types.ModuleType("kubernetes_asyncio")
        mock_watch_mod = types.ModuleType("kubernetes_asyncio.watch")

        mock_watch_instance = MagicMock()

        async def fake_stream(*args, **kwargs):
            yield {"object": {"status": {"conditions": [{"type": "Ready", "status": "True"}]}}}

        mock_watch_instance.stream = fake_stream
        mock_watch_instance.stop = MagicMock()
        mock_watch_mod.Watch = MagicMock(return_value=mock_watch_instance)
        mock_k8s.watch = mock_watch_mod

        with patch.dict(
            sys.modules,
            {
                "kubernetes_asyncio": mock_k8s,
                "kubernetes_asyncio.watch": mock_watch_mod,
            },
        ):
            from volundr.adapters.outbound.flux import FluxPodManager

            flux = FluxPodManager(namespace="test")
            session = Session(
                name="Test",
                model="test",
                source=GitSource(repo="test", branch="main"),
                status=SessionStatus.PROVISIONING,
            )

            with patch.object(flux, "status", new_callable=AsyncMock) as mock_status:
                mock_status.return_value = SessionStatus.STARTING
                with patch.object(flux, "_get_api", new_callable=AsyncMock):
                    result = await flux.wait_for_ready(session, timeout=5.0)

        assert result == SessionStatus.RUNNING

    @pytest.mark.asyncio
    async def test_flux_wait_for_ready_returns_failed_on_install_failure(self):
        """Flux wait_for_ready returns FAILED on InstallFailed condition."""
        import sys
        import types
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_k8s = types.ModuleType("kubernetes_asyncio")
        mock_watch_mod = types.ModuleType("kubernetes_asyncio.watch")

        mock_watch_instance = MagicMock()

        async def fake_stream(*args, **kwargs):
            yield {
                "object": {
                    "status": {
                        "conditions": [
                            {"type": "Ready", "status": "False", "reason": "InstallFailed"}
                        ]
                    }
                }
            }

        mock_watch_instance.stream = fake_stream
        mock_watch_instance.stop = MagicMock()
        mock_watch_mod.Watch = MagicMock(return_value=mock_watch_instance)
        mock_k8s.watch = mock_watch_mod

        with patch.dict(
            sys.modules,
            {
                "kubernetes_asyncio": mock_k8s,
                "kubernetes_asyncio.watch": mock_watch_mod,
            },
        ):
            from volundr.adapters.outbound.flux import FluxPodManager

            flux = FluxPodManager(namespace="test")
            session = Session(
                name="Test",
                model="test",
                source=GitSource(repo="test", branch="main"),
                status=SessionStatus.PROVISIONING,
            )

            with patch.object(flux, "status", new_callable=AsyncMock) as mock_status:
                mock_status.return_value = SessionStatus.STARTING
                with patch.object(flux, "_get_api", new_callable=AsyncMock):
                    result = await flux.wait_for_ready(session, timeout=5.0)

        assert result == SessionStatus.FAILED

    @pytest.mark.asyncio
    async def test_flux_wait_for_ready_shortcircuits_when_already_running(self):
        """Flux wait_for_ready returns immediately if already RUNNING."""
        from unittest.mock import AsyncMock, patch

        from volundr.adapters.outbound.flux import FluxPodManager

        flux = FluxPodManager(namespace="test")
        session = Session(
            name="Test",
            model="test",
            source=GitSource(repo="test", branch="main"),
            status=SessionStatus.PROVISIONING,
        )

        # status() returns RUNNING so wait_for_ready short-circuits
        # before importing kubernetes_asyncio.watch
        with patch.object(flux, "status", new_callable=AsyncMock) as mock_status:
            mock_status.return_value = SessionStatus.RUNNING
            result = await flux.wait_for_ready(session, timeout=5.0)

        assert result == SessionStatus.RUNNING
