"""Tests for configuration module."""

from volundr.config import (
    DatabaseConfig,
    EventPipelineConfig,
    FeatureModuleConfig,
    GitConfig,
    GitHubConfig,
    GitHubInstance,
    GitLabConfig,
    GitLabInstance,
    IntegrationType,
    OtelConfig,
    RabbitMQConfig,
    SecretType,
    SeededIntegrationConnectionConfig,
    Settings,
    _default_feature_modules,
)

_VOLUNDR_SETTINGS_ENV_VARS = (
    "NIUU_CONFIG",
    "DATABASE__HOST",
    "DATABASE__PORT",
    "POD_MANAGER__ADAPTER",
    "POD_MANAGER__KWARGS__WORKSPACES_DIR",
    "POD_MANAGER__KWARGS__CLAUDE_BINARY",
    "POD_MANAGER__KWARGS__MAX_CONCURRENT",
    "POD_MANAGER__KWARGS__SDK_PORT_START",
)


def _clear_settings_env(monkeypatch) -> None:
    """Remove env vars that can leak between config tests in the full suite."""
    for name in _VOLUNDR_SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


class TestDatabaseConfig:
    """Tests for DatabaseConfig."""

    def test_defaults(self):
        """Test default values."""
        config = DatabaseConfig()

        assert config.host == "localhost"
        assert config.port == 5432
        assert config.user == "volundr"
        assert config.password == "volundr"
        assert config.database == "volundr"
        assert config.min_pool_size == 5
        assert config.max_pool_size == 20

    def test_dsn_property(self):
        """Test DSN connection string generation."""
        config = DatabaseConfig(
            host="db.example.com",
            port=5433,
            user="myuser",
            password="mypass",
            name="mydb",
        )

        assert config.dsn == "postgresql://myuser:mypass@db.example.com:5433/mydb"

    def test_custom_values(self):
        """Test custom configuration values."""
        config = DatabaseConfig(
            host="postgres.local",
            port=15432,
            min_pool_size=10,
            max_pool_size=50,
        )

        assert config.host == "postgres.local"
        assert config.port == 15432
        assert config.min_pool_size == 10
        assert config.max_pool_size == 50


class TestSettings:
    """Tests for Settings."""

    def test_defaults(self):
        """Test that Settings creates default nested configs."""
        settings = Settings()

        assert isinstance(settings.database, DatabaseConfig)

    def test_nested_configuration(self):
        """Test nested configuration."""
        settings = Settings(
            database=DatabaseConfig(host="db.local"),
        )

        assert settings.database.host == "db.local"

    def test_git_config_included(self):
        """Test that Settings includes GitConfig."""
        settings = Settings()

        assert isinstance(settings.git, GitConfig)
        assert isinstance(settings.git.github, GitHubConfig)
        assert isinstance(settings.git.gitlab, GitLabConfig)


