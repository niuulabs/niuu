"""Tests for Skuld Helm chart templates."""

from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).parent.parent.parent / "charts" / "skuld"


class TestChartMetadata:
    """Tests for Chart.yaml."""

    @pytest.fixture
    def chart_yaml(self) -> dict:
        """Load Chart.yaml."""
        chart_path = CHART_DIR / "Chart.yaml"
        return yaml.safe_load(chart_path.read_text())

    def test_chart_name(self, chart_yaml):
        """Test chart name is skuld."""
        assert chart_yaml["name"] == "skuld"

    def test_chart_version(self, chart_yaml):
        """Test chart has version."""
        assert "version" in chart_yaml
        assert chart_yaml["version"]

    def test_chart_description_includes_editor(self, chart_yaml):
        """Test chart description mentions terminal sidecars."""
        assert "terminal" in chart_yaml["description"].lower()

    def test_chart_keywords_include_ide(self, chart_yaml):
        """Test chart keywords stay focused on session runtime concerns."""
        keywords = chart_yaml["keywords"]
        assert "websocket" in keywords
        assert "session" in keywords


class TestValuesDefaults:
    """Tests for values.yaml defaults."""

    @pytest.fixture
    def values_yaml(self) -> dict:
        """Load values.yaml."""
        values_path = CHART_DIR / "values.yaml"
        return yaml.safe_load(values_path.read_text())

    def test_transport_adapter_defaults_to_sdk_websocket(self, values_yaml):
        """Test broker transportAdapter defaults to SdkWebSocketTransport."""
        assert values_yaml["broker"]["transportAdapter"] == (
            "skuld.transports.sdk_websocket.SdkWebSocketTransport"
        )

    def test_broker_cli_type_defaults_to_claude(self, values_yaml):
        """Test legacy broker cliType defaults to claude (backward compat)."""
        assert values_yaml["broker"]["cliType"] == "claude"

    def test_env_secrets_default_has_anthropic_key(self, values_yaml):
        """Test envSecrets defaults to a list with ANTHROPIC_API_KEY."""
        env_secrets = values_yaml["envSecrets"]
        assert isinstance(env_secrets, list)
        assert len(env_secrets) == 1
        assert env_secrets[0]["envVar"] == "ANTHROPIC_API_KEY"
        assert env_secrets[0]["secretName"] == "anthropic-api-key"
        assert env_secrets[0]["secretKey"] == "api-key"

    def test_env_vars_defaults_to_empty_list(self, values_yaml):
        """Test envVars defaults to an empty list."""
        assert values_yaml["envVars"] == []

    def test_service_exposes_single_entry_port(self, values_yaml):
        """Test service configuration has single nginx entry port."""
        service = values_yaml["service"]
        assert service["port"] == 8080  # Nginx entry point

    def test_ingress_paths_configured(self, values_yaml):
        """Test ingress paths are configured."""
        paths = values_yaml["ingress"]["paths"]
        assert paths["session"] == "/session"
        assert paths["ide"] == "/"

    def test_ingress_has_cert_manager_annotation(self, values_yaml):
        """Test ingress has cert-manager annotation."""
        annotations = values_yaml["ingress"]["annotations"]
        assert "cert-manager.io/cluster-issuer" in annotations

    def test_ingress_tls_enabled_by_default(self, values_yaml):
        """Test ingress TLS is enabled by default."""
        assert values_yaml["ingress"]["tls"]["enabled"] is True

    def test_ingress_class_is_traefik(self, values_yaml):
        """Test ingress class defaults to traefik."""
        assert values_yaml["ingress"]["className"] == "traefik"

    def test_skuld_image_configured(self, values_yaml):
        """Test Skuld image is configured."""
        image = values_yaml["image"]
        assert image["repository"] == "ghcr.io/niuulabs/skuld"
        assert "tag" in image

    def test_persistence_configured(self, values_yaml):
        """Test persistence is configured."""
        persistence = values_yaml["persistence"]
        assert persistence["enabled"] is True
        assert persistence["existingClaim"] == "volundr-sessions"
        assert persistence["mountPath"] == "/volundr/sessions"


