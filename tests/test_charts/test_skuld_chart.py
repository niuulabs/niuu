"""Tests for Skuld Helm chart templates."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).parent.parent.parent / "charts" / "skuld"


PERSONA_DOCUMENT = {
    "schema_version": 1,
    "id": "reviewer",
    "revision": "content-0123456789abcdef",
    "definition": {
        "name": "reviewer",
        "system_prompt_template": 'Review "carefully".\n\nNever <edit> & never push.',
        "allowed_tools": ["file_read", "git_read"],
        "permission_mode": "read_only",
    },
}


def _rendered_skuld_config(chart: str, *extra: str) -> dict:
    command = ["helm", "template", "persona-test", str(CHART_DIR.parent / chart), *extra]
    return next(
        yaml.safe_load(doc["data"]["config.yaml"])
        for doc in yaml.safe_load_all(subprocess.check_output(command))
        if doc and doc.get("kind") == "ConfigMap" and "config.yaml" in doc.get("data", {})
    )


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
@pytest.mark.parametrize("chart", ["skuld", "skuld-planner"])
def test_pinned_persona_reaches_runtime_configuration_beside_the_request_prompt(chart):
    from skuld.config import SkuldSettings

    config = _rendered_skuld_config(
        chart,
        "--set-json",
        "session.persona=" + json.dumps(PERSONA_DOCUMENT),
        "--set",
        "session.systemPrompt=Focus on the migrations.",
    )

    assert config["session"]["persona"] == PERSONA_DOCUMENT
    assert config["session"]["system_prompt"] == "Focus on the migrations."
    settings = SkuldSettings(**config)
    assert settings.session.persona == PERSONA_DOCUMENT
    assert settings.session.system_prompt == "Focus on the migrations."


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
@pytest.mark.parametrize("chart", ["skuld", "skuld-planner"])
def test_no_persona_key_is_rendered_without_a_persona(chart):
    from skuld.config import SkuldSettings

    config = _rendered_skuld_config(chart)

    assert "persona" not in config["session"]
    assert SkuldSettings(**config).session.persona is None


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
def test_workflow_steps_are_off_unless_forge_marks_the_session_a_step_host():
    assert _rendered_skuld_config("skuld")["workflow_steps"] == {"enabled": False}


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
def test_a_step_host_session_reaches_runtime_configuration():
    from skuld.config import SkuldSettings

    config = _rendered_skuld_config(
        "skuld", "--set-json", "workflowSteps=" + json.dumps({"enabled": True})
    )

    assert config["workflow_steps"] == {"enabled": True}
    assert SkuldSettings(**config).workflow_steps.enabled is True


def test_step_host_flag_survives_the_environment_a_local_broker_is_started_with(monkeypatch):
    from skuld.config import SkuldSettings

    monkeypatch.setenv("SKULD__WORKFLOW_STEPS__ENABLED", "true")

    assert SkuldSettings().workflow_steps.enabled is True


def test_persona_document_survives_the_environment_a_local_broker_is_started_with(monkeypatch):
    from skuld.config import SkuldSettings

    monkeypatch.setenv(
        "SKULD__SESSION__PERSONA", json.dumps(PERSONA_DOCUMENT, separators=(",", ":"))
    )

    assert SkuldSettings().session.persona == PERSONA_DOCUMENT


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
@pytest.mark.parametrize("chart", ["skuld", "skuld-planner"])
@pytest.mark.parametrize("configured", [False, True])
def test_mcp_connections_reach_runtime_configuration(chart, configured):
    from skuld.config import SkuldSettings
    from skuld.transports.mcp_config import build_claude_mcp_config, build_codex_mcp_overrides

    servers = (
        [
            {
                "name": "linear",
                "type": "http",
                "url": "https://mcp.linear.app/mcp/readonly",
                "credential_file": "/run/secrets/mcp/test/token",
                "credential_format": "oauth",
                "auth_header": "Authorization",
                "auth_prefix": "Bearer ",
            },
            {"name": "local", "type": "stdio", "command": "tools", "args": ["serve"]},
        ]
        if configured
        else []
    )
    command = ["helm", "template", "mcp-test", str(CHART_DIR.parent / chart)]
    if configured:
        command.extend(["--set-json", "mcpServers=" + json.dumps(servers)])
    documents = yaml.safe_load_all(subprocess.check_output(command))
    config = next(
        yaml.safe_load(doc["data"]["config.yaml"])
        for doc in documents
        if doc and doc.get("kind") == "ConfigMap" and "config.yaml" in doc.get("data", {})
    )
    settings = SkuldSettings(**config)
    assert settings.mcp_servers == servers
    if configured:
        claude = json.loads(build_claude_mcp_config(settings.mcp_servers))
        assert "--oauth" in claude["mcpServers"]["linear"]["headersHelper"]
        codex = dict(build_codex_mcp_overrides(settings.mcp_servers))
        assert "--oauth" in codex["mcp_servers.linear.http_headers_helper"]


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

    def test_transport_adapter_defaults_to_sdk(self, values_yaml):
        """Test broker transportAdapter defaults to SDKTransport."""
        assert values_yaml["broker"]["transportAdapter"] == "skuld.transports.sdk.SDKTransport"

    def test_broker_cli_type_defaults_to_claude(self, values_yaml):
        """Test broker cliType defaults to claude."""
        assert values_yaml["broker"]["cliType"] == "claude"

    def test_behavior_settings_have_documented_defaults(self, values_yaml):
        """Runtime behavior is discoverable through canonical chart values."""
        broker = values_yaml["broker"]
        assert broker["cliBinary"] == "claude"
        assert broker["remoteControlPermissionMode"] == ""
        assert broker["maxPresentedFileBytes"] == 52_428_800

    def test_external_api_token_uses_secret_reference(self, values_yaml):
        """Outbound service credentials are references, never inline values."""
        assert values_yaml["volundr"]["externalApiTokenSecret"] == {
            "name": "",
            "key": "token",
        }

    def test_env_secrets_default_has_anthropic_key(self, values_yaml):
        """Test envSecrets defaults to a list with ANTHROPIC_API_KEY."""
        env_secrets = values_yaml["envSecrets"]
        assert isinstance(env_secrets, list)
        assert len(env_secrets) == 1
        assert env_secrets[0]["envVar"] == "ANTHROPIC_API_KEY"
        assert env_secrets[0]["secretName"] == "anthropic-api-key"
        assert env_secrets[0]["secretKey"] == "api-key"

    def test_env_vars_default_pins_api_key_auth(self, values_yaml):
        """Cluster pods have no ~/.claude login — Claude transports must keep
        the injected ANTHROPIC_API_KEY rather than the subscription default."""
        env_vars = values_yaml["envVars"]
        assert env_vars == [{"name": "SKULD__CLAUDE_AUTH", "value": "api_key"}]

    def test_claude_auto_update_disabled_by_default(self, values_yaml):
        """Cluster sessions run the CLI pinned in the image; a self-update at
        start-up restarts it inside the session and breaks transport checks."""
        assert values_yaml["claude"]["disableAutoUpdate"] is True

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
        """Test configmap includes cli_type field."""
        assert "cli_type" in configmap_yaml

    def test_configmap_cli_type_driven_by_values(self, configmap_yaml):
        """Test configmap cli_type reads from broker.cliType."""
        assert ".Values.broker.cliType" in configmap_yaml

    def test_configmap_cli_type_has_default_fallback(self, configmap_yaml):
        """Test configmap cli_type template has a default fallback value."""
        assert 'default "claude"' in configmap_yaml

    def test_configmap_has_service_auth_fields(self, configmap_yaml):
        """Test configmap includes service auth identity fields."""
        assert "service_user_id" in configmap_yaml
        assert "service_tenant_id" in configmap_yaml

    def test_configmap_renders_typed_behavior_settings(self, configmap_yaml):
        """Typed behavior settings are rendered from their canonical values."""
        expected = {
            "cli_binary": ".Values.broker.cliBinary",
            "remote_control_permission_mode": ".Values.broker.remoteControlPermissionMode",
            "max_presented_file_bytes": ".Values.broker.maxPresentedFileBytes",
        }
        for field, value in expected.items():
            assert field in configmap_yaml
            assert value in configmap_yaml


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

    def test_volume_ownership_is_not_rewalked_on_every_start(self, tmp_path):
        """A full walk of a user home delays each session by minutes."""
        rendered = _render_skuld_chart(tmp_path, {"session": {"id": "abc"}})
        pod_spec = _deployment_from_rendered(rendered)["spec"]["template"]["spec"]

        assert pod_spec["securityContext"]["fsGroupChangePolicy"] == "OnRootMismatch"

    @pytest.mark.parametrize("repo_url", ["", "https://github.com/org/repo"])
    def test_services_setup_precreates_dynamic_nginx_include(self, tmp_path, repo_url):
        """nginx must not wait on devrunner for the include it loads at startup."""
        rendered = _render_skuld_chart(
            tmp_path, {"session": {"id": "abc"}, "git": {"repoUrl": repo_url}}
        )
        pod_spec = _deployment_from_rendered(rendered)["spec"]["template"]["spec"]
        init_containers = pod_spec["initContainers"]

        assert init_containers[-1]["name"] == "services-setup"
        script = init_containers[-1]["args"][0]
        assert 'WORKSPACE="/volundr/sessions/abc/workspace"' in script
        assert 'mkdir -p "$WORKSPACE/.services"' in script
        assert 'touch "$WORKSPACE/.services/nginx.conf"' in script
        assert init_containers[-1]["securityContext"] == {
            "runAsUser": 1000,
            "allowPrivilegeEscalation": False,
        }
        assert init_containers[-1]["volumeMounts"] == [
            {"name": "sessions", "mountPath": "/volundr/sessions"}
        ]
        assert not any(".services" in " ".join(c.get("args", [])) for c in init_containers[:-1])

    def test_services_setup_omitted_without_local_services(self, tmp_path):
        """No nginx include is rendered, so there is nothing to pre-create."""
        rendered = _render_skuld_chart(tmp_path, {"localServices": {"enabled": False}})
        pod_spec = _deployment_from_rendered(rendered)["spec"]["template"]["spec"]

        assert "services-setup" not in [c["name"] for c in pod_spec.get("initContainers", [])]

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

    def test_deployment_disables_claude_auto_update(self, deployment_yaml):
        """DISABLE_AUTOUPDATER is set outside envVars, so overriding envVars keeps it."""
        assert "if .Values.claude.disableAutoUpdate" in deployment_yaml
        assert "name: DISABLE_AUTOUPDATER" in deployment_yaml

    def test_external_api_token_is_loaded_from_secret(self, deployment_yaml):
        """The control-plane token is never rendered into a ConfigMap or plain env value."""
        assert "SKULD__EXTERNAL_API_TOKEN" in deployment_yaml
        assert ".Values.volundr.externalApiTokenSecret.name" in deployment_yaml
        assert ".Values.volundr.externalApiTokenSecret.key" in deployment_yaml
        assert "secretKeyRef" in deployment_yaml

    def test_deployment_renders_extra_pod_additions(self, tmp_path):
        """Render proof for operator-supplied sidecars, init containers and mesh ports."""
        rendered = _render_skuld_chart(
            tmp_path,
            {
                "envVars": [{"name": "SKULD__MESH__ENABLED", "value": "true"}],
                "mesh": {
                    "enabled": True,
                    "peerPorts": [{"name": "mesh-pub", "containerPort": 7480, "protocol": "TCP"}],
                },
                "extraInitContainers": [
                    {
                        "name": "write-config",
                        "image": "busybox:latest",
                        "command": ["sh", "-c", "echo ok"],
                        "securityContext": {
                            "runAsUser": 1000,
                            "runAsGroup": 1000,
                            "runAsNonRoot": True,
                            "allowPrivilegeEscalation": False,
                        },
                    }
                ],
                "extraContainers": [
                    {
                        "name": "sidecar",
                        "image": "busybox:latest",
                        "volumeMounts": [
                            {
                                "name": "sessions",
                                "mountPath": "/workspace",
                                "subPath": "session-1/workspace",
                                "readOnly": True,
                            }
                        ],
                    }
                ],
            },
        )
        deployment = _deployment_from_rendered(rendered)
        pod_spec = deployment["spec"]["template"]["spec"]

        assert [container["name"] for container in pod_spec["initContainers"]] == [
            "services-setup",
            "write-config",
        ]
        assert pod_spec["initContainers"][1]["securityContext"] == {
            "runAsUser": 1000,
            "runAsGroup": 1000,
            "runAsNonRoot": True,
            "allowPrivilegeEscalation": False,
        }
        containers = {container["name"]: container for container in pod_spec["containers"]}
        assert "skuld" in containers
        assert "sidecar" in containers
        assert {"name": "SKULD__MESH__ENABLED", "value": "true"} in containers["skuld"]["env"]
        assert {"name": "mesh-pub", "containerPort": 7480, "protocol": "TCP"} in containers[
            "skuld"
        ]["ports"]
        volumes = {volume["name"] for volume in pod_spec["volumes"]}
        assert "sessions" in volumes
        for container in pod_spec["containers"]:
            for mount in container.get("volumeMounts", []):
                assert mount["name"] in volumes

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

    def test_codex_home_is_session_local_but_seeded_from_shared_home(self, deployment_yaml):
        """Codex auth/config is copied without sharing sqlite runtime state."""
        assert (
            'CODEX_STATE_DIR="{{ printf "%s/.codex" (include "skuld.workspacePath" .) }}"'
        ) in deployment_yaml
        assert 'if [ "$DEST_DIR" = ".codex" ]; then' in deployment_yaml
        assert 'chown "$TARGET_UID:$TARGET_GID" "$(dirname "$CODEX_STATE_DIR")"' in deployment_yaml
        assert "for name in auth.json config.toml version.json models_cache.json" in deployment_yaml
        assert 'cp -f "$HOME_DIR/$DEST_DIR/$name" "$CODEX_STATE_DIR/$name"' in deployment_yaml
        assert (
            "sqlite"
            not in deployment_yaml.split("Codex auth/config seeded")[0].split(
                "for name in auth.json"
            )[1]
        )


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


def test_session_restart_never_overlaps_workers(tmp_path):
    rendered = _render_skuld_chart(tmp_path, {"session": {"id": "research"}})
    assert _deployment_from_rendered(rendered)["spec"]["strategy"] == {"type": "Recreate"}


class TestSessionPodConfiguration:
    """Broker configuration and pod shape of a plain Skuld session release."""

    SESSION_VALUES = {
        "session": {"id": "research", "model": "gpt-5.6-sol", "reasoningEffort": "high"},
        "volundr": {"apiUrl": "https://volundr.example"},
    }

    @pytest.mark.parametrize(
        ("broker", "enabled"),
        [({}, True), ({"eventLogEnabled": False, "activityReportingEnabled": False}, False)],
        ids=["forge-session", "no-forge-session"],
    )
    def test_forge_session_event_log_and_activity_follow_broker_values(
        self, tmp_path, broker, enabled
    ):
        from skuld.config import SkuldSettings

        values = json.loads(json.dumps(self.SESSION_VALUES))
        values["broker"] = broker
        broker_cfg = _broker_config(_render_skuld_chart(tmp_path, values))

        assert broker_cfg["event_log_enabled"] is enabled
        assert broker_cfg["activity_reporting_enabled"] is enabled
        assert broker_cfg["volundr_api_url"] == "https://volundr.example"
        assert broker_cfg["session"]["model"] == "gpt-5.6-sol"
        assert broker_cfg["session"]["reasoning_effort"] == "high"
        settings = SkuldSettings.model_validate(broker_cfg)
        assert settings.event_log_enabled is enabled
        assert settings.activity_reporting_enabled is enabled

    def test_replica_count_supports_real_suspend_and_resume(self, tmp_path):
        suspended = dict(self.SESSION_VALUES)
        suspended["replicaCount"] = 0
        rendered = _render_skuld_chart(tmp_path, suspended)

        assert _deployment_from_rendered(rendered)["spec"]["replicas"] == 0

    def test_gateway_extracts_browser_websocket_token(self, tmp_path):
        values = dict(self.SESSION_VALUES)
        values["gateway"] = {
            "enabled": True,
            "jwt": {
                "enabled": True,
                "issuer": "https://keycloak.example/realms/volundr",
                "audiences": ["volundr-api"],
                "jwksUri": "https://keycloak.example/certs",
                "workload": {
                    "enabled": True,
                    "issuer": "https://volundr.example/workload",
                    "audiences": ["volundr-api"],
                    "jwksUri": "https://volundr.example/workload/jwks",
                },
            },
        }
        rendered = _render_skuld_chart(tmp_path, values)
        policy = next(
            doc
            for doc in yaml.safe_load_all(rendered)
            if isinstance(doc, dict) and doc.get("kind") == "SecurityPolicy"
        )

        for provider in policy["spec"]["jwt"]["providers"]:
            assert provider["extractFrom"]["params"] == ["access_token", "token"]

    def test_default_render_has_no_workload_identity_config(self, tmp_path):
        rendered = _render_skuld_chart(tmp_path, {})
        assert "workload_identity" not in rendered
        assert "NIUU_WORKLOAD_IDENTITY" not in rendered

    def test_pod_has_no_workload_identity_env_vars(self, tmp_path):
        deployment = _deployment_from_rendered(_render_skuld_chart(tmp_path, self.SESSION_VALUES))
        for container in deployment["spec"]["template"]["spec"]["containers"]:
            env_names = {entry["name"] for entry in container.get("env", [])}
            offending = {n for n in env_names if n.startswith("NIUU_WORKLOAD_IDENTITY")}
            assert not offending, f"{container['name']} still injects {offending}"


class TestVolundrReportingConfig:
    """Volundr reporting stays enabled for normal workflow sessions."""

    def _broker_config(self, rendered: str) -> dict:
        for doc in yaml.safe_load_all(rendered):
            if (
                isinstance(doc, dict)
                and doc.get("kind") == "ConfigMap"
                and "config.yaml" in doc.get("data", {})
                and "transport_adapter" in doc["data"]["config.yaml"]
            ):
                return yaml.safe_load(doc["data"]["config.yaml"])
        pytest.fail("Skuld broker config was not rendered")
        raise AssertionError("Skuld broker config was not rendered")

    def test_sessions_keep_volundr_reporting(self, tmp_path):
        rendered = _render_skuld_chart(
            tmp_path,
            {
                "session": {"id": "11111111-1111-4111-8111-111111111111"},
                "volundr": {"apiUrl": "https://volundr.example"},
            },
        )

        broker_cfg = self._broker_config(rendered)
        deployment = _deployment_from_rendered(rendered)
        broker = next(
            c for c in deployment["spec"]["template"]["spec"]["containers"] if c["name"] == "skuld"
        )
        volundr_env = next(
            entry for entry in broker.get("env", []) if entry["name"] == "SKULD__VOLUNDR_API_URL"
        )

        assert broker_cfg["volundr_api_url"] == "https://volundr.example"
        assert volundr_env["value"] == "https://volundr.example"


class TestBehaviorSettingsRendering:
    """Typed behavior values render to config while credentials stay in Secrets."""

    def test_config_and_external_token_secret_render(self, tmp_path):
        rendered = _render_skuld_chart(
            tmp_path,
            {
                "broker": {
                    "cliBinary": "claude-custom",
                    "remoteControlPermissionMode": "acceptEdits",
                    "maxPresentedFileBytes": 4096,
                },
                "volundr": {
                    "externalApiTokenSecret": {
                        "name": "skuld-control-plane",
                        "key": "service-token",
                    }
                },
            },
        )
        configmaps = [
            doc
            for doc in yaml.safe_load_all(rendered)
            if isinstance(doc, dict) and doc.get("kind") == "ConfigMap"
        ]
        broker_cfg = next(
            yaml.safe_load(doc["data"]["config.yaml"])
            for doc in configmaps
            if "transport_adapter" in doc.get("data", {}).get("config.yaml", "")
        )
        assert broker_cfg["cli_binary"] == "claude-custom"
        assert broker_cfg["remote_control_permission_mode"] == "acceptEdits"
        assert broker_cfg["max_presented_file_bytes"] == 4096
        assert "external_api_token" not in broker_cfg

        deployment = _deployment_from_rendered(rendered)
        broker = next(
            container
            for container in deployment["spec"]["template"]["spec"]["containers"]
            if container["name"] == "skuld"
        )
        token_env = next(
            entry for entry in broker["env"] if entry["name"] == "SKULD__EXTERNAL_API_TOKEN"
        )
        assert token_env["valueFrom"]["secretKeyRef"] == {
            "name": "skuld-control-plane",
            "key": "service-token",
        }


def _render_skuld_chart(tmp_path: Path, values: dict) -> str:
    helm = shutil.which("helm")
    if not helm:
        pytest.skip("helm is not installed")

    values_file = tmp_path / "values.yaml"
    values_file.write_text(yaml.safe_dump(values), encoding="utf-8")
    result = subprocess.run(
        [helm, "template", "skuld-test", str(CHART_DIR), "-f", str(values_file)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"helm template failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return result.stdout


def _deployment_from_rendered(rendered_yaml: str) -> dict:
    for document in yaml.safe_load_all(rendered_yaml):
        if isinstance(document, dict) and document.get("kind") == "Deployment":
            return document
    pytest.fail("Deployment was not rendered")
    raise AssertionError("Deployment was not rendered")


def test_user_git_integration_authenticates_nested_checkout(tmp_path):
    token = tmp_path / "integration-token"
    token.write_text("test-user-integration-token")
    rendered = _render_skuld_chart(
        tmp_path,
        {
            "git": {
                "repoUrl": "https://github.com/niuulabs/niuu.git",
                "credentials": {
                    "tokenFile": str(token),
                    "secretName": "unused-cluster-secret",
                    "username": "x-access-token",
                },
            },
        },
    )
    containers = _deployment_from_rendered(rendered)["spec"]["template"]["spec"]["containers"]
    env = next(c["env"] for c in containers if c["name"] == "skuld")
    assert not any(
        e.get("valueFrom", {}).get("secretKeyRef", {}).get("name") == "unused-cluster-secret"
        for e in env
    )
    process_env = {**os.environ, **{e["name"]: e["value"] for e in env if "value" in e}}
    process_env.update(
        GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull
    )
    result = subprocess.run(
        ["git", "credential", "fill"],
        input="url=https://github.com/niuulabs/niuu.git\n\n",
        text=True,
        capture_output=True,
        env=process_env,
        cwd=tmp_path,
        check=True,
    )
    assert "password=test-user-integration-token" in result.stdout


@pytest.mark.parametrize("enabled", [False, True])
def test_websocket_auth_configuration_reaches_broker(enabled):
    command = [
        "helm",
        "template",
        "test",
        str(CHART_DIR),
        "--set",
        f"wsAuth.enforce_ownership={str(enabled).lower()}",
        "--set",
        "session.ownerId=alice",
        "--set",
        "session.tenantId=acme",
        "--set",
        "gateway.enabled=true",
        "--set",
        "gateway.jwt.enabled=true",
        "--set",
        "gateway.jwt.issuer=https://issuer.test",
        "--set",
        "gateway.jwt.audiences[0]=skuld",
        "--set",
        "gateway.jwt.jwksUri=https://issuer.test/jwks",
    ]
    docs = list(yaml.safe_load_all(subprocess.check_output(command)))
    config = next(
        yaml.safe_load(d["data"]["config.yaml"])
        for d in docs
        if d and d["kind"] == "ConfigMap" and "config.yaml" in d.get("data", {})
    )
    assert config["ws_auth"]["enforce_ownership"] is enabled
    assert config["ws_auth"]["allow_loopback"] is False
    # "deployment" regardless of enforce_ownership: chart-deployed pods
    # (Kubernetes) always rely on this pod's own auth boundary, never the
    # session proxy's stamped header. Only the process backend's local
    # launcher (volundr.adapters.outbound.local_process) renders "proxy",
    # outside this chart entirely.
    assert config["ws_auth"]["room_role_source"] == "deployment"
    policy = next(d for d in docs if d and d["kind"] == "SecurityPolicy")
    headers = {c["header"] for c in policy["spec"]["jwt"]["providers"][0]["claimToHeaders"]}
    assert {"x-auth-user-id", "x-auth-tenant", "x-auth-roles"} <= headers


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
def test_the_gateway_strips_the_headers_only_forge_may_stamp():
    """A browser reaching the pod straight through the Gateway can't claim a room role or
    pose as a room member session: both headers are removed before the broker sees them."""
    command = ["helm", "template", "test", str(CHART_DIR), "--set", "gateway.enabled=true"]
    docs = list(yaml.safe_load_all(subprocess.check_output(command)))
    route = next(d for d in docs if d and d["kind"] == "HTTPRoute")
    removed = {
        header
        for rule in route["spec"]["rules"]
        for item in rule.get("filters", [])
        if item["type"] == "RequestHeaderModifier"
        for header in item["requestHeaderModifier"].get("remove", [])
    }

    assert {"x-niuu-room-role", "x-niuu-room-caller-session"} <= removed


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
def test_room_role_source_defaults_to_deployment_with_no_remote_adapter():
    """Existing clusters must render byte-identical wsAuth until they opt in."""
    from skuld.config import SkuldSettings

    command = ["helm", "template", "test", str(CHART_DIR)]
    docs = list(yaml.safe_load_all(subprocess.check_output(command)))
    config = next(
        yaml.safe_load(d["data"]["config.yaml"])
        for d in docs
        if d and d["kind"] == "ConfigMap" and "config.yaml" in d.get("data", {})
    )
    assert config["ws_auth"]["room_role_source"] == "deployment"
    # No placeholder block: room_role_remote must be entirely absent, not
    # merely inert, until an operator opts in.
    assert "room_role_remote" not in config["ws_auth"]
    settings = SkuldSettings(**config)
    assert settings.ws_auth.room_role_source == "deployment"
    assert settings.ws_auth.room_role_remote is None


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
def test_room_role_source_remote_with_enforce_ownership_fails_the_render():
    """The two together would 403 every participant at the ext_authz sidecar
    before a remote room-role lookup ever ran — reject the config outright."""
    command = [
        "helm",
        "template",
        "test",
        str(CHART_DIR),
        "--set",
        "wsAuth.room_role_source=remote",
        "--set",
        "wsAuth.enforce_ownership=true",
        "--set",
        "session.ownerId=alice",
        "--set",
        "session.tenantId=acme",
        "--set",
        "gateway.enabled=true",
        "--set",
        "gateway.jwt.enabled=true",
        "--set",
        "gateway.jwt.issuer=https://issuer.test",
        "--set",
        "gateway.jwt.audiences[0]=skuld",
        "--set",
        "gateway.jwt.jwksUri=https://issuer.test/jwks",
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode != 0
    assert "enforce_ownership: false" in result.stderr


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
def test_room_role_source_remote_renders_the_dynamic_adapter():
    from skuld.config import SkuldSettings

    command = [
        "helm",
        "template",
        "test",
        str(CHART_DIR),
        "--set",
        "wsAuth.room_role_source=remote",
        "--set",
        "wsAuth.room_role_remote.kwargs.volundr_api_url=http://volundr.volundr.svc:8080",
    ]
    docs = list(yaml.safe_load_all(subprocess.check_output(command)))
    config = next(
        yaml.safe_load(d["data"]["config.yaml"])
        for d in docs
        if d and d["kind"] == "ConfigMap" and "config.yaml" in d.get("data", {})
    )
    assert config["ws_auth"]["room_role_source"] == "remote"
    remote = config["ws_auth"]["room_role_remote"]
    assert remote["adapter"] == "skuld.room_role_remote.RemoteAuthorizationAdapter"
    assert remote["kwargs"]["volundr_api_url"] == "http://volundr.volundr.svc:8080"
    settings = SkuldSettings(**config)
    assert settings.ws_auth.room_role_source == "remote"
    assert settings.ws_auth.room_role_remote is not None


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
def test_room_role_remote_defaults_volundr_api_url_and_token_file():
    """A remote pod must be able to start without repeating volundr.apiUrl or
    the workload-identity token path a second time under room_role_remote."""
    command = [
        "helm",
        "template",
        "test",
        str(CHART_DIR),
        "--set",
        "wsAuth.room_role_source=remote",
        "--set",
        "volundr.apiUrl=http://volundr.volundr.svc:8080",
    ]
    docs = list(yaml.safe_load_all(subprocess.check_output(command)))
    config = next(
        yaml.safe_load(d["data"]["config.yaml"])
        for d in docs
        if d and d["kind"] == "ConfigMap" and "config.yaml" in d.get("data", {})
    )
    kwargs = config["ws_auth"]["room_role_remote"]["kwargs"]
    assert kwargs["volundr_api_url"] == "http://volundr.volundr.svc:8080"
    assert kwargs["token_file"] == "/var/run/secrets/niuu-workload/token"


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
def test_forge_controls_render_into_valid_skuld_configuration():
    from skuld.config import SkuldSettings

    result = subprocess.run(
        [
            "helm",
            "template",
            "forge-controls",
            str(CHART_DIR),
            "--set",
            "session.reasoningEffort=high",
            "--set",
            "broker.historyHydrationEnabled=false",
            "--set",
            "broker.codexReceiveMaxBytes=123456",
            "--set",
            "broker.pi.binary=/opt/pi",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    configs = [
        yaml.safe_load(doc["data"]["config.yaml"])
        for doc in yaml.safe_load_all(result.stdout)
        if doc and doc.get("kind") == "ConfigMap" and "config.yaml" in doc.get("data", {})
    ]
    config = next(item for item in configs if "session" in item)
    settings = SkuldSettings(**config)
    assert settings.session.reasoning_effort == "high"
    assert settings.history_hydration_enabled is False
    assert settings.codex_receive_max_bytes == 123456
    assert settings.pi.binary == "/opt/pi"


def _broker_config(rendered: str) -> dict:
    return next(
        yaml.safe_load(doc["data"]["config.yaml"])
        for doc in yaml.safe_load_all(rendered)
        if isinstance(doc, dict)
        and doc.get("kind") == "ConfigMap"
        and "transport_adapter" in doc.get("data", {}).get("config.yaml", "")
    )


class TestForgeSessionCredentialFile:
    """Forge mounts a session pod's Forge credential from a Secret; the values carry
    only the file it is read from, and the broker mounts what Forge contributes."""

    def test_no_credential_file_by_default(self, tmp_path):
        rendered = _render_skuld_chart(tmp_path, {"session": {"id": "abc"}})
        assert "forge_mcp" not in _broker_config(rendered)

    def test_the_broker_reads_the_mounted_credential(self, tmp_path):
        from skuld.config import ForgeMcpConfig

        mount = "/var/run/secrets/niuu-forge-session"
        values = {
            "session": {"id": "abc"},
            "forgeMcp": {"tokenFile": f"{mount}/token", "grants": ["message"]},
            "extraVolumes": [{"name": "forge-session-x", "secret": {"secretName": "fs-x"}}],
            "extraVolumeMounts": [{"name": "forge-session-x", "mountPath": mount}],
        }
        rendered = _render_skuld_chart(tmp_path, values)
        forge_mcp = _broker_config(rendered)["forge_mcp"]
        assert forge_mcp == {"token_file": f"{mount}/token", "grants": ["message"]}
        (tmp_path / "token").write_text("t")
        parsed = ForgeMcpConfig.model_validate({**forge_mcp, "token_file": str(tmp_path / "token")})
        assert parsed.token == "t"
        broker = next(
            c
            for c in _deployment_from_rendered(rendered)["spec"]["template"]["spec"]["containers"]
            if c["name"] == "skuld"
        )
        assert {"name": "forge-session-x", "mountPath": mount} in broker["volumeMounts"]


def _skuld_config(*set_args: str) -> dict:
    command = ["helm", "template", "secrets-test", str(CHART_DIR), *set_args]
    return next(
        yaml.safe_load(doc["data"]["config.yaml"])
        for doc in yaml.safe_load_all(subprocess.check_output(command))
        if doc and doc.get("kind") == "ConfigMap" and "config.yaml" in doc.get("data", {})
    )


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
def test_secret_request_knobs_reach_runtime_configuration():
    from pydantic import ValidationError

    from skuld.config import SkuldSettings

    defaults = _skuld_config()
    assert defaults["secret_requests"] == {"poll_interval_s": 2, "wait_max_s": 50}
    assert "forge_mcp" not in defaults
    assert SkuldSettings(**defaults).secret_requests.wait_max_s == 50

    tuned = _skuld_config(
        "--set",
        "secretRequests.waitMaxS=300,secretRequests.pollIntervalS=5",
        "--set",
        "forgeMcp.brokerRequestTimeoutS=400,forgeMcp.toolTimeoutS=500",
    )
    settings = SkuldSettings(**tuned)
    assert (settings.secret_requests.poll_interval_s, settings.secret_requests.wait_max_s) == (
        5,
        300,
    )
    assert (settings.forge_mcp.broker_request_timeout_s, settings.forge_mcp.tool_timeout_s) == (
        400,
        500,
    )

    too_long = _skuld_config("--set", "secretRequests.waitMaxS=300")
    with pytest.raises(ValidationError, match="secret_requests.wait_max_s"):
        SkuldSettings(**too_long)


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
def test_ask_knobs_reach_runtime_configuration():
    from pydantic import ValidationError

    from skuld.config import SkuldSettings

    defaults = _skuld_config()
    assert defaults["ask"] == {"poll_interval_s": 2, "wait_max_s": 50}
    assert SkuldSettings(**defaults).ask.wait_max_s == 50

    tuned = _skuld_config(
        "--set",
        "ask.waitMaxS=300,ask.pollIntervalS=5",
        "--set",
        "forgeMcp.brokerRequestTimeoutS=400,forgeMcp.toolTimeoutS=500",
    )
    settings = SkuldSettings(**tuned)
    assert (settings.ask.poll_interval_s, settings.ask.wait_max_s) == (5, 300)

    with pytest.raises(ValidationError, match="ask.wait_max_s"):
        SkuldSettings(**_skuld_config("--set", "ask.waitMaxS=300"))


MESH_VALUES = {
    "enabled": True,
    "transport": "nats",
    "peerId": "reviewer-1",
    "realmId": "flock-a",
    "persona": "reviewer",
    "role": "reviewer",
    "description": "Reviews pull requests.",
    "capabilities": ["review", "git"],
    "tools": ["codex"],
    "consumesEventTypes": ["review.requested"],
    "publishEventTypes": ["review.done"],
    "publishedEventUrgency": 0.4,
    "nng": {"pubSubAddress": "tcp://0.0.0.0:7001", "reqRepAddress": "tcp://0.0.0.0:7002"},
    "adapters": [{"adapter": "nats"}],
    "discoveryAdapters": [{"adapter": "event_bus", "heartbeat_interval_s": 5}],
    "nats": {"servers": ["nats://nats:4222"], "stream_name": "flock"},
    "rpcTimeoutS": 20,
    "rpcReplyCacheSize": 64,
    "diffMaxBytes": 4096,
    "diffTimeoutS": 7,
    "attestation": {
        "required": False,
        "renewFraction": 0.4,
        "retryFraction": 0.2,
        "clockSkewS": 60,
        "eventMaxAgeS": 120,
    },
}

#: Each chart value and the key of Skuld's `mesh` settings it renders to.
MESH_KEYS = {
    "enabled": "enabled",
    "transport": "transport",
    "peerId": "peer_id",
    "realmId": "realm_id",
    "persona": "persona",
    "role": "role",
    "description": "description",
    "capabilities": "capabilities",
    "tools": "tools",
    "consumesEventTypes": "consumes_event_types",
    "publishEventTypes": "publish_event_types",
    "publishedEventUrgency": "published_event_urgency",
    "nng": "nng",
    "adapters": "adapters",
    "discoveryAdapters": "discovery_adapters",
    "nats": "nats",
    "rpcTimeoutS": "rpc_timeout_s",
    "rpcReplyCacheSize": "rpc_reply_cache_size",
    "diffMaxBytes": "diff_max_bytes",
    "diffTimeoutS": "diff_timeout_s",
    "attestation": "attestation",
}


MESH_CHARTS = ["skuld", "skuld-planner"]


def _mesh_config(chart: str, **values) -> dict:
    args: list[str] = []
    for key, value in values.items():
        args += ["--set-json", f"mesh.{key}={json.dumps(value)}"]
    return _rendered_skuld_config(chart, *args)["mesh"]


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
@pytest.mark.parametrize("mesh_chart", MESH_CHARTS)
class TestMeshValues:
    def test_a_chart_that_sets_nothing_renders_what_skuld_defaults_to(self, mesh_chart):
        from skuld.config import MeshConfig, SkuldSettings

        config = _rendered_skuld_config(mesh_chart)

        assert config["mesh"] == {"enabled": False, "transport": "nng"}
        assert SkuldSettings(**config).mesh == MeshConfig()

    @pytest.mark.parametrize(("value_key", "config_key"), sorted(MESH_KEYS.items()))
    def test_each_value_renders_into_the_config_key_it_names(
        self, mesh_chart, value_key, config_key
    ):
        mesh = _mesh_config(mesh_chart, **{value_key: MESH_VALUES[value_key]})

        expected = MESH_VALUES[value_key]
        if value_key == "nng":
            expected = {
                "pub_sub_address": "tcp://0.0.0.0:7001",
                "req_rep_address": "tcp://0.0.0.0:7002",
            }
        if value_key == "attestation":
            expected = {
                "required": False,
                "renew_fraction": 0.4,
                "retry_fraction": 0.2,
                "clock_skew_s": 60,
                "event_max_age_s": 120,
            }
        assert mesh[config_key] == expected

    def test_every_value_together_is_accepted_by_skuld(self, mesh_chart):
        from skuld.config import SkuldSettings

        config = _rendered_skuld_config(
            mesh_chart,
            "--set-json",
            "mesh=" + json.dumps(MESH_VALUES),
        )

        mesh = SkuldSettings(**config).mesh
        assert mesh.enabled is True and mesh.transport == "nats"
        assert (mesh.peer_id, mesh.realm_id, mesh.persona) == ("reviewer-1", "flock-a", "reviewer")
        assert (mesh.role, mesh.description) == ("reviewer", "Reviews pull requests.")
        assert mesh.capabilities == ["review", "git"] and mesh.tools == ["codex"]
        assert mesh.consumes_event_types == ["review.requested"]
        assert mesh.publish_event_types == ["review.done"]
        assert mesh.published_event_urgency == 0.4
        assert mesh.nng.pub_sub_address == "tcp://0.0.0.0:7001"
        assert mesh.adapters == [{"adapter": "nats"}]
        assert mesh.discovery_adapters[0]["adapter"] == "event_bus"
        assert mesh.nats.servers == ["nats://nats:4222"] and mesh.nats.stream_name == "flock"
        assert (mesh.rpc_timeout_s, mesh.rpc_reply_cache_size) == (20, 64)
        assert (mesh.diff_max_bytes, mesh.diff_timeout_s) == (4096, 7)

    def test_the_chart_covers_every_setting_skuld_has_so_a_new_one_cannot_be_forgotten(
        self, mesh_chart
    ):
        from skuld.config import MeshConfig

        assert set(MESH_KEYS.values()) == set(MeshConfig.model_fields)

    def test_an_empty_string_or_null_keeps_skuld_s_default(self, mesh_chart):
        mesh = _mesh_config(
            mesh_chart, peerId="", realmId="", role="", capabilities=None, rpcTimeoutS=None
        )

        assert mesh == {"enabled": False, "transport": "nng"}

    def test_a_values_file_that_never_set_mesh_values_still_renders(self, mesh_chart):
        # Pre-existing releases only ever set `enabled`, `transport` and `peerPorts`.
        mesh = _mesh_config(
            mesh_chart, peerPorts=[{"name": "mesh", "containerPort": 7001}], enabled=True
        )

        # Naming no discovery on nng is the cluster default (TestMeshOnNngInACluster).
        assert mesh["enabled"] is True and mesh["transport"] == "nng"
        assert mesh["discovery_adapters"][0]["adapter"] == "k8s"


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
@pytest.mark.parametrize("mesh_chart", MESH_CHARTS)
def test_mesh_attestation_renders_nothing_by_default_and_only_what_is_set(mesh_chart):
    from skuld.config import SkuldSettings

    default = _rendered_skuld_config(mesh_chart)
    assert "attestation" not in default["mesh"]

    config = _rendered_skuld_config(
        mesh_chart,
        "--set",
        "mesh.attestation.required=false",
        "--set",
        "mesh.attestation.clockSkewS=60",
    )

    assert config["mesh"]["attestation"] == {"required": False, "clock_skew_s": 60}
    settings = SkuldSettings(**config)
    assert settings.mesh.attestation.required is False
    assert settings.mesh.attestation.clock_skew_s == 60
    assert settings.mesh.attestation.event_max_age_s == 3600


class TestThePlannerSharesTheSkuldMeshChart:
    """The planner renders the mesh settings with the very same template, and has the same
    values, so a setting added to one cannot be missing from the other."""

    def test_the_planner_uses_the_skuld_charts_template_file(self):
        skuld = CHART_DIR / "templates" / "_mesh-config.tpl"
        planner = CHART_DIR.parent / "skuld-planner" / "templates" / "_mesh-config.tpl"

        assert planner.is_symlink() and planner.resolve() == skuld.resolve()

    def test_the_planner_has_exactly_the_skuld_charts_mesh_values(self):
        skuld = yaml.safe_load((CHART_DIR / "values.yaml").read_text())["mesh"]
        planner = yaml.safe_load((CHART_DIR.parent / "skuld-planner" / "values.yaml").read_text())[
            "mesh"
        ]

        assert planner == skuld

    @pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
    def test_both_charts_render_the_same_mesh_section_for_the_same_values(self):
        values = [
            arg
            for k, v in MESH_VALUES.items()
            for arg in ("--set-json", f"mesh.{k}={json.dumps(v)}")
        ]

        skuld = _rendered_skuld_config("skuld", *values)["mesh"]
        planner = _rendered_skuld_config("skuld-planner", *values)["mesh"]

        assert planner == skuld and set(skuld) == set(MESH_KEYS.values())


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
class TestMeshOnNngInACluster:
    """`mesh.enabled: true` alone runs a cluster mesh on nng: discovery, address, labels."""

    def _docs(self, *extra: str) -> list[dict]:
        command = [
            "helm",
            "template",
            "mesh-test",
            str(CHART_DIR),
            "--namespace",
            "sessions",
            "--set",
            "session.id=11111111-2222-3333-4444-555555555555",
            "--set",
            "networkPolicy.enabled=true",
            *extra,
        ]
        return [d for d in yaml.safe_load_all(subprocess.check_output(command)) if d]

    def _kind(self, docs: list[dict], kind: str) -> dict:
        return next(d for d in docs if d["kind"] == kind)

    def test_the_session_config_names_k8s_discovery_and_a_wildcard_bind(self):
        from skuld.config import SkuldSettings

        docs = self._docs("--set", "mesh.enabled=true", "--set", "mesh.realmId=flock-a")
        config = next(
            yaml.safe_load(d["data"]["config.yaml"])
            for d in docs
            if d["kind"] == "ConfigMap" and "config.yaml" in d.get("data", {})
        )
        mesh = SkuldSettings(**config).mesh

        assert mesh.nng.pub_sub_address == "tcp://0.0.0.0:7480"
        assert mesh.discovery_adapters == [
            {"adapter": "k8s", "namespace": "sessions", "nng_port": 7480}
        ]

    def test_the_pod_gets_its_ip_its_realm_label_and_its_peer_id(self):
        docs = self._docs("--set", "mesh.enabled=true", "--set", "mesh.realmId=flock-a")
        pod = self._kind(docs, "Deployment")["spec"]["template"]
        skuld = next(c for c in pod["spec"]["containers"] if c["name"] == "skuld")
        env = {e["name"]: e for e in skuld["env"]}

        assert pod["metadata"]["labels"]["ravn.niuu.world/realm"] == "flock-a"
        assert pod["metadata"]["labels"]["ravn.niuu.world/role"] == "agent"
        assert pod["metadata"]["annotations"]["ravn.niuu.world/peer-id"] == (
            "11111111-2222-3333-4444-555555555555"
        )
        assert env["POD_IP"]["valueFrom"]["fieldRef"]["fieldPath"] == "status.podIP"
        assert env["SKULD__MESH__NNG__ADVERTISE_HOST"]["value"] == "$(POD_IP)"
        names = [e["name"] for e in skuld["env"]]
        assert names.index("POD_IP") < names.index("SKULD__MESH__NNG__ADVERTISE_HOST")
        assert {"name": "mesh-pub", "containerPort": 7480, "protocol": "TCP"} in skuld["ports"]

    def test_the_network_policy_lets_the_realms_pods_reach_the_pub_socket(self):
        docs = self._docs("--set", "mesh.enabled=true", "--set", "mesh.realmId=flock-a")
        rules = self._kind(docs, "NetworkPolicy")["spec"]["ingress"]
        [mesh_rule] = [r for r in rules if r["ports"] == [{"protocol": "TCP", "port": 7480}]]
        assert mesh_rule["from"] == [
            {
                "podSelector": {
                    "matchLabels": {
                        "ravn.niuu.world/realm": "flock-a",
                        "ravn.niuu.world/role": "agent",
                    }
                }
            }
        ]

    def test_the_port_and_the_interval_are_values(self):
        docs = self._docs(
            "--set",
            "mesh.enabled=true",
            "--set",
            "mesh.nng.port=9000",
            "--set",
            "mesh.nng.rediscoverIntervalS=2",
        )
        config = next(
            yaml.safe_load(d["data"]["config.yaml"])
            for d in docs
            if d["kind"] == "ConfigMap" and "config.yaml" in d.get("data", {})
        )
        assert config["mesh"]["nng"] == {
            "pub_sub_address": "tcp://0.0.0.0:9000",
            "rediscover_interval_s": 2,
        }
        assert config["mesh"]["discovery_adapters"][0]["nng_port"] == 9000

    @pytest.mark.parametrize(
        "extra",
        [
            ["--set-json", 'mesh.discoveryAdapters=[{"adapter":"static","peers":[]}]'],
            ["--set", "mesh.discoverPods=false"],
            ["--set", "mesh.transport=nats"],
        ],
        ids=["operator-discovery", "switched-off", "other-transport"],
    )
    def test_naming_discovery_or_another_transport_leaves_the_pod_alone(self, extra):
        docs = self._docs("--set", "mesh.enabled=true", *extra)
        pod = self._kind(docs, "Deployment")["spec"]["template"]
        skuld = next(c for c in pod["spec"]["containers"] if c["name"] == "skuld")

        assert "ravn.niuu.world/realm" not in pod["metadata"]["labels"]
        assert "POD_IP" not in [e["name"] for e in skuld["env"]]
        assert not any(
            "k8s" in json.dumps(r.get("from", []))
            for r in self._kind(docs, "NetworkPolicy")["spec"]["ingress"]
        )

    def test_the_planner_chart_gets_the_same_default(self):
        command = ["helm", "template", "p", str(CHART_DIR.parent / "skuld-planner")]
        command += ["--set", "mesh.enabled=true", "--namespace", "sessions"]
        docs = [d for d in yaml.safe_load_all(subprocess.check_output(command)) if d]
        config = next(
            yaml.safe_load(d["data"]["config.yaml"])
            for d in docs
            if d["kind"] == "ConfigMap" and "config.yaml" in d.get("data", {})
        )
        pod = next(d for d in docs if d["kind"] == "Deployment")["spec"]["template"]
        skuld = next(c for c in pod["spec"]["containers"] if c["name"] == "skuld")

        assert config["mesh"]["discovery_adapters"][0]["adapter"] == "k8s"
        assert pod["metadata"]["labels"]["ravn.niuu.world/role"] == "agent"
        assert "ravn.niuu.world/peer-id" in pod["metadata"]["annotations"]
        assert "POD_IP" in [e["name"] for e in skuld["env"]]