class TestGitHubConfig:
    """Tests for GitHubConfig."""

    def test_defaults(self):
        """Test default values."""
        config = GitHubConfig()

        assert config.enabled is False
        assert config.token is None
        assert config.base_url == "https://api.github.com"
        assert config.instances == []

    def test_custom_values(self):
        """Test custom configuration values."""
        config = GitHubConfig(
            token="ghp_xxxx",
            base_url="https://api.github.example.com",
        )

        assert config.token == "ghp_xxxx"
        assert config.base_url == "https://api.github.example.com"

    def test_get_instances_empty(self):
        """get_instances returns empty list when no token or instances."""
        config = GitHubConfig()

        instances = config.get_instances()

        assert instances == []

    def test_get_instances_default_only(self):
        """get_instances returns default instance when token is set."""
        config = GitHubConfig(token="ghp_xxxx", base_url="https://api.github.com")

        instances = config.get_instances()

        assert len(instances) == 1
        assert instances[0] == GitHubInstance("GitHub", "https://api.github.com", "ghp_xxxx")

    def test_get_instances_enabled_without_token(self):
        """get_instances returns default instance when enabled, even without token."""
        config = GitHubConfig(enabled=True)

        instances = config.get_instances()

        assert len(instances) == 1
        assert instances[0] == GitHubInstance("GitHub", "https://api.github.com", None)

    def test_get_instances_from_list(self):
        """get_instances parses list of instance dicts."""
        instances_list = [
            {"name": "GitHub", "base_url": "https://api.github.com", "token": "token1"},
            {
                "name": "Enterprise",
                "base_url": "https://github.company.com/api/v3",
                "token": "token2",
            },
        ]
        config = GitHubConfig(instances=instances_list)

        instances = config.get_instances()

        assert len(instances) == 2
        assert instances[0] == GitHubInstance("GitHub", "https://api.github.com", "token1")
        assert instances[1] == GitHubInstance(
            "Enterprise", "https://github.company.com/api/v3", "token2"
        )

    def test_get_instances_with_orgs(self):
        """get_instances parses orgs from instance dicts."""
        config = GitHubConfig(
            instances=[
                {
                    "name": "GitHub",
                    "base_url": "https://api.github.com",
                    "token": "token1",
                    "orgs": ["my-org", "other-org"],
                },
            ]
        )

        instances = config.get_instances()

        assert len(instances) == 1
        assert instances[0].orgs == ("my-org", "other-org")

    def test_get_instances_yaml_overrides_env_token(self):
        """Instance-level token takes precedence over top-level token."""
        config = GitHubConfig(
            token="env-token",
            base_url="https://api.github.com",
            instances=[
                {"name": "GitHub", "base_url": "https://api.github.com", "token": "yaml-token"}
            ],
        )

        instances = config.get_instances()

        assert len(instances) == 1
        assert instances[0].name == "GitHub"
        assert instances[0].token == "yaml-token"

    def test_get_instances_inherits_top_level_token(self):
        """Instance without token inherits top-level token."""
        config = GitHubConfig(
            token="env-token",
            instances=[
                {
                    "name": "GitHub",
                    "base_url": "https://api.github.com",
                    "orgs": ["my-org"],
                }
            ],
        )

        instances = config.get_instances()

        assert len(instances) == 1
        assert instances[0].token == "env-token"
        assert instances[0].orgs == ("my-org",)

    def test_get_instances_token_env(self, monkeypatch):
        """Instance reads token from env var specified by token_env."""
        monkeypatch.setenv("GIT_GITHUB_INSTANCE_0_TOKEN", "secret-from-env")
        config = GitHubConfig(
            instances=[
                {
                    "name": "GitHub",
                    "base_url": "https://api.github.com",
                    "token_env": "GIT_GITHUB_INSTANCE_0_TOKEN",
                    "orgs": ["my-org"],
                }
            ],
        )

        instances = config.get_instances()

        assert len(instances) == 1
        assert instances[0].token == "secret-from-env"

    def test_get_instances_token_env_overrides_top_level(self, monkeypatch):
        """token_env takes precedence over top-level token."""
        monkeypatch.setenv("GIT_GITHUB_INSTANCE_0_TOKEN", "instance-secret")
        config = GitHubConfig(
            token="top-level-token",
            instances=[
                {
                    "name": "GitHub",
                    "base_url": "https://api.github.com",
                    "token_env": "GIT_GITHUB_INSTANCE_0_TOKEN",
                }
            ],
        )

        instances = config.get_instances()

        assert len(instances) == 1
        assert instances[0].token == "instance-secret"

    def test_get_instances_explicit_token_overrides_token_env(self, monkeypatch):
        """Explicit token field takes precedence over token_env."""
        monkeypatch.setenv("GIT_GITHUB_INSTANCE_0_TOKEN", "env-secret")
        config = GitHubConfig(
            instances=[
                {
                    "name": "GitHub",
                    "base_url": "https://api.github.com",
                    "token": "explicit-token",
                    "token_env": "GIT_GITHUB_INSTANCE_0_TOKEN",
                }
            ],
        )

        instances = config.get_instances()

        assert len(instances) == 1
        assert instances[0].token == "explicit-token"

    def test_get_instances_token_env_missing_falls_back(self):
        """Falls back to top-level token when token_env var is not set."""
        config = GitHubConfig(
            token="fallback-token",
            instances=[
                {
                    "name": "GitHub",
                    "base_url": "https://api.github.com",
                    "token_env": "NONEXISTENT_ENV_VAR",
                }
            ],
        )

        instances = config.get_instances()

        assert len(instances) == 1
        assert instances[0].token == "fallback-token"


