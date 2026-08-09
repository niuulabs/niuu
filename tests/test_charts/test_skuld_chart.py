"""Tests for Skuld Helm chart templates."""

import shutil
import subprocess
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

    def test_external_api_token_is_loaded_from_secret(self, deployment_yaml):
        """The control-plane token is never rendered into a ConfigMap or plain env value."""
        assert "SKULD__EXTERNAL_API_TOKEN" in deployment_yaml
        assert ".Values.volundr.externalApiTokenSecret.name" in deployment_yaml
        assert ".Values.volundr.externalApiTokenSecret.key" in deployment_yaml
        assert "secretKeyRef" in deployment_yaml

    def test_deployment_renders_flock_pod_additions(self, tmp_path):
        """Render proof for Flux-provided flock sidecars and config writers."""
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
                        "name": "write-ravn-cfg-coder",
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
                        "name": "ravn-coder",
                        "image": "ghcr.io/niuulabs/ravn:test",
                        "env": [{"name": "RAVN_PERSONA", "value": "coder"}],
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
            "write-ravn-cfg-coder"
        ]
        assert pod_spec["initContainers"][0]["securityContext"] == {
            "runAsUser": 1000,
            "runAsGroup": 1000,
            "runAsNonRoot": True,
            "allowPrivilegeEscalation": False,
        }
        containers = {container["name"]: container for container in pod_spec["containers"]}
        assert "skuld" in containers
        assert "ravn-coder" in containers
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


