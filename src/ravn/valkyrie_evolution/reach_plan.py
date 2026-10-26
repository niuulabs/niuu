"""Turn a learned tool's declared reach into the egress a Job may have.

Resident-authored manifests name their reach in their own words — ``kubernetes``,
``k8s-api``, ``http`` with a target of ``kubernetes.default.svc:443`` or
``monitoring/kube-prom-kube-prometheus-prometheus:9090``. The ``k8s_job`` backend
can only enforce reach it can express as a NetworkPolicy plus a read-only
Kubernetes identity, so this module reads each grant into exactly one of:

* the Kubernetes API (egress to the API server, with the read-only tool
  identity mounted);
* an in-cluster Service (egress to the pods behind it, on its target port);
* an explicit host or IP with a port (egress to that address);
* unrestricted egress (an untargeted ``read_write`` network grant).

A target written as prose ("in-cluster Prometheus service") names no
address. The operator may declare what such words mean on this cluster
(``named_targets``: ``{"prometheus": "monitoring/kube-prom-prometheus:9090"}``);
a word is matched whole, in the grant's kind or target, and nothing else is
inferred.

A grant it cannot read that way is refused with the remedy — never widened to
unrestricted egress and never guessed.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from ravn.valkyrie_evolution.learned_tools import (
    PURE_COMPUTE_REACH_KINDS,
    LearnedToolError,
)
from ravn.valkyrie_evolution.models import ToolReachGrant

#: Where Kubernetes projects a pod's service-account token. A filesystem grant
#: on this path is the Kubernetes API identity, which k8s_job mounts itself.
SERVICE_ACCOUNT_TOKEN_DIR = PurePosixPath("/var/run/secrets/kubernetes.io/serviceaccount")

#: Reach kinds that name running a program inside the sandbox. They need no
#: egress of their own; what the program contacts is declared by other grants.
PROCESS_REACH_KINDS = frozenset({"process", "cli", "subprocess", "exec", "local"})

_FILESYSTEM_KINDS = frozenset({"file", "filesystem", "fs", "path", "workspace"})
_CREDENTIAL_KINDS = frozenset({"credential", "credentials", "secret", "secrets"})
_HOST_TOOL_KINDS = frozenset({"native_tool", "host_tool", "host_sdk"})

#: Substrings that name the Kubernetes API in a grant's kind or target.
_KUBERNETES_API_MARKERS = (
    "kubernetes",
    "k8s",
    "kube-api",
    "kube_api",
    "kubeapi",
    "apiserver",
    "api-server",
    "kubectl",
)

#: Host names that are the in-cluster Kubernetes API service.
_KUBERNETES_API_HOSTS = frozenset(
    {
        "kubernetes",
        "kubernetes.default",
        "kubernetes.default.svc",
        "kubernetes.default.svc.cluster.local",
    }
)

_DNS_LABEL = r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?"
#: ``namespace/service[:port]`` — the whole target, nothing else.
_SERVICE_REF_RE = re.compile(rf"^({_DNS_LABEL})/({_DNS_LABEL})(?::(\d{{1,5}}))?$")
#: ``service.namespace.svc[.cluster.local]`` host names.
_SERVICE_HOST_RE = re.compile(rf"^({_DNS_LABEL})\.({_DNS_LABEL})\.svc(?:\.cluster\.local)?$")
#: Address-like tokens inside free text: URLs, and ``host:port``.
_URL_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s,()]+")
_HOST_PORT_TOKEN_RE = re.compile(r"(?<![\w./:-])([A-Za-z0-9*][A-Za-z0-9.*-]*):(\d{1,5})(?![\w.])")

_DEFAULT_PORTS = {"http": 80, "https": 443}


@dataclass(frozen=True)
class ServiceTarget:
    """An in-cluster Service a tool may reach, optionally on one port."""

    namespace: str
    name: str
    port: int | None = None


@dataclass(frozen=True)
class HostTarget:
    """An explicit host name or IP address and port a tool may reach."""

    host: str
    port: int


@dataclass(frozen=True)
class EgressPlan:
    """What a learned tool's Job may reach, derived from its declared reach."""

    kubernetes_api: bool = False
    services: tuple[ServiceTarget, ...] = ()
    hosts: tuple[HostTarget, ...] = ()
    unrestricted: bool = False

    @property
    def scoped(self) -> bool:
        """True when the Job needs a per-run NetworkPolicy naming its targets."""
        if self.unrestricted:
            return False
        return self.kubernetes_api or bool(self.services) or bool(self.hosts)

    @property
    def network(self) -> bool:
        return self.unrestricted or self.scoped