class TestGitHubInstance:
    """Tests for GitHubInstance dataclass."""

    def test_create_instance(self):
        """GitHubInstance can be created with all fields."""
        instance = GitHubInstance(
            name="GitHub Enterprise",
            base_url="https://github.company.com/api/v3",
            token="ghp-xxx",
        )

        assert instance.name == "GitHub Enterprise"
        assert instance.base_url == "https://github.company.com/api/v3"
        assert instance.token == "ghp-xxx"

    def test_instance_without_token(self):
        """GitHubInstance can be created without token."""
        instance = GitHubInstance(
            name="GitHub",
            base_url="https://api.github.com",
        )

        assert instance.name == "GitHub"
        assert instance.base_url == "https://api.github.com"
        assert instance.token is None

    def test_instance_is_frozen(self):
        """GitHubInstance should be immutable."""
        instance = GitHubInstance(name="Test", base_url="https://test.com")

        try:
            instance.name = "Changed"
            assert False, "Should have raised an error"
        except AttributeError:
            pass  # Expected: frozen dataclass should reject mutation

    def test_instance_orgs_default_empty(self):
        """GitHubInstance orgs defaults to empty tuple."""
        instance = GitHubInstance(name="GitHub", base_url="https://api.github.com")

        assert instance.orgs == ()

    def test_instance_with_orgs(self):
        """GitHubInstance accepts orgs tuple."""
        instance = GitHubInstance(
            name="GitHub",
            base_url="https://api.github.com",
            token="token",
            orgs=("org1", "org2"),
        )

        assert instance.orgs == ("org1", "org2")


class TestGitLabInstance:
    """Tests for GitLabInstance dataclass."""

    def test_create_instance(self):
        """GitLabInstance can be created with all fields."""
        instance = GitLabInstance(
            name="Internal",
            base_url="https://git.company.com",
            token="glpat-xxx",
        )

        assert instance.name == "Internal"
        assert instance.base_url == "https://git.company.com"
        assert instance.token == "glpat-xxx"

    def test_instance_without_token(self):
        """GitLabInstance can be created without token."""
        instance = GitLabInstance(
            name="Public",
            base_url="https://gitlab.example.com",
        )

        assert instance.name == "Public"
        assert instance.base_url == "https://gitlab.example.com"
        assert instance.token is None

    def test_instance_is_frozen(self):
        """GitLabInstance should be immutable."""
        instance = GitLabInstance(name="Test", base_url="https://test.com")

        # Frozen dataclass raises FrozenInstanceError (subclass of AttributeError)
        try:
            instance.name = "Changed"
            assert False, "Should have raised an error"
        except AttributeError:
            pass  # Expected: frozen dataclass should reject mutation

    def test_instance_orgs_default_empty(self):
        """GitLabInstance orgs defaults to empty tuple."""
        instance = GitLabInstance(name="GitLab", base_url="https://gitlab.com")

        assert instance.orgs == ()

    def test_instance_with_orgs(self):
        """GitLabInstance accepts orgs tuple."""
        instance = GitLabInstance(
            name="GitLab",
            base_url="https://gitlab.com",
            token="token",
            orgs=("group1", "group2"),
        )

        assert instance.orgs == ("group1", "group2")


