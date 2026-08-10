"""Resolving an edge endpoint that names a node it cannot identify.

The rules here decide whether a cross-cluster relationship is drawn at all, so
each one is pinned: what resolves, what deliberately does not, and the cases
where refusing to answer is the answer.
"""

from __future__ import annotations

from niuu.domain.services.observatory_refs import resolve_by_url, resolve_node_ref

MIMIR = {
    "id": "runtime:ymir:volundr:mimir:mimir-shared",
    "typeId": "mimir",
    "label": "Mímir",
    "clusterName": "ymir",
    "namespace": "volundr",
    "labels": {"app.kubernetes.io/name": "mimir-shared"},
    "endpoints": {
        "internal": "http://niuu-mimir-shared.volundr.svc.cluster.local",
        "public": "https://mimir.yggdrasil.niuu.world",
    },
}
OBSERVATORY = {
    "id": "runtime:noatun:volundr:service:observatory",
    "typeId": "service",
    "label": "observatory",
    "clusterName": "noatun",
    "namespace": "volundr",
    "labels": {"app.kubernetes.io/name": "observatory"},
}
NODES = [MIMIR, OBSERVATORY]


def test_an_exact_id_is_itself() -> None:
    assert resolve_node_ref(MIMIR["id"], NODES) == MIMIR["id"]


def test_a_url_resolves_on_host_not_path() -> None:
    """The two ends spell the same service differently and both are right.

    The ingress publishes the bare host; the session that mounts it was
    configured with `/api/v1` on the end. Neither path contains the other.
    """
    assert resolve_node_ref("https://mimir.yggdrasil.niuu.world/api/v1", NODES) == MIMIR["id"]
    assert resolve_by_url("mimir.yggdrasil.niuu.world:443", NODES) == MIMIR["id"]


def test_a_url_nothing_publishes_resolves_to_nothing() -> None:
    assert resolve_node_ref("https://mimir.elsewhere.example/api/v1", NODES) == ""
    assert resolve_by_url("not a url at all", NODES) == ""


def test_a_scoped_component_reference_resolves() -> None:
    assert resolve_node_ref("service:observatory@noatun/volundr", NODES) == OBSERVATORY["id"]


def test_the_release_prefix_does_not_hide_a_name() -> None:
    """`mimir-shared` is what the release called it; `shared` is what it is."""
    assert resolve_node_ref("mimir:shared@ymir/volundr", NODES) == MIMIR["id"]


def test_a_component_alias_is_applied_when_the_caller_owns_one() -> None:
    assert (
        resolve_node_ref(
            "knowledge-service:mimir-shared@ymir/volundr",
            NODES,
            type_alias=lambda component: "mimir" if component == "knowledge-service" else component,
        )
        == MIMIR["id"]
    )


def test_two_matches_resolve_to_neither() -> None:
    """Drawing the wrong edge is worse than drawing none."""
    second = {**MIMIR, "id": "runtime:ymir:volundr:mimir:mimir-research"}
    assert resolve_node_ref("mimir:shared@ymir/volundr", [MIMIR, second]) == ""
    assert resolve_by_url("https://mimir.yggdrasil.niuu.world", [MIMIR, second]) == ""


def test_a_reference_to_nothing_is_not_an_error() -> None:
    assert resolve_node_ref("", NODES) == ""
    assert resolve_node_ref("   ", NODES) == ""
    assert resolve_node_ref("nothing-by-that-name", NODES) == ""


def test_placement_narrows_a_name_shared_by_two_clusters() -> None:
    twin = {**OBSERVATORY, "id": "runtime:ymir:volundr:service:observatory", "clusterName": "ymir"}
    nodes = [OBSERVATORY, twin]
    assert resolve_node_ref("service:observatory", nodes) == ""
    assert resolve_node_ref("service:observatory@ymir/volundr", nodes) == twin["id"]
