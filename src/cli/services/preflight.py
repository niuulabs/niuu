"""Preflight checks — validate the environment before starting services.

Each check returns a PreflightResult with pass/fail and an actionable error
message. Checks are run sequentially; the first failure can optionally abort
the entire startup.
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

# Defaults — override via PreflightConfig
DEFAULT_PORTS: list[int] = [8080, 8081, 9100]
MIN_DISK_SPACE_BYTES = 1_073_741_824  # 1 GB


@dataclass(frozen=True)
class PreflightResult:
    """Result of a single preflight check."""

    name: str
    passed: bool
    message: str
    warn_only: bool = False


@dataclass
class PreflightConfig:
    """Configuration for preflight checks."""

    claude_binary: str = "claude"
    api_key_env: str = "ANTHROPIC_API_KEY"
    ports: list[int] = field(default_factory=lambda: list(DEFAULT_PORTS))
    workspaces_dir: str = "~/.niuu/workspaces"
    min_disk_space_bytes: int = MIN_DISK_SPACE_BYTES
    database_mode: str = "embedded"
    database_dsn: str = ""
    # Cluster mode fields
    mode: str = "mini"
    kubeconfig: str = "~/.kube/config"
    namespace: str = "volundr"
    cluster_connect_timeout: int = 10


def check_claude_binary(config: PreflightConfig) -> PreflightResult:
    """Verify the claude binary is available and executable."""
    binary = config.claude_binary
    resolved = shutil.which(binary)
    if not resolved:
        return PreflightResult(
            name="claude binary",
            passed=False,
            message=f"claude binary '{binary}' not found in PATH. "
            "Install it or set claude_binary in config.",
        )
    return PreflightResult(
        name="claude binary",
        passed=True,
        message=f"claude binary found: {resolved}",
    )


def check_api_key(config: PreflightConfig) -> PreflightResult:
    """Verify the Anthropic API key is set."""
    key = os.environ.get(config.api_key_env, "")
    if not key:
        return PreflightResult(
            name="API key",
            passed=False,
            message=f"{config.api_key_env} is not set. Export it or add it to your secrets config.",
        )
    # Basic format validation — key should be non-trivial
    if len(key) < 10:
        return PreflightResult(
            name="API key",
            passed=False,
            message=f"{config.api_key_env} appears invalid (too short).",
        )
    return PreflightResult(
        name="API key",
        passed=True,
        message=f"{config.api_key_env} is set.",
    )


def check_port_available(port: int) -> PreflightResult:
    """Check a single port is not already bound."""
    with suppress(OSError):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            result = sock.connect_ex(("127.0.0.1", port))
            if result == 0:
                return PreflightResult(
                    name=f"port {port}",
                    passed=False,
                    message=f"Port {port} is already in use. "
                    "Stop the conflicting process or change the port in config.",
                )
    return PreflightResult(
        name=f"port {port}",
        passed=True,
        message=f"Port {port} is available.",
    )


def check_ports(config: PreflightConfig) -> list[PreflightResult]:
    """Check all configured ports are available."""
    return [check_port_available(port) for port in config.ports]


def check_workspace_dir(config: PreflightConfig) -> PreflightResult:
    """Verify workspaces_dir exists and is writable, create if missing."""
    ws_path = Path(config.workspaces_dir).expanduser()
    try:
        ws_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return PreflightResult(
            name="workspace dir",
            passed=False,
            message=f"Cannot create workspace directory '{ws_path}': {exc}",
        )
    if not os.access(ws_path, os.W_OK):
        return PreflightResult(
            name="workspace dir",
            passed=False,
            message=f"Workspace directory '{ws_path}' is not writable.",
        )
    return PreflightResult(
        name="workspace dir",
        passed=True,
        message=f"Workspace directory ready: {ws_path}",
    )


def check_database(config: PreflightConfig) -> PreflightResult:
    """Verify database availability based on mode."""
    if config.database_mode == "embedded":
        from niuu.adapters.embedded_postgres import pg_bin_dir

        bin_dir = pg_bin_dir()
        if bin_dir is not None and (bin_dir / "postgres").exists():
            return PreflightResult(
                name="database",
                passed=True,
                message=f"Embedded PostgreSQL binaries available at {bin_dir}.",
            )
        return PreflightResult(
            name="database",
            passed=False,
            message="Embedded PostgreSQL binaries not found. Rebuild with: make build-postgres",
        )
    # external mode — just check DSN is set
    if not config.database_dsn:
        return PreflightResult(
            name="database",
            passed=False,
            message="External database mode requires database_dsn to be set.",
        )
    return PreflightResult(
        name="database",
        passed=True,
        message="External database DSN configured.",
    )


def check_xcode() -> PreflightResult:
    """Check Xcode command line tools on macOS. Skip on other platforms."""
    import platform as _platform

    if _platform.system() != "Darwin":
        return PreflightResult(name="xcode", passed=True, message="Not macOS — skipped.")

    xcode_select = shutil.which("xcode-select")
    if not xcode_select:
        return PreflightResult(
            name="xcode",
            passed=True,
            warn_only=True,
            message="xcode-select not found — git operations may fail.",
        )

    try:
        result = subprocess.run(
            ["xcode-select", "-p"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return PreflightResult(
                name="xcode",
                passed=True,
                warn_only=True,
                message="Xcode command line tools not installed. Run: xcode-select --install",
            )
        return PreflightResult(
            name="xcode",
            passed=True,
            message=f"Xcode CLT: {result.stdout.strip()}",
        )
    except Exception:
        return PreflightResult(
            name="xcode",
            passed=True,
            warn_only=True,
            message="Could not check Xcode status.",
        )


def check_git() -> PreflightResult:
    """Verify git is installed. Warn if gh is missing."""
    git_path = shutil.which("git")
    if not git_path:
        return PreflightResult(
            name="git",
            passed=False,
            message="git is not installed. Install it to continue.",
        )
    gh_path = shutil.which("gh")
    if not gh_path:
        return PreflightResult(
            name="git",
            passed=True,
            warn_only=True,
            message="git found. Warning: gh (GitHub CLI) not installed — "
            "PR features will be unavailable.",
        )
    return PreflightResult(
        name="git",
        passed=True,
        message="git and gh found.",
    )


def check_disk_space(config: PreflightConfig) -> PreflightResult:
    """Warn if less than 1GB free in workspaces_dir."""
    ws_path = Path(config.workspaces_dir).expanduser()
    # Use parent if dir doesn't exist yet
    check_path = ws_path if ws_path.exists() else ws_path.parent
    if not check_path.exists():
        check_path = Path.home()
    try:
        stat = shutil.disk_usage(check_path)
        if stat.free < config.min_disk_space_bytes:
            free_mb = stat.free // (1024 * 1024)
            return PreflightResult(
                name="disk space",
                passed=True,
                warn_only=True,
                message=f"Low disk space: {free_mb}MB free in {check_path}. "
                "Recommend at least 1GB.",
            )
    except OSError as exc:
        return PreflightResult(
            name="disk space",
            passed=True,
            warn_only=True,
            message=f"Could not check disk space: {exc}",
        )
    return PreflightResult(
        name="disk space",
        passed=True,
        message="Sufficient disk space available.",
    )


def check_kubectl() -> PreflightResult:
    """Verify kubectl is available in PATH."""
    resolved = shutil.which("kubectl")
    if not resolved:
        return PreflightResult(
            name="kubectl",
            passed=False,
            message="kubectl not found in PATH. Install it: https://kubernetes.io/docs/tasks/tools/",
        )
    return PreflightResult(
        name="kubectl",
        passed=True,
        message=f"kubectl found: {resolved}",
    )


def check_kubeconfig(config: PreflightConfig) -> PreflightResult:
    """Verify kubeconfig file exists and is readable."""
    kubeconfig_path = Path(config.kubeconfig).expanduser()
    if not kubeconfig_path.exists():
        return PreflightResult(
            name="kubeconfig",
            passed=False,
            message=f"kubeconfig not found at '{kubeconfig_path}'. "
            "Set kubeconfig in config or check your cluster setup.",
        )
    if not os.access(kubeconfig_path, os.R_OK):
        return PreflightResult(
            name="kubeconfig",
            passed=False,
            message=f"kubeconfig at '{kubeconfig_path}' is not readable.",
        )
    return PreflightResult(
        name="kubeconfig",
        passed=True,
        message=f"kubeconfig found: {kubeconfig_path}",
    )


def check_k3d() -> PreflightResult:
    """Check k3d availability — warn-only if missing."""
    resolved = shutil.which("k3d")
    if not resolved:
        return PreflightResult(
            name="k3d",
            passed=True,
            warn_only=True,
            message="k3d not found in PATH. Not required unless using k3d clusters.",
        )
    return PreflightResult(
        name="k3d",
        passed=True,
        message=f"k3d found: {resolved}",
    )


def check_namespace(config: PreflightConfig) -> PreflightResult:
    """Check that the target namespace is configured."""
    if not config.namespace:
        return PreflightResult(
            name="namespace",
            passed=False,
            message="Target namespace is not configured. Set namespace in pod_manager config.",
        )
    return PreflightResult(
        name="namespace",
        passed=True,
        message=f"Target namespace: {config.namespace}",
    )


def check_cluster_connectivity(config: PreflightConfig) -> PreflightResult:
    """Verify cluster is reachable by running kubectl cluster-info."""
    kubectl = shutil.which("kubectl")
    if not kubectl:
        return PreflightResult(
            name="cluster connectivity",
            passed=False,
            message="kubectl not available — cannot verify cluster connectivity.",
        )
    kubeconfig_path = Path(config.kubeconfig).expanduser()
    cmd = [kubectl, "cluster-info"]
    if kubeconfig_path.exists():
        cmd.extend(["--kubeconfig", str(kubeconfig_path)])
    try:
        result = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            timeout=config.cluster_connect_timeout,
        )
        if result.returncode != 0:
            return PreflightResult(
                name="cluster connectivity",
                passed=False,
                message=f"Cannot connect to cluster: {result.stderr.strip()}",
            )
    except subprocess.TimeoutExpired:
        return PreflightResult(
            name="cluster connectivity",
            passed=False,
            message=f"Cluster connection timed out after {config.cluster_connect_timeout}s.",
        )
    except OSError as exc:
        return PreflightResult(
            name="cluster connectivity",
            passed=False,
            message=f"Failed to run kubectl: {exc}",
        )
    return PreflightResult(
        name="cluster connectivity",
        passed=True,
        message="Cluster is reachable.",
    )


def run_cluster_preflight_checks(config: PreflightConfig) -> list[PreflightResult]:
    """Run cluster-specific preflight checks."""
    results: list[PreflightResult] = []
    results.append(check_kubectl())
    results.append(check_kubeconfig(config))
    results.append(check_k3d())
    results.append(check_namespace(config))
    results.append(check_cluster_connectivity(config))
    return results


def run_preflight_checks(config: PreflightConfig) -> list[PreflightResult]:
    """Run all preflight checks and return results.

    Checks are run sequentially. All results are collected regardless
    of pass/fail so the caller can decide how to handle them.

    In cluster mode, mini-specific checks (claude binary, workspace dir) are
    replaced with cluster-specific checks (kubectl, kubeconfig, namespace).
    """
    results: list[PreflightResult] = []

    if config.mode == "cluster":
        results.extend(run_cluster_preflight_checks(config))
        results.append(check_api_key(config))
        results.extend(check_ports(config))
        results.append(check_database(config))
        results.append(check_git())
        results.append(check_disk_space(config))
        return results

    # Mini mode (default)
    results.append(check_claude_binary(config))
    results.append(check_api_key(config))
    results.extend(check_ports(config))
    results.append(check_workspace_dir(config))
    results.append(check_database(config))
    results.append(check_git())
    results.append(check_xcode())
    results.append(check_disk_space(config))
    return results


def has_failures(results: Sequence[PreflightResult]) -> bool:
    """Return True if any non-warning check failed."""
    return any(not r.passed and not r.warn_only for r in results)


def format_results(results: Sequence[PreflightResult]) -> str:
    """Format preflight results for terminal output."""
    lines: list[str] = []
    for r in results:
        if not r.passed:
            icon = "FAIL"
        elif r.warn_only:
            icon = "WARN"
        else:
            icon = " OK "
        lines.append(f"  [{icon}] {r.name}: {r.message}")
    return "\n".join(lines)