class TestGitLabConfig:
    """Tests for GitLabConfig."""

    def test_defaults(self):
        """Test default values."""
        config = GitLabConfig()

        assert config.enabled is False
        assert config.token is None
        assert config.base_url == "https://gitlab.com"
        assert config.instances == []

    def test_custom_values(self):
        """Test custom configuration values."""
        config = GitLabConfig(
            token="glpat-xxxx",
            base_url="https://gitlab.example.com",
        )

        assert config.token == "glpat-xxxx"
        assert config.base_url == "https://gitlab.example.com"

    def test_get_instances_empty(self):
        """get_instances returns empty list when no token or instances."""
        config = GitLabConfig()

        instances = config.get_instances()

        assert instances == []

    def test_get_instances_default_only(self):
        """get_instances returns default instance when token is set."""
        config = GitLabConfig(token="glpat-xxxx", base_url="https://gitlab.com")

        instances = config.get_instances()

        assert len(instances) == 1
        assert instances[0] == GitLabInstance("GitLab", "https://gitlab.com", "glpat-xxxx")

    def test_get_instances_enabled_without_token(self):
        """get_instances returns default instance when enabled, even without token."""
        config = GitLabConfig(enabled=True)

        instances = config.get_instances()

        assert len(instances) == 1
        assert instances[0] == GitLabInstance("GitLab", "https://gitlab.com", None)

    def test_get_instances_from_list(self):
        """get_instances parses list of instance dicts."""
        instances_list = [
            {"name": "Internal", "base_url": "https://git.company.com", "token": "token1"},
            {"name": "Other", "base_url": "https://git.other.com", "token": "token2"},
        ]
        config = GitLabConfig(instances=instances_list)

        instances = config.get_instances()

        assert len(instances) == 2
        assert instances[0] == GitLabInstance("Internal", "https://git.company.com", "token1")
        assert instances[1] == GitLabInstance("Other", "https://git.other.com", "token2")

    def test_get_instances_without_token(self):
        """get_instances parses instances without token."""
        config = GitLabConfig(
            instances=[{"name": "Public", "base_url": "https://gitlab.example.com"}]
        )

        instances = config.get_instances()

        assert len(instances) == 1
        assert instances[0] == GitLabInstance("Public", "https://gitlab.example.com", None)

    def test_get_instances_with_orgs(self):
        """get_instances parses orgs from instance dicts."""
        config = GitLabConfig(
            instances=[
                {
                    "name": "Internal",
                    "base_url": "https://git.company.com",
                    "token": "token1",
                    "orgs": ["platform", "infra"],
                },
            ]
        )

        instances = config.get_instances()

        assert len(instances) == 1
        assert instances[0].orgs == ("platform", "infra")

    def test_get_instances_yaml_overrides_env_token(self):
        """Instance-level token takes precedence over top-level token."""
        config = GitLabConfig(
            token="env-token",
            base_url="https://gitlab.com",
            instances=[
                {"name": "Internal", "base_url": "https://git.company.com", "token": "yaml-token"}
            ],
        )

        instances = config.get_instances()

        assert len(instances) == 1
        assert instances[0].name == "Internal"
        assert instances[0].token == "yaml-token"

    def test_get_instances_inherits_top_level_token(self):
        """Instance without token inherits top-level token."""
        config = GitLabConfig(
            token="env-token",
            instances=[
                {
                    "name": "Internal",
                    "base_url": "https://git.company.com",
                    "orgs": ["platform"],
                }
            ],
        )

        instances = config.get_instances()

        assert len(instances) == 1
        assert instances[0].token == "env-token"
        assert instances[0].orgs == ("platform",)

    def test_get_instances_token_env(self, monkeypatch):
        """Instance reads token from env var specified by token_env."""
        monkeypatch.setenv("GIT_GITLAB_INSTANCE_0_TOKEN", "secret-from-env")
        config = GitLabConfig(
            instances=[
                {
                    "name": "Internal",
                    "base_url": "https://git.company.com",
                    "token_env": "GIT_GITLAB_INSTANCE_0_TOKEN",
                    "orgs": ["platform"],
                }
            ],
        )

        instances = config.get_instances()

        assert len(instances) == 1
        assert instances[0].token == "secret-from-env"

    def test_get_instances_token_env_overrides_top_level(self, monkeypatch):
        """token_env takes precedence over top-level token."""
        monkeypatch.setenv("GIT_GITLAB_INSTANCE_0_TOKEN", "instance-secret")
        config = GitLabConfig(
            token="top-level-token",
            instances=[
                {
                    "name": "Internal",
                    "base_url": "https://git.company.com",
                    "token_env": "GIT_GITLAB_INSTANCE_0_TOKEN",
                }
            ],
        )

        instances = config.get_instances()

        assert len(instances) == 1
        assert instances[0].token == "instance-secret"

    def test_get_instances_skips_invalid_entries(self):
        """get_instances skips entries missing required fields."""
        config = GitLabConfig(
            instances=[
                {"name": "Valid", "base_url": "https://valid.com"},
                {"name": "Missing URL"},
                {"base_url": "https://missing-name.com"},
            ]
        )

        instances = config.get_instances()

        assert len(instances) == 1
        assert instances[0].name == "Valid"


