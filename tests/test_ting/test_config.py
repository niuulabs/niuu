"""Tests for Ting configuration."""

from __future__ import annotations

import pytest

from ting.config import (
    RAVN_FLOCK_REMOVED_MESSAGE,
    DatabaseConfig,
    DispatchConfig,
    DispatchMimirConfig,
    LLMConfig,
    LoggingConfig,
    Settings,
    TicketCoordinatorConfig,
    WorkflowExecutionConfig,
    WorkflowStepsConfig,
)


class TestWorkflowStepsConfig:
    def test_step_deadline_defaults_to_four_hours(self) -> None:
        assert WorkflowStepsConfig().step_deadline_seconds == 4 * 3600.0
        assert Settings().workflow_steps.step_deadline_seconds == 4 * 3600.0

    def test_step_deadline_is_set_from_config(self) -> None:
        config = Settings(workflow_steps={"step_deadline_seconds": 90})

        assert config.workflow_steps.step_deadline_seconds == 90.0

    @pytest.mark.parametrize("value", [0, -1])
    def test_step_deadline_must_be_positive(self, value: float) -> None:
        with pytest.raises(ValueError, match="greater than 0"):
            WorkflowStepsConfig(step_deadline_seconds=value)

    def test_the_description_says_what_bounds_it_and_what_a_hit_does(self) -> None:
        description = WorkflowStepsConfig.model_fields["step_deadline_seconds"].description or ""

        assert "bound" in description
        assert "never acts on it" in description
        assert "NIU-1181" in description


class TestStuckDetectionConfig:
    def test_it_is_on_by_default_with_bounded_intervals(self) -> None:
        stuck = Settings().workflow_steps.stuck

        assert stuck.enabled is True
        assert stuck.sweep_interval_seconds == 120.0
        assert stuck.start_grace_seconds == 600.0

    def test_it_is_set_from_config(self) -> None:
        config = Settings(
            workflow_steps={
                "stuck": {"enabled": False, "sweep_interval_seconds": 30, "start_grace_seconds": 45}
            }
        )

        assert config.workflow_steps.stuck.enabled is False
        assert config.workflow_steps.stuck.sweep_interval_seconds == 30.0
        assert config.workflow_steps.stuck.start_grace_seconds == 45.0

    @pytest.mark.parametrize("field", ["sweep_interval_seconds", "start_grace_seconds"])
    @pytest.mark.parametrize("value", [0, -5])
    def test_the_intervals_must_be_positive(self, field: str, value: float) -> None:
        with pytest.raises(ValueError, match="greater than 0"):
            Settings(workflow_steps={"stuck": {field: value}})

    @pytest.mark.parametrize("field", ["sweep_interval_seconds", "start_grace_seconds"])
    def test_each_interval_says_what_bounds_it(self, field: str) -> None:
        from ting.config import StuckDetectionConfig

        description = StuckDetectionConfig.model_fields[field].description or ""

        assert "Bounds" in description or "bound" in description


class TestDatabaseConfig:
    def test_defaults(self) -> None:
        config = DatabaseConfig()
        assert config.host == "localhost"
        assert config.port == 5432
        assert config.user == "ting"
        assert config.password == "ting"
        assert config.name == "ting"
        assert config.min_pool_size == 5
        assert config.max_pool_size == 20

    def test_dsn(self) -> None:
        config = DatabaseConfig(host="db", port=5433, user="u", password="p", name="mydb")
        assert config.dsn == "postgresql://u:p@db:5433/mydb"

    def test_custom_values(self) -> None:
        config = DatabaseConfig(host="prod-db", min_pool_size=10, max_pool_size=50)
        assert config.host == "prod-db"
        assert config.min_pool_size == 10


class TestLoggingConfig:
    def test_defaults(self) -> None:
        config = LoggingConfig()
        assert config.level == "info"
        assert config.format == "text"


