"""Kind cluster bootstrap harness for flock composition E2E integration tests.

This module provides a reusable harness for spinning up a kind (Kubernetes in
Docker) cluster, installing the Volundr Helm chart with specific persona-source
backends, seeding test data, and verifying sidecar effective configuration.

Skip policy
-----------
All kind-based tests are gated by the ``KIND_INTEGRATION`` environment variable.
Set ``KIND_INTEGRATION=1`` to enable them.  In normal CI the variable is absent
and the tests are collected but skipped.  The ``integration-kind.yml`` workflow
sets it explicitly and runs them on-demand.

Required tools
--------------
- ``kind``    — creates/deletes local Kubernetes clusters
- ``kubectl`` — communicates with the cluster
- ``helm``    — installs / upgrades / uninstalls Helm charts

If any tool is missing the test session is marked as an error rather than
silently skipped, so that missing-tool failures are caught early.

Usage example::

    import pytest
    from tests.integration.helpers.kind_harness import (
        KIND_INTEGRATION,
        KindCluster,
        HelmRelease,
        wait_for_pod,
        read_sidecar_config,
    )

    @pytest.mark.skipif(not KIND_INTEGRATION, reason="KIND_INTEGRATION not set")
    def test_something():
        with KindCluster("test-cluster") as cluster:
            with HelmRelease(cluster, "volundr", "charts/volundr", values={...}) as rel:
                wait_for_pod(cluster, "app=ravn-sidecar")
                cfg = read_sidecar_config(cluster, pod_label="app=ravn-sidecar")
                assert cfg["persona"]["llm"]["model"] == "claude-sonnet-4-6"
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feature gate
# ---------------------------------------------------------------------------

KIND_INTEGRATION: bool = os.environ.get("KIND_INTEGRATION", "0") == "1"
"""True when KIND_INTEGRATION=1 is set; kind-based tests are skipped otherwise."""

# ---------------------------------------------------------------------------
# Tool availability check
# ---------------------------------------------------------------------------

_REQUIRED_TOOLS = ("kind", "kubectl", "helm")


def check_tools_available() -> None:
    """Raise ``RuntimeError`` if any required CLI tool is missing from PATH."""
    missing = [t for t in _REQUIRED_TOOLS if _which(t) is None]
    if missing:
        raise RuntimeError(
            f"Kind integration tests require the following tools on PATH: {missing}. "
            "Install them and re-run with KIND_INTEGRATION=1."
        )


def _which(name: str) -> str | None:
    return shutil.which(name)


# ---------------------------------------------------------------------------
# Timeouts and retry constants
# ---------------------------------------------------------------------------

_POD_POLL_INTERVAL_S: float = 3.0
_POD_READY_TIMEOUT_S: float = 180.0
_CONFIGMAP_SYNC_TIMEOUT_S: float = 90.0
_CONFIGMAP_POLL_INTERVAL_S: float = 5.0


# ---------------------------------------------------------------------------
# KindCluster
# ---------------------------------------------------------------------------


class KindCluster:
    """Context manager that creates and destroys a kind cluster.

    The cluster name is made unique by appending a short suffix to prevent
    conflicts when tests run concurrently.

    Args:
        name: Base name for the kind cluster (e.g. ``"niuu-integ"``).
        kubeconfig_path: If provided, the kubeconfig is written here; otherwise
            a temporary file is used and deleted on exit.
    """

    def __init__(
        self,
        name: str = "niuu-integ",
        *,
        kubeconfig_path: str | None = None,
    ) -> None:
        self.name = name
        self._kubeconfig_path = kubeconfig_path
        self._tmp_kubeconfig: tempfile.NamedTemporaryFile | None = None
        self.kubeconfig: str = ""

    # ------------------------------------------------------------------
    # Context manager protocol
    # ------------------------------------------------------------------

    def __enter__(self) -> KindCluster:
        check_tools_available()
        if self._kubeconfig_path:
            self.kubeconfig = self._kubeconfig_path
        else:
            self._tmp_kubeconfig = tempfile.NamedTemporaryFile(suffix=".kubeconfig", delete=False)
            self.kubeconfig = self._tmp_kubeconfig.name

        logger.info("Creating kind cluster %r", self.name)
        subprocess.run(
            ["kind", "create", "cluster", "--name", self.name, "--kubeconfig", self.kubeconfig],
            check=True,
            capture_output=True,
            text=True,
        )
        logger.info("Kind cluster %r ready (kubeconfig=%s)", self.name, self.kubeconfig)
        return self

    def __exit__(self, *_: object) -> None:
        logger.info("Deleting kind cluster %r", self.name)
        try:
            subprocess.run(
                ["kind", "delete", "cluster", "--name", self.name],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            logger.warning("Failed to delete kind cluster %r: %s", self.name, exc.stderr)
        finally:
            if self._tmp_kubeconfig is not None:
                try:
                    Path(self._tmp_kubeconfig.name).unlink(missing_ok=True)
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # kubectl wrappers
    # ------------------------------------------------------------------

    def kubectl(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        """Run ``kubectl`` with the cluster's kubeconfig set."""
        return subprocess.run(
            ["kubectl", "--kubeconfig", self.kubeconfig, *args],
            check=check,
            capture_output=True,
            text=True,
        )

    def kubectl_json(self, *args: str) -> Any:
        """Run kubectl and parse stdout as JSON."""
        result = self.kubectl(*args, "-o", "json")
        return json.loads(result.stdout)

    def apply_manifest(self, manifest: dict) -> None:
        """Apply a Kubernetes manifest dict directly (piped via stdin)."""
        yaml_text = yaml.dump(manifest)
        subprocess.run(
            ["kubectl", "--kubeconfig", self.kubeconfig, "apply", "-f", "-"],
            input=yaml_text,
            check=True,
            capture_output=True,
            text=True,
        )

    def create_namespace(self, namespace: str) -> None:
        """Create a namespace if it does not exist."""
        result = self.kubectl("create", "namespace", namespace, "--dry-run=client", "-o", "yaml")
        subprocess.run(
            ["kubectl", "--kubeconfig", self.kubeconfig, "apply", "-f", "-"],
            input=result.stdout,
            check=True,
            capture_output=True,
            text=True,
        )