class TestNginxConfigMap:
    """Tests for nginx-configmap.yaml template structure."""

    @pytest.fixture
    def nginx_yaml(self) -> str:
        template_path = CHART_DIR / "templates" / "nginx-configmap.yaml"
        return template_path.read_text()

    def test_routes_terminal_traffic(self, nginx_yaml):
        """Test nginx config routes terminal traffic."""
        assert "location /terminal/" in nginx_yaml
        assert "proxy_set_header Upgrade" in nginx_yaml

    def test_has_no_reh_upstream(self, nginx_yaml):
        """Test nginx config no longer references the REH sidecar."""
        assert "upstream reh" not in nginx_yaml
        assert "location /reh/" not in nginx_yaml


class TestConfigMapTemplate:
    """Tests for skuld-configmap.yaml template structure."""

    @pytest.fixture
    def configmap_yaml(self) -> str:
        template_path = CHART_DIR / "templates" / "skuld-configmap.yaml"
        return template_path.read_text()

    def test_configmap_has_transport_adapter(self, configmap_yaml):
        """Test configmap includes transport_adapter field."""
        assert "transport_adapter" in configmap_yaml

    def test_configmap_transport_adapter_driven_by_values(self, configmap_yaml):
        """Test configmap transport_adapter reads from broker.transportAdapter."""
        assert ".Values.broker.transportAdapter" in configmap_yaml

    def test_configmap_has_cli_type(self, configmap_yaml):
        """Test configmap includes legacy cli_type field for backward compat."""
        assert "cli_type" in configmap_yaml

    def test_configmap_cli_type_driven_by_values(self, configmap_yaml):
        """Test configmap legacy cli_type reads from broker.cliType."""
        assert ".Values.broker.cliType" in configmap_yaml

    def test_configmap_cli_type_has_default_fallback(self, configmap_yaml):
        """Test configmap cli_type template has a default fallback value."""
        assert 'default "claude"' in configmap_yaml

    def test_configmap_has_service_auth_fields(self, configmap_yaml):
        """Test configmap includes service auth identity fields."""
        assert "service_user_id" in configmap_yaml
        assert "service_tenant_id" in configmap_yaml


class TestDeploymentTemplate:
    """Tests for deployment.yaml template structure."""

    @pytest.fixture
    def deployment_yaml(self) -> str:
        """Load deployment.yaml template."""
        template_path = CHART_DIR / "templates" / "deployment.yaml"
        return template_path.read_text()

    def test_contains_skuld_container(self, deployment_yaml):
        """Test deployment contains skuld container."""
        assert "name: skuld" in deployment_yaml

    def test_deployment_has_nginx_container(self, deployment_yaml):
        """Test deployment contains nginx entry point container."""
        assert "name: nginx" in deployment_yaml

    def test_deployment_has_devrunner_container(self, deployment_yaml):
        """Test deployment contains devrunner container."""
        assert "name: devrunner" in deployment_yaml

    def test_nginx_mounts_config(self, deployment_yaml):
        """Test nginx mounts its configmap."""
        assert "nginx-config" in deployment_yaml

    def test_sessions_volume_mounted(self, deployment_yaml):
        """Test sessions volume is mounted by multiple containers."""
        assert deployment_yaml.count("name: sessions") >= 2

    def test_git_clone_ensures_dynamic_nginx_include_exists(self, deployment_yaml):
        """Test session bootstrap always pre-creates .services/nginx.conf."""
        assert 'mkdir -p "$WORKSPACE/.services"' in deployment_yaml
        assert 'touch "$WORKSPACE/.services/nginx.conf"' in deployment_yaml

    def test_has_no_reh_container(self, deployment_yaml):
        """Test deployment no longer contains the retired REH container."""
        assert "name: vscode-reh" not in deployment_yaml
        assert "--without-connection-token" not in deployment_yaml

    def test_broker_port_is_8081(self, deployment_yaml):
        """Test broker runs on port 8081 (nginx is entry at 8080)."""
        assert "containerPort: 8081" in deployment_yaml

    def test_deployment_uses_env_secrets_range_loop(self, deployment_yaml):
        """Test deployment injects secrets via generic range loop, not per-provider."""
        assert "range .Values.envSecrets" in deployment_yaml
        assert ".envVar" in deployment_yaml
        assert ".secretName" in deployment_yaml
        assert ".secretKey" in deployment_yaml

    def test_deployment_uses_env_vars_range_loop(self, deployment_yaml):
        """Test deployment injects plain env vars via generic range loop."""
        assert "range .Values.envVars" in deployment_yaml

    def test_deployment_has_no_per_provider_api_fields(self, deployment_yaml):
        """Test deployment does not contain old per-provider api fields."""
        assert "anthropicApiKeySecret" not in deployment_yaml
        assert "openaiApiKeySecret" not in deployment_yaml
        assert "api.baseUrl" not in deployment_yaml

    def test_credential_files_volume_gated_on_secret_name(self, deployment_yaml):
        """Test credential-files volume is gated on credentialFiles.secretName, not cli_type."""
        assert "credential-files" in deployment_yaml
        assert "credentialFiles.secretName" in deployment_yaml
        # Credential volume wiring must not reference broker.cliType
        before_volume = deployment_yaml.split("credential-files")[0].split("homeVolume")[-1]
        assert "broker.cliType" not in before_volume


