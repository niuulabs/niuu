"""Tests for Ravn configuration."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from ravn.config import (
    TRUST_CATEGORY_TOOLS,
    AgentConfig,
    ChannelConfig,
    ContextConfig,
    HookConfig,
    HooksConfig,
    LLMAdapterConfig,
    LLMConfig,
    LLMProviderConfig,
    LoggingConfig,
    MCPServerConfig,
    MemoryConfig,
    PermissionConfig,
    PermissionRuleConfig,
    Settings,
    ThreadConfig,
    ToolAdapterConfig,
    ToolsConfig,
    TrustGradientConfig,
    resolve_trust_tools,
)


class TestAgentConfig:
    def test_defaults(self) -> None:
        c = AgentConfig()
        assert "claude" in c.model
        assert c.max_tokens > 0
        assert c.max_iterations > 0
        assert c.system_prompt != ""


class TestLLMAdapterConfig:
    def test_defaults(self) -> None:
        c = LLMAdapterConfig()
        assert "AnthropicAdapter" in c.adapter
        assert c.max_retries >= 0
        assert c.timeout > 0


# ---------------------------------------------------------------------------
# New NIU-427 config section tests
# ---------------------------------------------------------------------------


class TestLLMProviderConfig:
    def test_defaults(self) -> None:
        c = LLMProviderConfig()
        assert "AnthropicAdapter" in c.adapter
        assert isinstance(c.kwargs, dict)
        assert isinstance(c.secret_kwargs_env, dict)

    def test_custom_adapter(self) -> None:
        c = LLMProviderConfig(adapter="mypackage.MyAdapter", kwargs={"timeout": 30})
        assert c.adapter == "mypackage.MyAdapter"
        assert c.kwargs["timeout"] == 30


class TestLLMConfig:
    def test_defaults(self) -> None:
        c = LLMConfig()
        assert "claude" in c.model
        assert c.max_tokens > 0
        assert c.max_retries >= 0
        assert c.timeout > 0
        assert isinstance(c.provider, LLMProviderConfig)
        assert c.fallbacks == []

    def test_fallback_chain(self) -> None:
        c = LLMConfig(
            fallbacks=[
                LLMProviderConfig(adapter="pkg.FallbackA"),
                LLMProviderConfig(adapter="pkg.FallbackB"),
            ]
        )
        assert len(c.fallbacks) == 2
        assert c.fallbacks[0].adapter == "pkg.FallbackA"


class TestToolAdapterConfig:
    def test_required_adapter(self) -> None:
        c = ToolAdapterConfig(adapter="mypkg.MyTool")
        assert c.adapter == "mypkg.MyTool"
        assert c.kwargs == {}

    def test_kwargs_and_secrets(self) -> None:
        c = ToolAdapterConfig(
            adapter="mypkg.MyTool",
            kwargs={"base_url": "http://example.com"},
            secret_kwargs_env={"api_key": "MY_TOOL_KEY"},
        )
        assert c.kwargs["base_url"] == "http://example.com"
        assert c.secret_kwargs_env["api_key"] == "MY_TOOL_KEY"


class TestToolsConfig:
    def test_defaults(self) -> None:
        c = ToolsConfig()
        assert c.enabled == []
        assert c.disabled == []
        assert c.custom == []

    def test_with_values(self) -> None:
        c = ToolsConfig(
            enabled=["bash", "read"],
            disabled=["write"],
            custom=[ToolAdapterConfig(adapter="mypkg.ExtraTool")],
        )
        assert "bash" in c.enabled
        assert "write" in c.disabled
        assert len(c.custom) == 1


class TestMemoryConfig:
    def test_defaults(self) -> None:
        c = MemoryConfig()
        assert c.backend == "sqlite"
        assert "ravn" in c.path
        assert c.dsn == ""
        assert c.dsn_env == ""

    def test_postgres_backend(self) -> None:
        c = MemoryConfig(backend="postgres", dsn="postgresql://localhost/ravn")
        assert c.backend == "postgres"
        assert "postgresql" in c.dsn

    def test_dsn_from_env(self) -> None:
        c = MemoryConfig(backend="postgres", dsn_env="DATABASE_URL")
        assert c.dsn_env == "DATABASE_URL"


class TestPermissionConfig:
    def test_defaults(self) -> None:
        c = PermissionConfig()
        assert c.mode == "workspace_write"
        assert c.allow == []
        assert c.deny == []
        assert c.ask == []
        assert c.rules == []

    def test_with_rules(self) -> None:
        c = PermissionConfig(
            mode="prompt",
            allow=["read_file"],
            deny=["delete_file"],
            ask=["write_file"],
            rules=[PermissionRuleConfig(pattern="net:*", action="deny")],
        )
        assert c.mode == "prompt"
        assert "read_file" in c.allow
        assert "delete_file" in c.deny
        assert "write_file" in c.ask
        assert c.rules[0].pattern == "net:*"
        assert c.rules[0].action == "deny"


class TestContextConfig:
    def test_defaults(self) -> None:
        c = ContextConfig()
        assert c.per_file_limit == 4096
        assert c.total_budget == 12288

    def test_custom_values(self) -> None:
        c = ContextConfig(per_file_limit=1024, total_budget=8192)
        assert c.per_file_limit == 1024
        assert c.total_budget == 8192


class TestPermissionRuleConfig:
    def test_defaults(self) -> None:
        r = PermissionRuleConfig(pattern="tool:*")
        assert r.pattern == "tool:*"
        assert r.action == "ask"

    def test_deny(self) -> None:
        r = PermissionRuleConfig(pattern="fs:write", action="deny")
        assert r.action == "deny"


class TestMCPServerConfig:
    def test_defaults(self) -> None:
        c = MCPServerConfig(name="my-server")
        assert c.name == "my-server"
        assert c.transport == "stdio"
        assert c.command == ""
        assert c.args == []
        assert c.env == {}
        assert c.url == ""
        assert c.enabled is True

    def test_stdio_config(self) -> None:
        c = MCPServerConfig(
            name="filesystem",
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem"],
        )
        assert c.command == "npx"
        assert len(c.args) == 2

    def test_http_config(self) -> None:
        c = MCPServerConfig(name="remote", transport="http", url="https://mcp.example.com")
        assert c.transport == "http"
        assert c.url == "https://mcp.example.com"

    def test_disabled(self) -> None:
        c = MCPServerConfig(name="disabled-server", enabled=False)
        assert c.enabled is False


class TestHookConfig:
    def test_defaults(self) -> None:
        c = HookConfig(adapter="mypkg.AuditHook")
        assert c.adapter == "mypkg.AuditHook"
        assert "pre_tool" in c.events
        assert "post_tool" in c.events

    def test_custom_events(self) -> None:
        c = HookConfig(adapter="mypkg.PreHook", events=["pre_tool"])
        assert c.events == ["pre_tool"]


class TestHooksConfig:
    def test_defaults(self) -> None:
        c = HooksConfig()
        assert c.pre_tool == []
        assert c.post_tool == []

    def test_with_hooks(self) -> None:
        c = HooksConfig(
            pre_tool=[HookConfig(adapter="mypkg.AuthHook", events=["pre_tool"])],
            post_tool=[HookConfig(adapter="mypkg.LogHook", events=["post_tool"])],
        )
        assert len(c.pre_tool) == 1
        assert len(c.post_tool) == 1


class TestChannelConfig:
    def test_defaults(self) -> None:
        c = ChannelConfig()
        assert "CliChannel" in c.adapter

    def test_custom_adapter(self) -> None:
        c = ChannelConfig(
            adapter="mypkg.SlackChannel",
            kwargs={"webhook": "https://hooks.slack.com/xxx"},
        )
        assert "Slack" in c.adapter
        assert "webhook" in c.kwargs


class TestLoggingConfig:
    def test_defaults(self) -> None:
        c = LoggingConfig()
        assert c.level in ("debug", "info", "warning", "error", "critical")


class TestSettings:
    def test_defaults(self) -> None:
        s = Settings()
        assert isinstance(s.agent, AgentConfig)
        assert isinstance(s.llm_adapter, LLMAdapterConfig)
        assert isinstance(s.permission, PermissionConfig)
        assert isinstance(s.logging, LoggingConfig)
        # NIU-427 new sections
        assert isinstance(s.context, ContextConfig)
        assert isinstance(s.llm, LLMConfig)
        assert isinstance(s.tools, ToolsConfig)
        assert isinstance(s.memory, MemoryConfig)
        assert isinstance(s.hooks, HooksConfig)
        assert s.mcp_servers == []
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            assert s.channels == []

    def test_env_override(self) -> None:
        with patch.dict(os.environ, {"RAVN_AGENT__MODEL": "claude-haiku-4-5-20251001"}):
            s = Settings()
            assert s.agent.model == "claude-haiku-4-5-20251001"

    def test_env_override_llm_model(self) -> None:
        with patch.dict(os.environ, {"RAVN_LLM__MODEL": "claude-opus-4-6"}):
            s = Settings()
            assert s.llm.model == "claude-opus-4-6"

    def test_env_override_memory_backend(self) -> None:
        with patch.dict(os.environ, {"RAVN_MEMORY__BACKEND": "postgres"}):
            s = Settings()
            assert s.memory.backend == "postgres"

    def test_env_override_permission_mode(self) -> None:
        with patch.dict(os.environ, {"RAVN_PERMISSION__MODE": "deny_all"}):
            s = Settings()
            assert s.permission.mode == "deny_all"

    def test_config_path_resolved_at_instantiation(self, tmp_path) -> None:
        """RAVN_CONFIG set before Settings() is constructed is picked up."""
        cfg = tmp_path / "custom.yaml"
        cfg.write_text("agent:\n  model: claude-custom\n")
        with patch.dict(os.environ, {"RAVN_CONFIG": str(cfg)}):
            s = Settings()
            assert s.agent.model == "claude-custom"

    def test_yaml_llm_section(self, tmp_path) -> None:
        cfg = tmp_path / "ravn.yaml"
        cfg.write_text(
            "llm:\n"
            "  model: claude-test\n"
            "  max_tokens: 4096\n"
            "  provider:\n"
            "    adapter: mypkg.TestAdapter\n"
        )
        with patch.dict(os.environ, {"RAVN_CONFIG": str(cfg)}):
            s = Settings()
            assert s.llm.model == "claude-test"
            assert s.llm.max_tokens == 4096
            assert s.llm.provider.adapter == "mypkg.TestAdapter"

    def test_yaml_mcp_servers(self, tmp_path) -> None:
        cfg = tmp_path / "ravn.yaml"
        cfg.write_text(
            "mcp_servers:\n"
            "  - name: filesystem\n"
            "    transport: stdio\n"
            "    command: npx\n"
            "    args:\n"
            "      - -y\n"
            "      - '@modelcontextprotocol/server-filesystem'\n"
        )
        with patch.dict(os.environ, {"RAVN_CONFIG": str(cfg)}):
            s = Settings()
            assert len(s.mcp_servers) == 1
            assert s.mcp_servers[0].name == "filesystem"
            assert s.mcp_servers[0].command == "npx"

    def test_yaml_hooks_section(self, tmp_path) -> None:
        cfg = tmp_path / "ravn.yaml"
        cfg.write_text(
            "hooks:\n  pre_tool:\n    - adapter: mypkg.AuditHook\n      events: [pre_tool]\n"
        )
        with patch.dict(os.environ, {"RAVN_CONFIG": str(cfg)}):
            s = Settings()
            assert len(s.hooks.pre_tool) == 1
            assert s.hooks.pre_tool[0].adapter == "mypkg.AuditHook"

    def test_yaml_tools_section(self, tmp_path) -> None:
        cfg = tmp_path / "ravn.yaml"
        cfg.write_text("tools:\n  enabled:\n    - bash\n    - read\n  disabled:\n    - write\n")
        with patch.dict(os.environ, {"RAVN_CONFIG": str(cfg)}):
            s = Settings()
            assert "bash" in s.tools.enabled
            assert "write" in s.tools.disabled

    def test_yaml_channels_section(self, tmp_path) -> None:
        import warnings

        cfg = tmp_path / "ravn.yaml"
        cfg.write_text(
            "channels:\n"
            "  - adapter: ravn.adapters.cli_channel.CliChannel\n"
            "    kwargs:\n"
            "      result_truncation_limit: 200\n"
        )
        with patch.dict(os.environ, {"RAVN_CONFIG": str(cfg)}):
            s = Settings()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                assert len(s.channels) == 1
                assert s.channels[0].kwargs["result_truncation_limit"] == 200

    def test_yaml_context_section(self, tmp_path) -> None:
        cfg = tmp_path / "ravn.yaml"
        cfg.write_text("context:\n  per_file_limit: 2048\n  total_budget: 6144\n")
        with patch.dict(os.environ, {"RAVN_CONFIG": str(cfg)}):
            s = Settings()
            assert s.context.per_file_limit == 2048
            assert s.context.total_budget == 6144


# ---------------------------------------------------------------------------
# NIU-558: ThreadConfig
# ---------------------------------------------------------------------------


class TestThreadConfig:
    def test_defaults(self) -> None:
        c = ThreadConfig()
        assert c.enabled is False
        assert c.max_queue_size == 200
        assert c.enricher_poll_interval_seconds == 300
        assert c.enricher_llm_alias == "fast"
        assert c.decay_rate_per_day == 0.05
        assert c.recency_weight == 1.0
        assert c.mention_weight == 0.3
        assert c.engagement_weight == 0.5
        assert c.peer_weight == 0.2
        assert c.sub_thread_weight == 0.4
        assert c.owner_id is None

    def test_enabled_override(self) -> None:
        c = ThreadConfig(enabled=True)
        assert c.enabled is True

    def test_custom_weights(self) -> None:
        c = ThreadConfig(recency_weight=2.0, mention_weight=0.8)
        assert c.recency_weight == 2.0
        assert c.mention_weight == 0.8

    def test_owner_id_set(self) -> None:
        c = ThreadConfig(owner_id="ravn-instance-42")
        assert c.owner_id == "ravn-instance-42"


class TestSettingsThread:
    def test_settings_thread_defaults(self) -> None:
        s = Settings()
        assert s.thread.enabled is False
        assert s.thread.max_queue_size == 200
        assert s.thread.owner_id is None

    def test_yaml_thread_section(self, tmp_path) -> None:
        cfg = tmp_path / "ravn.yaml"
        cfg.write_text(
            "thread:\n"
            "  enabled: true\n"
            "  max_queue_size: 500\n"
            "  enricher_poll_interval_seconds: 60\n"
            "  enricher_llm_alias: turbo\n"
            "  decay_rate_per_day: 0.1\n"
            "  recency_weight: 2.0\n"
            "  mention_weight: 0.6\n"
            "  engagement_weight: 0.9\n"
            "  peer_weight: 0.4\n"
            "  sub_thread_weight: 0.7\n"
            "  owner_id: ravn-prod-1\n"
        )
        with patch.dict(os.environ, {"RAVN_CONFIG": str(cfg)}):
            s = Settings()
            assert s.thread.enabled is True
            assert s.thread.max_queue_size == 500
            assert s.thread.enricher_poll_interval_seconds == 60
            assert s.thread.enricher_llm_alias == "turbo"
            assert s.thread.decay_rate_per_day == 0.1
            assert s.thread.recency_weight == 2.0
            assert s.thread.mention_weight == 0.6
            assert s.thread.engagement_weight == 0.9
            assert s.thread.peer_weight == 0.4
            assert s.thread.sub_thread_weight == 0.7
            assert s.thread.owner_id == "ravn-prod-1"

    def test_env_var_thread_enabled(self) -> None:
        with patch.dict(os.environ, {"RAVN_THREAD__ENABLED": "true"}):
            s = Settings()
            assert s.thread.enabled is True

    def test_env_var_thread_decay_rate(self) -> None:
        with patch.dict(os.environ, {"RAVN_THREAD__DECAY_RATE_PER_DAY": "0.2"}):
            s = Settings()
            assert s.thread.decay_rate_per_day == 0.2

    def test_env_var_thread_owner_id(self) -> None:
        with patch.dict(os.environ, {"RAVN_THREAD__OWNER_ID": "instance-xyz"}):
            s = Settings()
            assert s.thread.owner_id == "instance-xyz"


# ---------------------------------------------------------------------------
# NIU-571: Trust gradient config
# ---------------------------------------------------------------------------


class TestTrustGradientConfig:
    def test_defaults_match_vision_doc(self) -> None:
        """Default config matches vaka-vision.md §5 table."""
        c = TrustGradientConfig()
        assert c.reading == "free"
        assert c.writing_notes == "free"
        assert c.sandbox_experiments == "free"
        assert c.consulting_peers == "free"
        assert c.drafting_tickets == "free"
        assert c.producing_recaps == "free"
        assert c.opening_tickets == "approval"
        assert c.closing_tickets == "approval"
        assert c.pushing_branches == "approval"
        assert c.pushing_main == "never"
        assert c.external_messages == "approval"
        assert c.spending_beyond_cap == "approval"

    def test_custom_override(self) -> None:
        c = TrustGradientConfig(reading="approval", pushing_main="approval")
        assert c.reading == "approval"
        assert c.pushing_main == "approval"

    def test_all_free(self) -> None:
        c = TrustGradientConfig(
            opening_tickets="free",
            closing_tickets="free",
            pushing_branches="free",
            pushing_main="free",
            external_messages="free",
            spending_beyond_cap="free",
        )
        assert c.opening_tickets == "free"
        assert c.pushing_main == "free"

    def test_all_never(self) -> None:
        c = TrustGradientConfig(
            reading="never",
            writing_notes="never",
            sandbox_experiments="never",
            consulting_peers="never",
            drafting_tickets="never",
            producing_recaps="never",
            opening_tickets="never",
            closing_tickets="never",
            pushing_branches="never",
            pushing_main="never",
            external_messages="never",
            spending_beyond_cap="never",
        )
        for field_name in TrustGradientConfig.model_fields:
            assert getattr(c, field_name) == "never"

    def test_invalid_level_rejected(self) -> None:
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            TrustGradientConfig(reading="maybe")  # type: ignore[arg-type]


class TestResolveTrustTools:
    def test_free_tools_in_allowed(self) -> None:
        c = TrustGradientConfig()
        allowed, forbidden = resolve_trust_tools(c)
        # reading is free, so its tools should be in allowed
        for tool in TRUST_CATEGORY_TOOLS["reading"]:
            assert tool in allowed

    def test_approval_tools_in_forbidden(self) -> None:
        c = TrustGradientConfig()
        allowed, forbidden = resolve_trust_tools(c)
        # opening_tickets is approval, so its tools should be in forbidden
        for tool in TRUST_CATEGORY_TOOLS["opening_tickets"]:
            assert tool in forbidden

    def test_never_tools_in_forbidden(self) -> None:
        c = TrustGradientConfig()
        allowed, forbidden = resolve_trust_tools(c)
        # pushing_main is never, so its tools should be in forbidden
        for tool in TRUST_CATEGORY_TOOLS["pushing_main"]:
            assert tool in forbidden

    def test_free_tools_not_in_forbidden(self) -> None:
        c = TrustGradientConfig()
        allowed, forbidden = resolve_trust_tools(c)
        for tool in TRUST_CATEGORY_TOOLS["reading"]:
            assert tool not in forbidden

    def test_approval_tools_not_in_allowed(self) -> None:
        c = TrustGradientConfig()
        allowed, forbidden = resolve_trust_tools(c)
        for tool in TRUST_CATEGORY_TOOLS["opening_tickets"]:
            assert tool not in allowed

    def test_merge_with_persona_constraints_intersection(self) -> None:
        """Tool resolution merges with persona constraints (intersection)."""
        c = TrustGradientConfig()
        # Persona allows only "file_read" and "mimir_query" — both trust-free
        persona_allowed = ["file_read", "mimir_query"]
        allowed, forbidden = resolve_trust_tools(
            c,
            persona_allowed=persona_allowed,
        )
        # Only persona-allowed tools that are also trust-free should appear
        assert "file_read" in allowed
        assert "mimir_query" in allowed

    def test_merge_persona_forbidden_union(self) -> None:
        """Persona forbidden list is unioned with trust forbidden list."""
        c = TrustGradientConfig()
        persona_forbidden = ["custom_tool"]
        allowed, forbidden = resolve_trust_tools(
            c,
            persona_forbidden=persona_forbidden,
        )
        # Both trust-forbidden and persona-forbidden should appear
        assert "custom_tool" in forbidden
        for tool in TRUST_CATEGORY_TOOLS["opening_tickets"]:
            assert tool in forbidden

    def test_persona_allowed_tool_not_in_trust_categories_passes_through(self) -> None:
        """Persona-allowed tools not governed by trust gradient pass through."""
        c = TrustGradientConfig()
        persona_allowed = ["file_read", "custom_untracked_tool"]
        allowed, _ = resolve_trust_tools(c, persona_allowed=persona_allowed)
        # custom_untracked_tool is not in any trust category, so it passes through
        assert "custom_untracked_tool" in allowed

    def test_all_categories_have_tool_mappings(self) -> None:
        """Every trust category has a corresponding entry in TRUST_CATEGORY_TOOLS."""
        for field_name in TrustGradientConfig.model_fields:
            assert field_name in TRUST_CATEGORY_TOOLS, (
                f"Category {field_name!r} missing from TRUST_CATEGORY_TOOLS"
            )

    def test_no_persona_constraints(self) -> None:
        """Without persona constraints, returns raw trust lists."""
        c = TrustGradientConfig()
        allowed, forbidden = resolve_trust_tools(c)
        # All free-category tools in allowed
        free_tools = []
        for cat in [
            "reading",
            "writing_notes",
            "sandbox_experiments",
            "consulting_peers",
            "drafting_tickets",
            "producing_recaps",
        ]:
            free_tools.extend(TRUST_CATEGORY_TOOLS[cat])
        for tool in free_tools:
            assert tool in allowed

    def test_custom_trust_config(self) -> None:
        """Changing a category from free to never moves tools to forbidden."""
        c = TrustGradientConfig(reading="never")
        allowed, forbidden = resolve_trust_tools(c)
        for tool in TRUST_CATEGORY_TOOLS["reading"]:
            assert tool in forbidden
            assert tool not in allowed


class TestSettingsTrust:
    def test_settings_trust_defaults(self) -> None:
        s = Settings()
        assert s.trust.reading == "free"
        assert s.trust.pushing_main == "never"
        assert s.trust.opening_tickets == "approval"

    def test_yaml_trust_section(self, tmp_path) -> None:
        cfg = tmp_path / "ravn.yaml"
        cfg.write_text(
            "trust:\n  reading: approval\n  pushing_main: approval\n  opening_tickets: free\n"
        )
        with patch.dict(os.environ, {"RAVN_CONFIG": str(cfg)}):
            s = Settings()
            assert s.trust.reading == "approval"
            assert s.trust.pushing_main == "approval"
            assert s.trust.opening_tickets == "free"

    def test_env_var_trust_override(self) -> None:
        with patch.dict(os.environ, {"RAVN_TRUST__READING": "never"}):
            s = Settings()
            assert s.trust.reading == "never"

    def test_env_var_trust_pushing_main(self) -> None:
        with patch.dict(os.environ, {"RAVN_TRUST__PUSHING_MAIN": "approval"}):
            s = Settings()
            assert s.trust.pushing_main == "approval"


class TestInGroups:
    """Tests for the module-level _in_groups helper in commands.py."""

    def test_exact_match(self) -> None:
        from ravn.cli.commands import _in_groups

        assert _in_groups("mimir", {"mimir"}) is True

    def test_prefix_match(self) -> None:
        from ravn.cli.commands import _in_groups

        assert _in_groups("mimir_query", {"mimir"}) is True

    def test_no_match(self) -> None:
        from ravn.cli.commands import _in_groups

        assert _in_groups("git_push", {"mimir"}) is False

    def test_partial_no_match(self) -> None:
        from ravn.cli.commands import _in_groups

        # "mimir" should NOT match "mimirx" (no underscore separator)
        assert _in_groups("mimirx", {"mimir"}) is False

    def test_empty_groups(self) -> None:
        from ravn.cli.commands import _in_groups

        assert _in_groups("mimir", set()) is False

    def test_multiple_groups(self) -> None:
        from ravn.cli.commands import _in_groups

        assert _in_groups("slack_post", {"mimir", "slack"}) is True


class TestApplyTrustFilter:
    """Tests for the _apply_trust_filter helper in commands.py.

    This is the extracted function that the _agent_factory calls for
    thread-triggered tasks.
    """

    def _fake_tool(self, name: str):
        class FakeTool:
            def __init__(self, n: str):
                self.name = n

        return FakeTool(name)

    def test_thread_triggered_filters_forbidden_tools(self) -> None:
        """Thread-triggered tasks have forbidden tools removed."""
        from ravn.cli.commands import _apply_trust_filter

        s = Settings()
        tools = [
            self._fake_tool("file_read"),
            self._fake_tool("git_push_main"),
            self._fake_tool("mimir_query"),
            self._fake_tool("linear_create"),
        ]
        filtered = _apply_trust_filter(tools, s, "thread:test")
        names = [t.name for t in filtered]
        assert "file_read" in names
        assert "mimir_query" in names
        # pushing_main defaults to "never" → forbidden
        assert "git_push_main" not in names
        # opening_tickets defaults to "approval" → forbidden
        assert "linear_create" not in names

    def test_non_thread_trigger_skips_filtering(self) -> None:
        """Non-thread triggers bypass trust gradient filtering."""
        from ravn.cli.commands import _apply_trust_filter

        s = Settings()
        tools = [self._fake_tool("git_push_main"), self._fake_tool("linear_create")]
        filtered = _apply_trust_filter(tools, s, "cron:daily")
        assert len(filtered) == len(tools)

    def test_none_triggered_by_skips_filtering(self) -> None:
        """None triggered_by bypasses trust gradient filtering."""
        from ravn.cli.commands import _apply_trust_filter

        s = Settings()
        tools = [self._fake_tool("git_push_main")]
        filtered = _apply_trust_filter(tools, s, None)
        assert len(filtered) == len(tools)

    def test_all_free_no_filtering(self) -> None:
        """When all categories are free, nothing is filtered."""
        from ravn.cli.commands import _apply_trust_filter

        s = Settings()
        s.trust = TrustGradientConfig(
            **{field: "free" for field in TrustGradientConfig.model_fields},
        )
        tools = [self._fake_tool("git_push_main"), self._fake_tool("linear_create")]
        filtered = _apply_trust_filter(tools, s, "thread:test")
        assert len(filtered) == len(tools)

    def test_prefix_matching(self) -> None:
        """Tool names are matched by prefix (e.g. 'mimir' matches 'mimir_write')."""
        from ravn.cli.commands import _apply_trust_filter

        s = Settings()
        s.trust = TrustGradientConfig(writing_notes="never")
        tools = [
            self._fake_tool("mimir_write"),
            self._fake_tool("mimir_ingest"),
            self._fake_tool("mimir_query"),
        ]
        filtered = _apply_trust_filter(tools, s, "thread:test")
        names = [t.name for t in filtered]
        assert "mimir_write" not in names
        assert "mimir_ingest" not in names
        # mimir_query is under "reading" which defaults to "free"
        assert "mimir_query" in names

    def test_empty_tools_returns_empty(self) -> None:
        """Empty tool list returns empty."""
        from ravn.cli.commands import _apply_trust_filter

        s = Settings()
        assert _apply_trust_filter([], s, "thread:test") == []
