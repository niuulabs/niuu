"""Tests for Skuld configuration."""

import json

import pytest

from skuld.config import (
    SkuldSessionConfig,
    SkuldSettings,
    WorkflowRuntimeConfig,
)


@pytest.fixture(autouse=True)
def _no_yaml_config(monkeypatch):
    """Disable YAML config file loading so real files on disk don't interfere."""
    monkeypatch.setitem(SkuldSettings.model_config, "yaml_file", [])


class TestSkuldSessionConfig:
    """Tests for SkuldSessionConfig defaults."""

    def test_defaults(self):
        config = SkuldSessionConfig()
        assert config.id == "unknown"
        assert config.name == "unknown"
        assert config.model == "claude-opus-4-8"
        assert config.workspace_dir is None

    def test_explicit_values(self):
        config = SkuldSessionConfig(
            id="sess-1", name="my-session", model="opus", workspace_dir="/tmp/ws"
        )
        assert config.id == "sess-1"
        assert config.name == "my-session"
        assert config.model == "opus"
        assert config.workspace_dir == "/tmp/ws"


class TestSkuldSettings:
    """Tests for SkuldSettings."""

    def test_defaults(self, monkeypatch):
        """Test all default values when no env vars or config files."""
        # Clear any env vars that might interfere
        for var in [
            "SKULD__TRANSPORT",
            "SKULD__HOST",
            "SKULD__PORT",
            "SKULD__SESSION__ID",
            "SKULD__SESSION__MODEL",
        ]:
            monkeypatch.delenv(var, raising=False)

        s = SkuldSettings()
        assert s.transport == "sdk"
        assert s.host == "0.0.0.0"
        assert s.port == 8081
        assert s.volundr_api_url == ""
        assert s.session.id == "unknown"
        assert s.session.name == "unknown"
        assert s.codex_auth.adapter == "skuld.codex_auth.HostCodexAuthProvider"
        assert s.session.model == "claude-opus-4-8"
        assert s.observability.service_name == "skuld"
        assert s.persistence_mount_path == "/volundr/sessions"
        assert s.peer_watchdog.enabled is True
        assert s.peer_watchdog.poll_seconds == 5.0
        assert s.peer_watchdog.silence_seconds == 300.0
        assert s.peer_watchdog.tool_silence_seconds == 300.0

    def test_workspace_path_computed(self, monkeypatch):
        """Test workspace_path computed from session ID when workspace_dir is None."""
        monkeypatch.setenv("SKULD__SESSION__ID", "sess-42")

        s = SkuldSettings()
        assert s.workspace_path == "/volundr/sessions/sess-42/workspace"

    def test_workspace_path_explicit(self, monkeypatch):
        """Test workspace_path returns explicit workspace_dir when set."""
        monkeypatch.setenv("SKULD__SESSION__WORKSPACE_DIR", "/custom/path")

        s = SkuldSettings()
        assert s.workspace_path == "/custom/path"

    def test_prefixed_env_vars(self, monkeypatch):
        """Test SKULD__ prefixed env vars override defaults."""
        monkeypatch.setenv("SKULD__TRANSPORT", "subprocess")
        monkeypatch.setenv("SKULD__HOST", "127.0.0.1")
        monkeypatch.setenv("SKULD__PORT", "9999")

        s = SkuldSettings()
        assert s.transport == "subprocess"
        assert s.host == "127.0.0.1"
        assert s.port == 9999

    def test_observability_partial_env_keeps_skuld_service_name(self, monkeypatch):
        monkeypatch.setenv("SKULD__OBSERVABILITY__ENABLED", "true")
        monkeypatch.setenv("SKULD__OBSERVABILITY__TRACE_ENDPOINT", "https://tempo:4317")
        monkeypatch.setenv(
            "SKULD__OBSERVABILITY__METRIC_ENDPOINT",
            "https://mimir:4318/v1/metrics",
        )

        s = SkuldSettings()

        assert s.observability.enabled is True
        assert s.observability.service_name == "skuld"

    def test_nested_env_vars(self, monkeypatch):
        """Test SKULD__SESSION__* nested env vars."""
        monkeypatch.setenv("SKULD__SESSION__ID", "nested-id")
        monkeypatch.setenv("SKULD__SESSION__MODEL", "opus")

        s = SkuldSettings()
        assert s.session.id == "nested-id"
        assert s.session.model == "opus"

    def test_peer_watchdog_nested_env_vars(self, monkeypatch):
        monkeypatch.setenv("SKULD__PEER_WATCHDOG__SILENCE_SECONDS", "600")
        monkeypatch.setenv("SKULD__PEER_WATCHDOG__TOOL_SILENCE_SECONDS", "900")

        s = SkuldSettings()
        assert s.peer_watchdog.silence_seconds == 600.0
        assert s.peer_watchdog.tool_silence_seconds == 900.0

    def test_mcp_servers_from_env(self, monkeypatch):
        monkeypatch.setenv(
            "SKULD__MCP_SERVERS",
            '[{"name":"mimir-local","type":"stdio","command":"python3","args":["-m","mimir"]}]',
        )

        s = SkuldSettings()
        assert s.mcp_servers == [
            {
                "name": "mimir-local",
                "type": "stdio",
                "command": "python3",
                "args": ["-m", "mimir"],
            }
        ]

    def test_flat_env_vars_are_ignored(self, monkeypatch):
        """Skuld broker config only accepts SKULD__ structured env vars."""
        monkeypatch.setenv("SESSION_ID", "flat-id")
        monkeypatch.setenv("MODEL", "flat-model")
        monkeypatch.setenv("HOST", "10.0.0.1")
        monkeypatch.setenv("PORT", "7777")
        monkeypatch.setenv("VOLUNDR_API_URL", "http://volundr:80")

        s = SkuldSettings()
        assert s.session.id == "unknown"
        assert s.session.model == "claude-opus-4-8"
        assert s.host == "0.0.0.0"
        assert s.port == 8081
        assert s.volundr_api_url == ""

    def test_prefixed_env_vars_work_when_flat_envs_exist(self, monkeypatch):
        """SKULD__ env vars are the supported configuration surface."""
        monkeypatch.setenv("SESSION_ID", "flat")
        monkeypatch.setenv("SKULD__SESSION__ID", "prefixed")
        monkeypatch.setenv("SKULD__TRANSPORT", "subprocess")

        s = SkuldSettings()
        assert s.session.id == "prefixed"
        assert s.transport == "subprocess"

    def test_yaml_config_loading(self, tmp_path, monkeypatch):
        """Test loading configuration from a YAML file."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "transport: subprocess\n"
            "host: 192.168.1.1\n"
            "port: 5555\n"
            "session:\n"
            "  id: yaml-session\n"
            "  model: haiku\n"
        )

        # Clear env vars
        for var in [
            "SKULD__TRANSPORT",
            "SKULD__HOST",
            "SKULD__PORT",
            "SKULD__SESSION__ID",
            "SKULD__SESSION__MODEL",
        ]:
            monkeypatch.delenv(var, raising=False)

        # Point to the test YAML file (overrides the autouse fixture)
        monkeypatch.setitem(SkuldSettings.model_config, "yaml_file", [config_file])

        s = SkuldSettings()
        assert s.transport == "subprocess"
        assert s.host == "192.168.1.1"
        assert s.port == 5555
        assert s.session.id == "yaml-session"
        assert s.session.model == "haiku"

    @pytest.mark.parametrize("transport", ["sdk", "subprocess"])
    def test_valid_transport_values(self, transport, monkeypatch):
        """Test both valid transport values are accepted."""
        monkeypatch.setenv("SKULD__TRANSPORT", transport)
        s = SkuldSettings()
        assert s.transport == transport

    def test_init_kwargs(self, monkeypatch):
        """Test explicit constructor arguments take highest priority."""
        monkeypatch.setenv("SKULD__TRANSPORT", "subprocess")

        s = SkuldSettings(transport="sdk")
        assert s.transport == "sdk"

    def test_skip_permissions_default(self):
        s = SkuldSettings()
        assert s.skip_permissions is True

    def test_skip_permissions_false(self, monkeypatch):
        monkeypatch.setenv("SKULD__SKIP_PERMISSIONS", "false")
        s = SkuldSettings()
        assert s.skip_permissions is False

    def test_codex_permission_thread_params(self, monkeypatch):
        monkeypatch.setenv("SKULD__APPROVAL_POLICY", "untrusted")
        monkeypatch.setenv("SKULD__SANDBOX", "workspace-write")
        s = SkuldSettings()
        assert s.approval_policy == "untrusted"
        assert s.sandbox == "workspace-write"

    def test_agent_teams_default_on(self):
        # Default ON: Claude tmux sessions form a team of agents by default.
        s = SkuldSettings()
        assert s.agent_teams is True

    def test_agent_teams_can_be_disabled(self, monkeypatch):
        monkeypatch.setenv("SKULD__AGENT_TEAMS", "false")
        s = SkuldSettings()
        assert s.agent_teams is False

    def test_session_prompt_defaults_empty(self):
        config = SkuldSessionConfig()
        assert config.system_prompt == ""
        assert config.initial_prompt == ""

    def test_session_prompt_explicit(self):
        config = SkuldSessionConfig(
            system_prompt="You are an agent.",
            initial_prompt="Fix the bug.",
        )
        assert config.system_prompt == "You are an agent."
        assert config.initial_prompt == "Fix the bug."

    def test_prefixed_env_prompt(self, monkeypatch):
        monkeypatch.setenv("SKULD__SESSION__SYSTEM_PROMPT", "prefixed")

        s = SkuldSettings()
        assert s.session.system_prompt == "prefixed"


class TestTransportAdapter:
    """Tests for the transport_adapter config field and legacy migration."""

    def test_default_resolves_to_sdk(self, monkeypatch):
        """Default config resolves to SDKTransport."""
        for var in ["SKULD__CLI_TYPE", "SKULD__TRANSPORT"]:
            monkeypatch.delenv(var, raising=False)

        s = SkuldSettings()
        assert s.transport_adapter == "skuld.transports.sdk.SDKTransport"

    def test_cli_type_codex_resolves_to_codex_transport(self, monkeypatch):
        """cli_type=codex maps to CodexSubprocessTransport."""
        monkeypatch.setenv("SKULD__CLI_TYPE", "codex")

        s = SkuldSettings()
        assert s.transport_adapter == "skuld.transports.codex.CodexSubprocessTransport"

    def test_transport_subprocess_resolves(self, monkeypatch):
        """transport=subprocess maps to SubprocessTransport."""
        monkeypatch.setenv("SKULD__TRANSPORT", "subprocess")

        s = SkuldSettings()
        assert s.transport_adapter == "skuld.transports.subprocess.SubprocessTransport"

    def test_transport_tmux_interactive_resolves(self, monkeypatch):
        """transport=tmux-interactive maps to TmuxInteractiveTransport."""
        monkeypatch.setenv("SKULD__TRANSPORT", "tmux-interactive")

        s = SkuldSettings()
        assert s.transport_adapter == "skuld.transports.tmux_interactive.TmuxInteractiveTransport"

    def test_explicit_transport_adapter_takes_precedence(self, monkeypatch):
        """Explicit transport_adapter overrides legacy fields."""
        monkeypatch.setenv("SKULD__CLI_TYPE", "codex")
        monkeypatch.setenv(
            "SKULD__TRANSPORT_ADAPTER",
            "my.custom.Transport",
        )

        s = SkuldSettings()
        assert s.transport_adapter == "my.custom.Transport"

    def test_flat_cli_type_env_var_is_ignored(self, monkeypatch):
        """CLI_TYPE is not a supported broker config alias."""
        for var in ["SKULD__CLI_TYPE", "SKULD__TRANSPORT"]:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("CLI_TYPE", "codex")

        s = SkuldSettings()
        assert s.transport_adapter == "skuld.transports.sdk.SDKTransport"

    def test_codex_takes_precedence_over_subprocess(self, monkeypatch):
        """When both cli_type=codex and transport=subprocess, codex wins."""
        monkeypatch.setenv("SKULD__CLI_TYPE", "codex")
        monkeypatch.setenv("SKULD__TRANSPORT", "subprocess")

        s = SkuldSettings()
        assert s.transport_adapter == "skuld.transports.codex.CodexSubprocessTransport"

    def test_init_kwarg_transport_adapter(self):
        """Constructor kwarg for transport_adapter takes precedence."""
        s = SkuldSettings(
            cli_type="codex",
            transport_adapter="my.override.Transport",
        )
        assert s.transport_adapter == "my.override.Transport"

    def test_yaml_transport_adapter(self, tmp_path, monkeypatch):
        """YAML transport_adapter field is loaded correctly."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "transport_adapter: skuld.transports.subprocess.SubprocessTransport\n"
        )
        for var in ["SKULD__TRANSPORT_ADAPTER", "SKULD__CLI_TYPE", "CLI_TYPE"]:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setitem(SkuldSettings.model_config, "yaml_file", [config_file])

        s = SkuldSettings()
        assert s.transport_adapter == "skuld.transports.subprocess.SubprocessTransport"