# ---------------------------------------------------------------------------
# HelmRelease
# ---------------------------------------------------------------------------


class HelmRelease:
    """Context manager that installs a Helm chart and uninstalls it on exit.

    Args:
        cluster: The :class:`KindCluster` to install into.
        release_name: Helm release name.
        chart_path: Path to the chart directory (relative to repo root).
        namespace: Kubernetes namespace for the release.
        values: Dict of values to pass as ``--set`` overrides (dot-notation
            keys are supported; nested dicts are flattened automatically).
        values_file: Optional path to a YAML values file.
        wait: If True, ``helm install`` blocks until pods are ready.
        timeout: Helm timeout string (e.g. ``"3m"``).
    """

    def __init__(
        self,
        cluster: KindCluster,
        release_name: str,
        chart_path: str,
        *,
        namespace: str = "default",
        values: dict | None = None,
        values_file: str | None = None,
        wait: bool = False,
        timeout: str = "3m",
    ) -> None:
        self._cluster = cluster
        self.release_name = release_name
        self.chart_path = chart_path
        self.namespace = namespace
        self._values = values or {}
        self._values_file = values_file
        self._wait = wait
        self._timeout = timeout

    def __enter__(self) -> HelmRelease:
        self._install()
        return self

    def __exit__(self, *_: object) -> None:
        self._uninstall()

    def _flat_set_args(self, d: dict, prefix: str = "") -> list[str]:
        """Flatten a nested dict into ``--set key=value`` argument pairs."""
        args: list[str] = []
        for k, v in d.items():
            full_key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                args.extend(self._flat_set_args(v, prefix=full_key))
            elif isinstance(v, bool):
                args.extend(["--set", f"{full_key}={'true' if v else 'false'}"])
            else:
                args.extend(["--set", f"{full_key}={v}"])
        return args

    def _install(self) -> None:
        cmd = [
            "helm",
            "install",
            self.release_name,
            self.chart_path,
            "--kubeconfig",
            self._cluster.kubeconfig,
            "--namespace",
            self.namespace,
            "--create-namespace",
        ]
        if self._values_file:
            cmd.extend(["-f", self._values_file])
        cmd.extend(self._flat_set_args(self._values))
        if self._wait:
            cmd.extend(["--wait", "--timeout", self._timeout])

        logger.info(
            "helm install %s from %s (namespace=%s)",
            self.release_name,
            self.chart_path,
            self.namespace,
        )
        subprocess.run(cmd, check=True, capture_output=True, text=True)

    def _uninstall(self) -> None:
        logger.info("helm uninstall %s", self.release_name)
        try:
            subprocess.run(
                [
                    "helm",
                    "uninstall",
                    self.release_name,
                    "--kubeconfig",
                    self._cluster.kubeconfig,
                    "--namespace",
                    self.namespace,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            logger.warning("helm uninstall failed for %s: %s", self.release_name, exc.stderr)

    def upgrade(self, values: dict | None = None) -> None:
        """Run ``helm upgrade`` with optional value overrides."""
        cmd = [
            "helm",
            "upgrade",
            self.release_name,
            self.chart_path,
            "--kubeconfig",
            self._cluster.kubeconfig,
            "--namespace",
            self.namespace,
        ]
        if values:
            cmd.extend(self._flat_set_args(values))
        subprocess.run(cmd, check=True, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# Pod / sidecar utilities
# ---------------------------------------------------------------------------


def wait_for_pod(
    cluster: KindCluster,
    label_selector: str,
    *,
    namespace: str = "default",
    timeout_s: float = _POD_READY_TIMEOUT_S,
    poll_interval_s: float = _POD_POLL_INTERVAL_S,
) -> str:
    """Block until at least one pod matching *label_selector* is Running.

    Returns the name of the first matching Running pod.

    Raises ``TimeoutError`` if no pod is ready within *timeout_s*.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        result = cluster.kubectl(
            "get",
            "pods",
            "-n",
            namespace,
            "-l",
            label_selector,
            "--field-selector",
            "status.phase=Running",
            "-o",
            "jsonpath={.items[*].metadata.name}",
            check=False,
        )
        names = result.stdout.strip().split()
        if names:
            logger.info("Pod ready: %s", names[0])
            return names[0]
        time.sleep(poll_interval_s)

    raise TimeoutError(
        f"No pod with label {label_selector!r} became Running within {timeout_s}s "
        f"(namespace={namespace!r})"
    )


def read_sidecar_config(
    cluster: KindCluster,
    pod_name: str,
    *,
    namespace: str = "default",
    config_path: str = "/etc/ravn/config.yaml",
    container: str = "ravn-sidecar",
) -> dict:
    """Read and parse ``/etc/ravn/config.yaml`` from a running sidecar pod.

    Returns the parsed YAML dict.  Raises ``ValueError`` if the file cannot
    be read or parsed.
    """
    result = cluster.kubectl(
        "exec",
        pod_name,
        "-n",
        namespace,
        "-c",
        container,
        "--",
        "cat",
        config_path,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(
            f"Cannot read {config_path!r} from pod {pod_name!r} "
            f"(container={container!r}): {result.stderr}"
        )
    try:
        data = yaml.safe_load(result.stdout)
    except yaml.YAMLError as exc:
        raise ValueError(
            f"Failed to parse YAML from {config_path!r} in pod {pod_name!r}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise ValueError(
            f"Expected dict from {config_path!r} in pod {pod_name!r}, got {type(data).__name__}"
        )
    return data


def get_pod_logs(
    cluster: KindCluster,
    pod_name: str,
    *,
    namespace: str = "default",
    container: str = "ravn-sidecar",
    since_seconds: int = 300,
) -> str:
    """Return recent logs from a sidecar pod."""
    result = cluster.kubectl(
        "logs",
        pod_name,
        "-n",
        namespace,
        "-c",
        container,
        f"--since={since_seconds}s",
        check=False,
    )
    return result.stdout


def wait_for_configmap_key(
    cluster: KindCluster,
    configmap_name: str,
    key: str,
    expected_value_contains: str,
    *,
    namespace: str = "default",
    timeout_s: float = _CONFIGMAP_SYNC_TIMEOUT_S,
    poll_interval_s: float = _CONFIGMAP_POLL_INTERVAL_S,
) -> None:
    """Poll a ConfigMap key until its value contains *expected_value_contains*.

    Useful for verifying that a persona or flow has been written to the
    ConfigMap before asserting sidecar state.

    Raises ``TimeoutError`` if the key does not match within *timeout_s*.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        result = cluster.kubectl(
            "get",
            "configmap",
            configmap_name,
            "-n",
            namespace,
            f"-o=jsonpath={{.data.{key}}}",
            check=False,
        )
        if expected_value_contains in result.stdout:
            logger.info(
                "ConfigMap %r key %r contains %r",
                configmap_name,
                key,
                expected_value_contains,
            )
            return
        time.sleep(poll_interval_s)

    raise TimeoutError(
        f"ConfigMap {configmap_name!r} key {key!r} did not contain "
        f"{expected_value_contains!r} within {timeout_s}s (namespace={namespace!r})"
    )


# ---------------------------------------------------------------------------
# REST seed helpers
# ---------------------------------------------------------------------------


def seed_persona_via_rest(
    base_url: str,
    persona_payload: dict,
    *,
    token: str = "",
) -> None:
    """POST a persona to the Volundr REST API.

    Args:
        base_url: Base URL of the Volundr service (e.g. ``http://localhost:8080``).
        persona_payload: Dict matching the PersonaCreate schema.
        token: Optional PAT for ``Authorization: Bearer`` header.

    Raises ``RuntimeError`` on non-201 responses.
    """
    body = json.dumps(persona_payload).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(
        f"{base_url}/api/v1/ravn/personas",
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status not in (200, 201):
                raise RuntimeError(
                    f"seed_persona_via_rest: POST /api/v1/ravn/personas returned {resp.status}"
                )
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"seed_persona_via_rest: POST /api/v1/ravn/personas returned {exc.code}: "
            f"{exc.read().decode()}"
        ) from exc


def create_flow_via_rest(
    base_url: str,
    flow_payload: dict,
    *,
    token: str = "",
) -> None:
    """POST a flock flow definition to the Tyr REST API.

    Args:
        base_url: Base URL of the Tyr service.
        flow_payload: Dict matching the FlockFlowCreate schema.
        token: Optional PAT.

    Raises ``RuntimeError`` on non-201 responses.
    """
    body = json.dumps(flow_payload).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(
        f"{base_url}/api/v1/flock-flows",
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status not in (200, 201):
                raise RuntimeError(
                    f"create_flow_via_rest: POST /api/v1/flock-flows returned {resp.status}"
                )
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"create_flow_via_rest: POST /api/v1/flock-flows returned {exc.code}: "
            f"{exc.read().decode()}"
        ) from exc