class TestGitConfig:
    """Tests for GitConfig."""

    def test_defaults(self):
        """Test default values."""
        config = GitConfig()

        assert isinstance(config.github, GitHubConfig)
        assert isinstance(config.gitlab, GitLabConfig)
        assert config.validate_on_create is True

    def test_custom_values(self):
        """Test custom configuration values."""
        config = GitConfig(
            github=GitHubConfig(token="gh-token"),
            gitlab=GitLabConfig(token="gl-token"),
            validate_on_create=False,
        )

        assert config.github.token == "gh-token"
        assert config.gitlab.token == "gl-token"
        assert config.validate_on_create is False


class TestSettingsYamlLoading:
    """Tests for Settings YAML loading via pydantic-settings."""

    def test_settings_loads_from_yaml(self, tmp_path, monkeypatch):
        """Settings loads configuration from YAML file."""
        yaml_content = """
database:
  host: yaml-db-host
  port: 5433

pod_manager:
  adapter: "volundr.adapters.outbound.flux.FluxPodManager"
  kwargs:
    namespace: "volundr-test"
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml_content)

        # Change working directory so ./config.yaml resolves to our temp file
        monkeypatch.chdir(tmp_path)
        _clear_settings_env(monkeypatch)

        settings = Settings()

        assert settings.database.host == "yaml-db-host"
        assert settings.database.port == 5433
        assert settings.pod_manager.kwargs["namespace"] == "volundr-test"

    def test_env_vars_override_yaml(self, tmp_path, monkeypatch):
        """Environment variables override YAML config values.

        Uses env_nested_delimiter ('__') for nested config fields.
        """
        yaml_content = """
database:
  host: yaml-host
  port: 5432
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml_content)

        monkeypatch.chdir(tmp_path)
        _clear_settings_env(monkeypatch)
        # Use nested delimiter (DATABASE__HOST) to override nested config
        monkeypatch.setenv("DATABASE__HOST", "env-host")

        settings = Settings()

        # Env var should override YAML
        assert settings.database.host == "env-host"
        # YAML value should still be used for port
        assert settings.database.port == 5432

    def test_settings_with_git_instances_from_yaml(self, tmp_path, monkeypatch):
        """Settings loads git instances from YAML."""
        yaml_content = """
git:
  validate_on_create: false
  github:
    instances:
      - name: GitHub
        base_url: https://api.github.com
        token: ghp_yaml
  gitlab:
    instances:
      - name: Internal
        base_url: https://git.internal.com
        token: glpat-internal
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml_content)

        monkeypatch.chdir(tmp_path)
        _clear_settings_env(monkeypatch)

        settings = Settings()

        assert settings.git.validate_on_create is False

        gh_instances = settings.git.github.get_instances()
        assert len(gh_instances) == 1
        assert gh_instances[0].name == "GitHub"
        assert gh_instances[0].token == "ghp_yaml"

        gl_instances = settings.git.gitlab.get_instances()
        assert len(gl_instances) == 1
        assert gl_instances[0].name == "Internal"

    def test_settings_without_yaml_uses_defaults(self, tmp_path, monkeypatch):
        """Settings uses defaults when no YAML file exists."""
        # Change to a directory without config.yaml
        monkeypatch.chdir(tmp_path)
        _clear_settings_env(monkeypatch)

        settings = Settings()

        assert settings.database.host == "localhost"
        assert settings.pod_manager.kwargs == {}

    def test_settings_reloads_config_paths_when_niuu_config_changes(self, tmp_path, monkeypatch):
        """Settings should resolve NIUU_CONFIG at instantiation time, not import time."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
