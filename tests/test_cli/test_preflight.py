"""Tests for cli.services.preflight — preflight checks."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cli.services.preflight import (
    PreflightConfig,
    PreflightResult,
    check_claude_binary,
    check_cluster_connectivity,
    check_database,
    check_disk_space,
    check_git,
    check_k3d,
    check_kubeconfig,
    check_kubectl,
    check_namespace,
    check_port_available,
    check_ports,
    check_workspace_dir,
    format_results,
    has_failures,
    run_cluster_preflight_checks,
    run_preflight_checks,
)


@pytest.fixture
def default_config() -> PreflightConfig:
    return PreflightConfig()


class TestCheckClaudeBinary:
    def test_found_in_path(self, default_config: PreflightConfig) -> None:
        with patch("cli.services.preflight.shutil.which", return_value="/usr/bin/claude"):
            result = check_claude_binary(default_config)
        assert result.passed is True
        assert "/usr/bin/claude" in result.message

    def test_not_found(self, default_config: PreflightConfig) -> None:
        with patch("cli.services.preflight.shutil.which", return_value=None):
            result = check_claude_binary(default_config)
        assert result.passed is False
        assert "not found" in result.message

    def test_custom_binary_name(self) -> None:
        config = PreflightConfig(claude_binary="/opt/claude-custom")
        with patch("cli.services.preflight.shutil.which", return_value=None):
            result = check_claude_binary(config)
        assert result.passed is False
        assert "/opt/claude-custom" in result.message


class TestCheckPortAvailable:
    def test_port_available(self) -> None:
        with patch("cli.services.preflight.socket.socket") as mock_sock:
            mock_instance = mock_sock.return_value.__enter__.return_value
            mock_instance.connect_ex.return_value = 1  # Connection refused = port free
            result = check_port_available(8080)
        assert result.passed is True
        assert "8080" in result.name

    def test_port_in_use(self) -> None:
        with patch("cli.services.preflight.socket.socket") as mock_sock:
            mock_instance = mock_sock.return_value.__enter__.return_value
            mock_instance.connect_ex.return_value = 0  # Connection success = port in use
            result = check_port_available(8080)
        assert result.passed is False
        assert "already in use" in result.message

    def test_socket_error_treated_as_available(self) -> None:
        with patch("cli.services.preflight.socket.socket") as mock_sock:
            mock_sock.return_value.__enter__.side_effect = OSError("network error")
            result = check_port_available(9100)
        assert result.passed is True

    def test_check_ports_multiple(self) -> None:
        config = PreflightConfig(ports=[8080, 8081])
        with patch("cli.services.preflight.socket.socket") as mock_sock:
            mock_instance = mock_sock.return_value.__enter__.return_value
            mock_instance.connect_ex.return_value = 1
            results = check_ports(config)
        assert len(results) == 2
        assert all(r.passed for r in results)


class TestCheckWorkspaceDir:
    def test_dir_exists_writable(self, tmp_path: Path) -> None:
        config = PreflightConfig(workspaces_dir=str(tmp_path))
        result = check_workspace_dir(config)
        assert result.passed is True

    def test_dir_created(self, tmp_path: Path) -> None:
        ws = tmp_path / "new_workspaces"
        config = PreflightConfig(workspaces_dir=str(ws))
        result = check_workspace_dir(config)
        assert result.passed is True
        assert ws.exists()

    def test_dir_not_writable(self, tmp_path: Path) -> None:
        config = PreflightConfig(workspaces_dir="/root/impossible_path_for_test")
        with patch("cli.services.preflight.Path.mkdir", side_effect=OSError("permission denied")):
            result = check_workspace_dir(config)
        assert result.passed is False
        assert "Cannot create" in result.message


class TestCheckDatabase:
    def test_embedded_with_binaries(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "postgres").touch()
        config = PreflightConfig(database_mode="embedded")
        with patch("niuu.adapters.embedded_postgres.pg_bin_dir", return_value=bin_dir):
            result = check_database(config)
        assert result.passed is True
        assert "available" in result.message

    def test_embedded_without_binaries(self) -> None:
        config = PreflightConfig(database_mode="embedded")
        with patch("niuu.adapters.embedded_postgres.pg_bin_dir", return_value=None):
            result = check_database(config)
        assert result.passed is False
        assert "not found" in result.message

    def test_external_with_dsn(self) -> None:
        config = PreflightConfig(
            database_mode="external",
            database_dsn="postgresql://localhost:5432/niuu",
        )
        result = check_database(config)
        assert result.passed is True

    def test_external_without_dsn(self) -> None:
        config = PreflightConfig(database_mode="external", database_dsn="")
        result = check_database(config)
        assert result.passed is False
        assert "database_dsn" in result.message


class TestCheckGit:
    def test_git_and_gh_found(self) -> None:
        with patch("cli.services.preflight.shutil.which", side_effect=lambda x: f"/usr/bin/{x}"):
            result = check_git()
        assert result.passed is True
        assert "git and gh" in result.message

    def test_git_found_no_gh(self) -> None:
        def which_side_effect(binary: str) -> str | None:
            if binary == "git":
                return "/usr/bin/git"
            return None

        with patch("cli.services.preflight.shutil.which", side_effect=which_side_effect):
            result = check_git()
        assert result.passed is True
        assert result.warn_only is True
        assert "gh" in result.message

    def test_git_not_found(self) -> None:
        with patch("cli.services.preflight.shutil.which", return_value=None):
            result = check_git()
        assert result.passed is False
        assert "not installed" in result.message


class TestCheckDiskSpace:
    def test_sufficient_space(self, tmp_path: Path) -> None:
        config = PreflightConfig(workspaces_dir=str(tmp_path))
        # Real disk usually has > 1GB
        result = check_disk_space(config)
        assert result.passed is True

    def test_low_space_warning(self, tmp_path: Path) -> None:
        config = PreflightConfig(workspaces_dir=str(tmp_path))
        with patch(
            "cli.services.preflight.shutil.disk_usage",
            return_value=type(
                "Usage", (), {"free": 500_000_000, "total": 1_000_000_000, "used": 500_000_000}
            )(),
        ):
            result = check_disk_space(config)
        assert result.passed is True
        assert result.warn_only is True
        assert "Low disk space" in result.message

    def test_disk_usage_error(self, tmp_path: Path) -> None:
        config = PreflightConfig(workspaces_dir=str(tmp_path))
        with patch("cli.services.preflight.shutil.disk_usage", side_effect=OSError("no fs")):
            result = check_disk_space(config)
        assert result.passed is True
        assert result.warn_only is True


class TestRunPreflightChecks:
    def test_all_checks_run(self, tmp_path: Path) -> None:
        config = PreflightConfig(
            workspaces_dir=str(tmp_path),
            ports=[8080],
        )
        with (
            patch("cli.services.preflight.shutil.which", return_value="/usr/bin/claude"),
            patch("cli.services.preflight.socket.socket") as mock_sock,
        ):
            mock_instance = mock_sock.return_value.__enter__.return_value
            mock_instance.connect_ex.return_value = 1
            results = run_preflight_checks(config)

        names = [r.name for r in results]
        assert "API key" not in names
        # Should have: claude, 1 port, workspace, database, git, disk
        assert len(results) >= 7

    def test_has_failures_with_failing_check(self) -> None:
        results = [
            PreflightResult(name="ok", passed=True, message="ok"),
            PreflightResult(name="fail", passed=False, message="bad"),
        ]
        assert has_failures(results) is True

    def test_has_failures_all_passing(self) -> None:
        results = [
            PreflightResult(name="ok", passed=True, message="ok"),
            PreflightResult(name="warn", passed=True, warn_only=True, message="warn"),
        ]
        assert has_failures(results) is False

    def test_has_failures_warn_only_not_counted(self) -> None:
        results = [
            PreflightResult(name="warn", passed=False, warn_only=True, message="warn"),
        ]
        assert has_failures(results) is False

    def test_format_results(self) -> None:
        results = [
            PreflightResult(name="test1", passed=True, message="all good"),
            PreflightResult(name="test2", passed=False, message="broken"),
            PreflightResult(name="test3", passed=True, warn_only=True, message="meh"),
        ]
        output = format_results(results)
        assert "[ OK ]" in output
        assert "[FAIL]" in output
        assert "[WARN]" in output
        assert "test1" in output
        assert "test2" in output
        assert "test3" in output

    def test_cluster_mode_runs_cluster_checks(self, tmp_path: Path) -> None:
        """In cluster mode, cluster-specific checks run instead of mini checks."""
        kubeconfig = tmp_path / "kubeconfig"
        kubeconfig.write_text("apiVersion: v1\n")
        config = PreflightConfig(
            mode="cluster",
            kubeconfig=str(kubeconfig),
            namespace="test-ns",
            ports=[8080],
        )
        with (
            patch("cli.services.preflight.shutil.which", return_value="/usr/bin/kubectl"),
            patch("cli.services.preflight.socket.socket") as mock_sock,
            patch("cli.services.preflight.subprocess.run") as mock_run,
        ):
            mock_instance = mock_sock.return_value.__enter__.return_value
            mock_instance.connect_ex.return_value = 1
            mock_run.return_value = type(
                "Result", (), {"returncode": 0, "stdout": "ok", "stderr": ""}
            )()
            results = run_preflight_checks(config)

        names = [r.name for r in results]
        assert "kubectl" in names
        assert "kubeconfig" in names
        assert "namespace" in names
        assert "API key" not in names
        # Should NOT include mini-specific checks
        assert "claude binary" not in names
        assert "workspace dir" not in names

    def test_mini_mode_runs_mini_checks(self, tmp_path: Path) -> None:
        """In mini mode, mini-specific checks run (default behavior)."""
        config = PreflightConfig(
            mode="mini",
            workspaces_dir=str(tmp_path),
            ports=[8080],
        )
        with (
            patch("cli.services.preflight.shutil.which", return_value="/usr/bin/claude"),
            patch("cli.services.preflight.socket.socket") as mock_sock,
        ):
            mock_instance = mock_sock.return_value.__enter__.return_value
            mock_instance.connect_ex.return_value = 1
            results = run_preflight_checks(config)

        names = [r.name for r in results]
        assert "claude binary" in names
        assert "workspace dir" in names
        assert "API key" not in names
        # Should NOT include cluster-specific checks
        assert "kubectl" not in names
        assert "kubeconfig" not in names


class TestCheckKubectl:
    def test_found(self) -> None:
        with patch("cli.services.preflight.shutil.which", return_value="/usr/bin/kubectl"):
            result = check_kubectl()
        assert result.passed is True
        assert "/usr/bin/kubectl" in result.message

    def test_not_found(self) -> None:
        with patch("cli.services.preflight.shutil.which", return_value=None):
            result = check_kubectl()
        assert result.passed is False
        assert "not found" in result.message


class TestCheckKubeconfig:
    def test_exists_and_readable(self, tmp_path: Path) -> None:
        kubeconfig = tmp_path / "kubeconfig"
        kubeconfig.write_text("apiVersion: v1\n")
        config = PreflightConfig(kubeconfig=str(kubeconfig))
        result = check_kubeconfig(config)
        assert result.passed is True

    def test_not_found(self, tmp_path: Path) -> None:
        config = PreflightConfig(kubeconfig=str(tmp_path / "nonexistent"))
        result = check_kubeconfig(config)
        assert result.passed is False
        assert "not found" in result.message

    def test_not_readable(self, tmp_path: Path) -> None:
        kubeconfig = tmp_path / "kubeconfig"
        kubeconfig.write_text("apiVersion: v1\n")
        config = PreflightConfig(kubeconfig=str(kubeconfig))
        with patch("cli.services.preflight.os.access", return_value=False):
            result = check_kubeconfig(config)
        assert result.passed is False
        assert "not readable" in result.message


class TestCheckK3d:
    def test_found(self) -> None:
        with patch("cli.services.preflight.shutil.which", return_value="/usr/bin/k3d"):
            result = check_k3d()
        assert result.passed is True
        assert "k3d found" in result.message

    def test_not_found_warns(self) -> None:
        with patch("cli.services.preflight.shutil.which", return_value=None):
            result = check_k3d()
        assert result.passed is True
        assert result.warn_only is True


class TestCheckNamespace:
    def test_configured(self) -> None:
        config = PreflightConfig(namespace="volundr")
        result = check_namespace(config)
        assert result.passed is True
        assert "volundr" in result.message

    def test_empty(self) -> None:
        config = PreflightConfig(namespace="")
        result = check_namespace(config)
        assert result.passed is False
        assert "not configured" in result.message


class TestCheckClusterConnectivity:
    def test_reachable(self, tmp_path: Path) -> None:
        kubeconfig = tmp_path / "kubeconfig"
        kubeconfig.write_text("apiVersion: v1\n")
        config = PreflightConfig(kubeconfig=str(kubeconfig))
        with (
            patch("cli.services.preflight.shutil.which", return_value="/usr/bin/kubectl"),
            patch("cli.services.preflight.subprocess.run") as mock_run,
        ):
            mock_run.return_value = type(
                "Result", (), {"returncode": 0, "stdout": "ok", "stderr": ""}
            )()
            result = check_cluster_connectivity(config)
        assert result.passed is True
        assert "reachable" in result.message

    def test_unreachable(self, tmp_path: Path) -> None:
        kubeconfig = tmp_path / "kubeconfig"
        kubeconfig.write_text("apiVersion: v1\n")
        config = PreflightConfig(kubeconfig=str(kubeconfig))
        with (
            patch("cli.services.preflight.shutil.which", return_value="/usr/bin/kubectl"),
            patch("cli.services.preflight.subprocess.run") as mock_run,
        ):
            mock_run.return_value = type(
                "Result",
                (),
                {"returncode": 1, "stdout": "", "stderr": "connection refused"},
            )()
            result = check_cluster_connectivity(config)
        assert result.passed is False
        assert "Cannot connect" in result.message

    def test_timeout(self, tmp_path: Path) -> None:
        import subprocess

        kubeconfig = tmp_path / "kubeconfig"
        kubeconfig.write_text("apiVersion: v1\n")
        config = PreflightConfig(kubeconfig=str(kubeconfig), cluster_connect_timeout=5)
        with (
            patch("cli.services.preflight.shutil.which", return_value="/usr/bin/kubectl"),
            patch(
                "cli.services.preflight.subprocess.run",
                side_effect=subprocess.TimeoutExpired("kubectl", 5),
            ),
        ):
            result = check_cluster_connectivity(config)
        assert result.passed is False
        assert "timed out" in result.message
        assert "5s" in result.message

    def test_no_kubectl(self) -> None:
        config = PreflightConfig()
        with patch("cli.services.preflight.shutil.which", return_value=None):
            result = check_cluster_connectivity(config)
        assert result.passed is False
        assert "kubectl not available" in result.message

    def test_os_error(self, tmp_path: Path) -> None:
        kubeconfig = tmp_path / "kubeconfig"
        kubeconfig.write_text("apiVersion: v1\n")
        config = PreflightConfig(kubeconfig=str(kubeconfig))
        with (
            patch("cli.services.preflight.shutil.which", return_value="/usr/bin/kubectl"),
            patch(
                "cli.services.preflight.subprocess.run",
                side_effect=OSError("exec failed"),
            ),
        ):
            result = check_cluster_connectivity(config)
        assert result.passed is False
        assert "Failed to run" in result.message


class TestRunClusterPreflightChecks:
    def test_all_cluster_checks_run(self, tmp_path: Path) -> None:
        kubeconfig = tmp_path / "kubeconfig"
        kubeconfig.write_text("apiVersion: v1\n")
        config = PreflightConfig(
            mode="cluster",
            kubeconfig=str(kubeconfig),
            namespace="test-ns",
        )
        with (
            patch("cli.services.preflight.shutil.which", return_value="/usr/bin/kubectl"),
            patch("cli.services.preflight.subprocess.run") as mock_run,
        ):
            mock_run.return_value = type(
                "Result", (), {"returncode": 0, "stdout": "ok", "stderr": ""}
            )()
            results = run_cluster_preflight_checks(config)

        names = [r.name for r in results]
        assert "kubectl" in names
        assert "kubeconfig" in names
        assert "k3d" in names
        assert "namespace" in names
        assert "cluster connectivity" in names
        assert len(results) == 5
