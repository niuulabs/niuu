"""Integration tests for workspace sharing between editor containers.

These tests verify that the Helm chart configuration correctly sets up
workspace sharing between the Skuld broker and editor containers.
"""

from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).parent.parent.parent / "charts" / "skuld"


class TestWorkspacePathConsistency:
    """Tests that both containers use the same workspace path."""

    @pytest.fixture
    def values_yaml(self) -> dict:
        """Load values.yaml."""
        values_path = CHART_DIR / "values.yaml"
        return yaml.safe_load(values_path.read_text())

    @pytest.fixture
    def deployment_yaml(self) -> str:
        """Load deployment.yaml template."""
        template_path = CHART_DIR / "templates" / "deployment.yaml"
        return template_path.read_text()

    @pytest.fixture
    def helpers_tpl(self) -> str:
        """Load _helpers.tpl template."""
        template_path = CHART_DIR / "templates" / "_helpers.tpl"
        return template_path.read_text()

    def test_both_containers_use_workspace_path_helper(self, deployment_yaml):
        """Test both Skuld and editor containers use the same workspace path helper."""
        # Count occurrences of the workspace path helper
        workspace_helper_count = deployment_yaml.count('include "skuld.workspacePath"')
        # Should appear at least twice (once for Skuld env, once for editor container)
        assert workspace_helper_count >= 2

    def test_all_containers_mount_sessions_volume(self, deployment_yaml):
        """Test all containers mount the sessions volume."""
        # Find volume mount sections
        volume_mount_count = deployment_yaml.count("name: sessions")
        # Should appear at least 3 times: 2 volume mounts + 1 volume definition
        assert volume_mount_count >= 3

    def test_workspace_path_includes_session_id(self, helpers_tpl):
        """Test workspace path template includes session ID for isolation."""
        assert ".Values.session.id" in helpers_tpl
        assert ".Values.persistence.mountPath" in helpers_tpl

    def test_skuld_workspace_env_var(self, deployment_yaml):
        """Test Skuld container has structured workspace config."""
        assert "SKULD__SESSION__WORKSPACE_DIR" in deployment_yaml
        # And it uses the helper
        assert 'include "skuld.workspacePath"' in deployment_yaml

    def test_workspace_path_helper_used_in_deployment(self, deployment_yaml):
        """Test workspace path helper is referenced in the deployment template."""
        assert 'include "skuld.workspacePath"' in deployment_yaml


class TestPersistenceConfiguration:
    """Tests for shared PVC configuration."""

    @pytest.fixture
    def values_yaml(self) -> dict:
        """Load values.yaml."""
        values_path = CHART_DIR / "values.yaml"
        return yaml.safe_load(values_path.read_text())

    @pytest.fixture
    def deployment_yaml(self) -> str:
        """Load deployment.yaml template."""
        template_path = CHART_DIR / "templates" / "deployment.yaml"
        return template_path.read_text()

    def test_persistence_enabled_by_default(self, values_yaml):
        """Test persistence is enabled by default."""
        assert values_yaml["persistence"]["enabled"] is True

    def test_existing_claim_configured(self, values_yaml):
        """Test existing PVC claim is configured."""
        assert values_yaml["persistence"]["existingClaim"] == "volundr-sessions"

    def test_mount_path_configured(self, values_yaml):
        """Test mount path is configured."""
        mount_path = values_yaml["persistence"]["mountPath"]
        assert mount_path == "/volundr/sessions"

    def test_volume_uses_existing_claim(self, deployment_yaml):
        """Test volume references existing PVC claim."""
        assert ".Values.persistence.existingClaim" in deployment_yaml

    def test_volume_conditionally_enabled(self, deployment_yaml):
        """Test volume is only created when persistence is enabled."""
        assert "if .Values.persistence.enabled" in deployment_yaml


class TestSecurityContext:
    """Tests for security context consistency between containers."""

    @pytest.fixture
    def deployment_yaml(self) -> str:
        """Load deployment.yaml template."""
        template_path = CHART_DIR / "templates" / "deployment.yaml"
        return template_path.read_text()

    @pytest.fixture
    def values_yaml(self) -> dict:
        """Load values.yaml."""
        values_path = CHART_DIR / "values.yaml"
        return yaml.safe_load(values_path.read_text())

    def test_same_user_for_both_containers(self, deployment_yaml):
        """Test both containers run as the same user for file permissions."""
        # Both should use .Values.securityContext.runAsUser
        run_as_user_count = deployment_yaml.count(".Values.securityContext.runAsUser")
        # Should appear twice (once per container)
        assert run_as_user_count >= 2

    def test_fs_group_configured(self, deployment_yaml):
        """Test fsGroup is configured for shared volume access."""
        assert ".Values.securityContext.fsGroup" in deployment_yaml

    def test_security_context_defaults(self, values_yaml):
        """Test security context has reasonable defaults."""
        security = values_yaml["securityContext"]
        assert security["runAsNonRoot"] is True
        assert security["runAsUser"] == 1000
        assert security["fsGroup"] == 1000