pod_manager:
  kwargs:
    namespace: dynamic-test
""".strip()
        )
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        _clear_settings_env(monkeypatch)
        monkeypatch.setenv("NIUU_CONFIG", str(config_file))
        configured = Settings()
        assert configured.pod_manager.kwargs == {"namespace": "dynamic-test"}

        monkeypatch.chdir(empty_dir)
        _clear_settings_env(monkeypatch)
        defaults = Settings()
        assert defaults.pod_manager.kwargs == {}

    def test_settings_loads_seeded_integrations_from_yaml(self, tmp_path, monkeypatch):
        """Settings parses config-seeded integration connections."""
        yaml_content = """
integrations:
  seed_connections:
    - id: linear-seed
      owner_id: dev-user
      integration_type: issue_tracker
      adapter: volundr.adapters.outbound.linear.LinearAdapter
      credential_name: linear-config
      slug: linear
      enabled: true
      credential:
        secret_type: api_key
        data:
          api_key: linear-foobar
    - owner_id: dev-user
      integration_type: messaging
      adapter: tyr.adapters.telegram_notification.TelegramNotificationAdapter
      credential_name: telegram-main
      slug: telegram
      enabled: true
      config:
        notify_only: true
      credential:
        secret_type: generic
        data:
          bot_token: foobar
          chat_id: foobar
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml_content)

        monkeypatch.chdir(tmp_path)
        _clear_settings_env(monkeypatch)

        settings = Settings()

        assert len(settings.integrations.seed_connections) == 2
        linear_seed = settings.integrations.seed_connections[0]
        assert linear_seed.id == "linear-seed"
        assert linear_seed.integration_type == IntegrationType.ISSUE_TRACKER
        assert linear_seed.adapter == "volundr.adapters.outbound.linear.LinearAdapter"
        assert linear_seed.credential_name == "linear-config"
        assert linear_seed.slug == "linear"
        assert linear_seed.credential is not None
        assert linear_seed.credential.secret_type == SecretType.API_KEY
        assert linear_seed.credential.data == {"api_key": "linear-foobar"}

        seed = settings.integrations.seed_connections[1]
        assert isinstance(seed, SeededIntegrationConnectionConfig)
        assert seed.owner_id == "dev-user"
        assert seed.integration_type == IntegrationType.MESSAGING
        assert seed.adapter == "tyr.adapters.telegram_notification.TelegramNotificationAdapter"
        assert seed.credential_name == "telegram-main"
        assert seed.slug == "telegram"
        assert seed.config == {"notify_only": True}
        assert seed.credential is not None
        assert seed.credential.secret_type == SecretType.GENERIC
        assert seed.credential.data == {"bot_token": "foobar", "chat_id": "foobar"}


