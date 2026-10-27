"""OpenShell request rules Völundr checks before it sends anything to a gateway.

Pure Python, no SDK import: the flock contributor and the CLI-driven VM runtime
use these rules without the ``openshell`` extra. Each rule mirrors one the
pinned OpenShell gateway (or its Kubernetes driver) enforces, so a bad request
fails here with the remedy instead of as an opaque gRPC rejection.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath
from typing import Any

#: OpenShell's peer-workload limits (``openshell_sandbox_backend::peer_workloads``).
MAX_PEER_WORKLOADS = 16
MAX_PEER_NAME_LENGTH = 40
RESERVED_PEER_NAMES = frozenset({"agent", "supervisor"})
RESERVED_PEER_NAME_PREFIX = "openshell"
#: The only fields the Kubernetes driver accepts on ``containers.workloads[]``.
PEER_WORKLOAD_FIELDS = frozenset(
    {"name", "image", "command", "environment", "resources", "volume_mounts"}
)
PEER_RESOURCE_FIELDS = frozenset({"requests", "limits"})
VOLUME_MOUNT_FIELDS = frozenset({"name", "mount_path", "sub_path", "read_only"})
#: Volume names the driver keeps for its own peer volumes.
RESERVED_VOLUME_PREFIX = "openshell-peer-"
#: Mount targets the driver owns in every container.
PROTECTED_MOUNT_ROOTS = (PurePosixPath("/.openshell"), PurePosixPath("/run"))
RESERVED_ENV_PREFIX = "OPENSHELL_"

#: The workspace Völundr's sandboxes live in.
OPENSHELL_WORKSPACE = "default"
#: The labels OpenShell's resource admission requires, by default, on a PVC a
#: sandbox mounts through its driver config (``required_labels`` defaults;
#: ``${workspace}`` resolved to OPENSHELL_WORKSPACE).
DEFAULT_SANDBOX_ATTACHABLE_LABELS = {
    "openshell.ai/sandbox-attachable": "true",
    "openshell.ai/sandbox-attachable-workspace": OPENSHELL_WORKSPACE,
}

#: Pod label/annotation domains no gateway allowlist can admit.
RESERVED_POD_METADATA_DOMAINS = ("openshell.ai", "agents.x-k8s.io")

#: NetworkEndpoint mode fields, spelled as in OpenShell's policy YAML.
NETWORK_TLS_MODES = ("", "skip")
#: Accepted before OpenShell v0.1.6; the gateway now rejects them.
REMOVED_TLS_MODES = frozenset({"terminate", "passthrough"})
NETWORK_ENFORCEMENT_MODES = ("", "enforce", "audit")
NETWORK_ACCESS_PRESETS = ("", "read-only", "read-write", "full")
_NETWORK_MODES = {
    "tls": NETWORK_TLS_MODES,
    "enforcement": NETWORK_ENFORCEMENT_MODES,
    "access": NETWORK_ACCESS_PRESETS,
}

_DNS_1123_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
_DNS_1123_LABEL_MAX = 63


def network_mode(field: str, value: object) -> str:
    """The canonical policy-YAML spelling of a NetworkEndpoint mode, or ValueError."""
    text = str(value if value is not None else "").strip()
    if field == "tls" and text in REMOVED_TLS_MODES:
        raise ValueError(
            f"OpenShell network endpoint tls: {text!r} is no longer accepted; "
            "omit tls to keep automatic TLS termination, or set tls: skip to relay "
            "the connection uninspected"
        )
    allowed = _NETWORK_MODES[field]
    if text not in allowed:
        choices = ", ".join(repr(mode) for mode in allowed if mode)
        raise ValueError(
            f"OpenShell network endpoint {field}: {text!r} is not one of {choices} (or omitted)"
        )
    return text


def check_network_endpoint_modes(endpoint: Mapping[str, Any]) -> None:
    """Raise when a policy-YAML endpoint names a mode the gateway rejects."""
    for field in _NETWORK_MODES:
        if field in endpoint:
            network_mode(field, endpoint[field])


def check_pod_metadata_keys(kind: str, keys: Iterable[str]) -> None:
    """Raise for pod label/annotation keys in a domain OpenShell reserves."""
    reserved = sorted(key for key in keys if _reserved_metadata_key(str(key)))
    if reserved:
        raise ValueError(
            f"OpenShell sandbox pod {kind} {', '.join(reserved)} use a domain OpenShell "
            f"reserves ({', '.join(RESERVED_POD_METADATA_DOMAINS)}); no gateway allowlist "
            "admits them — rename them"
        )


def _reserved_metadata_key(key: str) -> bool:
    if "/" not in key:
        return False
    domain = key.split("/", 1)[0]
    return any(
        domain == reserved or domain.endswith(f".{reserved}")
        for reserved in RESERVED_POD_METADATA_DOMAINS
    )


def check_peer_workload_name(name: object) -> str:
    """A peer workload container name OpenShell accepts, or ValueError naming the rule."""
    text = str(name or "")
    if not text or len(text) > MAX_PEER_NAME_LENGTH or not _DNS_1123_LABEL.match(text):
        raise ValueError(
            f"OpenShell peer workload name {text!r} must be a DNS-1123 label of at most "
            f"{MAX_PEER_NAME_LENGTH} characters (lowercase letters, digits and '-')"
        )
    if text in RESERVED_PEER_NAMES or text.startswith(RESERVED_PEER_NAME_PREFIX):
        raise ValueError(
            f"OpenShell peer workload name {text!r} is reserved: not 'agent', 'supervisor' "
            f"or a name starting with {RESERVED_PEER_NAME_PREFIX!r}"
        )
    return text


def check_peer_workloads(
    workloads: list[dict[str, Any]],
    volumes: list[dict[str, Any]],
) -> None:
    """Raise unless ``workloads``/``volumes`` are exactly what the driver accepts.

    Mirrors the Kubernetes driver's ``containers.workloads`` and ``volumes``
    validation: field sets, names, commands, environment keys and mounts.
    Peers also need the gateway's ``allow_driver_config = true``.
    """
    if len(workloads) > MAX_PEER_WORKLOADS:
        raise ValueError(
            f"OpenShell supports at most {MAX_PEER_WORKLOADS} peer workloads per sandbox, "
            f"got {len(workloads)}; split the flock across sandboxes"
        )
    volume_read_only = _check_volumes(volumes)
    names: set[str] = set()
    for workload in workloads:
        if not isinstance(workload, dict):
            raise ValueError("OpenShell peer workload must be a mapping")
        unknown = sorted(set(workload) - PEER_WORKLOAD_FIELDS)
        if unknown:
            raise ValueError(
                f"OpenShell peer workload {workload.get('name')!r} has fields the driver "
                f"rejects: {', '.join(unknown)}; peers accept only "
                f"{', '.join(sorted(PEER_WORKLOAD_FIELDS))}"
            )
        name = check_peer_workload_name(workload.get("name"))
        if name in names:
            raise ValueError(f"OpenShell peer workload name {name!r} is used twice")
        names.add(name)
        image = workload.get("image")
        if not isinstance(image, str) or not image or any(c.isspace() for c in image):
            raise ValueError(f"OpenShell peer workload {name!r} needs an image reference")
        command = workload.get("command")
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in command)
        ):
            raise ValueError(
                f"OpenShell peer workload {name!r} needs an explicit command of non-empty "
                "arguments; peers never fall back to the image entrypoint"
            )
        _check_peer_environment(name, workload.get("environment") or {})
        _check_peer_resources(name, workload.get("resources"))
        _check_volume_mounts(
            f"peer workload {name!r}", workload.get("volume_mounts") or [], volume_read_only
        )


def check_volume_mounts(
    owner: str,
    mounts: list[dict[str, Any]],
    volumes: list[dict[str, Any]],
) -> None:
    """Raise unless ``mounts`` reference ``volumes`` the way the driver accepts."""
    _check_volume_mounts(owner, mounts, _check_volumes(volumes))


def _check_volumes(volumes: list[dict[str, Any]]) -> dict[str, bool]:
    read_only: dict[str, bool] = {}
    for volume in volumes:
        if not isinstance(volume, dict):
            raise ValueError("OpenShell driver volume must be a mapping")
        name = str(volume.get("name") or "")
        if not _dns_label(name):
            raise ValueError(f"OpenShell driver volume name {name!r} must be a DNS-1123 label")
        if name.startswith(RESERVED_VOLUME_PREFIX):
            raise ValueError(
                f"OpenShell driver volume name {name!r} uses the reserved prefix "
                f"{RESERVED_VOLUME_PREFIX!r}"
            )
        if name in read_only:
            raise ValueError(f"OpenShell driver volume {name!r} is declared twice")
        unknown = sorted(set(volume) - {"name", "persistent_volume_claim", "empty_dir"})
        if unknown:
            raise ValueError(
                f"OpenShell driver volume {name!r} has fields the driver rejects: "
                f"{', '.join(unknown)}"
            )
        claim = volume.get("persistent_volume_claim")
        empty_dir = volume.get("empty_dir")
        if (claim is None) == (empty_dir is None):
            raise ValueError(
                f"OpenShell driver volume {name!r} needs exactly one of "
                "persistent_volume_claim or empty_dir"
            )
        if empty_dir is not None and empty_dir != {}:
            raise ValueError(
                f"OpenShell driver volume {name!r} empty_dir takes no options; use empty_dir: {{}}"
            )
        if claim is not None:
            if not isinstance(claim, dict) or not claim.get("claim_name"):
                raise ValueError(f"OpenShell driver volume {name!r} needs a claim_name")
            unknown_claim = sorted(set(claim) - {"claim_name", "read_only"})
            if unknown_claim:
                raise ValueError(
                    f"OpenShell driver volume {name!r} persistent_volume_claim has fields "
                    f"the driver rejects: {', '.join(unknown_claim)}"
                )
        read_only[name] = bool(claim.get("read_only", True)) if claim is not None else False
    return read_only


def _check_volume_mounts(
    owner: str,
    mounts: list[dict[str, Any]],
    volume_read_only: dict[str, bool],
) -> None:
    targets: set[PurePosixPath] = set()
    for mount in mounts:
        if not isinstance(mount, dict):
            raise ValueError(f"OpenShell {owner} volume mount must be a mapping")
        unknown = sorted(set(mount) - VOLUME_MOUNT_FIELDS)
        if unknown:
            raise ValueError(
                f"OpenShell {owner} volume mount has fields the driver rejects: "
                f"{', '.join(unknown)}"
            )
        name = str(mount.get("name") or "")
        if name not in volume_read_only:
            raise ValueError(f"OpenShell {owner} mounts undeclared volume {name!r}")
        if volume_read_only[name] and mount.get("read_only") is False:
            raise ValueError(
                f"OpenShell {owner} mounts read-only volume {name!r} with read_only: false"
            )
        raw_target = str(mount.get("mount_path") or "")
        target = PurePosixPath(raw_target)
        if not target.is_absolute() or ".." in target.parts:
            raise ValueError(
                f"OpenShell {owner} mount path {raw_target!r} must be absolute, without '..'"
            )
        for protected in PROTECTED_MOUNT_ROOTS:
            if target.is_relative_to(protected) or protected.is_relative_to(target):
                raise ValueError(
                    f"OpenShell {owner} mount path {raw_target!r} overlaps {protected}, "
                    "which OpenShell owns"
                )
        if target in targets:
            raise ValueError(f"OpenShell {owner} mounts {raw_target!r} twice")
        targets.add(target)


def _check_peer_environment(name: str, environment: object) -> None:
    if not isinstance(environment, dict):
        raise ValueError(f"OpenShell peer workload {name!r} environment must be a mapping")
    for key, value in environment.items():
        key_text = str(key)
        if (
            not key_text
            or "=" in key_text
            or "\0" in key_text
            or key_text.startswith(RESERVED_ENV_PREFIX)
        ):
            raise ValueError(
                f"OpenShell peer workload {name!r} environment key {key_text!r} is empty, "
                f"contains '=' or NUL, or uses the reserved {RESERVED_ENV_PREFIX} prefix"
            )
        if not isinstance(value, str) or "\0" in value:
            raise ValueError(
                f"OpenShell peer workload {name!r} environment value for {key_text!r} "
                "must be a string without NUL"
            )


def _check_peer_resources(name: str, resources: object) -> None:
    if resources is None:
        return
    if not isinstance(resources, dict) or set(resources) - PEER_RESOURCE_FIELDS:
        raise ValueError(
            f"OpenShell peer workload {name!r} resources take only requests and limits"
        )
    for section in resources.values():
        if not isinstance(section, dict) or any(
            not isinstance(value, str) for value in section.values()
        ):
            raise ValueError(
                f"OpenShell peer workload {name!r} resource quantities must be strings"
            )


def _dns_label(value: str) -> bool:
    return len(value) <= _DNS_1123_LABEL_MAX and bool(_DNS_1123_LABEL.match(value))