class TestServiceTemplate:
    """Tests for service.yaml template structure."""

    @pytest.fixture
    def service_yaml(self) -> str:
        """Load service.yaml template."""
        template_path = CHART_DIR / "templates" / "service.yaml"
        return template_path.read_text()

    def test_exposes_single_http_port(self, service_yaml):
        """Test service exposes single http port (nginx entry point)."""
        assert "name: http" in service_yaml

    def test_no_separate_ide_port(self, service_yaml):
        """Test service does not expose separate IDE port (nginx handles routing)."""
        assert "name: ide" not in service_yaml


class TestIngressTemplate:
    """Tests for ingress.yaml template structure."""

    @pytest.fixture
    def ingress_yaml(self) -> str:
        """Load ingress.yaml template."""
        template_path = CHART_DIR / "templates" / "ingress.yaml"
        return template_path.read_text()

    def test_annotations_come_from_values(self, ingress_yaml):
        """Test ingress annotations are driven by values, not hardcoded."""
        assert ".Values.ingress.annotations" in ingress_yaml

    def test_routes_all_to_nginx(self, ingress_yaml):
        """Test all traffic routes to single nginx entry port."""
        assert "name: http" in ingress_yaml

    def test_single_catch_all_path(self, ingress_yaml):
        """Test ingress uses single catch-all path (nginx routes internally)."""
        # Should NOT have separate /session and /ide paths
        assert ".Values.ingress.paths.session" not in ingress_yaml
        assert ".Values.ingress.paths.ide" not in ingress_yaml

    def test_has_default_route(self, ingress_yaml):
        """Test ingress has default route."""
        assert "path: /" in ingress_yaml


class TestHelpersTemplate:
    """Tests for _helpers.tpl template."""

    @pytest.fixture
    def helpers_tpl(self) -> str:
        """Load _helpers.tpl template."""
        template_path = CHART_DIR / "templates" / "_helpers.tpl"
        return template_path.read_text()

    def test_has_workspace_path_helper(self, helpers_tpl):
        """Test helpers has workspace path function."""
        assert 'define "skuld.workspacePath"' in helpers_tpl

    def test_workspace_path_includes_session_id(self, helpers_tpl):
        """Test workspace path includes session ID."""
        assert ".Values.session.id" in helpers_tpl

    def test_has_fullname_helper(self, helpers_tpl):
        """Test helpers has fullname function."""
        assert 'define "skuld.fullname"' in helpers_tpl

    def test_has_labels_helper(self, helpers_tpl):
        """Test helpers has labels function."""
        assert 'define "skuld.labels"' in helpers_tpl