class TestFeatureModuleConfig:
    """Tests for FeatureModuleConfig."""

    def test_defaults(self):
        config = FeatureModuleConfig(
            key="test",
            label="Test",
            icon="Settings",
            scope="user",
        )
        assert config.default_enabled is True
        assert config.admin_only is False
        assert config.order == 0

    def test_custom_values(self):
        config = FeatureModuleConfig(
            key="storage",
            label="Storage",
            icon="HardDrive",
            scope="admin",
            default_enabled=False,
            admin_only=True,
            order=30,
        )
        assert config.key == "storage"
        assert config.scope == "admin"
        assert config.default_enabled is False
        assert config.admin_only is True
        assert config.order == 30

    def test_default_feature_modules_not_empty(self):
        modules = _default_feature_modules()
        assert len(modules) > 0
        keys = [m.key for m in modules]
        assert "users" in keys
        assert "credentials" in keys

    def test_default_modules_have_both_scopes(self):
        modules = _default_feature_modules()
        scopes = {m.scope for m in modules}
        assert "admin" in scopes
        assert "user" in scopes

    def test_settings_includes_features(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        settings = Settings()
        assert len(settings.features) > 0
        assert all(isinstance(f, FeatureModuleConfig) for f in settings.features)

    def test_settings_features_from_yaml(self, tmp_path, monkeypatch):
        yaml_content = """
features:
  - key: custom
    label: Custom Module
    icon: Settings
    scope: user
    default_enabled: true
    order: 100
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml_content)
        monkeypatch.chdir(tmp_path)
        _clear_settings_env(monkeypatch)

        settings = Settings()
        assert len(settings.features) == 1
        assert settings.features[0].key == "custom"
        assert settings.features[0].label == "Custom Module"


class TestRabbitMQConfig:
    """Tests for RabbitMQConfig."""

    def test_defaults(self):
        config = RabbitMQConfig()
        assert config.enabled is False
        assert "amqp://" in config.url
        assert config.exchange_name == "volundr.events"
        assert config.exchange_type == "topic"

    def test_custom_values(self):
        config = RabbitMQConfig(
            enabled=True,
            url="amqp://user:pass@rabbitmq:5672/vhost",
            exchange_name="custom.exchange",
            exchange_type="fanout",
        )
        assert config.enabled is True
        assert config.url == "amqp://user:pass@rabbitmq:5672/vhost"
        assert config.exchange_name == "custom.exchange"
        assert config.exchange_type == "fanout"


class TestOtelConfig:
    """Tests for OtelConfig."""

    def test_defaults(self):
        config = OtelConfig()
        assert config.enabled is False
        assert config.endpoint == "http://localhost:4317"
        assert config.protocol == "grpc"
        assert config.service_name == "volundr"
        assert config.provider_name == "anthropic"
        assert config.insecure is True

    def test_custom_values(self):
        config = OtelConfig(
            enabled=True,
            endpoint="https://tempo.internal:4317",
            protocol="grpc",
            service_name="volundr-prod",
            provider_name="anthropic",
            insecure=False,
        )
        assert config.enabled is True
        assert config.endpoint == "https://tempo.internal:4317"
        assert config.insecure is False


class TestEventPipelineConfig:
    """Tests for EventPipelineConfig."""

    def test_defaults(self):
        config = EventPipelineConfig()
        assert config.postgres_buffer_size == 1
        assert isinstance(config.rabbitmq, RabbitMQConfig)
        assert isinstance(config.otel, OtelConfig)
        assert config.rabbitmq.enabled is False
        assert config.otel.enabled is False

    def test_nested_config(self):
        config = EventPipelineConfig(
            postgres_buffer_size=10,
            rabbitmq=RabbitMQConfig(enabled=True),
            otel=OtelConfig(enabled=True, endpoint="http://tempo:4317"),
        )
        assert config.postgres_buffer_size == 10
        assert config.rabbitmq.enabled is True
        assert config.otel.enabled is True
        assert config.otel.endpoint == "http://tempo:4317"

    def test_settings_includes_event_pipeline(self, tmp_path, monkeypatch):
        """Settings includes event_pipeline with nested rabbitmq and otel."""
        monkeypatch.chdir(tmp_path)
        settings = Settings()
        assert isinstance(settings.event_pipeline, EventPipelineConfig)
        assert settings.event_pipeline.rabbitmq.enabled is False
        assert settings.event_pipeline.otel.enabled is False