class TestResidentWorkloadIdentityConfigFirst:
    """Workload identity is rendered into config files, not env vars."""

    RESIDENT_VALUES = {
        "resident": {
            "enabled": True,
            "environmentId": "environment-a",
            "name": "Muninn",
            "persona": "product-steward",
            "routeId": "muninn",
            "skuld": {
                "reconnectDelaySeconds": 1,
                "maxReconnectAttempts": 120,
                "sessionReadyTimeoutSeconds": 900,
            },
            "platform": {
                "enabled": True,
                "baseUrl": "http://niuu-volundr.volundr.svc.cluster.local:80",
                "workflowAliases": {
                    "research": {
                        "name": "Research Campaign",
                        "defaults": {"gate_auto_forward_after": ""},
                    }
                },
            },
            "mimir": {
                "sourceTrigger": {"enabled": False, "pollIntervalSeconds": 300},
                "stalenessTrigger": {"enabled": False, "scheduleHours": 24},
            },
        },
        "mimir": {
            "instances": [
                {
                    "name": "shared",
                    "role": "shared",
                    "url": "http://niuu-mimir-shared.volundr.svc.cluster.local",
                }
            ]
        },
        "session": {"model": "gpt-5.6-sol", "reasoningEffort": "high"},
        "volundr": {"apiUrl": "https://volundr.example"},
    }

    @pytest.fixture
    def rendered(self, tmp_path) -> str:
        return _render_skuld_chart(tmp_path, dict(self.RESIDENT_VALUES))

    def _configmaps(self, rendered: str) -> dict[str, dict]:
        return {
            doc["metadata"]["name"]: doc
            for doc in yaml.safe_load_all(rendered)
            if isinstance(doc, dict) and doc.get("kind") == "ConfigMap"
        }

    def test_broker_config_carries_workload_identity_section(self, rendered):
        configmaps = self._configmaps(rendered)
        broker_cfg = next(
            yaml.safe_load(cm["data"]["config.yaml"])
            for cm in configmaps.values()
            if "config.yaml" in cm.get("data", {})
            and "transport_adapter" in cm["data"]["config.yaml"]
        )
        workload = broker_cfg["workload_identity"]
        assert workload["token_file"] == "/var/run/secrets/niuu-workload/token"
        assert workload["exchange_url"] == (
            "https://volundr.example/api/v1/tokens/workload/exchange"
        )
        assert broker_cfg["session"]["model"] == "gpt-5.6-sol"
        assert broker_cfg["session"]["reasoning_effort"] == "high"

    def test_resident_broker_reports_usage_to_resident_runtime(self, rendered):
        configmaps = self._configmaps(rendered)
        broker_cfg = next(
            yaml.safe_load(cm["data"]["config.yaml"])
            for cm in configmaps.values()
            if "config.yaml" in cm.get("data", {})
            and "transport_adapter" in cm["data"]["config.yaml"]
        )
        assert broker_cfg["volundr_api_url"] == "https://volundr.example"
        assert broker_cfg["usage_report_path"] == ("/api/v1/forge/resident-runtimes/muninn/usage")

    def test_resident_replica_count_supports_real_suspend_and_resume(self, tmp_path):
        suspended = dict(self.RESIDENT_VALUES)
        suspended["replicaCount"] = 0
        rendered = _render_skuld_chart(tmp_path, suspended)

        assert _deployment_from_rendered(rendered)["spec"]["replicas"] == 0

    def test_resident_restart_never_overlaps_agent_replicas(self, rendered):
        assert _deployment_from_rendered(rendered)["spec"]["strategy"] == {"type": "Recreate"}

    def test_resident_name_annotation_can_be_supplied_by_control_plane(self, tmp_path):
        values = dict(self.RESIDENT_VALUES)
        values["podAnnotations"] = {
            "niuu.world/resident-name": "managed-resident",
            "niuu.world/resident-id": "resident-id",
        }

        rendered = _render_skuld_chart(tmp_path, values)
        annotations = _deployment_from_rendered(rendered)["spec"]["template"]["metadata"][
            "annotations"
        ]

        assert annotations["niuu.world/resident-name"] == "managed-resident"
        assert annotations["niuu.world/resident-id"] == "resident-id"

    def test_gateway_extracts_browser_websocket_token(self, tmp_path):
        values = dict(self.RESIDENT_VALUES)
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

    def test_pod_has_no_workload_identity_env_vars(self, rendered):
        deployment = _deployment_from_rendered(rendered)
        for container in deployment["spec"]["template"]["spec"]["containers"]:
            env_names = {entry["name"] for entry in container.get("env", [])}
            offending = {n for n in env_names if n.startswith("NIUU_WORKLOAD_IDENTITY")}
            assert not offending, f"{container['name']} still injects {offending}"

    def test_resident_broker_has_no_volundr_api_env_var(self, rendered):
        deployment = _deployment_from_rendered(rendered)
        broker = next(
            c for c in deployment["spec"]["template"]["spec"]["containers"] if c["name"] == "skuld"
        )
        env_names = {entry["name"] for entry in broker.get("env", [])}
        assert "SKULD__VOLUNDR_API_URL" not in env_names

    def test_ravn_container_receives_only_resident_env_secrets(self, tmp_path):
        values = dict(self.RESIDENT_VALUES)
        values["envSecrets"] = [
            {
                "envVar": "BROKER_ONLY",
                "secretName": "broker-secret",
                "secretKey": "value",
            }
        ]
        values["resident"] = {
            **values["resident"],
            "envSecrets": [
                {
                    "envVar": "RAVN_NATS_PASSWORD",
                    "secretName": "flock-nats",
                    "secretKey": "password",
                }
            ],
        }

        rendered = _render_skuld_chart(tmp_path, values)
        deployment = _deployment_from_rendered(rendered)
        ravn = next(
            container
            for container in deployment["spec"]["template"]["spec"]["containers"]
            if container["name"] == "ravn"
        )
        env = {entry["name"]: entry for entry in ravn["env"]}

        assert "BROKER_ONLY" not in env
        assert env["RAVN_NATS_PASSWORD"]["valueFrom"]["secretKeyRef"] == {
            "name": "flock-nats",
            "key": "password",
        }

    def test_ravn_config_carries_platform_workload_fields(self, rendered):
        configmaps = self._configmaps(rendered)
        ravn_cm = next(cm for name, cm in configmaps.items() if name.endswith("-ravn-config"))
        ravn_cfg = yaml.safe_load(ravn_cm["data"]["config.yaml"])
        assert ravn_cfg["environment"]["id"] == "environment-a"
        platform = ravn_cfg["gateway"]["platform"]
        assert platform["enabled"] is True
        assert platform["workload_token_file"] == "/var/run/secrets/niuu-workload/token"
        assert platform["workload_exchange_url"] == (
            "https://volundr.example/api/v1/tokens/workload/exchange"
        )
        assert platform["workflow_aliases"]["research"]["name"] == "Research Campaign"
        assert platform["workflow_aliases"]["research"]["defaults"]["gate_auto_forward_after"] == ""
        skuld = ravn_cfg["skuld"]
        assert skuld["reconnect_delay_seconds"] == 1
        assert skuld["max_reconnect_attempts"] == 120
        assert skuld["session_ready_timeout_seconds"] == 900
        mimir = ravn_cfg["mimir"]
        assert mimir["source_trigger"]["enabled"] is False
        assert mimir["source_trigger"]["poll_interval_seconds"] == 300
        assert mimir["staleness_trigger"]["enabled"] is False
        assert mimir["staleness_trigger"]["schedule_hours"] == 24

    def test_room_and_ravn_share_the_same_environment_identity(self, rendered):
        configmaps = self._configmaps(rendered)
        configs = [
            yaml.safe_load(cm["data"]["config.yaml"])
            for cm in configmaps.values()
            if "config.yaml" in cm.get("data", {})
        ]
        broker_cfg = next(config for config in configs if "transport_adapter" in config)
        ravn_cfg = next(config for config in configs if "persona" in config)

        assert broker_cfg["room"]["environment_id"] == "environment-a"
        assert ravn_cfg["environment"]["id"] == "environment-a"

    def test_ravn_config_can_override_platform_workload_exchange_url(self, tmp_path):
        values = dict(self.RESIDENT_VALUES)
        values["resident"] = {
            **values["resident"],
            "platform": {
                **values["resident"]["platform"],
                "workloadExchangeUrl": "https://yggdrasil.niuu.world/api/v1/tokens/workload/exchange",
            },
        }
        rendered = _render_skuld_chart(tmp_path, values)
        configmaps = self._configmaps(rendered)
        ravn_cm = next(cm for name, cm in configmaps.items() if name.endswith("-ravn-config"))
        ravn_cfg = yaml.safe_load(ravn_cm["data"]["config.yaml"])

        assert (
            ravn_cfg["gateway"]["platform"]["workload_exchange_url"]
            == "https://yggdrasil.niuu.world/api/v1/tokens/workload/exchange"
        )

    def test_resident_wakefulness_is_rendered(self, tmp_path):
        values = dict(self.RESIDENT_VALUES)
        values["resident"] = {
            **values["resident"],
            "wakefulness": {"enabled": True, "silence_threshold_seconds": 900},
        }
        rendered = _render_skuld_chart(tmp_path, values)
        configmaps = self._configmaps(rendered)
        ravn_cm = next(cm for name, cm in configmaps.items() if name.endswith("-ravn-config"))
        ravn_cfg = yaml.safe_load(ravn_cm["data"]["config.yaml"])

        assert ravn_cfg["wakefulness"] == {
            "enabled": True,
            "silence_threshold_seconds": 900,
        }

    def test_default_render_has_no_workload_identity_config(self, tmp_path):
        rendered = _render_skuld_chart(tmp_path, {})
        assert "workload_identity" not in rendered
        assert "NIUU_WORKLOAD_IDENTITY" not in rendered


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

    def test_non_resident_sessions_keep_volundr_reporting(self, tmp_path):
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