class TestWorkloadIdentityConfig:
    """Workload identity settings — config file canonical, legacy env aliases."""

    _LEGACY_VARS = [
        "NIUU_WORKLOAD_IDENTITY_TOKEN_FILE",
        "NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL",
        "NIUU_WORKLOAD_IDENTITY_AUDIENCES",
        "SKULD__WORKLOAD_IDENTITY__TOKEN_FILE",
        "SKULD__WORKLOAD_IDENTITY__EXCHANGE_URL",
        "SKULD__WORKLOAD_IDENTITY__AUDIENCES",
    ]

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        for var in self._LEGACY_VARS:
            monkeypatch.delenv(var, raising=False)

    def test_defaults(self):
        s = SkuldSettings()
        assert s.workload_identity.token_file == "/var/run/secrets/niuu-workload/token"
        assert s.workload_identity.exchange_url == ""
        assert s.workload_identity.audiences == ["volundr-api", "forge", "ting", "mimir", "guild"]

    def test_config_dict_is_canonical(self):
        s = SkuldSettings(
            workload_identity={
                "token_file": "/etc/tokens/proof",
                "exchange_url": "http://volundr/api/v1/tokens/workload/exchange",
                "audiences": ["volundr-api"],
            }
        )
        assert s.workload_identity.token_file == "/etc/tokens/proof"
        assert s.workload_identity.exchange_url == "http://volundr/api/v1/tokens/workload/exchange"
        assert s.workload_identity.audiences == ["volundr-api"]

    def test_yaml_section_loaded(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "workload_identity:\n"
            "  token_file: /run/proof\n"
            "  exchange_url: http://from-yaml/exchange\n"
        )
        monkeypatch.setitem(SkuldSettings.model_config, "yaml_file", [config_file])

        s = SkuldSettings()
        assert s.workload_identity.token_file == "/run/proof"
        assert s.workload_identity.exchange_url == "http://from-yaml/exchange"

    def test_legacy_bare_env_vars_still_work(self, monkeypatch):
        """Deployed charts set bare NIUU_WORKLOAD_IDENTITY_* env vars."""
        monkeypatch.setenv("NIUU_WORKLOAD_IDENTITY_TOKEN_FILE", "/legacy/token")
        monkeypatch.setenv("NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL", "http://legacy/exchange")
        monkeypatch.setenv("NIUU_WORKLOAD_IDENTITY_AUDIENCES", "volundr-api, mimir")

        s = SkuldSettings()
        assert s.workload_identity.token_file == "/legacy/token"
        assert s.workload_identity.exchange_url == "http://legacy/exchange"
        assert s.workload_identity.audiences == ["volundr-api", "mimir"]

    def test_legacy_env_overrides_yaml(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("workload_identity:\n  exchange_url: http://from-yaml/exchange\n")
        monkeypatch.setitem(SkuldSettings.model_config, "yaml_file", [config_file])
        monkeypatch.setenv("NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL", "http://legacy/exchange")

        s = SkuldSettings()
        assert s.workload_identity.exchange_url == "http://legacy/exchange"

    def test_prefixed_env_beats_legacy_env(self, monkeypatch):
        monkeypatch.setenv("NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL", "http://legacy/exchange")
        monkeypatch.setenv("SKULD__WORKLOAD_IDENTITY__EXCHANGE_URL", "http://prefixed/exchange")

        s = SkuldSettings()
        assert s.workload_identity.exchange_url == "http://prefixed/exchange"

    def test_init_kwargs_beat_legacy_env(self, monkeypatch):
        monkeypatch.setenv("NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL", "http://legacy/exchange")

        s = SkuldSettings(workload_identity={"exchange_url": "http://init/exchange"})
        assert s.workload_identity.exchange_url == "http://init/exchange"


class TestBehaviorSettingsAliases:
    def test_legacy_external_token_alias(self, monkeypatch):
        monkeypatch.setenv("VOLUNDR_EXTERNAL_API_TOKEN", "legacy-token")
        assert SkuldSettings().external_api_token == "legacy-token"

    def test_presented_file_limit_alias_and_validation(self, monkeypatch):
        monkeypatch.setenv("SKULD__MAX_PRESENTED_FILE_BYTES", "4096")
        assert SkuldSettings().max_presented_file_bytes == 4096
        monkeypatch.setenv("SKULD__MAX_PRESENTED_FILE_BYTES", "0")
        with pytest.raises(ValueError):
            SkuldSettings()

    def test_remote_control_settings_are_typed(self, monkeypatch):
        monkeypatch.setenv("SKULD__CLI_BINARY", "claude-custom")
        monkeypatch.setenv("SKULD__REMOTE_CONTROL_PERMISSION_MODE", "acceptEdits")

        settings = SkuldSettings()

        assert settings.cli_binary == "claude-custom"
        assert settings.remote_control_permission_mode == "acceptEdits"


def test_workflow_trace_context_loads_from_nested_env(monkeypatch) -> None:
    monkeypatch.setenv(
        "SKULD__WORKFLOW__TRACE_CONTEXT",
        '{"traceparent":"00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"}',
    )

    settings = SkuldSettings()

    assert settings.workflow.trace_context == {
        "traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
    }


class TestWorkflowRuntimeConfigInitialContext:
    """The brief travels as a container env value, and the sandbox provisioner
    rejects any value holding a newline. Volundr JSON-encodes it so the
    newlines are escaped; these tests hold the decode to that contract."""

    def test_json_encoded_multiline_brief_round_trips(self):
        brief = "Search the repo for error code 7.\n\nReport what you find.\r\nEnd."

        config = WorkflowRuntimeConfig(initial_context=json.dumps(brief))

        assert config.initial_context == brief

    def test_plain_prose_is_left_alone(self):
        """An older Volundr sends the value raw. Decoding must not mangle it."""
        config = WorkflowRuntimeConfig(initial_context="just some prose")

        assert config.initial_context == "just some prose"

    def test_prose_that_happens_to_be_json_stays_literal(self):
        """A brief whose text parses as a JSON object is still the author's
        prose, not a structure — decoding it would change the instruction."""
        config = WorkflowRuntimeConfig(initial_context='{"not": "a brief"}')

        assert config.initial_context == '{"not": "a brief"}'

    def test_prose_that_is_a_bare_json_number_stays_literal(self):
        config = WorkflowRuntimeConfig(initial_context="42")

        assert config.initial_context == "42"

    def test_empty_stays_empty(self):
        assert WorkflowRuntimeConfig().initial_context == ""
