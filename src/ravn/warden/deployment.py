"""Composition helpers for warden deployment backends."""

from __future__ import annotations

import importlib
from pathlib import Path

from ravn.ports.warden_deployer import WardenDeploymentPort
from ravn.warden.models import WardenSpec
from ravn.warden.store import WardenStore

_DEFAULT_ADAPTERS = {
    "launchd": "ravn.adapters.deployment.LaunchdWardenDeploymentAdapter",
    "systemd": "ravn.adapters.deployment.SystemdUserWardenDeploymentAdapter",
    "k8s-apply": "ravn.adapters.deployment.KubernetesApplyWardenDeploymentAdapter",
    "k8s-gitops": "ravn.adapters.deployment.KubernetesGitOpsWardenDeploymentAdapter",
}


def resolve_deployment_adapter(deployment: str) -> str:
    """Resolve a deployment shorthand to its adapter class path."""
    return _DEFAULT_ADAPTERS.get(deployment, _DEFAULT_ADAPTERS["launchd"])


def create_warden_deployer(spec: WardenSpec) -> WardenDeploymentPort:
    """Construct the deployment adapter configured for one warden."""
    dotted_path = spec.deployment_adapter or resolve_deployment_adapter(spec.deployment)
    module_path, class_name = dotted_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    kwargs = dict(spec.deployment_kwargs)
    return cls(**kwargs)


def build_warden_store(root: Path | None = None) -> WardenStore:
    """Return a store wired with the default deployment adapter factory."""
    return WardenStore(root=root, deployer_factory=create_warden_deployer)
