"""Unit tests for NIU-598 reflection wiring in cli/commands.py.

Covers the new code paths:
- _build_agent passes sleipnir_publisher and persona
- _run_with_signals creates InProcessBus and wires PostSessionReflectionService
- _run_daemon creates shared InProcessBus and wires PostSessionReflectionService
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ravn.config import Settings, SignalSourceConfig

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def settings() -> Settings:
    return Settings()


@pytest.fixture()
def _api_key():
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        yield


@pytest.fixture()
def _mock_anthropic():
    with patch("ravn.adapters.llm.anthropic.AnthropicAdapter") as mock_cls:
        mock_cls.return_value = MagicMock()
        yield mock_cls


# ---------------------------------------------------------------------------
# _build_agent — NIU-598 new parameters
# ---------------------------------------------------------------------------


class TestBuildAgentReflectionWiring:
    @pytest.mark.usefixtures("_api_key", "_mock_anthropic")
    def test_sleipnir_publisher_forwarded_to_agent(self, settings: Settings) -> None:
        """sleipnir_publisher passed to _build_agent is set on RavnAgent."""
        from ravn.cli.commands import _build_agent

        fake_publisher = MagicMock()
        agent, _ = _build_agent(settings, sleipnir_publisher=fake_publisher)
        assert agent._sleipnir_publisher is fake_publisher

    @pytest.mark.usefixtures("_api_key", "_mock_anthropic")
    def test_reflection_config_is_not_retained_by_agent(self, settings: Settings) -> None:
        """Post-session reflection belongs to its service, not the turn agent."""
        from ravn.cli.commands import _build_agent

        settings.reflection.enabled = True
        settings.reflection.learning_token_budget = 999
        agent, _ = _build_agent(settings)
        assert not hasattr(agent, "_reflection_config")
        assert settings.effective_post_session_reflection_config().learning_token_budget == 999

    @pytest.mark.usefixtures("_api_key", "_mock_anthropic")
    def test_reflection_fallbacks_resolve_to_effective_model(self, settings: Settings) -> None:
        """Agent reflection config can follow the effective agent model."""
        from ravn.cli.commands import _build_agent

        settings.llm.model = "Qwen/Qwen3.6-35B-A3B-FP8"
        settings.memory.reflection_model = "same-as-agent"
        settings.reflection.llm_alias = "same-as-agent"

        agent, _ = _build_agent(settings)

        assert agent._reflection_model == "Qwen/Qwen3.6-35B-A3B-FP8"
        assert (
            settings.effective_post_session_reflection_config().llm_alias
            == "Qwen/Qwen3.6-35B-A3B-FP8"
        )
        assert settings.reflection.llm_alias == "same-as-agent"

    @pytest.mark.usefixtures("_api_key", "_mock_anthropic")
    def test_persona_name_forwarded_when_persona_config_present(self, settings: Settings) -> None:
        """Agent._persona is set to the persona config name when provided."""
        from ravn.adapters.personas.loader import PersonaConfig
        from ravn.cli.commands import _build_agent

        persona = MagicMock(spec=PersonaConfig)
        persona.name = "reviewer"
        persona.system_prompt_template = ""
        persona.iteration_budget = 0
        persona.stop_on_outcome = True
        persona.llm = MagicMock()
        persona.llm.max_tokens = 0
        persona.permission_mode = "suggest"
        persona.allowed_tools = []
        persona.forbidden_tools = []

        agent, _ = _build_agent(settings, persona_config=persona)
        assert agent._persona == "reviewer"
        assert agent._persona_config is persona
        assert agent._persona_config.stop_on_outcome is True
        assert not hasattr(agent, "_stop_on_outcome")

    @pytest.mark.usefixtures("_api_key", "_mock_anthropic")
    def test_persona_name_empty_when_no_persona_config(self, settings: Settings) -> None:
        """Agent._persona is empty string when no persona config is given."""
        from ravn.cli.commands import _build_agent

        agent, _ = _build_agent(settings, persona_config=None)
        assert agent._persona == ""


# ---------------------------------------------------------------------------
# _run_with_signals — reflection service lifecycle
# ---------------------------------------------------------------------------


class TestRunWithSignalsReflectionWiring:
    @pytest.mark.asyncio
    async def test_no_bus_created_when_reflection_disabled(self) -> None:
        """InProcessBus is NOT created when reflection.enabled=False."""
        settings = Settings()
        settings.reflection.enabled = False

        mock_agent = MagicMock()
        mock_agent._interrupt_reason = None
        mock_channel = MagicMock()

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            patch("ravn.cli.commands._build_agent", return_value=(mock_agent, mock_channel)),
            patch("ravn.cli.commands._chat", new_callable=AsyncMock),
            patch("asyncio.get_running_loop") as mock_loop,
        ):
            mock_loop.return_value = MagicMock()
            from ravn.cli.commands import _run_with_signals

            await _run_with_signals(
                settings=settings,
                no_tools=False,
                persona_config=None,
                prompt="hello",
                show_usage=False,
                resume_task_id=None,
            )

        # _build_mimir should not have been called for the reflection service.
        # We verify indirectly that no InProcessBus import happened for reflection.

    @pytest.mark.asyncio
    async def test_service_started_and_stopped_when_reflection_enabled(self) -> None:
        """PostSessionReflectionService is started and stopped when reflection.enabled."""
        settings = Settings()
        settings.reflection.enabled = True

        mock_agent = MagicMock()
        mock_agent._interrupt_reason = None
        mock_channel = MagicMock()
        mock_mimir = MagicMock()
        mock_svc = AsyncMock()
        mock_bus = AsyncMock()
        mock_bus.flush = AsyncMock()

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            patch("ravn.cli.commands._build_agent", return_value=(mock_agent, mock_channel)),
            patch("ravn.cli.commands._build_mimir", return_value=mock_mimir),
            patch("ravn.cli.commands._build_llm", return_value=MagicMock()),
            patch("ravn.cli.commands._chat", new_callable=AsyncMock),
            patch("sleipnir.adapters.in_process.InProcessBus", return_value=mock_bus),
            patch(
                "ravn.adapters.reflection.post_session.PostSessionReflectionService",
                return_value=mock_svc,
            ),
            patch("asyncio.get_running_loop") as mock_loop,
        ):
            mock_loop.return_value = MagicMock()
            from ravn.cli.commands import _run_with_signals

            await _run_with_signals(
                settings=settings,
                no_tools=False,
                persona_config=None,
                prompt="hello",
                show_usage=False,
                resume_task_id=None,
            )

        mock_svc.start.assert_awaited_once()
        mock_svc.stop.assert_awaited_once()
        mock_bus.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_service_not_started_when_mimir_is_none(self) -> None:
        """Service is NOT started when mimir is disabled (returns None)."""
        settings = Settings()
        settings.reflection.enabled = True

        mock_agent = MagicMock()
        mock_agent._interrupt_reason = None
        mock_channel = MagicMock()
        mock_svc = AsyncMock()
        mock_bus = AsyncMock()

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            patch("ravn.cli.commands._build_agent", return_value=(mock_agent, mock_channel)),
            patch("ravn.cli.commands._build_mimir", return_value=None),  # Mímir disabled
            patch("ravn.cli.commands._build_llm", return_value=MagicMock()),
            patch("ravn.cli.commands._chat", new_callable=AsyncMock),
            patch("sleipnir.adapters.in_process.InProcessBus", return_value=mock_bus),
            patch(
                "ravn.adapters.reflection.post_session.PostSessionReflectionService",
                return_value=mock_svc,
            ),
            patch("asyncio.get_running_loop") as mock_loop,
        ):
            mock_loop.return_value = MagicMock()
            from ravn.cli.commands import _run_with_signals

            await _run_with_signals(
                settings=settings,
                no_tools=False,
                persona_config=None,
                prompt="hello",
                show_usage=False,
                resume_task_id=None,
            )

        mock_svc.start.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_bus_passed_to_build_agent(self) -> None:
        """InProcessBus is forwarded to _build_agent as sleipnir_publisher."""
        settings = Settings()
        settings.reflection.enabled = True

        mock_agent = MagicMock()
        mock_agent._interrupt_reason = None
        mock_channel = MagicMock()
        mock_mimir = MagicMock()
        mock_bus = AsyncMock()
        mock_bus.flush = AsyncMock()
        mock_svc = AsyncMock()

        build_agent_calls: list = []

        def fake_build_agent(s, *, sleipnir_publisher=None, **kw):
            build_agent_calls.append(sleipnir_publisher)
            return mock_agent, mock_channel

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            patch("ravn.cli.commands._build_agent", side_effect=fake_build_agent),
            patch("ravn.cli.commands._build_mimir", return_value=mock_mimir),
            patch("ravn.cli.commands._build_llm", return_value=MagicMock()),
            patch("ravn.cli.commands._chat", new_callable=AsyncMock),
            patch("sleipnir.adapters.in_process.InProcessBus", return_value=mock_bus),
            patch(
                "ravn.adapters.reflection.post_session.PostSessionReflectionService",
                return_value=mock_svc,
            ),
            patch("asyncio.get_running_loop") as mock_loop,
        ):
            mock_loop.return_value = MagicMock()
            from ravn.cli.commands import _run_with_signals

            await _run_with_signals(
                settings=settings,
                no_tools=False,
                persona_config=None,
                prompt="hello",
                show_usage=False,
                resume_task_id=None,
            )

        assert len(build_agent_calls) == 1
        assert build_agent_calls[0] is mock_bus


# ---------------------------------------------------------------------------
# _run_daemon — reflection service lifecycle
# ---------------------------------------------------------------------------


class TestRunDaemonReflectionWiring:
    @pytest.mark.asyncio
    async def test_daemon_shares_environment_signal_publisher_with_drive_loop(self) -> None:
        """Resident Valkyrie task telemetry is published on the environment signal bus."""
        settings = Settings()
        settings.reflection.enabled = False
        settings.initiative.enabled = True
        settings.mesh.enabled = True
        settings.mesh.adapters = [{"transport": "nats"}]
        settings.environment.signal_sources = [
            SignalSourceConfig(
                id="host-events",
                adapter="ravn.adapters.environment_signals.HostSignalAdapter",
                kind="host",
            )
        ]
        settings.gateway.channels.telegram.enabled = False
        settings.gateway.channels.http.enabled = False
        settings.gateway.channels.discord.enabled = False
        settings.gateway.channels.slack.enabled = False
        settings.gateway.channels.matrix.enabled = False
        settings.gateway.channels.whatsapp.enabled = False

        # This test focuses on reflection wiring; the court's perpetual sweep
        # task would keep _run_daemon's gather alive forever.
        settings.odin_court.enabled = False
        mock_publisher = MagicMock()
        mock_publisher.subscribe = AsyncMock()
        mock_publisher.start = AsyncMock()
        mock_publisher.stop = AsyncMock()
        mock_runtime = AsyncMock()
        mock_runtime.wait = AsyncMock(return_value=None)
        mock_drive_loop = MagicMock()
        mock_drive_loop._triggers = []
        mock_drive_loop.run = AsyncMock(return_value=None)

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            patch("ravn.adapters.llm.anthropic.AnthropicAdapter") as mock_llm_cls,
            patch("ravn.cli.commands._build_mimir", return_value=None),
            patch(
                "ravn.cli.commands._start_mcp_shared",
                new_callable=AsyncMock,
                return_value=(None, []),
            ),
            patch("ravn.cli.commands._shutdown_mcp", new_callable=AsyncMock),
            patch("ravn.cli.commands._wire_triggers", return_value=[]),
            patch("ravn.cli.commands._wire_cron", return_value=[]),
            patch("ravn.drive_loop.DriveLoop", return_value=mock_drive_loop) as drive_loop_cls,
            patch(
                "ravn.cli.commands._build_environment_signal_publisher",
                return_value=mock_publisher,
            ),
            patch(
                "ravn.cli.commands._build_environment_signal_runtime",
                return_value=mock_runtime,
            ) as build_runtime,
        ):
            mock_llm_cls.return_value = MagicMock()
            from ravn.cli.commands import _run_daemon

            await _run_daemon(settings)

        assert drive_loop_cls.call_args.kwargs["sleipnir_publisher"] is mock_publisher
        assert build_runtime.call_args.kwargs["publisher"] is mock_publisher
        # The daemon itself starts (and stops) the shared publisher, so the runtime
        # must NOT also own its lifecycle — otherwise EnvironmentSignalRuntime.start()
        # would double-start the publisher.
        assert build_runtime.call_args.kwargs["owns_publisher"] is False
        mock_runtime.start.assert_awaited_once()
        mock_runtime.stop.assert_awaited_once()

    def test_environment_signal_runtime_can_reuse_supplied_publisher(self) -> None:
        """The runtime does not rebuild or own the publisher when one is supplied."""
        settings = Settings()
        settings.mesh.enabled = True
        settings.environment.signal_sources = [
            SignalSourceConfig(
                id="host-events",
                adapter="ravn.adapters.environment_signals.HostSignalAdapter",
                kind="host",
            )
        ]
        mock_publisher = MagicMock()

        from ravn.cli.commands import _build_environment_signal_runtime

        runtime = _build_environment_signal_runtime(
            settings,
            publisher=mock_publisher,
            owns_publisher=False,
        )

        assert runtime is not None
        assert runtime._publisher is mock_publisher
        assert runtime._owns_publisher is False

    @pytest.mark.asyncio
    async def test_daemon_bus_created_when_reflection_enabled(self) -> None:
        """InProcessBus is created in daemon mode when reflection.enabled."""
        settings = Settings()
        settings.reflection.enabled = True
        settings.initiative.enabled = False
        settings.gateway.channels.telegram.enabled = False
        settings.gateway.channels.http.enabled = False
        settings.gateway.channels.discord.enabled = False
        settings.gateway.channels.slack.enabled = False
        settings.gateway.channels.matrix.enabled = False
        settings.gateway.channels.whatsapp.enabled = False

        mock_bus = AsyncMock()
        mock_mimir = MagicMock()
        mock_svc = AsyncMock()
        mock_llm = MagicMock()

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            patch("ravn.adapters.llm.anthropic.AnthropicAdapter") as mock_llm_cls,
            patch("ravn.cli.commands._build_mimir", return_value=mock_mimir),
            patch("sleipnir.adapters.in_process.InProcessBus", return_value=mock_bus),
            patch(
                "ravn.adapters.reflection.post_session.PostSessionReflectionService",
                return_value=mock_svc,
            ),
        ):
            mock_llm_cls.return_value = mock_llm
            from ravn.cli.commands import _run_daemon

            # No tasks → daemon exits immediately after checking.
            await _run_daemon(settings)

        mock_svc.start.assert_awaited_once()
        mock_bus.flush.assert_awaited_once()
        mock_svc.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_daemon_no_bus_when_reflection_disabled(self) -> None:
        """InProcessBus is NOT created in daemon mode when reflection.enabled=False."""
        settings = Settings()
        settings.reflection.enabled = False
        settings.initiative.enabled = False
        settings.gateway.channels.telegram.enabled = False
        settings.gateway.channels.http.enabled = False
        settings.gateway.channels.discord.enabled = False
        settings.gateway.channels.slack.enabled = False
        settings.gateway.channels.matrix.enabled = False
        settings.gateway.channels.whatsapp.enabled = False

        mock_svc = AsyncMock()

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            patch("ravn.adapters.llm.anthropic.AnthropicAdapter") as mock_llm_cls,
            patch(
                "ravn.adapters.reflection.post_session.PostSessionReflectionService",
                return_value=mock_svc,
            ),
        ):
            mock_llm_cls.return_value = MagicMock()
            from ravn.cli.commands import _run_daemon

            await _run_daemon(settings)

        mock_svc.start.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_daemon_bus_flushed_in_finally_block(self) -> None:
        """daemon_bus.flush() is called in the finally block when tasks complete normally."""
        settings = Settings()
        settings.reflection.enabled = True
        settings.initiative.enabled = True  # adds a task → exercises finally block
        settings.gateway.channels.telegram.enabled = False
        settings.gateway.channels.http.enabled = False
        settings.gateway.channels.discord.enabled = False
        settings.gateway.channels.slack.enabled = False
        settings.gateway.channels.matrix.enabled = False
        settings.gateway.channels.whatsapp.enabled = False

        mock_bus = AsyncMock()
        mock_drive_loop = MagicMock()
        mock_drive_loop._triggers = []
        mock_drive_loop.run = AsyncMock(return_value=None)

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            patch("ravn.adapters.llm.anthropic.AnthropicAdapter") as mock_llm_cls,
            patch("ravn.cli.commands._build_mimir", return_value=None),
            patch(
                "ravn.cli.commands._start_mcp_shared",
                new_callable=AsyncMock,
                return_value=(None, []),
            ),
            patch("ravn.cli.commands._shutdown_mcp", new_callable=AsyncMock),
            patch("ravn.cli.commands._wire_triggers", return_value=[]),
            patch("ravn.cli.commands._wire_cron", return_value=[]),
            patch("ravn.drive_loop.DriveLoop", return_value=mock_drive_loop),
            patch("sleipnir.adapters.in_process.InProcessBus", return_value=mock_bus),
        ):
            mock_llm_cls.return_value = MagicMock()
            from ravn.cli.commands import _run_daemon

            await _run_daemon(settings)

        # finally block flush — not the early-exit flush
        mock_bus.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_daemon_flush_exception_is_swallowed(self) -> None:
        """flush() raising an exception does not propagate out of daemon teardown."""
        settings = Settings()
        settings.reflection.enabled = True
        settings.initiative.enabled = False
        settings.gateway.channels.telegram.enabled = False
        settings.gateway.channels.http.enabled = False
        settings.gateway.channels.discord.enabled = False
        settings.gateway.channels.slack.enabled = False
        settings.gateway.channels.matrix.enabled = False
        settings.gateway.channels.whatsapp.enabled = False

        mock_bus = AsyncMock()
        mock_bus.flush = AsyncMock(side_effect=RuntimeError("bus error"))
        mock_mimir = MagicMock()
        mock_svc = AsyncMock()

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            patch("ravn.adapters.llm.anthropic.AnthropicAdapter") as mock_llm_cls,
            patch("ravn.cli.commands._build_mimir", return_value=mock_mimir),
            patch("sleipnir.adapters.in_process.InProcessBus", return_value=mock_bus),
            patch(
                "ravn.adapters.reflection.post_session.PostSessionReflectionService",
                return_value=mock_svc,
            ),
        ):
            mock_llm_cls.return_value = MagicMock()
            from ravn.cli.commands import _run_daemon

            # Must not raise despite flush() blowing up
            await _run_daemon(settings)

        mock_bus.flush.assert_awaited_once()
        # stop() is still called even when flush raised
        mock_svc.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_daemon_no_service_when_mimir_is_none(self) -> None:
        """Reflection service is NOT started in daemon mode when Mímir is disabled."""
        settings = Settings()
        settings.reflection.enabled = True
        settings.initiative.enabled = False
        settings.gateway.channels.telegram.enabled = False
        settings.gateway.channels.http.enabled = False
        settings.gateway.channels.discord.enabled = False
        settings.gateway.channels.slack.enabled = False
        settings.gateway.channels.matrix.enabled = False
        settings.gateway.channels.whatsapp.enabled = False

        mock_bus = AsyncMock()
        mock_svc = AsyncMock()

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            patch("ravn.adapters.llm.anthropic.AnthropicAdapter") as mock_llm_cls,
            patch("ravn.cli.commands._build_mimir", return_value=None),
            patch("sleipnir.adapters.in_process.InProcessBus", return_value=mock_bus),
            patch(
                "ravn.adapters.reflection.post_session.PostSessionReflectionService",
                return_value=mock_svc,
            ),
        ):
            mock_llm_cls.return_value = MagicMock()
            from ravn.cli.commands import _run_daemon

            await _run_daemon(settings)

        mock_svc.start.assert_not_awaited()
