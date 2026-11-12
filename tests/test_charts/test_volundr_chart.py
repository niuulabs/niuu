"""Tests for Volundr Helm chart templates."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).parent.parent.parent / "charts" / "volundr"
BIFROST_URL = ("--set", "bifrost.url=http://bifrost.test")
#: The smallest cluster setup for durable presented files: the quota and a signing key
#: for session credentials, which then follow into session pods by themselves.
WORKLOAD_SIGNING_KEY = (
    "--set",
    "workloadIdentity.enabled=true",
    "--set",
    "workloadIdentity.signingKey.existingSecret=wi-key",
)
PRESENTED_FILES_ON = (
    "--set",
    "presentedFiles.enabled=true",
    "--set",
    "presentedFiles.maxTotalBytes=5368709120",
    *WORKLOAD_SIGNING_KEY,
    "--set",
    "podManager.kwargs.namespace=volundr-sessions",
)


class TestChartMetadata:
    """Tests for Chart.yaml."""

    @pytest.fixture
    def chart_yaml(self) -> dict:
        """Load Chart.yaml."""
        chart_path = CHART_DIR / "Chart.yaml"
        return yaml.safe_load(chart_path.read_text())

    def test_chart_name(self, chart_yaml):
        """Test chart name is volundr."""
        assert chart_yaml["name"] == "volundr"

    def test_chart_version(self, chart_yaml):
        """Test chart has version."""
        assert "version" in chart_yaml
        assert chart_yaml["version"]


class TestEnvoySidecarConfig:
    """Tests for the Envoy sidecar configmap template."""

    @pytest.fixture
    def envoy_template(self) -> str:
        """Load the raw envoy configmap template text."""
        template_path = CHART_DIR / "templates" / "envoy-configmap.yaml"
        return template_path.read_text()

    def test_websocket_upgrades_enabled(self, envoy_template):
        """Browser session WebSockets (/s/{id}/session) terminate at this
        sidecar; without upgrade_configs Envoy rejects every Upgrade request
        with 403 before JWT auth or routing runs."""
        assert "upgrade_configs:" in envoy_template
        assert "- upgrade_type: websocket" in envoy_template


class TestValuesDefaults:
    """Tests for values.yaml defaults."""

    @pytest.fixture
    def values_yaml(self) -> dict:
        """Load values.yaml."""
        values_path = CHART_DIR / "values.yaml"
        return yaml.safe_load(values_path.read_text())

    def test_storage_configured(self, values_yaml):
        """Test storage is configured."""
        storage = values_yaml["storage"]["sessions"]
        assert storage["storageClass"] == "longhorn"
        assert storage["accessMode"] == "ReadWriteMany"
        assert storage["size"] == "1Gi"

    def test_existing_secrets_configured(self, values_yaml):
        """Test existing secrets are configured."""
        assert values_yaml["existingSecrets"]["anthropic"] == "volundr-anthropic-api"

    def test_skuld_claude_session_definition_enabled(self, values_yaml):
        """Test skuld-claude session definition is enabled by default."""
        skuld = values_yaml["sessionDefinitions"]["skuldClaude"]
        assert skuld["enabled"] is True
        assert skuld["active"] is True

    def test_skuld_codex_session_definition_disabled_by_default(self, values_yaml):
        """Test skuld-codex session definition is disabled by default."""
        codex = values_yaml["sessionDefinitions"]["skuldCodex"]
        assert codex["enabled"] is False

    def test_skuld_claude_session_definition_labels(self, values_yaml):
        """Test skuld-claude session definition has routing labels."""
        skuld = values_yaml["sessionDefinitions"]["skuldClaude"]
        assert "session" in skuld["labels"]

    def test_skuld_claude_interactive_session_definition_enabled(self, values_yaml):
        """Test skuld-claude-interactive session definition is enabled."""
        skuld = values_yaml["sessionDefinitions"]["skuldClaudeInteractive"]
        assert skuld["enabled"] is True
        assert skuld["active"] is True
        assert "interactive" in skuld["labels"]

    def test_skuld_claude_defaults_session_model(self, values_yaml):
        """Test skuld-claude defaults include session model."""
        defaults = values_yaml["sessionDefinitions"]["skuldClaude"]["defaults"]
        assert defaults["session"]["model"] == "claude-sonnet-4-6"

    def test_skuld_codex_defaults_session_model(self, values_yaml):
        """Test skuld-codex defaults have a model field."""
        defaults = values_yaml["sessionDefinitions"]["skuldCodex"]["defaults"]
        assert "model" in defaults["session"]

    def test_skuld_claude_defaults_image_repository(self, values_yaml):
        """Test skuld-claude defaults have correct image repository."""
        defaults = values_yaml["sessionDefinitions"]["skuldClaude"]["defaults"]
        assert defaults["image"]["repository"] == "ghcr.io/niuulabs/skuld"

    def test_skuld_codex_defaults_image_repository(self, values_yaml):
        """Test skuld-codex uses the merged skuld image."""
        defaults = values_yaml["sessionDefinitions"]["skuldCodex"]["defaults"]
        assert defaults["image"]["repository"] == "ghcr.io/niuulabs/skuld"

    def test_session_definition_defaults_do_not_own_volundr_api_url(self, values_yaml):
        """Deployment config owns Volundr API URLs through podManager.session_defaults."""
        for definition in values_yaml["sessionDefinitions"].values():
            defaults = definition.get("defaults") or {}
            volundr = defaults.get("volundr") or {}
            assert "apiUrl" not in volundr

    def test_skuld_claude_helm_repo_configured(self, values_yaml):
        """Test skuld-claude helm repo is configured for OCI."""
        helm = values_yaml["sessionDefinitions"]["skuldClaude"]["helm"]
        assert helm["repo"] == "oci://ghcr.io/niuulabs/charts"
        assert helm["chart"] == "skuld"

    def test_skuld_codex_uses_same_skuld_chart(self, values_yaml):
        """Test skuld-codex references the same skuld chart."""
        helm = values_yaml["sessionDefinitions"]["skuldCodex"]["helm"]
        assert helm["chart"] == "skuld"
        assert helm["repo"] == "oci://ghcr.io/niuulabs/charts"

    def test_skuld_claude_defaults_resources(self, values_yaml):
        """Test skuld-claude defaults have resource limits."""
        defaults = values_yaml["sessionDefinitions"]["skuldClaude"]["defaults"]
        assert "requests" in defaults["resources"]
        assert "limits" in defaults["resources"]

    def test_skuld_claude_defaults_ingress(self, values_yaml):
        """Test skuld-claude defaults have ingress configuration."""
        ingress = values_yaml["sessionDefinitions"]["skuldClaude"]["defaults"]["ingress"]
        assert ingress["className"] == ""
        assert ingress["annotations"] == {}

    def test_skuld_claude_defaults_persistence(self, values_yaml):
        """Test skuld-claude defaults have persistence configuration."""
        persistence = values_yaml["sessionDefinitions"]["skuldClaude"]["defaults"]["persistence"]
        assert persistence["mountPath"] == "/volundr/sessions"

    def test_skuld_claude_defaults_security_context(self, values_yaml):
        """Test skuld-claude defaults have security context."""
        ctx = values_yaml["sessionDefinitions"]["skuldClaude"]["defaults"]["securityContext"]
        assert ctx["runAsNonRoot"] is True
        assert ctx["runAsUser"] == 1000
        assert ctx["fsGroup"] == 1000

    def test_skuld_claude_credential_files_dest_dir(self, values_yaml):
        """Test skuld-claude uses .claude as credential destDir."""
        hv = values_yaml["sessionDefinitions"]["skuldClaude"]["defaults"]["homeVolume"]
        assert hv["credentialFiles"]["destDir"] == ".claude"

    def test_skuld_codex_credential_files_dest_dir(self, values_yaml):
        """Test skuld-codex uses .codex as credential destDir."""
        hv = values_yaml["sessionDefinitions"]["skuldCodex"]["defaults"]["homeVolume"]
        assert hv["credentialFiles"]["destDir"] == ".codex"

    def test_skuld_claude_broker_cli_type(self, values_yaml):
        """Test skuld-claude broker cliType is claude."""
        broker = values_yaml["sessionDefinitions"]["skuldClaude"]["defaults"]["broker"]
        assert broker["cliType"] == "claude"

    def test_skuld_claude_broker_transport_adapter(self, values_yaml):
        """Test skuld-claude broker defaults to the SDK adapter."""
        broker = values_yaml["sessionDefinitions"]["skuldClaude"]["defaults"]["broker"]
        assert broker["transport"] == "sdk"
        assert broker["transportAdapter"] == "skuld.transports.sdk.SDKTransport"

    def test_skuld_claude_interactive_broker_transport_adapter(self, values_yaml):
        """Test skuld-claude-interactive broker uses the tmux adapter."""
        broker = values_yaml["sessionDefinitions"]["skuldClaudeInteractive"]["defaults"]["broker"]
        assert broker["transport"] == "tmux-interactive"
        assert (
            broker["transportAdapter"]
            == "skuld.transports.tmux_interactive.TmuxInteractiveTransport"
        )
        assert broker["skipPermissions"] is True

    def test_skuld_codex_broker_cli_type(self, values_yaml):
        """Test skuld-codex broker cliType is codex-ws (WebSocket transport)."""
        broker = values_yaml["sessionDefinitions"]["skuldCodex"]["defaults"]["broker"]
        assert broker["cliType"] == "codex-ws"

    def test_skuld_codex_broker_transport_adapter(self, values_yaml):
        """Test skuld-codex broker has correct transportAdapter class path."""
        broker = values_yaml["sessionDefinitions"]["skuldCodex"]["defaults"]["broker"]
        assert broker["transportAdapter"] == "skuld.transports.codex_ws.CodexWebSocketTransport"

    @pytest.mark.parametrize(
        "definition",
        ["skuldClaude", "skuldClaudeInteractive", "skuldCodex", "skuldOpenCode"],
    )
    def test_agent_session_definitions_default_to_full_access(self, values_yaml, definition):
        broker = values_yaml["sessionDefinitions"][definition]["defaults"]["broker"]
        assert broker["skipPermissions"] is True

    def test_both_session_defs_use_same_image_repo(self, values_yaml):
        """Test skuld-claude and skuld-codex reference the same image repo."""
        defs = values_yaml["sessionDefinitions"]
        claude_repo = defs["skuldClaude"]["defaults"]["image"]["repository"]
        codex_repo = defs["skuldCodex"]["defaults"]["image"]["repository"]
        assert claude_repo == codex_repo == "ghcr.io/niuulabs/skuld"

    def test_pod_manager_default_chart_name_is_skuld(self, values_yaml):
        """Test podManager default chart_name is skuld."""
        assert values_yaml["podManager"]["kwargs"]["chart_name"] == "skuld"

    def test_skuld_claude_env_secrets_has_anthropic_key(self, values_yaml):
        """Test skuld-claude defaults have ANTHROPIC_API_KEY in envSecrets."""
        secrets = values_yaml["sessionDefinitions"]["skuldClaude"]["defaults"]["envSecrets"]
        assert isinstance(secrets, list)
        assert len(secrets) == 1
        assert secrets[0]["envVar"] == "ANTHROPIC_API_KEY"

    def test_skuld_codex_has_no_static_auth_projection(self, values_yaml):
        """Codex subscription auth comes from the access-only broker."""
        secrets = values_yaml["sessionDefinitions"]["skuldCodex"]["defaults"]["envSecrets"]
        credential_files = values_yaml["sessionDefinitions"]["skuldCodex"]["defaults"][
            "homeVolume"
        ]["credentialFiles"]
        assert secrets == []
        assert credential_files["secretName"] == ""


class TestHelpersTemplate:
    """Tests for _helpers.tpl template."""

    @pytest.fixture
    def helpers_tpl(self) -> str:
        """Load _helpers.tpl template."""
        template_path = CHART_DIR / "templates" / "_helpers.tpl"
        return template_path.read_text()

    def test_has_fullname_helper(self, helpers_tpl):
        """Test helpers has fullname function."""
        assert 'define "volundr.fullname"' in helpers_tpl

    def test_has_labels_helper(self, helpers_tpl):
        """Test helpers has labels function."""
        assert 'define "volundr.labels"' in helpers_tpl

    def test_has_sessions_pvc_name_helper(self, helpers_tpl):
        """Test helpers has sessionsPvcName function."""
        assert 'define "volundr.sessionsPvcName"' in helpers_tpl

    def test_has_service_account_name_helper(self, helpers_tpl):
        """Test helpers has serviceAccountName function."""
        assert 'define "volundr.serviceAccountName"' in helpers_tpl

    def test_has_image_helper(self, helpers_tpl):
        """Test helpers has image function."""
        assert 'define "volundr.image"' in helpers_tpl

    def test_has_database_secret_name_helper(self, helpers_tpl):
        """Test helpers has databaseSecretName function."""
        assert 'define "volundr.databaseSecretName"' in helpers_tpl

    def test_has_database_host_helper(self, helpers_tpl):
        """Test helpers has databaseHost function."""
        assert 'define "volundr.databaseHost"' in helpers_tpl

    def test_has_checksum_annotations_helper(self, helpers_tpl):
        """Test helpers has checksumAnnotations function."""
        assert 'define "volundr.checksumAnnotations"' in helpers_tpl


class TestDeploymentTemplate:
    """Tests for deployment.yaml template."""

    @pytest.fixture
    def template_yaml(self) -> str:
        """Load deployment.yaml template."""
        template_path = CHART_DIR / "templates" / "deployment.yaml"
        return template_path.read_text()

    def test_has_correct_api_version(self, template_yaml):
        """Test template uses correct API version."""
        assert "apiVersion: apps/v1" in template_yaml

    def test_has_correct_kind(self, template_yaml):
        """Test template uses correct kind."""
        assert "kind: Deployment" in template_yaml

    def test_has_name_with_fullname(self, template_yaml):
        """Test template uses fullname helper for name."""
        assert 'include "volundr.fullname"' in template_yaml

    def test_has_labels(self, template_yaml):
        """Test template includes labels."""
        assert 'include "volundr.labels"' in template_yaml

    def test_has_selector_labels(self, template_yaml):
        """Test template includes selector labels."""
        assert 'include "volundr.selectorLabels"' in template_yaml

    def test_has_service_account_name(self, template_yaml):
        """Test template uses service account helper."""
        assert 'include "volundr.serviceAccountName"' in template_yaml

    def test_has_image_helper(self, template_yaml):
        """Test template uses image helper."""
        assert 'include "volundr.image"' in template_yaml

    def test_has_liveness_probe(self, template_yaml):
        """Test template has liveness probe."""
        assert "livenessProbe:" in template_yaml
        assert ".Values.livenessProbe.enabled" in template_yaml

    def test_has_readiness_probe(self, template_yaml):
        """Test template has readiness probe."""
        assert "readinessProbe:" in template_yaml
        assert ".Values.readinessProbe.enabled" in template_yaml

    def test_has_resources(self, template_yaml):
        """Test template includes resources."""
        assert ".Values.resources" in template_yaml

    def test_has_security_context(self, template_yaml):
        """Test template includes security context."""
        assert ".Values.securityContext" in template_yaml
        assert ".Values.podSecurityContext" in template_yaml

    def test_has_database_env_vars(self, template_yaml):
        """Test template has database environment variables."""
        assert "DATABASE__HOST" in template_yaml
        assert "DATABASE__PORT" in template_yaml
        assert "DATABASE__NAME" in template_yaml
        assert "DATABASE__USER" in template_yaml
        assert "DATABASE__PASSWORD" in template_yaml

    def test_has_pod_manager_token_env_var(self, template_yaml):
        """Test template has pod manager token env var."""
        assert "POD_MANAGER_TOKEN" in template_yaml

    def test_has_configmap_volume(self, template_yaml):
        """Test template has configmap volume."""
        assert "configMap:" in template_yaml

    def test_has_autoscaling_conditional(self, template_yaml):
        """Test template has conditional for autoscaling."""
        assert ".Values.autoscaling.enabled" in template_yaml

    def test_has_strategy(self, template_yaml):
        """Test template includes deployment strategy."""
        assert ".Values.strategy" in template_yaml


class TestServiceTemplate:
    """Tests for service.yaml template."""

    @pytest.fixture
    def template_yaml(self) -> str:
        """Load service.yaml template."""
        template_path = CHART_DIR / "templates" / "service.yaml"
        return template_path.read_text()

    def test_has_correct_api_version(self, template_yaml):
        """Test template uses correct API version."""
        assert "apiVersion: v1" in template_yaml

    def test_has_correct_kind(self, template_yaml):
        """Test template uses correct kind."""
        assert "kind: Service" in template_yaml

    def test_has_name_with_fullname(self, template_yaml):
        """Test template uses fullname helper for name."""
        assert 'include "volundr.fullname"' in template_yaml

    def test_has_selector_labels(self, template_yaml):
        """Test template includes selector labels."""
        assert 'include "volundr.selectorLabels"' in template_yaml

    def test_has_service_type(self, template_yaml):
        """Test template has service type."""
        assert ".Values.service.type" in template_yaml

    def test_has_port_configuration(self, template_yaml):
        """Test template has port configuration."""
        assert ".Values.service.port" in template_yaml
        assert "targetPort:" in template_yaml


class TestIngressTemplate:
    """Tests for ingress.yaml template."""

    @pytest.fixture
    def template_yaml(self) -> str:
        """Load ingress.yaml template."""
        template_path = CHART_DIR / "templates" / "ingress.yaml"
        return template_path.read_text()

    def test_has_conditional_enabled(self, template_yaml):
        """Test template is conditionally enabled."""
        assert ".Values.ingress.enabled" in template_yaml

    def test_has_correct_api_version(self, template_yaml):
        """Test template uses correct API version."""
        assert "apiVersion: networking.k8s.io/v1" in template_yaml

    def test_has_correct_kind(self, template_yaml):
        """Test template uses correct kind."""
        assert "kind: Ingress" in template_yaml

    def test_has_ingress_class_name(self, template_yaml):
        """Test template has ingress class name."""
        assert ".Values.ingress.className" in template_yaml

    def test_has_tls_configuration(self, template_yaml):
        """Test template has TLS configuration."""
        assert ".Values.ingress.tls" in template_yaml

    def test_has_hosts_configuration(self, template_yaml):
        """Test template has hosts configuration."""
        assert ".Values.ingress.hosts" in template_yaml


class TestServiceAccountTemplate:
    """Tests for serviceaccount.yaml template."""

    @pytest.fixture
    def template_yaml(self) -> str:
        """Load serviceaccount.yaml template."""
        template_path = CHART_DIR / "templates" / "serviceaccount.yaml"
        return template_path.read_text()

    def test_has_conditional_create(self, template_yaml):
        """Test template is conditionally created."""
        assert ".Values.serviceAccount.create" in template_yaml

    def test_has_correct_api_version(self, template_yaml):
        """Test template uses correct API version."""
        assert "apiVersion: v1" in template_yaml

    def test_has_correct_kind(self, template_yaml):
        """Test template uses correct kind."""
        assert "kind: ServiceAccount" in template_yaml

    def test_has_automount_token(self, template_yaml):
        """Test template has automount token setting."""
        assert "automountServiceAccountToken" in template_yaml


class TestRbacTemplate:
    """Tests for rbac.yaml template."""

    @pytest.fixture
    def template_yaml(self) -> str:
        """Load rbac.yaml template."""
        template_path = CHART_DIR / "templates" / "rbac.yaml"
        return template_path.read_text()

    def test_has_conditional_create(self, template_yaml):
        """Test template is conditionally created."""
        assert ".Values.rbac.create" in template_yaml

    def test_has_role(self, template_yaml):
        """Test template creates Role."""
        assert "kind: Role" in template_yaml

    def test_has_role_binding(self, template_yaml):
        """Test template creates RoleBinding."""
        assert "kind: RoleBinding" in template_yaml

    def test_has_flux_api_group(self, template_yaml):
        """Test template has Flux API group permissions."""
        flux_api_group = "helm.toolkit.fluxcd.io"
        assert f'apiGroups: ["{flux_api_group}"]' in template_yaml

    def test_has_cluster_wide_conditional(self, template_yaml):
        """Test template has cluster-wide conditional."""
        assert ".Values.rbac.clusterWide" in template_yaml

    @staticmethod
    def _render_rbac(*overrides: str) -> list[dict]:
        result = subprocess.run(
            [
                "helm",
                "template",
                "test",
                str(CHART_DIR),
                *BIFROST_URL,
                "--namespace",
                "forge",
                "--set",
                "rbac.clusterWide=true",
                *overrides,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return [doc for doc in yaml.safe_load_all(result.stdout) if doc]

    @staticmethod
    def _secret_verbs(document: dict) -> list[list[str]]:
        return [
            rule["verbs"] for rule in document.get("rules", []) if "secrets" in rule["resources"]
        ]

    @pytest.mark.parametrize("enabled", ["true", "false"])
    def test_cluster_role_never_grants_secrets_for_execution_credentials(self, enabled):
        """Per-session bearer Secrets live in the release namespace only, so a
        cluster-wide Secret write grant would be pure excess privilege."""
        documents = self._render_rbac("--set", f"workflowExecutionCredentials.enabled={enabled}")
        cluster_roles = [doc for doc in documents if doc.get("kind") == "ClusterRole"]

        assert cluster_roles
        for cluster_role in cluster_roles:
            assert self._secret_verbs(cluster_role) == []

    def test_namespaced_role_grants_secret_writes_for_execution_credentials(self):
        documents = self._render_rbac("--set", "workflowExecutionCredentials.enabled=true")
        role = next(
            doc
            for doc in documents
            if doc.get("kind") == "Role" and doc["metadata"]["name"] == "test-volundr"
        )

        assert role["metadata"].get("namespace", "forge") == "forge"
        assert self._secret_verbs(role) == [["get", "list", "watch", "create", "patch", "delete"]]

    def test_namespaced_role_is_read_only_on_secrets_without_execution_credentials(self):
        documents = self._render_rbac()
        role = next(
            doc
            for doc in documents
            if doc.get("kind") == "Role" and doc["metadata"]["name"] == "test-volundr"
        )

        assert self._secret_verbs(role) == [["get", "list", "watch"]]

    @staticmethod
    def _namespaced_role(*overrides: str) -> dict:
        documents = TestRbacTemplate._render_rbac("--set", "rbac.clusterWide=false", *overrides)
        assert not [doc for doc in documents if doc.get("kind") == "ClusterRole"]
        return next(
            doc
            for doc in documents
            if doc.get("kind") == "Role" and doc["metadata"]["name"] == "test-volundr"
        )

    @staticmethod
    def _verbs_for(document: dict, resource: str) -> list[list[str]]:
        return [rule["verbs"] for rule in document["rules"] if resource in rule["resources"]]

    def test_namespaced_role_grants_openbao_session_resources(self):
        """OpenBao injection creates a ServiceAccount and an agent ConfigMap per
        session; a namespaced release must be allowed to write both."""
        role = self._namespaced_role(
            "--set",
            "secretInjection.adapter="
            "volundr.adapters.outbound.openbao_secret_injection.OpenBaoAgentInjectionAdapter",
        )

        writes = ["get", "list", "watch", "create", "update", "patch", "delete"]
        assert self._verbs_for(role, "serviceaccounts") == [writes]
        assert self._verbs_for(role, "configmaps") == [writes]

    def test_namespaced_role_grants_infisical_configmaps_only(self):
        role = self._namespaced_role(
            "--set",
            "secretInjection.adapter="
            "volundr.adapters.outbound.infisical_secret_injection.InfisicalAgentInjectionAdapter",
        )

        assert self._verbs_for(role, "serviceaccounts") == []
        assert self._verbs_for(role, "configmaps") == [
            ["get", "list", "watch", "create", "update", "patch", "delete"]
        ]

    DIRECT = "volundr.adapters.outbound.direct_k8s_pod_manager.DirectK8sPodManager"

    def test_namespaced_role_may_manage_roles_for_the_direct_pod_manager_only(self):
        """The direct pod manager gives a session whose mesh lists pods a Role and binding."""
        role = self._namespaced_role("--set", f"podManager.adapter={self.DIRECT}")
        assert self._verbs_for(role, "roles") == [["get", "create", "patch", "delete"]]
        assert self._verbs_for(role, "rolebindings") == [["get", "create", "patch", "delete"]]
        # It may only grant what it holds: reading pods.
        assert ["get", "list", "watch"] in self._verbs_for(role, "pods")

        assert self._verbs_for(self._namespaced_role(), "roles") == []

    def test_cluster_wide_release_gets_the_same_grant_only_for_the_direct_pod_manager(self):
        documents = self._render_rbac("--set", f"podManager.adapter={self.DIRECT}")
        cluster_role = next(doc for doc in documents if doc.get("kind") == "ClusterRole")
        assert self._verbs_for(cluster_role, "rolebindings") == [
            ["get", "create", "patch", "delete"]
        ]

        documents = self._render_rbac()
        cluster_role = next(doc for doc in documents if doc.get("kind") == "ClusterRole")
        assert self._verbs_for(cluster_role, "rolebindings") == []

    def test_namespaced_role_reads_only_without_agent_injection(self):
        role = self._namespaced_role()

        assert self._verbs_for(role, "serviceaccounts") == []
        assert self._verbs_for(role, "configmaps") == [["get", "list", "watch"]]

    def test_cluster_wide_openbao_keeps_writes_on_the_cluster_role(self):
        documents = self._render_rbac(
            "--set",
            "secretInjection.adapter="
            "volundr.adapters.outbound.openbao_secret_injection.OpenBaoAgentInjectionAdapter",
        )
        role = next(
            doc
            for doc in documents
            if doc.get("kind") == "Role" and doc["metadata"]["name"] == "test-volundr"
        )
        cluster_role = next(doc for doc in documents if doc.get("kind") == "ClusterRole")

        assert self._verbs_for(role, "serviceaccounts") == []
        assert self._verbs_for(role, "configmaps") == [["get", "list", "watch"]]
        assert self._verbs_for(cluster_role, "serviceaccounts") == [
            ["get", "list", "watch", "create", "update", "patch", "delete"]
        ]


class TestConfigMapTemplate:
    """Tests for configmap.yaml template."""

    @pytest.fixture
    def template_yaml(self) -> str:
        """Load configmap.yaml template."""
        template_path = CHART_DIR / "templates" / "configmap.yaml"
        return template_path.read_text()

    def test_has_correct_api_version(self, template_yaml):
        """Test template uses correct API version."""
        assert "apiVersion: v1" in template_yaml

    def test_has_correct_kind(self, template_yaml):
        """Test template uses correct kind."""
        assert "kind: ConfigMap" in template_yaml

    def test_has_log_level(self, template_yaml):
        """Test template has LOG_LEVEL."""
        assert "LOG_LEVEL" in template_yaml

    def test_has_host_and_port(self, template_yaml):
        """Test template has HOST and PORT."""
        assert "HOST:" in template_yaml
        assert "PORT:" in template_yaml

    def test_empty_resident_profiles_render_as_list(self):
        result = subprocess.run(
            ["helm", "template", "test", str(CHART_DIR), *BIFROST_URL],
            check=True,
            capture_output=True,
            text=True,
        )
        documents = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
        configmap = next(
            doc
            for doc in documents
            if doc.get("kind") == "ConfigMap"
            and doc.get("metadata", {}).get("name") == "test-volundr"
        )
        config = yaml.safe_load(configmap["data"]["config.yaml"])

        assert config["resident_runtimes"]["profiles"] == []

    def test_missing_bifrost_url_fails_the_render_and_names_the_value(self):
        result = subprocess.run(
            ["helm", "template", "test", str(CHART_DIR)],
            capture_output=True,
            text=True,
        )

        assert result.returncode != 0
        assert "bifrost.url must be the URL of the Bifrost service" in result.stderr

    def test_bifrost_url_is_rendered_with_tpl(self):
        result = subprocess.run(
            [
                "helm",
                "template",
                "test",
                str(CHART_DIR),
                "--set",
                "bifrost.url=http://{{ .Release.Name }}-bifrost",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        configmap = next(
            doc
            for doc in yaml.safe_load_all(result.stdout)
            if doc
            and doc.get("kind") == "ConfigMap"
            and doc.get("metadata", {}).get("name") == "test-volundr"
        )

        assert yaml.safe_load(configmap["data"]["config.yaml"])["bifrost"]["url"] == (
            "http://test-bifrost"
        )

    def test_resident_a2a_settings_render_into_config(self):
        from volundr.config import ResidentRuntimesConfig

        def render(*extra: str) -> dict:
            result = subprocess.run(
                ["helm", "template", "test", str(CHART_DIR), *BIFROST_URL, *extra],
                check=True,
                capture_output=True,
                text=True,
            )
            documents = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
            configmap = next(
                doc
                for doc in documents
                if doc.get("kind") == "ConfigMap"
                and doc.get("metadata", {}).get("name") == "test-volundr"
            )
            return yaml.safe_load(configmap["data"]["config.yaml"])["resident_runtimes"]

        default = ResidentRuntimesConfig.model_validate(render())
        assert default.a2a_public_base_url == ""
        assert default.a2a_reply_timeout_seconds == 45

        configured = ResidentRuntimesConfig.model_validate(
            render(
                "--set",
                "residentA2APublicBaseUrl=https://guild.example",
                "--set",
                "residentA2AReplyTimeoutSeconds=20",
            )
        )
        assert configured.a2a_public_base_url == "https://guild.example"
        assert configured.a2a_reply_timeout_seconds == 20

    def test_artifact_and_presented_file_sections_render_into_config(self):
        from volundr.config import ArtifactsConfig, HostIdentityConfig, PresentedFilesConfig

        def render(*extra: str) -> dict:
            result = subprocess.run(
                ["helm", "template", "test", str(CHART_DIR), *BIFROST_URL, *extra],
                check=True,
                capture_output=True,
                text=True,
            )
            documents = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
            configmap = next(
                doc
                for doc in documents
                if doc.get("kind") == "ConfigMap"
                and doc.get("metadata", {}).get("name") == "test-volundr"
            )
            return yaml.safe_load(configmap["data"]["config.yaml"])

        default = render()
        assert not {"artifacts", "presented_files", "host_identity"} & set(default)
        config = render(
            "--set-json",
            'artifacts={"hold_seconds":60,"backfill_on_start":true}',
            *PRESENTED_FILES_ON,
            "--set",
            "presentedFiles.publicBaseUrl=https://forge.example.com",
            "--set-json",
            'hostIdentity={"host":"thor","tailnet_name":"thor.t.ts.net"}',
        )
        artifacts = ArtifactsConfig.model_validate(config["artifacts"])
        assert (artifacts.hold_seconds, artifacts.backfill_on_start) == (60, True)
        files = PresentedFilesConfig.model_validate(config["presented_files"])
        assert files.enabled and files.public_base_url == "https://forge.example.com"
        assert files.public_access.value == "authenticated"
        assert files.max_total_bytes == 5368709120 and files.max_bytes == 52428800
        assert files.store == {
            "adapter": "volundr.adapters.outbound.postgres_presented_files."
            "PostgresPresentedFileStore",
            "root": "/volundr/presented-files",
        }
        assert HostIdentityConfig.model_validate(config["host_identity"]).host == "thor"

    def test_notifications_section_renders_into_config(self):
        from volundr.config import NotificationsConfig

        def render(*extra: str) -> dict:
            result = subprocess.run(
                ["helm", "template", "test", str(CHART_DIR), *BIFROST_URL, *extra],
                check=True,
                capture_output=True,
                text=True,
            )
            documents = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
            configmap = next(
                doc
                for doc in documents
                if doc.get("kind") == "ConfigMap"
                and doc.get("metadata", {}).get("name") == "test-volundr"
            )
            return yaml.safe_load(configmap["data"]["config.yaml"])

        assert "notifications" not in render()
        sinks = '[{"name":"ops","adapter":"a.B","url":"https://x"}]'
        config = render(
            "--set-json",
            f'notifications={{"reply_ready":{{"body_chars":400}},"sinks":{sinks}}}',
        )
        parsed = NotificationsConfig.model_validate(config["notifications"])
        assert parsed.reply_ready.body_chars == 400
        assert parsed.sinks[0]["url"] == "https://x"

        delivery = render(
            "--set-json",
            'notifications={"public_web_url":"https://forge.example.com",'
            '"dispatcher":{"max_attempts":5,"default_rate_limit":null},'
            '"integration_sinks":{"telegram":"niuu.adapters.notifications.telegram.'
            'TelegramNotificationSink"},'
            '"sinks":[{"name":"ops","adapter":"a.B","url":"https://x",'
            '"secret_kwargs_env":{"secret":"FORGE_OPS_WEBHOOK_SECRET"}}]}',
        )
        parsed = NotificationsConfig.model_validate(delivery["notifications"])
        assert parsed.public_web_url == "https://forge.example.com"
        assert parsed.dispatcher.max_attempts == 5
        assert parsed.dispatcher.default_rate_limit is None
        assert parsed.sinks[0]["secret_kwargs_env"] == {"secret": "FORGE_OPS_WEBHOOK_SECRET"}

    def test_push_renders_with_the_sink_the_needs_input_push_needs(self):
        from volundr.config import Settings

        def render(*extra: str) -> list[dict]:
            result = subprocess.run(
                ["helm", "template", "test", str(CHART_DIR), *BIFROST_URL, *extra],
                check=True,
                capture_output=True,
                text=True,
            )
            return [doc for doc in yaml.safe_load_all(result.stdout) if doc]

        def config(documents: list[dict]) -> dict:
            configmap = next(
                doc
                for doc in documents
                if doc.get("kind") == "ConfigMap"
                and doc.get("metadata", {}).get("name") == "test-volundr"
            )
            return yaml.safe_load(configmap["data"]["config.yaml"])

        def env(documents: list[dict]) -> dict:
            deployment = next(
                doc
                for doc in documents
                if doc.get("kind") == "Deployment"
                and doc.get("metadata", {}).get("name") == "test-volundr"
            )
            container = deployment["spec"]["template"]["spec"]["containers"][0]
            return {item["name"]: item for item in container.get("env", [])}

        off = config(render())
        assert off["push"]["enabled"] is False
        assert "notifications" not in off
        assert Settings(push=off["push"]).push.adapter.endswith("LoggingNotificationChannel")

        documents = render(
            "--set",
            "push.enabled=true",
            "--set",
            "push.adapter=volundr.adapters.outbound.push_channels.ApnsNotificationChannel",
            "--set-json",
            'push.secretKwargs=[{"kwarg":"private_key","secretName":"forge-apns",'
            '"secretKey":"p8"}]',
        )
        on = config(documents)
        settings = Settings(push=on["push"], notifications=on["notifications"])
        assert settings.push.enabled and settings.push.attention_sink == "push"
        assert settings.push.secret_kwargs_env == {"private_key": "PUSH_SK_PRIVATE_KEY"}
        [sink] = settings.notifications.sinks
        assert sink["name"] == "push" and sink["adapter"].endswith("PushNotificationSink")
        secret = env(documents)["PUSH_SK_PRIVATE_KEY"]["valueFrom"]["secretKeyRef"]
        assert secret == {"name": "forge-apns", "key": "p8"}

        # A sink the operator already configured under that name is kept as is.
        own = config(
            render(
                "--set",
                "push.enabled=true",
                "--set",
                "push.attentionSink=phone",
                "--set-json",
                'notifications={"sinks":[{"name":"phone","label":"Mine","adapter":"a.B"}]}',
            )
        )
        assert own["notifications"]["sinks"] == [
            {"name": "phone", "label": "Mine", "adapter": "a.B"}
        ]
        assert Settings(push=own["push"], notifications=own["notifications"])

    def test_push_gateway_renders_without_the_needs_input_sink(self, tmp_path):
        from tests.helpers.apns_keys import write_p8
        from volundr.config import Settings
        from volundr.domain.push import PushTokenKind

        key = write_p8(tmp_path)
        result = subprocess.run(
            [
                "helm",
                "template",
                "test",
                str(CHART_DIR),
                *BIFROST_URL,
                "--set",
                "push.enabled=true",
                "--set-json",
                'push.apns={"team_id":"ABCDE12345","key_id":"KEY1234567",'
                f'"bundle_id":"com.example.app","private_key_path":"{key}"}}',
                "--set-json",
                'push.gateway={"enabled":true,"guild_url":"http://niuu-guild:8080",'
                '"origin_label":"Forge","alerts":{"rate_limit":null},'
                '"live_activity":{"start":{"enabled":true},"relevance_score":10}}',
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        documents = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
        configmap = next(
            doc
            for doc in documents
            if doc.get("kind") == "ConfigMap"
            and doc.get("metadata", {}).get("name") == "test-volundr"
        )
        config = yaml.safe_load(configmap["data"]["config.yaml"])
        # The gateway alerts for the needs-input moment itself: no push sink is added.
        assert "notifications" not in config
        push = Settings(push=config["push"]).push
        assert push.enabled and push.gateway.enabled
        # One registry: every token kind on this (the gateway's) node, tokens in the
        # credential store.
        assert push.device_registry == "auto"
        assert push.accepted_token_kinds() == set(PushTokenKind)
        # No node pushes the alerts itself, so the gateway alerts for every node.
        assert not push.pushes_alerts_itself()
        assert push.apns.key_id == "KEY1234567"
        assert push.apns.private_key_path == str(key)
        assert push.gateway.guild_url == "http://niuu-guild:8080"
        assert push.gateway.alerts.rate_limit is None
        assert push.gateway.live_activity.start.enabled is True
        assert push.gateway.live_activity.relevance_score == 10

    def test_forge_mcp_section_renders_into_config(self):
        from volundr.config import ForgeMcpConfig

        def render(*extra: str) -> dict:
            result = subprocess.run(
                ["helm", "template", "test", str(CHART_DIR), *BIFROST_URL, *extra],
                check=True,
                capture_output=True,
                text=True,
            )
            documents = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
            configmap = next(
                doc
                for doc in documents
                if doc.get("kind") == "ConfigMap"
                and doc.get("metadata", {}).get("name") == "test-volundr"
            )
            return yaml.safe_load(configmap["data"]["config.yaml"])

        # The read-only root filesystem rules out Forge's default key file: session
        # credentials default on only where Forge has a key it can keep.
        assert render()["forge_mcp"] == {"session_tokens": {"enabled": False}}
        signed = render(*WORKLOAD_SIGNING_KEY)["forge_mcp"]["session_tokens"]
        assert signed["enabled"] is True
        assert signed["projection_adapter"].endswith("KubernetesExecutionCredentialProjection")
        config = render(
            "--set-json",
            'forgeMcp={"default_grants":["message"],"session_tokens":{"ttl_seconds":604800},'
            '"http":{"allowed_origins":["https://forge.example.com"]}}',
        )
        parsed = ForgeMcpConfig.model_validate(config["forge_mcp"])
        assert [grant.value for grant in parsed.default_grants] == ["message"]
        assert parsed.session_tokens.ttl_seconds == 604800
        assert parsed.session_tokens.enabled is False
        assert parsed.http.allowed_origins == ["https://forge.example.com"]

    def test_observability_is_absent_by_default(self):
        """config.observability: {} (the default) must not render a block
        that would validate as enabled: false with no endpoints — Helm's
        `with` treats an empty map as falsy, so the key should be omitted
        entirely, matching ObservabilityConfig's own "unset, not disabled"
        default."""
        result = subprocess.run(
            ["helm", "template", "test", str(CHART_DIR), *BIFROST_URL],
            check=True,
            capture_output=True,
            text=True,
        )
        documents = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
        configmap = next(
            doc
            for doc in documents
            if doc.get("kind") == "ConfigMap"
            and doc.get("metadata", {}).get("name") == "test-volundr"
        )
        config = yaml.safe_load(configmap["data"]["config.yaml"])

        assert "observability" not in config

    def test_observability_block_renders_from_values(self):
        result = subprocess.run(
            [
                "helm",
                "template",
                "test",
                str(CHART_DIR),
                *BIFROST_URL,
                "--set",
                "config.observability.enabled=true",
                "--set",
                "config.observability.trace_endpoint=http://otel-collector:4317",
                "--set",
                "config.observability.metric_endpoint=http://otel-collector:4318/v1/metrics",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        documents = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
        configmap = next(
            doc
            for doc in documents
            if doc.get("kind") == "ConfigMap"
            and doc.get("metadata", {}).get("name") == "test-volundr"
        )
        config = yaml.safe_load(configmap["data"]["config.yaml"])

        assert config["observability"]["enabled"] is True
        assert config["observability"]["trace_endpoint"] == "http://otel-collector:4317"
        assert config["observability"]["metric_endpoint"] == "http://otel-collector:4318/v1/metrics"

    @staticmethod
    def _execution_credentials_config(*overrides: str) -> dict:
        result = subprocess.run(
            [
                "helm",
                "template",
                "test",
                str(CHART_DIR),
                *BIFROST_URL,
                "--namespace",
                "forge",
                "--set",
                "workflowExecutionCredentials.enabled=true",
                *overrides,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        documents = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
        configmap = next(
            doc
            for doc in documents
            if doc.get("kind") == "ConfigMap"
            and doc.get("metadata", {}).get("name") == "test-volundr"
        )
        assert configmap["data"]["config.yaml"].count("projection_kwargs:") == 1
        config = yaml.safe_load(configmap["data"]["config.yaml"])
        return config["workflow_execution_credentials"]

    def test_execution_credential_projection_defaults_to_release_namespace(self):
        config = self._execution_credentials_config()

        assert config["projection_kwargs"] == {"namespace": "forge"}

    def test_execution_credential_projection_namespace_override_is_single_key(self):
        result_config = self._execution_credentials_config(
            "--set",
            "workflowExecutionCredentials.projectionKwargs.namespace=sessions",
            "--set",
            "workflowExecutionCredentials.projectionKwargs.label=x",
        )

        assert result_config["projection_kwargs"] == {"namespace": "sessions", "label": "x"}

    def test_execution_credential_projection_namespace_is_not_duplicated(self):
        result = subprocess.run(
            [
                "helm",
                "template",
                "test",
                str(CHART_DIR),
                *BIFROST_URL,
                "--set",
                "workflowExecutionCredentials.projectionKwargs.namespace=sessions",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        configmap = next(
            doc
            for doc in yaml.safe_load_all(result.stdout)
            if doc
            and doc.get("kind") == "ConfigMap"
            and doc.get("metadata", {}).get("name") == "test-volundr"
        )
        rendered = configmap["data"]["config.yaml"]
        start = rendered.index("projection_kwargs:")
        end = rendered.index("projection_secret_kwargs_env:")

        assert rendered[start:end].count("namespace:") == 1

    @pytest.mark.parametrize(
        "overrides,expected_image",
        [
            (["--set", "image.tag=pr-993"], "ghcr.io/niuulabs/niuu:pr-993"),
            (
                [
                    "--set",
                    "global.image.registry=registry.example,global.image.repository=niuu,global.image.tag=release",
                ],
                "registry.example/niuu:release",
            ),
        ],
    )
    def test_ci_values_run_resident_runtime_migrations(self, overrides, expected_image):
        result = subprocess.run(
            [
                "helm",
                "template",
                "test",
                str(CHART_DIR),
                *BIFROST_URL,
                "-f",
                str(CHART_DIR / "ci-values.yaml"),
                *overrides,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        documents = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
        deployment = next(doc for doc in documents if doc.get("kind") == "Deployment")
        init_containers = deployment["spec"]["template"]["spec"].get("initContainers", [])
        lineage = next(c for c in init_containers if c["name"] == "migration-lineage")
        app = deployment["spec"]["template"]["spec"]["containers"][0]
        assert lineage["image"] == app["image"] == expected_image
        migrations = next(
            container for container in init_containers if container["name"] == "migrate"
        )
        migration_config = next(
            doc
            for doc in documents
            if doc.get("kind") == "ConfigMap"
            and doc.get("metadata", {}).get("name") == "test-volundr-migrations"
        )

        assert migrations["args"][-1] == "up"
        assert "000055_resident_runtimes.up.sql" in migration_config["data"]
        assert "000056_resident_usage.up.sql" in migration_config["data"]
        assert "000058_resident_session_traces.up.sql" in migration_config["data"]
        assert "000059_resident_session_events.up.sql" in migration_config["data"]

        migration_dir = CHART_DIR.parent.parent / "migrations"
        expected_migrations = {path.name for path in migration_dir.glob("*.sql")} | {
            "lineage-aliases.json"
        }
        assert set(migration_config["data"]) == expected_migrations

    def test_resident_session_controllers_render_with_backend_binding(self):
        result = subprocess.run(
            [
                "helm",
                "template",
                "test",
                str(CHART_DIR),
                *BIFROST_URL,
                "--set",
                (
                    "residentRuntimeSessionControllers[0].adapter="
                    "volundr.adapters.outbound.hermes_gateway.HermesResidentSessionController"
                ),
                "--set",
                "residentRuntimeSessionControllers[0].runtimeBackend=openshell",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        documents = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
        configmap = next(
            doc
            for doc in documents
            if doc.get("kind") == "ConfigMap"
            and doc.get("metadata", {}).get("name") == "test-volundr"
        )
        config = yaml.safe_load(configmap["data"]["config.yaml"])

        assert config["resident_runtimes"]["session_controllers"] == [
            {
                "adapter": (
                    "volundr.adapters.outbound.hermes_gateway.HermesResidentSessionController"
                ),
                "runtime_backend": "openshell",
                "optional": False,
                "kwargs": {},
                "secret_kwargs_env": {},
            }
        ]

    def test_resident_session_controller_secret_kwargs_reach_the_container(self):
        result = subprocess.run(
            [
                "helm",
                "template",
                "test",
                str(CHART_DIR),
                *BIFROST_URL,
                "--set",
                (
                    "residentRuntimeSessionControllers[0].adapter="
                    "volundr.adapters.outbound.hermes_gateway.HermesResidentSessionController"
                ),
                "--set",
                "residentRuntimeSessionControllers[0].runtimeBackend=openshell",
                "--set",
                "residentRuntimeSessionControllers[0].secretKwargs[0].kwarg=gateway_token",
                "--set",
                (
                    "residentRuntimeSessionControllers[0].secretKwargs[0].secretName="
                    "volundr-hermes-gateway"
                ),
                "--set",
                "residentRuntimeSessionControllers[0].secretKwargs[0].secretKey=token",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        documents = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
        configmap = next(
            doc
            for doc in documents
            if doc.get("kind") == "ConfigMap"
            and doc.get("metadata", {}).get("name") == "test-volundr"
        )
        config = yaml.safe_load(configmap["data"]["config.yaml"])
        env_name = "RESIDENT_SESSION_CONTROLLER_0_SK_GATEWAY_TOKEN"
        assert config["resident_runtimes"]["session_controllers"][0]["secret_kwargs_env"] == {
            "gateway_token": env_name
        }
        deployment = next(doc for doc in documents if doc.get("kind") == "Deployment")
        env = {
            item["name"]: item
            for container in deployment["spec"]["template"]["spec"]["containers"]
            for item in container.get("env", [])
        }
        assert env[env_name]["valueFrom"]["secretKeyRef"] == {
            "name": "volundr-hermes-gateway",
            "key": "token",
        }

    @staticmethod
    def _rbac(*overrides: str) -> list[dict]:
        result = subprocess.run(
            ["helm", "template", "test", str(CHART_DIR), *BIFROST_URL, *overrides],
            check=True,
            capture_output=True,
            text=True,
        )
        return [doc for doc in yaml.safe_load_all(result.stdout) if doc]

    def test_cluster_wide_flux_may_read_session_pod_events(self):
        """status_detail reads the pods' Warning events. valhalla runs
        cluster-wide RBAC with no events rule: the read got 403 during
        startup reconciliation and Völundr crash-looped."""
        documents = self._rbac(
            "--set",
            "podManager.adapter=volundr.adapters.outbound.flux.FluxPodManager",
            "--set",
            "podManager.kwargs.namespace=skuld",
            "--set",
            "rbac.clusterWide=true",
        )
        cluster_role = next(doc for doc in documents if doc["kind"] == "ClusterRole")
        assert {
            "apiGroups": [""],
            "resources": ["events"],
            "verbs": ["get", "list", "watch"],
        } in cluster_role["rules"]

    _OPENSHELL = "volundr.adapters.outbound.openshell_gateway.OpenShellGatewayPodManager"

    @staticmethod
    def _configmap_kwargs(documents: list[dict], path: tuple[str, ...]) -> dict:
        configmap = next(
            doc
            for doc in documents
            if doc["kind"] == "ConfigMap" and "config.yaml" in (doc.get("data") or {})
        )
        config = yaml.safe_load(configmap["data"]["config.yaml"])
        for key in path:
            config = config[key]
        return config

    def test_openshell_residents_can_always_manage_their_workspace_claims(self):
        """No per-cluster grant: the sandbox namespace follows from the storage adapter."""
        documents = self._rbac(
            "--namespace",
            "volundr",
            "--set",
            f"podManager.adapter={self._OPENSHELL}",
            "--set",
            "storageAdapter.kwargs.namespace=openshell-sandboxes",
        )
        role = next(
            doc
            for doc in documents
            if doc["kind"] == "Role"
            and doc["metadata"]["name"] == "test-volundr-resident-workspaces"
        )
        assert role["metadata"]["namespace"] == "openshell-sandboxes"
        assert role["rules"] == [
            {
                "apiGroups": [""],
                "resources": ["persistentvolumeclaims"],
                "verbs": ["get", "create", "patch", "delete"],
            }
        ]
        binding = next(
            doc
            for doc in documents
            if doc["kind"] == "RoleBinding"
            and doc["metadata"]["name"] == "test-volundr-resident-workspaces"
        )
        assert binding["metadata"]["namespace"] == "openshell-sandboxes"
        assert binding["subjects"] == [
            {"kind": "ServiceAccount", "name": "test-volundr", "namespace": "volundr"}
        ]
        kwargs = self._configmap_kwargs(documents, ("pod_manager", "kwargs"))
        assert kwargs["resident_workspace_namespace"] == "openshell-sandboxes"
        # Size, storage class, access mode and the migration cap are the gateway's own
        # defaults unless an operator sets them: the chart renders none for a Flux default.
        assert "resident_workspace_size" not in kwargs
        assert "resident_workspace_migration_max_size" not in kwargs

    def test_openshell_resident_controller_uses_its_own_workspace_namespace(self):
        documents = self._rbac(
            "--set",
            "podManager.adapter=volundr.adapters.outbound.direct_k8s_pod_manager.DirectK8sPodManager",
            "--set",
            f"residentRuntimeControllers[0].adapter={self._OPENSHELL}",
            "--set",
            "residentRuntimeControllers[0].kwargs.resident_workspace_namespace=agents",
        )
        role = next(
            doc
            for doc in documents
            if doc["kind"] == "Role"
            and doc["metadata"]["name"] == "test-volundr-resident-workspaces"
        )
        assert role["metadata"]["namespace"] == "agents"
        kwargs = self._configmap_kwargs(documents, ("resident_runtimes", "controllers"))[0][
            "kwargs"
        ]
        assert kwargs == {"resident_workspace_namespace": "agents"}

    def test_no_workspace_role_when_openshell_residents_keep_workspaces_in_sandboxes(self):
        documents = self._rbac(
            "--set",
            f"podManager.adapter={self._OPENSHELL}",
            "--set",
            "storageAdapter.kwargs.namespace=openshell-sandboxes",
            "--set",
            "podManager.kwargs.resident_workspace_namespace=",
        )
        names = {doc["metadata"]["name"] for doc in documents}
        assert "test-volundr-resident-workspaces" not in names
        kwargs = self._configmap_kwargs(documents, ("pod_manager", "kwargs"))
        assert kwargs["resident_workspace_namespace"] == ""

    def test_no_workspace_role_without_an_openshell_controller(self):
        documents = self._rbac(
            "--set",
            "podManager.adapter=volundr.adapters.outbound.flux.FluxPodManager",
            "--set",
            "storageAdapter.kwargs.namespace=openshell-sandboxes",
        )
        names = {doc["metadata"]["name"] for doc in documents}
        assert "test-volundr-resident-workspaces" not in names
        kwargs = self._configmap_kwargs(documents, ("pod_manager", "kwargs"))
        assert "resident_workspace_namespace" not in kwargs

    def test_flux_renders_no_resident_roles(self):
        """The Flux resident controller is gone: no shim-token Secret or resident-logs grant."""
        documents = self._rbac(
            "--set",
            "podManager.adapter=volundr.adapters.outbound.flux.FluxPodManager",
            "--set",
            "podManager.kwargs.namespace=skuld",
        )
        names = {doc["metadata"]["name"] for doc in documents}
        assert "test-volundr-resident-openclaw" not in names
        assert "test-volundr-resident-logs" not in names

    def test_ravn_resident_sessions_value_is_gone(self):
        values = yaml.safe_load((CHART_DIR / "values.yaml").read_text())
        assert "ravnResidentSessions" not in values
        deployment = next(doc for doc in self._rbac() if doc["kind"] == "Deployment")
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        names = {item["name"] for item in container.get("env", [])}
        assert "RESIDENT_RUNTIMES__RAVN_SESSIONS_ENABLED" not in names


class TestPreviewCacheStorage:
    """Tests for the tool-result image preview cache mount.

    The volundr container's root filesystem is read-only
    (securityContext.readOnlyRootFilesystem), so config.preview_cache_dir must
    always resolve to a writable emptyDir mount, never Settings.preview_cache_dir's
    ~/.niuu mini-mode default (that resolves to /.niuu under the pod's HOME=/).
    """

    @staticmethod
    def _render(*overrides: str) -> tuple[dict, dict]:
        result = subprocess.run(
            ["helm", "template", "test", str(CHART_DIR), *BIFROST_URL, *overrides],
            check=True,
            capture_output=True,
            text=True,
        )
        documents = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
        configmap = next(
            doc
            for doc in documents
            if doc.get("kind") == "ConfigMap"
            and doc.get("metadata", {}).get("name") == "test-volundr"
        )
        deployment = next(doc for doc in documents if doc.get("kind") == "Deployment")
        return configmap, deployment

    def test_configmap_renders_preview_cache_dir_default(self):
        configmap, _ = self._render()
        config = yaml.safe_load(configmap["data"]["config.yaml"])

        assert config["preview_cache_dir"] == "/volundr/preview-cache"

    def test_configmap_renders_preview_cache_dir_override(self):
        configmap, _ = self._render("--set", "previewCache.mountPath=/custom/preview")
        config = yaml.safe_load(configmap["data"]["config.yaml"])

        assert config["preview_cache_dir"] == "/custom/preview"

    def test_configmap_leaves_preview_edges_to_defaults(self):
        configmap, _ = self._render()
        config = yaml.safe_load(configmap["data"]["config.yaml"])

        assert "tool_result_preview" not in config

    def test_configmap_renders_preview_edges(self):
        configmap, _ = self._render("--set", "previewCache.edges.max_edge=2048")
        config = yaml.safe_load(configmap["data"]["config.yaml"])

        assert config["tool_result_preview"] == {"max_edge": 2048}

    def test_deployment_mounts_preview_cache_emptydir_at_configured_path(self):
        _, deployment = self._render()
        containers = deployment["spec"]["template"]["spec"]["containers"]
        volundr_container = next(c for c in containers if c["name"] == "volundr")
        mount = next(m for m in volundr_container["volumeMounts"] if m["name"] == "preview-cache")
        assert mount["mountPath"] == "/volundr/preview-cache"

        volumes = deployment["spec"]["template"]["spec"]["volumes"]
        volume = next(v for v in volumes if v["name"] == "preview-cache")
        assert volume["emptyDir"]["sizeLimit"] == "1Gi"

    def test_deployment_mounts_scratch_emptydir_at_tmp(self):
        """The root filesystem is read-only, so /tmp is an emptyDir (tempfile needs it)."""
        _, deployment = self._render()
        pod = deployment["spec"]["template"]["spec"]
        volundr_container = next(c for c in pod["containers"] if c["name"] == "volundr")
        assert volundr_container["securityContext"]["readOnlyRootFilesystem"] is True
        mount = next(m for m in volundr_container["volumeMounts"] if m["name"] == "scratch")
        assert mount["mountPath"] == "/tmp"
        volume = next(v for v in pod["volumes"] if v["name"] == "scratch")
        assert volume["emptyDir"]["sizeLimit"] == "4Gi"

    def test_deployment_preview_cache_mount_path_follows_override(self):
        _, deployment = self._render("--set", "previewCache.mountPath=/custom/preview")
        containers = deployment["spec"]["template"]["spec"]["containers"]
        volundr_container = next(c for c in containers if c["name"] == "volundr")
        mount = next(m for m in volundr_container["volumeMounts"] if m["name"] == "preview-cache")
        assert mount["mountPath"] == "/custom/preview"

    def test_deployment_preview_cache_emptydir_has_no_size_limit_when_unset(self):
        _, deployment = self._render("--set-string", "previewCache.sizeLimit=")
        volumes = deployment["spec"]["template"]["spec"]["volumes"]
        volume = next(v for v in volumes if v["name"] == "preview-cache")
        assert volume["emptyDir"] == {}


class TestHpaTemplate:
    """Tests for hpa.yaml template."""

    @pytest.fixture
    def template_yaml(self) -> str:
        """Load hpa.yaml template."""
        template_path = CHART_DIR / "templates" / "hpa.yaml"
        return template_path.read_text()

    def test_has_conditional_enabled(self, template_yaml):
        """Test template is conditionally enabled."""
        assert ".Values.autoscaling.enabled" in template_yaml

    def test_has_correct_api_version(self, template_yaml):
        """Test template uses correct API version."""
        assert "apiVersion: autoscaling/v2" in template_yaml

    def test_has_correct_kind(self, template_yaml):
        """Test template uses correct kind."""
        assert "kind: HorizontalPodAutoscaler" in template_yaml

    def test_has_min_max_replicas(self, template_yaml):
        """Test template has min/max replicas."""
        assert ".Values.autoscaling.minReplicas" in template_yaml
        assert ".Values.autoscaling.maxReplicas" in template_yaml

    def test_has_cpu_metric(self, template_yaml):
        """Test template has CPU metric."""
        assert ".Values.autoscaling.targetCPUUtilizationPercentage" in template_yaml


class TestPdbTemplate:
    """Tests for pdb.yaml template."""

    @pytest.fixture
    def template_yaml(self) -> str:
        """Load pdb.yaml template."""
        template_path = CHART_DIR / "templates" / "pdb.yaml"
        return template_path.read_text()

    def test_has_conditional_enabled(self, template_yaml):
        """Test template is conditionally enabled."""
        assert ".Values.podDisruptionBudget.enabled" in template_yaml

    def test_has_correct_api_version(self, template_yaml):
        """Test template uses correct API version."""
        assert "apiVersion: policy/v1" in template_yaml

    def test_has_correct_kind(self, template_yaml):
        """Test template uses correct kind."""
        assert "kind: PodDisruptionBudget" in template_yaml

    def test_has_min_available(self, template_yaml):
        """Test template has minAvailable."""
        assert ".Values.podDisruptionBudget.minAvailable" in template_yaml


class TestNetworkPolicyTemplate:
    """Tests for networkpolicy.yaml template."""

    @pytest.fixture
    def template_yaml(self) -> str:
        """Load networkpolicy.yaml template."""
        template_path = CHART_DIR / "templates" / "networkpolicy.yaml"
        return template_path.read_text()

    def test_has_conditional_enabled(self, template_yaml):
        """Test template is conditionally enabled."""
        assert ".Values.networkPolicy.enabled" in template_yaml

    def test_has_correct_api_version(self, template_yaml):
        """Test template uses correct API version."""
        assert "apiVersion: networking.k8s.io/v1" in template_yaml

    def test_has_correct_kind(self, template_yaml):
        """Test template uses correct kind."""
        assert "kind: NetworkPolicy" in template_yaml

    def test_has_ingress_and_egress(self, template_yaml):
        """Test template has ingress and egress rules."""
        assert "policyTypes:" in template_yaml
        assert "- Ingress" in template_yaml
        assert "- Egress" in template_yaml


class TestNewValuesDefaults:
    """Tests for new values.yaml defaults."""

    @pytest.fixture
    def values_yaml(self) -> dict:
        """Load values.yaml."""
        values_path = CHART_DIR / "values.yaml"
        return yaml.safe_load(values_path.read_text())

    def test_service_account_configured(self, values_yaml):
        """Test service account is configured."""
        sa = values_yaml["serviceAccount"]
        assert sa["create"] is True
        assert sa["automount"] is True

    def test_rbac_configured(self, values_yaml):
        """Test RBAC is configured."""
        rbac = values_yaml["rbac"]
        assert rbac["create"] is True

    def test_service_configured(self, values_yaml):
        """Test service is configured."""
        svc = values_yaml["service"]
        assert svc["type"] == "ClusterIP"
        assert svc["port"] == 80
        assert svc["targetPort"] == 8080

    def test_ingress_configured(self, values_yaml):
        """Test ingress is configured."""
        ingress = values_yaml["ingress"]
        assert ingress["enabled"] is False
        assert ingress["className"] == ""
        assert ingress["annotations"] == {}

    def test_database_configured(self, values_yaml):
        """Test database is configured."""
        db = values_yaml["database"]
        assert db["name"] == "volundr"
        assert db["existingSecret"] == ""
        assert db["external"]["enabled"] is True

    def test_pod_manager_configured(self, values_yaml):
        """Test pod manager is configured."""
        pm = values_yaml["podManager"]
        assert "adapter" in pm
        assert "kwargs" in pm
        assert pm["kwargs"]["chart_name"] == "skuld"

    def test_resources_configured(self, values_yaml):
        """Test resources are configured."""
        resources = values_yaml["resources"]
        assert "requests" in resources
        assert "limits" in resources

    def test_probes_configured(self, values_yaml):
        """Test probes are configured."""
        assert values_yaml["livenessProbe"]["enabled"] is True
        assert values_yaml["readinessProbe"]["enabled"] is True

    def test_security_context_configured(self, values_yaml):
        """Test security context is configured."""
        pod_ctx = values_yaml["podSecurityContext"]
        assert pod_ctx["runAsNonRoot"] is True
        assert pod_ctx["runAsUser"] == 1000
        container_ctx = values_yaml["securityContext"]
        assert container_ctx["allowPrivilegeEscalation"] is False
        assert container_ctx["readOnlyRootFilesystem"] is True

    def test_autoscaling_configured(self, values_yaml):
        """Test autoscaling is configured."""
        hpa = values_yaml["autoscaling"]
        assert hpa["enabled"] is False
        assert hpa["minReplicas"] == 1
        assert hpa["maxReplicas"] == 10

    def test_pdb_configured(self, values_yaml):
        """Test PDB is configured."""
        pdb = values_yaml["podDisruptionBudget"]
        assert pdb["enabled"] is False

    def test_network_policy_configured(self, values_yaml):
        """Test network policy is configured."""
        np = values_yaml["networkPolicy"]
        assert np["enabled"] is False


def _rendered_configmap(*extra_args: str) -> dict:
    command = ["helm", "template", "test", str(CHART_DIR), *BIFROST_URL, *extra_args]
    docs = list(yaml.safe_load_all(subprocess.check_output(command)))
    return next(
        yaml.safe_load(d["data"]["config.yaml"])
        for d in docs
        if d and d["kind"] == "ConfigMap" and "config.yaml" in d.get("data", {})
    )


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
class TestPodManagerRoomRoleSource:
    """pod_manager.room_role_source — the single setting the 409 gate and
    RoomRoleSourceContributor both read (see rest_session_participants.py)."""

    def test_defaults_to_deployment(self):
        config = _rendered_configmap()
        assert config["pod_manager"]["room_role_source"] == "deployment"
        assert config["pod_manager"]["room_role_cache_ttl_seconds"] == 5.0

    def test_renders_remote_and_a_custom_cache_ttl(self):
        config = _rendered_configmap(
            "--set",
            "podManager.roomRoleSource=remote",
            "--set",
            "podManager.roomRoleCacheTtlSeconds=12",
        )
        assert config["pod_manager"]["room_role_source"] == "remote"
        assert config["pod_manager"]["room_role_cache_ttl_seconds"] == 12

    def test_config_parses_as_settings(self):
        from volundr.config import PodManagerConfig

        config = _rendered_configmap(
            "--set",
            "podManager.roomRoleSource=remote",
        )
        pm = PodManagerConfig(**config["pod_manager"])
        assert pm.room_role_source == "remote"


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
class TestProvisioningStartupPoll:
    """provisioning.startup_poll_seconds — how often a starting session's launch
    stages are read from its runtime backend."""

    def test_defaults_to_the_values_file_and_is_declared_there(self):
        values = yaml.safe_load((CHART_DIR / "values.yaml").read_text())
        provisioning = _rendered_configmap()["provisioning"]

        assert values["provisioning"]["startupPollSeconds"] == 2.0
        assert provisioning["startup_poll_seconds"] == 2.0
        assert provisioning["timeout_seconds"] == 300.0
        assert provisioning["initial_delay_seconds"] == 5.0

    def test_renders_a_custom_interval(self):
        config = _rendered_configmap("--set", "provisioning.startupPollSeconds=0.5")

        assert config["provisioning"]["startup_poll_seconds"] == 0.5

    def test_config_parses_as_settings(self):
        from volundr.config import ProvisioningConfig

        config = _rendered_configmap("--set", "provisioning.startupPollSeconds=7")

        assert ProvisioningConfig(**config["provisioning"]).startup_poll_seconds == 7


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
def test_forge_envoy_authorization_route_for_room_role_endpoint_is_valid():
    """Without this route, Forge's own ext_authz sidecar denies the scoped
    workload token before it ever reaches the FastAPI-level scope check —
    see identity/policies/authorization.cedar's gateway-scoped-credential rule."""
    from identity.authz_config import AuthorizationGatewayConfig, JWTMetadataProvider

    with open(CHART_DIR / "values.yaml") as fh:
        values = yaml.safe_load(fh)
    routes = values["envoy"]["authorization"]["routes"]
    role_route = next(
        r for r in routes if r["path"] == "/api/v1/forge/sessions/{session_id}/participants/role"
    )
    assert role_route["required_scope"] == "forge:session:room-role"
    assert role_route["methods"] == ["GET"]
    assert role_route.get("path_template") is True

    config = AuthorizationGatewayConfig(
        routes=routes,
        providers=[JWTMetadataProvider(issuer="https://issuer.test", audiences=["volundr"])],
    )
    matched = next(
        r
        for r in config.routes
        if "GET" in r.methods and r.matches("/api/v1/forge/sessions/abc-123/participants/role")
    )
    assert matched.required_scope == "forge:session:room-role"


def test_forge_envoy_authorization_admits_a_residents_ring_before_the_forge_prefix():
    """A resident's forge:push:call credential reaches POST /push/call; the first match
    wins, so the route precedes the /api/v1/forge prefix (which has no required_scope
    and would refuse the scoped token)."""
    from identity.authz_config import AuthorizationGatewayConfig, JWTMetadataProvider

    with open(CHART_DIR / "values.yaml") as fh:
        values = yaml.safe_load(fh)
    config = AuthorizationGatewayConfig(
        routes=values["envoy"]["authorization"]["routes"],
        providers=[JWTMetadataProvider(issuer="https://issuer.test", audiences=["volundr"])],
    )
    matched = next(
        r for r in config.routes if "POST" in r.methods and r.matches("/api/v1/forge/push/call")
    )
    assert matched.required_scope == "forge:push:call"


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
class TestGuildMembership:
    """``guild:`` names the Guild this Forge belongs to (GET /api/v1/forge/guild)."""

    @staticmethod
    def _config(chart: str, *overrides: str) -> dict:
        result = subprocess.run(
            ["helm", "template", "test", str(CHART_DIR.parent / chart), *BIFROST_URL, *overrides],
            check=True,
            capture_output=True,
            text=True,
        )
        for doc in yaml.safe_load_all(result.stdout):
            if doc and doc.get("kind") == "ConfigMap" and "config.yaml" in doc.get("data", {}):
                return yaml.safe_load(doc["data"]["config.yaml"])
        raise AssertionError(f"{chart} rendered no config.yaml")

    def test_a_forge_belongs_to_no_guild_by_default(self):
        assert "guild" not in self._config("volundr")

    def test_renders_the_guild_and_this_forges_instance_in_it(self):
        from niuu.config_models import GuildMembershipConfig

        config = self._config(
            "volundr",
            "--set",
            "guild.url=https://yggdrasil.niuu.world",
            "--set",
            "guild.instanceId=ae40cfda-426a-46b4-a080-1ad668000859",
            "--set",
            "guild.instanceName=Valhalla",
            "--set",
            "guild.instanceSlug=valhalla",
        )

        assert config["guild"] == {
            "url": "https://yggdrasil.niuu.world",
            "instance_id": "ae40cfda-426a-46b4-a080-1ad668000859",
            "instance_name": "Valhalla",
            "instance_slug": "valhalla",
        }
        assert GuildMembershipConfig(**config["guild"]).membership() is not None

    def test_a_guild_url_without_this_forges_instance_fails_to_render(self):
        result = subprocess.run(
            [
                "helm",
                "template",
                "test",
                str(CHART_DIR),
                *BIFROST_URL,
                "--set",
                "guild.url=https://yggdrasil.niuu.world",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "guild.instanceId" in result.stderr

    def test_the_guild_chart_renders_its_own_public_origin(self):
        assert "guild" not in self._config("guild")
        config = self._config("guild", "--set", "guild.url=https://yggdrasil.niuu.world")
        assert config["guild"] == {"url": "https://yggdrasil.niuu.world"}


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
class TestStorageContributorHomePlacement:
    """The storage contributor's home placement comes from storage.home."""

    @staticmethod
    def _storage_kwargs(*extra: str) -> dict:
        config = _rendered_configmap(*extra)
        return next(
            c["kwargs"]
            for c in config["session_contributors"]
            if c["adapter"].endswith("contributors.storage.StorageContributor")
        )

    def test_shared_home_mounts_everywhere_without_colocation(self):
        kwargs = self._storage_kwargs()
        assert kwargs["home_colocate"] is False
        assert kwargs["home_excluded_workload_types"] == []

    def test_rwo_home_colocates_and_excludes_configured_workloads(self):
        kwargs = self._storage_kwargs(
            "--set",
            "storage.home.accessMode=ReadWriteOnce",
            "--set",
            "storage.home.excludeWorkloadTypes={batch}",
        )
        assert kwargs["home_colocate"] is True
        assert kwargs["home_excluded_workload_types"] == ["batch"]


def _rendered_volundr_deployment(*extra_args: str) -> dict:
    command = ["helm", "template", "test", str(CHART_DIR), *BIFROST_URL, *extra_args]
    docs = list(yaml.safe_load_all(subprocess.check_output(command)))
    return next(
        d
        for d in docs
        if d and d["kind"] == "Deployment" and d["metadata"]["name"] == "test-volundr"
    )


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
class TestZeroDowntimeRollout:
    """Recreate stopped Völundr before starting the new pod; every request in
    between got a 503, which failed a resident's delivery launch."""

    def test_rolls_with_a_ready_surge_pod_before_the_old_one_stops(self):
        spec = _rendered_volundr_deployment()["spec"]
        assert spec["strategy"] == {
            "type": "RollingUpdate",
            "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0},
        }

    def test_surge_pod_lands_beside_the_current_one_for_the_rwo_volume(self):
        affinity = _rendered_volundr_deployment()["spec"]["template"]["spec"]["affinity"]
        (term,) = affinity["podAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]
        assert term["topologyKey"] == "kubernetes.io/hostname"
        assert term["labelSelector"]["matchLabels"] == {
            "app.kubernetes.io/name": "volundr",
            "app.kubernetes.io/instance": "test",
        }

    def test_colocation_keeps_operator_affinity(self):
        affinity = _rendered_volundr_deployment(
            "--set",
            "affinity.nodeAffinity.preferredDuringSchedulingIgnoredDuringExecution[0].weight=1",
            "--set",
            "affinity.nodeAffinity.preferredDuringSchedulingIgnoredDuringExecution[0]"
            ".preference.matchExpressions[0].key=tier",
            "--set",
            "affinity.nodeAffinity.preferredDuringSchedulingIgnoredDuringExecution[0]"
            ".preference.matchExpressions[0].operator=Exists",
        )["spec"]["template"]["spec"]["affinity"]
        assert "nodeAffinity" in affinity and "podAffinity" in affinity

    def test_serving_containers_drain_before_exiting(self):
        pod = _rendered_volundr_deployment("--set", "envoy.enabled=true")["spec"]["template"][
            "spec"
        ]
        drains = {c["name"]: c.get("lifecycle") for c in pod["containers"]}
        assert drains["volundr"] == {"preStop": {"sleep": {"seconds": 10}}}
        assert drains["envoy"] == {"preStop": {"sleep": {"seconds": 10}}}
        assert pod["terminationGracePeriodSeconds"] > 10

    def test_colocation_and_drain_can_be_turned_off(self):
        pod = _rendered_volundr_deployment(
            "--set", "rollout.colocate=false", "--set", "rollout.drainSeconds=0"
        )["spec"]["template"]["spec"]
        assert "affinity" not in pod
        assert all("lifecycle" not in c for c in pod["containers"])


def _rendered_documents(*extra_args: str) -> list[dict]:
    command = ["helm", "template", "test", str(CHART_DIR), *BIFROST_URL, *extra_args]
    return [doc for doc in yaml.safe_load_all(subprocess.check_output(command)) if doc]


def _render_failure(*extra_args: str) -> str:
    result = subprocess.run(
        ["helm", "template", "test", str(CHART_DIR), *BIFROST_URL, *extra_args],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, "the chart rendered a configuration it must refuse"
    return result.stderr


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
class TestPresentedFilesOnACluster:
    """Durable presented files on a cluster Forge: a shared volume, the Postgres store,
    and every session broker holding the credential it uploads with."""

    def test_off_by_default_with_no_volume_or_credential_projection(self):
        docs = _rendered_documents()
        assert "presented_files" not in _rendered_configmap()
        assert "projection_adapter" not in _rendered_configmap()["forge_mcp"]["session_tokens"]
        names = {doc["metadata"]["name"] for doc in docs}
        assert "test-volundr-presented-files" not in names
        assert "test-volundr-session-credentials" not in names

    def test_the_volume_is_claimed_kept_and_mounted_where_the_store_writes(self):
        docs = _rendered_documents(*PRESENTED_FILES_ON)
        claim = next(
            doc
            for doc in docs
            if doc["kind"] == "PersistentVolumeClaim"
            and doc["metadata"]["name"] == "test-volundr-presented-files"
        )
        assert claim["spec"]["accessModes"] == ["ReadWriteMany"]
        assert claim["spec"]["resources"]["requests"]["storage"] == "10Gi"
        # Copies outlive their sessions; an uninstall must not delete them.
        assert claim["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"
        pod = _rendered_volundr_deployment(*PRESENTED_FILES_ON)["spec"]["template"]["spec"]
        volundr = next(c for c in pod["containers"] if c["name"] == "volundr")
        mount = next(m for m in volundr["volumeMounts"] if m["name"] == "presented-files")
        config = _rendered_configmap(*PRESENTED_FILES_ON)["presented_files"]
        assert mount["mountPath"] == config["store"]["root"] == "/volundr/presented-files"
        volume = next(v for v in pod["volumes"] if v["name"] == "presented-files")
        assert volume["persistentVolumeClaim"]["claimName"] == "test-volundr-presented-files"

    def test_an_existing_claim_is_used_and_none_is_created(self):
        args = (*PRESENTED_FILES_ON, "--set", "presentedFiles.persistence.existingClaim=mine")
        docs = _rendered_documents(*args)
        assert not [
            doc
            for doc in docs
            if doc["kind"] == "PersistentVolumeClaim" and "presented" in doc["metadata"]["name"]
        ]
        pod = _rendered_volundr_deployment(*args)["spec"]["template"]["spec"]
        volume = next(v for v in pod["volumes"] if v["name"] == "presented-files")
        assert volume["persistentVolumeClaim"]["claimName"] == "mine"

    def test_session_credentials_are_projected_into_the_session_namespace(self):
        from volundr.config import ForgeMcpConfig

        docs = _rendered_documents(*PRESENTED_FILES_ON)
        tokens = ForgeMcpConfig.model_validate(
            _rendered_configmap(*PRESENTED_FILES_ON)["forge_mcp"]
        ).session_tokens
        assert tokens.enabled
        assert tokens.projection_adapter == (
            "volundr.adapters.outbound.execution_credential_k8s."
            "KubernetesExecutionCredentialProjection"
        )
        assert tokens.projection_kwargs == {
            "namespace": "volundr-sessions",
            "mount_path": "/var/run/secrets/niuu-forge-session",
            "secret_prefix": "forge-session",
            "volume_prefix": "forge-session",
            "purpose": "forge-session-credential",
        }
        role = next(
            doc
            for doc in docs
            if doc["kind"] == "Role"
            and doc["metadata"]["name"] == "test-volundr-session-credentials"
        )
        assert role["metadata"]["namespace"] == "volundr-sessions"
        assert role["rules"] == [
            {
                "apiGroups": [""],
                "resources": ["secrets"],
                "verbs": ["get", "create", "patch", "delete"],
            }
        ]
        binding = next(
            doc
            for doc in docs
            if doc["kind"] == "RoleBinding"
            and doc["metadata"]["name"] == "test-volundr-session-credentials"
        )
        assert binding["metadata"]["namespace"] == "volundr-sessions"
        assert binding["roleRef"]["name"] == "test-volundr-session-credentials"

    @pytest.mark.parametrize(
        ("drop", "extra", "message"),
        [
            ("presentedFiles.maxTotalBytes=5368709120", (), "presentedFiles.maxTotalBytes"),
            (
                "workloadIdentity.enabled=true",
                (),
                "presentedFiles.enabled needs Forge session credentials",
            ),
            (
                None,
                ("sessionCredentialProjection.enabled=false",),
                "needs session pods to hold their Forge credential",
            ),
            (
                None,
                ("presentedFiles.persistence.accessMode=ReadWriteOnce", "rollout.colocate=false"),
                "ReadWriteOnce mounts on one node only",
            ),
        ],
    )
    def test_a_configuration_no_upload_could_succeed_in_is_refused(self, drop, extra, message):
        args: list[str] = []
        pairs = list(zip(PRESENTED_FILES_ON[::2], PRESENTED_FILES_ON[1::2], strict=True))
        for flag, value in pairs:
            if value != drop:
                args += [flag, value]
        for value in extra:
            args += ["--set", value]
        assert message in _render_failure(*args)

    def test_an_openshell_forge_needs_no_projection(self):
        """The OpenShell pod manager writes the credential into the sandbox itself."""
        args: list[str] = list(PRESENTED_FILES_ON)
        args += [
            "--set",
            "podManager.adapter=volundr.adapters.outbound.openshell_gateway."
            "OpenShellGatewayPodManager",
        ]
        config = _rendered_configmap(*args)
        assert config["presented_files"]["enabled"] is True
        assert "projection_adapter" not in config["forge_mcp"]["session_tokens"]

    def test_read_write_once_is_accepted_with_colocated_pods(self):
        args = (*PRESENTED_FILES_ON, "--set", "presentedFiles.persistence.accessMode=ReadWriteOnce")
        assert _rendered_configmap(*args)["presented_files"]["enabled"] is True


SESSION_CREDENTIALS_ROLE = "test-volundr-session-credentials"
DIRECT_K8S = "volundr.adapters.outbound.direct_k8s_pod_manager.DirectK8sPodManager"
OPENSHELL = "volundr.adapters.outbound.openshell_gateway.OpenShellGatewayPodManager"
KEY_FILE = ("--set", "forgeMcp.session_tokens.signing_key_file=/keys/forge-session.pem")


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
class TestSessionCredentialsNeedNoSetup:
    """A session broker asks Forge for its mesh certificate with the credential Forge mints
    at launch, so a cluster that can sign gets the credential, its Secret projection and
    the RBAC for it without further values."""

    @staticmethod
    def _tokens(*args: str) -> dict:
        return _rendered_configmap(*args)["forge_mcp"]["session_tokens"]

    @staticmethod
    def _has_secret_role(*args: str) -> bool:
        return any(
            doc["kind"] == "Role" and doc["metadata"]["name"] == SESSION_CREDENTIALS_ROLE
            for doc in _rendered_documents(*args)
        )

    def test_workload_identity_key_turns_on_credentials_projection_and_rbac(self):
        tokens = self._tokens(*WORKLOAD_SIGNING_KEY)
        assert tokens["enabled"] is True
        assert tokens["projection_adapter"] == (
            "volundr.adapters.outbound.execution_credential_k8s."
            "KubernetesExecutionCredentialProjection"
        )
        assert tokens["projection_kwargs"]["mount_path"] == "/var/run/secrets/niuu-forge-session"
        assert self._has_secret_role(*WORKLOAD_SIGNING_KEY)

    def test_an_operator_key_file_is_a_key_forge_keeps(self):
        tokens = self._tokens(*KEY_FILE)
        assert tokens["enabled"] is True
        assert tokens["signing_key_file"] == "/keys/forge-session.pem"
        assert "projection_adapter" in tokens
        assert self._has_secret_role(*KEY_FILE)

    def test_the_direct_kubernetes_pod_manager_is_projected_too(self):
        args = (*WORKLOAD_SIGNING_KEY, "--set", f"podManager.adapter={DIRECT_K8S}")
        assert "projection_adapter" in self._tokens(*args)
        assert self._has_secret_role(*args)

    def test_a_pod_manager_that_delivers_itself_gets_no_projection_or_role(self):
        args = (*WORKLOAD_SIGNING_KEY, "--set", f"podManager.adapter={OPENSHELL}")
        assert self._tokens(*args) == {"enabled": True}
        assert not self._has_secret_role(*args)

    def test_no_key_means_off_and_nothing_is_rendered_for_it(self):
        assert self._tokens() == {"enabled": False}
        assert not self._has_secret_role()

    def test_credentials_can_still_be_switched_off(self):
        args = (*WORKLOAD_SIGNING_KEY, "--set", "forgeMcp.session_tokens.enabled=false")
        assert self._tokens(*args) == {"enabled": False}
        assert not self._has_secret_role(*args)
        # The mesh identity follows them, so it is not asked for while they are off.
        assert "mesh_identity" not in _rendered_configmap(*args)["forge_mcp"]

    def test_the_projection_can_be_switched_off_on_its_own(self):
        args = (*WORKLOAD_SIGNING_KEY, "--set", "sessionCredentialProjection.enabled=false")
        assert self._tokens(*args) == {"enabled": True}
        assert not self._has_secret_role(*args)

    def test_rbac_follows_rbac_create(self):
        args = (*WORKLOAD_SIGNING_KEY, "--set", "rbac.create=false")
        assert "projection_adapter" in self._tokens(*args)
        assert not self._has_secret_role(*args)

    def test_an_operator_projection_adapter_is_kept(self):
        args = (
            *WORKLOAD_SIGNING_KEY,
            "--set",
            "forgeMcp.session_tokens.projection_adapter=acme.Projection",
        )
        assert self._tokens(*args)["projection_adapter"] == "acme.Projection"

    def test_mesh_identity_stays_on_with_credentials(self):
        from volundr.config import ForgeMcpConfig

        args = (*WORKLOAD_SIGNING_KEY, "--set", "forgeMcp.mesh_identity.ttl_seconds=600")
        parsed = ForgeMcpConfig.model_validate(_rendered_configmap(*args)["forge_mcp"])
        assert parsed.session_tokens.enabled is True
        assert parsed.mesh_identity.ttl_seconds == 600

    def test_enabling_credentials_without_a_key_is_refused_with_the_remedy(self):
        error = _render_failure("--set", "forgeMcp.session_tokens.enabled=true")
        assert "needs a signing key Forge can keep" in error
        for remedy in ("workloadIdentity", "signing_key_file", "enabled: false"):
            assert remedy in error

    def test_workload_identity_without_its_key_secret_is_not_a_key(self):
        error = _render_failure(
            "--set",
            "workloadIdentity.enabled=true",
            "--set",
            "forgeMcp.session_tokens.enabled=true",
        )
        assert "needs a signing key Forge can keep" in error

    def test_the_old_mesh_identity_switch_is_refused_with_the_new_name(self):
        error = _render_failure("--set", "forgeMcp.mesh_identity.enabled=false")
        assert "mesh.attestation.required" in error

    def test_mesh_signing_renders_nothing_by_default_and_the_one_switch_when_set(self):
        assert "mesh" not in _rendered_configmap(*WORKLOAD_SIGNING_KEY)
        config = _rendered_configmap(
            *WORKLOAD_SIGNING_KEY, "--set", "mesh.attestation.required=false"
        )
        assert config["mesh"] == {"attestation": {"required": False}}
        from volundr.config import MeshSettings

        assert MeshSettings.model_validate(config["mesh"]).attestation.required is False

    def test_a_projection_with_nothing_to_project_is_refused(self):
        error = _render_failure("--set", "sessionCredentialProjection.enabled=true")
        assert "sessionCredentialProjection.enabled has no credential to project" in error

    def test_an_explicit_projection_is_honoured_for_any_pod_manager(self):
        args = (
            *WORKLOAD_SIGNING_KEY,
            "--set",
            f"podManager.adapter={OPENSHELL}",
            "--set",
            "sessionCredentialProjection.enabled=true",
        )
        assert "projection_adapter" in self._tokens(*args)
        assert self._has_secret_role(*args)


GUEST_METADATA_ON = ("--set", "guestMetadata.enabled=true")


def _gateway_http(config: dict) -> dict:
    return config["static_resources"]["listeners"][0]["filter_chains"][0]["filters"][0][
        "typed_config"
    ]


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
class TestGuestMetadataEndpoint:
    """VM guests authenticate to the metadata endpoint themselves (host-key signature or
    SPIFFE JWT-SVID), so the gateway must pass exactly that route and Forge must be able
    to request the ServiceAccount tokens it hands out."""

    @staticmethod
    def _gateway(*overrides: str) -> dict:
        from tests.test_charts.test_cedar_authz import envoy_config, render_cedar

        values = {"guestMetadata.enabled": "true", "podManager.kwargs.namespace": "vm-sessions"}
        values.update(dict(item.split("=", 1) for item in overrides))
        return envoy_config(render_cedar(**values))

    def test_off_by_default_no_bypass_and_no_role(self):
        names = {doc["metadata"]["name"] for doc in _rendered_documents()}
        assert "test-volundr-guest-metadata" not in names

    def test_regex_is_exactly_the_endpoint_path(self):
        from volundr.domain.guest_credentials import METADATA_CREDENTIALS_PATH

        http = _gateway_http(self._gateway())
        jwt_filter = next(f for f in http["http_filters"] if f["name"].endswith("jwt_authn"))
        bypasses = [
            rule["match"]
            for rule in jwt_filter["typed_config"]["rules"]
            if "safe_regex" in rule["match"]
        ]
        assert bypasses == [
            {
                "safe_regex": {"google_re2": {}, "regex": f"^{METADATA_CREDENTIALS_PATH}$"},
                "headers": [{"name": ":method", "exact_match": "POST"}],
            }
        ]
        # Exactly that route: no prefix match covers it or its siblings.
        assert not any(
            "prefix" in rule["match"]
            and rule["match"]["prefix"] != "/"
            and METADATA_CREDENTIALS_PATH.startswith(rule["match"]["prefix"])
            for rule in jwt_filter["typed_config"]["rules"]
        )

    def test_cedar_admission_is_skipped_for_the_same_route_only(self):
        http = _gateway_http(self._gateway())
        routes = http["route_config"]["virtual_hosts"][0]["routes"]
        skipped = [
            route["match"]
            for route in routes
            if route.get("typed_per_filter_config", {})
            .get("envoy.filters.http.ext_authz", {})
            .get("disabled")
            and "safe_regex" in route["match"]
        ]
        assert [match["safe_regex"]["regex"] for match in skipped] == [
            "^/api/v1/internal/compute/metadata/credentials$"
        ]
        assert skipped[0]["headers"] == [{"name": ":method", "exact_match": "POST"}]

    def test_without_the_switch_the_gateway_has_no_such_bypass(self):
        from tests.test_charts.test_cedar_authz import envoy_config, render_cedar

        http = _gateway_http(envoy_config(render_cedar()))
        assert "safe_regex" not in json.dumps(http["route_config"])
        jwt_filter = next(f for f in http["http_filters"] if f["name"].endswith("jwt_authn"))
        assert "safe_regex" not in json.dumps(jwt_filter)

    def test_ssh_push_gets_the_token_role_without_the_gateway_bypass(self):
        docs = _rendered_documents(
            "--set",
            "guestMetadata.issueServiceAccountTokens=true",
            "--set",
            "podManager.kwargs.namespace=vm",
        )
        names = {(d["kind"], d["metadata"]["name"]) for d in docs}
        assert ("Role", "test-volundr-guest-metadata") in names
        from tests.test_charts.test_cedar_authz import envoy_config, render_cedar

        gateway = envoy_config(render_cedar(**{"guestMetadata.issueServiceAccountTokens": "true"}))
        assert "/compute/metadata/" not in json.dumps(gateway)

    def test_spiffe_registration_grants_only_cluster_static_entries(self):
        assert not any(
            d["metadata"]["name"].endswith("-guest-spiffe") for d in _rendered_documents()
        )
        docs = _rendered_documents("--set", "guestMetadata.spiffeRegistration=true")
        role = next(
            d
            for d in docs
            if d["kind"] == "ClusterRole" and d["metadata"]["name"].endswith("-guest-spiffe")
        )
        assert role["rules"] == [
            {
                "apiGroups": ["spire.spiffe.io"],
                "resources": ["clusterstaticentries"],
                "verbs": ["get", "create", "update", "delete"],
            }
        ]
        binding = next(
            d
            for d in docs
            if d["kind"] == "ClusterRoleBinding" and d["metadata"]["name"].endswith("-guest-spiffe")
        )
        assert binding["roleRef"]["name"] == role["metadata"]["name"]

    def test_forge_may_only_create_tokens_in_the_session_namespace(self):
        docs = _rendered_documents(*GUEST_METADATA_ON, "--set", "podManager.kwargs.namespace=vm")
        role = next(
            d
            for d in docs
            if d["kind"] == "Role" and d["metadata"]["name"].endswith("-guest-metadata")
        )
        assert role["metadata"]["namespace"] == "vm"
        assert role["rules"] == [
            {"apiGroups": [""], "resources": ["serviceaccounts/token"], "verbs": ["create"]}
        ]
        binding = next(
            d
            for d in docs
            if d["kind"] == "RoleBinding" and d["metadata"]["name"].endswith("-guest-metadata")
        )
        assert binding["metadata"]["namespace"] == "vm"
        assert binding["roleRef"]["name"] == role["metadata"]["name"]

    def test_the_namespace_can_be_named_explicitly_and_defaults_to_the_release(self):
        explicit = _rendered_documents(
            *GUEST_METADATA_ON,
            "--set",
            "guestMetadata.serviceAccountNamespace=vm-accounts",
            "--set",
            "podManager.kwargs.namespace=vm",
        )
        role = next(d for d in explicit if d["metadata"]["name"].endswith("-guest-metadata"))
        assert role["metadata"]["namespace"] == "vm-accounts"
        default = _rendered_documents(
            *GUEST_METADATA_ON, "--set", "podManager.kwargs.namespace=null", "--namespace", "forge"
        )
        role = next(
            d
            for d in default
            if d["kind"] == "Role" and d["metadata"]["name"].endswith("-guest-metadata")
        )
        assert role["metadata"]["namespace"] == "forge"

    def test_no_role_without_rbac(self):
        docs = _rendered_documents(*GUEST_METADATA_ON, "--set", "rbac.create=false")
        assert not any(d["metadata"]["name"].endswith("-guest-metadata") for d in docs)


class TestComputeHosts:
    """computeHosts: a pool of registered hosts is one switch and the Forge's URL."""

    ON = (
        "--set",
        "computeHosts.enabled=true",
        "--set",
        "computeHosts.joinUrl=https://forge-hosts.example",
        "--set",
        "image.tag=dev-abc123@sha256:" + "0" * 64,
    )

    @staticmethod
    def _render(*extra: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["helm", "template", "test", str(CHART_DIR), *BIFROST_URL, *extra],
            check=check,
            capture_output=True,
            text=True,
        )

    def _config(self, *extra: str) -> dict:
        documents = [doc for doc in yaml.safe_load_all(self._render(*extra).stdout) if doc]
        configmap = next(
            doc
            for doc in documents
            if doc.get("kind") == "ConfigMap"
            and doc.get("metadata", {}).get("name") == "test-volundr"
        )
        return yaml.safe_load(configmap["data"]["config.yaml"])

    def test_renders_the_pool_forge_validates(self):
        from volundr.compute.config import ComputeConfig

        config = self._config(*self.ON)
        compute = ComputeConfig.model_validate(config["compute"])
        assert compute.provider.adapter.endswith("RegisteredHostMachineProvider")
        assert compute.execution_catalog is not None
        assert compute.execution_catalog.adapter.endswith("BuiltinHostCatalog")
        assert compute.reuse_policy == "reuse"
        assert compute.hosts is not None
        assert compute.hosts.join_url == "https://forge-hosts.example"
        assert compute.session_images is not None
        # The release's own images, from its image tag without the digest.
        assert compute.session_images.linux == "ghcr.io/niuulabs/openshell:dev-abc123"
        assert compute.session_images.darwin == "ghcr.io/niuulabs/skuld-darwin:dev-abc123"
        assert config["pod_manager"]["adapter"].endswith("VmPodManager")
        assert config["pod_manager"]["runtime_backend"] == "vm"
        assert config["pod_manager"]["kwargs"]["pool_id"] == compute.pool_id
        # Without guestMetadata there is no credential delivery to configure.
        assert compute.credential_delivery is None

    def test_the_catalog_it_renders_resolves(self, tmp_path):
        from niuu.utils import import_class
        from volundr.compute.config import ComputeConfig
        from volundr.domain.services.execution_catalog import ExecutionPlanResolver

        compute = ComputeConfig.model_validate(self._config(*self.ON)["compute"])
        catalog_config = compute.execution_catalog
        kwargs = {**catalog_config.kwargs, "data_dir": str(tmp_path)}
        catalog = import_class(catalog_config.adapter)(**kwargs)
        default, _ = ExecutionPlanResolver(catalog).resolve_all()
        assert default.pool_id == compute.pool_id
        assert default.provider_binding == compute.provider_binding

    def test_guest_metadata_adds_the_credential_delivery(self):
        config = self._config(*self.ON, "--set", "guestMetadata.enabled=true")
        compute = config["compute"]
        assert compute["credential_delivery"]["kwargs"]["metadata_url"] == (
            "https://forge-hosts.example"
        )
        assert compute["guest_identity"]["kwargs"] == {"key_source": "registered"}
        assert compute["service_account_tokens"]["kwargs"]["namespace"]

    def test_platform_port_is_envoys_when_envoy_runs(self):
        plain = self._config(*self.ON)["compute"]["execution_catalog"]["kwargs"]
        assert plain["platform_port"] == 8080
        envoy = self._config(*self.ON, "--set", "envoy.enabled=true", "--set", "envoy.port=8443")
        assert envoy["compute"]["execution_catalog"]["kwargs"]["platform_port"] == 8443

    def test_session_images_can_be_set(self):
        config = self._config(
            *self.ON,
            "--set",
            "computeHosts.platforms={linux}",
            "--set",
            "computeHosts.sessionImages.linux=registry.example/skuld:1",
        )
        assert config["compute"]["session_images"] == {
            "linux": "registry.example/skuld:1",
            "darwin": "",
            "native": [],
        }

    def test_native_images_default_to_the_secret_injection_agent(self):
        # Session images, outside the catalog: bumping the agent keeps the hosts' revision.
        agent = "quay.io/openbao/openbao@sha256:" + "1" * 64
        config = self._config(
            *self.ON, "--set", f"secretInjection.kwargs.guest_agent_image={agent}"
        )
        assert config["compute"]["session_images"]["native"] == [agent]
        assert "native_images" not in config["compute"]["execution_catalog"]["kwargs"]

    @pytest.mark.parametrize(
        ("extra", "message"),
        [
            (("--set", "computeHosts.joinUrl="), "computeHosts.joinUrl"),
            (("--set", "image.tag="), "image.tag"),
            (("--set", "storage.sessions.enabled=false"), "storage.sessions"),
            (
                ("--set", "compute.pool_id=vms", "--set", "compute.max_machines=1"),
                "both define this Forge's compute pool",
            ),
        ],
    )
    def test_refuses_what_it_cannot_honour(self, extra, message):
        result = self._render(*self.ON, *extra, check=False)
        assert result.returncode != 0
        assert message in result.stderr

    def test_off_leaves_pod_manager_and_compute_alone(self):
        config = self._config()
        assert "compute" not in config
        assert config["pod_manager"]["adapter"].endswith("FluxPodManager")

    def test_rbac_follows_the_effective_pod_manager(self):
        documents = [doc for doc in yaml.safe_load_all(self._render(*self.ON).stdout) if doc]
        names = {doc["metadata"]["name"] for doc in documents if doc.get("kind") == "Role"}
        flux = [
            doc
            for doc in documents
            if doc.get("kind") in {"Role", "ClusterRole"}
            and any("helmreleases" in rule.get("resources", []) for rule in doc.get("rules", []))
        ]
        assert not flux, names


def test_forge_keeps_its_private_files_private_across_restarts():
    """A recursive fsGroup change on every start would make Forge's 0600 keys 0660."""
    result = subprocess.run(
        ["helm", "template", "test", str(CHART_DIR), *BIFROST_URL],
        check=True,
        capture_output=True,
        text=True,
    )
    deployment = next(
        doc
        for doc in yaml.safe_load_all(result.stdout)
        if doc and doc.get("kind") == "Deployment" and doc["metadata"]["name"] == "test-volundr"
    )
    assert deployment["spec"]["template"]["spec"]["securityContext"]["fsGroupChangePolicy"] == (
        "OnRootMismatch"
    )


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
class TestProjectMemoryDirectory:
    """Where a project's memory mounts are resolved: the file in mini mode, the hosted Mimir."""

    def test_without_a_hosted_mimir_the_file_directory_is_left_to_the_default(self):
        from volundr.config import ProjectsConfig

        config = _rendered_configmap()
        memory = ProjectsConfig.model_validate(config["projects"]).memory
        assert memory.directory_adapter.endswith("MimirRegistryFileDirectory")
        assert "mimir" not in config["workload_identity"]["audiences"]

    def test_a_hosted_mimir_makes_the_http_directory_the_default(self):
        from volundr.config import ProjectsConfig

        config = _rendered_configmap(
            "--set", "projectMemory.mimirUrl=https://mimir.example.com", *WORKLOAD_SIGNING_KEY
        )
        memory = ProjectsConfig.model_validate(config["projects"]).memory
        assert memory.directory_adapter.endswith("MimirRegistryHttpDirectory")
        assert memory.directory_kwargs == {
            "base_url": "https://mimir.example.com",
            "audience": "mimir",
        }
        assert config["workload_identity"]["audiences"] == ["volundr-api", "mimir"]

    def test_the_audience_is_not_listed_twice(self):
        config = _rendered_configmap(
            "--set",
            "projectMemory.mimirUrl=https://mimir.example.com",
            "--set-json",
            'workloadIdentity.audiences=["volundr-api","mimir"]',
            *WORKLOAD_SIGNING_KEY,
        )
        assert config["workload_identity"]["audiences"] == ["volundr-api", "mimir"]

    def test_a_hosted_mimir_without_workload_identity_refuses_to_render(self):
        command = [
            "helm",
            "template",
            "test",
            str(CHART_DIR),
            *BIFROST_URL,
            "--set",
            "projectMemory.mimirUrl=https://mimir.example.com",
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        assert result.returncode != 0
        assert "projectMemory.mimirUrl asks the hosted Mimir with a workload token" in result.stderr


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
class TestMemoryGateway:
    """memory.gatewayUrl: the gateway every session's memory server calls."""

    def test_without_a_gateway_no_memory_section_is_rendered(self):
        assert "memory" not in _rendered_configmap()

    def test_a_gateway_is_rendered_for_forge_to_hand_to_every_session(self):
        from volundr.config import MemoryConfig

        config = _rendered_configmap(
            "--set",
            "memory.gatewayUrl=http://mimir.volundr.svc/api/v1/mimir",
            *WORKLOAD_SIGNING_KEY,
        )
        memory = MemoryConfig.model_validate(config["memory"])
        assert memory.gateway_url == "http://mimir.volundr.svc/api/v1/mimir"

    def test_a_gateway_without_session_credentials_refuses_to_render(self):
        command = [
            "helm",
            "template",
            "test",
            str(CHART_DIR),
            *BIFROST_URL,
            "--set",
            "memory.gatewayUrl=http://mimir.volundr.svc/api/v1/mimir",
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        assert result.returncode != 0
        assert "memory.gatewayUrl needs Forge session credentials" in result.stderr