class TestResidentObservability:
    """The resident's OTel export must be renderable from values.

    ravn's ObservabilityConfig.enabled defaults to false, so a resident
    rendered without this block emits no traces or metrics at all — the state
    Muninn was found in while every other fleet was reporting.
    """

    def test_observability_is_absent_by_default(self, tmp_path: Path) -> None:
        rendered = _render_skuld_chart(
            tmp_path,
            {"resident": {"enabled": True, "persona": "product-steward"}},
        )
        config = _ravn_config_from_rendered(rendered)
        assert "observability" not in config

    def test_observability_block_is_rendered_when_supplied(self, tmp_path: Path) -> None:
        rendered = _render_skuld_chart(
            tmp_path,
            {
                "resident": {
                    "enabled": True,
                    "persona": "product-steward",
                    "environmentId": "muninn",
                    "observability": {
                        "enabled": True,
                        "service_name": "ravn",
                        "metric_endpoint": "https://mimir.example/valhalla/otlp/v1/metrics",
                        "metric_export_interval_milliseconds": 5000,
                    },
                }
            },
        )
        config = _ravn_config_from_rendered(rendered)
        assert config["observability"]["enabled"] is True
        assert config["observability"]["metric_endpoint"].endswith("/otlp/v1/metrics")
        # The environment id is what the dashboard's environment picker filters on.
        assert config["environment"]["id"] == "muninn"


