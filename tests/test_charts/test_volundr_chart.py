"""Tests for Volundr Helm chart templates."""

from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).parent.parent.parent / "charts" / "volundr"


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
        """Test skuld-claude broker defaults to the subprocess adapter."""
        broker = values_yaml["sessionDefinitions"]["skuldClaude"]["defaults"]["broker"]
        assert broker["transport"] == "subprocess"
        assert broker["transportAdapter"] == "skuld.transports.subprocess.SubprocessTransport"

    def test_skuld_codex_broker_cli_type(self, values_yaml):
        """Test skuld-codex broker cliType is codex-ws (WebSocket transport)."""
        broker = values_yaml["sessionDefinitions"]["skuldCodex"]["defaults"]["broker"]
        assert broker["cliType"] == "codex-ws"

    def test_skuld_codex_broker_transport_adapter(self, values_yaml):
        """Test skuld-codex broker has correct transportAdapter class path."""
        broker = values_yaml["sessionDefinitions"]["skuldCodex"]["defaults"]["broker"]
        assert broker["transportAdapter"] == "skuld.transports.codex_ws.CodexWebSocketTransport"

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

    def test_skuld_codex_env_secrets_has_openai_key(self, values_yaml):
        """Test skuld-codex defaults have OPENAI_API_KEY in envSecrets."""
        secrets = values_yaml["sessionDefinitions"]["skuldCodex"]["defaults"]["envSecrets"]
        assert isinstance(secrets, list)
        assert len(secrets) == 1
        assert secrets[0]["envVar"] == "OPENAI_API_KEY"


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
