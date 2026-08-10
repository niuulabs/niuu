"""Resolving a topology edge's endpoints to real nodes.

An adapter that emits an edge often cannot name the node on the other end. It
knows a service by component and placement (`service:observatory@noatun/volundr`)
or by the URL it was configured to call
(`https://mimir.yggdrasil.niuu.world/api/v1`) — never by the id some other
scout, in some other cluster, happened to mint for it.

Lives in `niuu` because both sides need it and the dependency runs one way:
`observatory` resolves what it can inside its own fragment, and Guild resolves
the rest once every fragment has been merged and the far node actually exists.
Cross-cluster edges were silently dropped before this existed — every
`observes` edge between Observatories, and every mount a workflow session holds
on a Mímir in another cluster.

Resolution never guesses. A reference matching two nodes resolves to neither,
because drawing the wrong edge is worse than drawing none.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any
from urllib.parse import urlsplit


def slug(value: str) -> str:
    """Lowercase, non-alphanumerics collapsed to hyphens."""
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-") or "entity"


def _node_urls(node: Mapping[str, Any]) -> list[str]:
    endpoints = node.get("endpoints")
    if not isinstance(endpoints, Mapping):
        return []
    return [str(value) for value in endpoints.values() if str(value).strip()]


def _host(url: str) -> str:
    """The host a URL addresses, without port or credentials."""
    split = urlsplit(url if "://" in url else f"//{url}")
    return (split.hostname or "").lower()


def _logical_names(node: Mapping[str, Any]) -> set[str]:
    labels = node.get("labels") if isinstance(node.get("labels"), Mapping) else {}
    node_id = str(node.get("id") or "")
    return {
        slug(str(node.get("label") or "")),
        slug(node_id.rsplit(":", 1)[-1]),
        slug(str(labels.get("app.kubernetes.io/name") or "")),
        slug(str(labels.get("app.kubernetes.io/component") or "")),
        slug(str(labels.get("niuu.world/entity-id") or "")),
        slug(str(labels.get("niuu.world/service-id") or "")),
    }


def _sole(candidates: list[str]) -> str:
    """The one node this reference names, or nothing.

    Two candidates is not a tie to be broken. A workflow session mounting "the
    Mímir in volundr" when there are two of them has not said which, and an
    edge drawn to whichever sorted first would read as fact.
    """
    unique = set(candidates)
    return candidates[0] if len(unique) == 1 else ""


def resolve_by_url(url: str, nodes: Iterable[Mapping[str, Any]]) -> str:
    """The node reachable at this URL, matched on host.

    Host rather than full URL because the two ends describe the same service
    differently and both are right: an ingress publishes
    `https://mimir.yggdrasil.niuu.world`, while the resident that mounts it was
    configured with `https://mimir.yggdrasil.niuu.world/api/v1`. Neither path
    is a prefix of the other, so comparing paths matches nothing.

    This is why the Kubernetes scout attributes a hostname to the workload
    behind it only when that hostname has exactly one backend: a host shared by
    a dozen services identifies none of them.
    """
    host = _host(url)
    if not host:
        return ""
    return _sole(
        [
            str(node.get("id") or "")
            for node in nodes
            if any(_host(candidate) == host for candidate in _node_urls(node))
        ]
    )


def resolve_node_ref(
    ref: str,
    nodes: Iterable[Mapping[str, Any]],
    *,
    type_alias: Callable[[str], str] | None = None,
) -> str:
    """Resolve one endpoint reference to a node id, or "" if it names none.

    Accepted forms, in the order they are tried:

    - an exact node id;
    - a URL, resolved against what each node publishes;
    - ``type:name@cluster/namespace``, with every part after ``name`` optional.

    ``type_alias`` maps a reference's component word onto the type id the graph
    uses — `knowledge-service` means `mimir` — and is supplied by the caller
    that owns that vocabulary.
    """
    value = ref.strip()
    if not value:
        return ""

    node_list = list(nodes)
    by_id = {str(node.get("id") or ""): node for node in node_list}
    if value in by_id:
        return value

    if "://" in value:
        return resolve_by_url(value, node_list)

    if "@" in value:
        value, scope = value.split("@", 1)
        cluster, _, namespace = scope.partition("/")
    else:
        cluster = ""
        namespace = ""

    if ":" in value:
        type_id, _, name = value.partition(":")
    else:
        type_id = ""
        name = value
    if type_id and type_alias is not None:
        type_id = type_alias(type_id)
    name_slug = slug(name)

    candidates: list[str] = []
    for node in node_list:
        if type_id and node.get("typeId") != type_id:
            continue
        if cluster and node.get("clusterName") != cluster:
            continue
        if namespace and node.get("namespace") != namespace:
            continue
        logical_names = _logical_names(node)
        if name_slug in logical_names or any(
            logical_name.endswith(f"-{name_slug}") for logical_name in logical_names
        ):
            candidates.append(str(node.get("id") or ""))
    return _sole(candidates)