class TestSettings:
    def test_defaults(self) -> None:
        settings = Settings()
        assert isinstance(settings.database, DatabaseConfig)
        assert isinstance(settings.logging, LoggingConfig)

    def test_nested_override(self) -> None:
        settings = Settings(database=DatabaseConfig(host="custom-host"))
        assert settings.database.host == "custom-host"

    def test_observability_requires_trace_metric_and_log_endpoints(self) -> None:
        with pytest.raises(ValueError, match="trace_endpoint, metric_endpoint, and log_endpoint"):
            Settings(observability={"enabled": True, "trace_endpoint": "http://tempo:4317"})

    def test_shared_integrations_accepts_platform_env_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("INTEGRATIONS__DATABASE_NAME", "niuu_shared")

        settings = Settings()

        assert settings.shared_integrations.database_name == "niuu_shared"

    def test_server_and_platform_legacy_aliases(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOST", "127.0.0.2")
        monkeypatch.setenv("PORT", "8181")
        monkeypatch.setenv("WORKERS", "2")
        monkeypatch.setenv("NIUU_SERVER_HOST", "192.0.2.10")
        monkeypatch.setenv("NIUU_SERVER_PORT", "9090")

        settings = Settings()

        assert settings.server_host == "127.0.0.2"
        assert settings.server_port == 8181
        assert settings.server_workers == 2
        assert settings.local_platform_host == "192.0.2.10"
        assert settings.local_platform_port == 9090

    @pytest.mark.parametrize(
        ("name", "value", "field"),
        [
            ("PORT", "invalid", "server_port"),
            ("PORT", "70000", "server_port"),
            ("WORKERS", "0", "server_workers"),
            ("NIUU_SERVER_PORT", "0", "local_platform_port"),
        ],
    )
    def test_malformed_server_aliases_fail_loudly(
        self,
        monkeypatch: pytest.MonkeyPatch,
        name: str,
        value: str,
        field: str,
    ) -> None:
        monkeypatch.setenv(name, value)

        with pytest.raises(ValueError, match=field):
            Settings()


class TestRemovedWorkflowRunnerSettings:
    """Settings that configured the removed Ravn flock path stop Ting instead of being ignored."""

    def test_enabling_flock_is_fatal_with_the_refusal_message_and_the_remedy(self) -> None:
        with pytest.raises(ValueError) as raised:
            Settings(dispatch={"flock": {"enabled": True}})

        assert RAVN_FLOCK_REMOVED_MESSAGE in str(raised.value)
        assert "remove dispatch.flock.enabled" in str(raised.value)

    def test_enabling_flock_through_the_environment_is_fatal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DISPATCH__FLOCK__ENABLED", "true")

        with pytest.raises(ValueError, match="remove dispatch.flock.enabled"):
            Settings()

    @pytest.mark.parametrize(
        "flock",
        [
            {"enabled": False},
            {"default_personas": ["coordinator"]},
            {"default_workflow_name": "reviewed-delivery-flow"},
            {"mimir_hosted_url": "https://mimir.test"},
        ],
    )
    def test_any_flock_setting_is_fatal_and_names_where_mimir_moved(self, flock: dict) -> None:
        with pytest.raises(ValueError, match=r"dispatch\.flock was removed.*dispatch\.mimir"):
            DispatchConfig(flock=flock)

    @pytest.mark.parametrize(
        ("key", "value"),
        [("in_process", {"llm_config": {}}), ("workflow_cli_turn_timeout_seconds", 900)],
    )
    def test_removed_dispatch_keys_are_fatal(self, key: str, value: object) -> None:
        with pytest.raises(ValueError, match=rf"dispatch\.{key} was removed"):
            DispatchConfig(**{key: value})

    @pytest.mark.parametrize("key", ["model", "ravn_config"])
    def test_removed_ticket_coordinator_keys_are_fatal(self, key: str) -> None:
        with pytest.raises(ValueError, match=rf"ticket_coordinator\.{key} was removed"):
            TicketCoordinatorConfig(**{key: "x" if key == "model" else {}})

    @pytest.mark.parametrize("key", ["ravn_decomposer_enabled", "ravn_decomposer_timeout"])
    def test_removed_decomposer_keys_are_fatal(self, key: str) -> None:
        with pytest.raises(ValueError, match=rf"llm\.{key} was removed"):
            LLMConfig(**{key: True})

    def test_ravn_outcome_section_is_fatal(self) -> None:
        with pytest.raises(ValueError) as raised:
            Settings(ravn_outcome={"enabled": True})

        assert RAVN_FLOCK_REMOVED_MESSAGE in str(raised.value)
        assert "Remove ravn_outcome" in str(raised.value)

    def test_flock_flows_section_is_fatal(self) -> None:
        with pytest.raises(ValueError, match="settings.flock_flows was removed"):
            Settings(flock_flows={"adapter": "x"})

    def test_mimir_settings_live_under_dispatch_mimir(self) -> None:
        settings = Settings(
            dispatch={
                "mimir": {
                    "hosted_url": "https://mimir.test",
                    "registry_path": "/etc/mimir/registry.json",
                }
            }
        )

        assert settings.dispatch.mimir.hosted_url == "https://mimir.test"
        assert settings.dispatch.mimir.registry_path == "/etc/mimir/registry.json"

    def test_mimir_defaults(self) -> None:
        config = DispatchMimirConfig()

        assert config.hosted_url == ""
        assert config.registry_path == "~/.ravn/mimir/.mimir-registry.json"


class TestWorkflowExecutionConfig:
    """The generic pack and its code-delivery specialization gate independently."""

    def test_defaults_are_generic_only(self) -> None:
        config = WorkflowExecutionConfig()
        assert config.enabled is False
        assert config.delivery.enabled is False
        # Delivery-only settings moved off the top-level model entirely.
        assert not hasattr(config, "evidence_policy_id")
        assert not hasattr(config, "integration_policy_id")
        assert not hasattr(config, "review_producers")

    def test_delivery_enabled_requires_workflow_execution_enabled(self) -> None:
        with pytest.raises(ValueError, match="requires workflow_execution.enabled"):
            WorkflowExecutionConfig(enabled=False, delivery={"enabled": True})

    def test_delivery_enabled_with_workflow_execution_enabled_is_accepted(self) -> None:
        config = WorkflowExecutionConfig(enabled=True, delivery={"enabled": True})
        assert config.delivery.enabled is True

    def test_rejects_ting_delivery_wait_observer_while_delivery_is_disabled(self) -> None:
        with pytest.raises(ValueError, match="ting.delivery"):
            WorkflowExecutionConfig(
                enabled=True,
                delivery={"enabled": False},
                wait_observers=[
                    {
                        "condition_type": "forge.checks",
                        "adapter": "ting.delivery.wait_observers.ForgeChecksWaitObserver",
                    }
                ],
            )

    def test_accepts_ting_delivery_wait_observer_once_delivery_is_enabled(self) -> None:
        config = WorkflowExecutionConfig(
            enabled=True,
            delivery={"enabled": True},
            wait_observers=[
                {
                    "condition_type": "forge.checks",
                    "adapter": "ting.delivery.wait_observers.ForgeChecksWaitObserver",
                }
            ],
        )
        assert config.wait_observers[0]["adapter"].endswith("ForgeChecksWaitObserver")

    def test_generic_only_wait_observer_is_unaffected_by_delivery_state(self) -> None:
        config = WorkflowExecutionConfig(
            enabled=True,
            delivery={"enabled": False},
            wait_observers=[
                {
                    "condition_type": "timer",
                    "adapter": "ting.adapters.timer_wait_observer.TimerWaitObserver",
                }
            ],
        )
        assert config.wait_observers[0]["condition_type"] == "timer"