def parse_service_ref(ref: str) -> ServiceTarget:
    """Parse ``namespace/service[:port]``; raises :class:`ValueError` otherwise."""
    match = _SERVICE_REF_RE.fullmatch(ref.strip())
    if not match:
        raise ValueError(f"expected 'namespace/service[:port]', got {ref!r}")
    port = int(match.group(3)) if match.group(3) else None
    return ServiceTarget(namespace=match.group(1), name=match.group(2), port=port)


def plan_egress(
    declared_reach: Sequence[ToolReachGrant],
    *,
    named_targets: Mapping[str, str] | None = None,
) -> EgressPlan:
    """Read *declared_reach* into the egress a k8s_job run may have.

    *named_targets* maps a word to the ``namespace/service[:port]`` it means
    on this cluster. Raises :class:`LearnedToolError`, naming the grant and
    the remedy, for any grant k8s_job cannot enforce.
    """
    aliases = {
        word.casefold(): parse_service_ref(ref) for word, ref in (named_targets or {}).items()
    }
    kubernetes_api = False
    services: list[ServiceTarget] = []
    hosts: list[HostTarget] = []
    unrestricted = False
    for grant in declared_reach:
        kind = grant.kind.strip().casefold()
        if grant.access == "none" or kind in PURE_COMPUTE_REACH_KINDS:
            continue
        if kind in PROCESS_REACH_KINDS:
            continue
        if kind in _FILESYSTEM_KINDS:
            _require_service_account_path(grant)
            kubernetes_api = True
            continue
        if kind in _CREDENTIAL_KINDS:
            raise LearnedToolError(
                f"k8s_job passes no credentials into a tool Job (reach {grant.kind!r} "
                f"{grant.target!r}); rebuild the tool to use its read-only Kubernetes "
                "identity, or run it on a backend that grants credentials"
            )
        if kind in _HOST_TOOL_KINDS:
            raise LearnedToolError(
                f"k8s_job has no channel back to the resident's own tools (reach "
                f"{grant.kind!r} {grant.target!r}); rebuild the tool self-contained"
            )
        destination = _destination(grant, aliases)
        match destination:
            case "kubernetes_api":
                _require_read_only_kubernetes(grant)
                kubernetes_api = True
            case "unrestricted":
                unrestricted = True
            case list():
                for target in destination:
                    if isinstance(target, ServiceTarget):
                        services.append(target)
                    elif target == "kubernetes_api":
                        _require_read_only_kubernetes(grant)
                        kubernetes_api = True
                    else:
                        hosts.append(target)
    return EgressPlan(
        kubernetes_api=kubernetes_api,
        services=tuple(dict.fromkeys(services)),
        hosts=tuple(dict.fromkeys(hosts)),
        unrestricted=unrestricted,
    )