class TestResidentMemoryPersistence:
    """The memory path must be settable, or episodes die with the pod.

    The chart's default workspace is an emptyDir for residents, and ravn's
    memory defaults to $HOME/.ravn/memory.db inside it — so a resident's entire
    episodic history was erased on every restart, with prefetch reporting an
    honest but useless zero hit rate against an empty corpus.
    """

    def test_memory_is_absent_by_default(self, tmp_path: Path) -> None:
        rendered = _render_skuld_chart(
            tmp_path, {"resident": {"enabled": True, "persona": "product-steward"}}
        )
        assert "memory" not in _ravn_config_from_rendered(rendered)

    def test_memory_path_can_be_pointed_at_a_persistent_mount(self, tmp_path: Path) -> None:
        rendered = _render_skuld_chart(
            tmp_path,
            {
                "resident": {
                    "enabled": True,
                    "persona": "product-steward",
                    "memory": {"backend": "sqlite", "path": "/volundr/home/.ravn/memory.db"},
                }
            },
        )
        config = _ravn_config_from_rendered(rendered)
        assert config["memory"]["path"] == "/volundr/home/.ravn/memory.db"
        assert config["memory"]["backend"] == "sqlite"


class TestRavnHomeVolume:
    """Ravn must see the persistent home claim, not only the emptyDir workspace.

    Making the memory path configurable achieves nothing if the only volume
    the ravn container can write to is erased with the pod.
    """

    def test_ravn_mounts_the_home_volume_when_enabled(self, tmp_path: Path) -> None:
        rendered = _render_skuld_chart(
            tmp_path,
            {
                "resident": {"enabled": True, "persona": "product-steward"},
                "homeVolume": {
                    "enabled": True,
                    "existingClaim": "some-home-claim",
                    "mountPath": "/volundr/home",
                },
            },
        )
        deployment = _deployment_from_rendered(rendered)
        ravn = next(
            c for c in deployment["spec"]["template"]["spec"]["containers"] if c["name"] == "ravn"
        )
        paths = {m["mountPath"] for m in ravn["volumeMounts"]}
        assert "/volundr/home" in paths
        assert "/workspace" in paths

    def test_ravn_has_no_home_mount_when_disabled(self, tmp_path: Path) -> None:
        rendered = _render_skuld_chart(
            tmp_path,
            {
                "resident": {"enabled": True, "persona": "product-steward"},
                "homeVolume": {"enabled": False},
            },
        )
        deployment = _deployment_from_rendered(rendered)
        ravn = next(
            c for c in deployment["spec"]["template"]["spec"]["containers"] if c["name"] == "ravn"
        )
        assert all("home" not in m["mountPath"] for m in ravn["volumeMounts"])


def _ravn_config_from_rendered(rendered_yaml: str) -> dict:
    for document in yaml.safe_load_all(rendered_yaml):
        if not isinstance(document, dict) or document.get("kind") != "ConfigMap":
            continue
        body = (document.get("data") or {}).get("config.yaml")
        if body and "environment:" in body:
            return yaml.safe_load(body)
    pytest.fail("ravn config.yaml was not rendered")
    raise AssertionError("ravn config.yaml was not rendered")


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