def _destination(
    grant: ToolReachGrant, aliases: Mapping[str, ServiceTarget]
) -> str | list[ServiceTarget | HostTarget | str]:
    target = grant.target.strip()
    # A kind that names the Kubernetes API is the API; its target describes
    # resources ("pods/log", "namespace"), never an address to reach.
    if any(marker in grant.kind.casefold() for marker in _KUBERNETES_API_MARKERS):
        return "kubernetes_api"
    service = _SERVICE_REF_RE.fullmatch(target)
    if service:
        port = int(service.group(3)) if service.group(3) else None
        return [ServiceTarget(namespace=service.group(1), name=service.group(2), port=port)]

    addresses = _addresses(target)
    if addresses:
        return addresses
    text = f"{grant.kind} {target}".casefold()
    if any(marker in text for marker in _KUBERNETES_API_MARKERS):
        return "kubernetes_api"
    named = [
        service for word, service in aliases.items() if re.search(rf"\b{re.escape(word)}\b", text)
    ]
    if named:
        return list(named)
    if not target:
        if grant.access != "read_write":
            raise LearnedToolError(
                f"reach {grant.kind!r} names no target, and k8s_job will not open "
                f"unrestricted egress for {grant.access!r} access; name the target as "
                "namespace/service:port, a service DNS name, a URL or host:port"
            )
        return "unrestricted"
    raise LearnedToolError(
        f"k8s_job cannot derive a NetworkPolicy destination from reach {grant.kind!r} "
        f"target {target!r}; name it as namespace/service:port, a service DNS name, "
        "a URL or host:port (or declare the word in learned_tool_k8s.named_targets)"
    )


def _addresses(target: str) -> list[ServiceTarget | HostTarget | str]:
    """Every address named in *target*, classified; refuses wildcards."""
    found: list[tuple[str, int | None, str]] = []
    for url in _URL_TOKEN_RE.findall(target):
        parts = urlsplit(url)
        host = parts.hostname or ""
        try:
            port = parts.port
        except ValueError as exc:
            raise LearnedToolError(f"reach target {url!r} has an invalid port") from exc
        found.append((host, port or _DEFAULT_PORTS.get(parts.scheme.casefold()), url))
    remainder = _URL_TOKEN_RE.sub(" ", target)
    for host, port in _HOST_PORT_TOKEN_RE.findall(remainder):
        found.append((host, int(port), f"{host}:{port}"))

    addresses: list[ServiceTarget | HostTarget | str] = []
    for host, port, raw in found:
        host = host.casefold().rstrip(".")
        if "*" in host or not host:
            raise LearnedToolError(
                f"k8s_job cannot enforce wildcard reach target {raw!r}; name each host"
            )
        if host in _KUBERNETES_API_HOSTS:
            addresses.append("kubernetes_api")
            continue
        service = _SERVICE_HOST_RE.fullmatch(host)
        if service:
            addresses.append(
                ServiceTarget(namespace=service.group(2), name=service.group(1), port=port)
            )
            continue
        if port is None:
            raise LearnedToolError(f"reach target {raw!r} names no port; add one")
        if not _is_ip(host) and "." not in host:
            raise LearnedToolError(
                f"reach target {raw!r} is neither an IP, a fully qualified host, nor "
                "a service DNS name (service.namespace.svc)"
            )
        addresses.append(HostTarget(host=host, port=port))
    return addresses


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _require_service_account_path(grant: ToolReachGrant) -> None:
    raw = grant.target.strip().split(" ", 1)[0]
    if grant.access not in {"read", "none"}:
        raise LearnedToolError(
            f"k8s_job mounts the service-account token read-only; reach {grant.kind!r} "
            f"{grant.target!r} asks for {grant.access!r}"
        )
    path = PurePosixPath(raw) if raw else None
    if path is None or not (
        path == SERVICE_ACCOUNT_TOKEN_DIR or SERVICE_ACCOUNT_TOKEN_DIR in path.parents
    ):
        raise LearnedToolError(
            f"k8s_job mounts no filesystem path into a tool Job except the read-only "
            f"service-account token ({SERVICE_ACCOUNT_TOKEN_DIR}); reach {grant.kind!r} "
            f"{grant.target!r} cannot be enforced — take the data as input instead"
        )


def _require_read_only_kubernetes(grant: ToolReachGrant) -> None:
    if grant.access != "read":
        raise LearnedToolError(
            f"k8s_job gives a tool only a read-only Kubernetes identity; reach "
            f"{grant.kind!r} {grant.target!r} asks for {grant.access!r} — writes to the "
            "cluster go through the infrastructure repository, not a learned tool"
        )
