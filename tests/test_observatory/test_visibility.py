"""Each org sees what it owns of the estate an Observatory discovered."""

from __future__ import annotations

from niuu.domain.agent_directory import is_agent_scope_visible
from niuu.domain.models import Principal
from observatory.entity_discovery import DiscoveredEntity, _entity_to_node
from observatory.visibility import events_for, topology_for

PLATFORM = "default"


def _caller(tenant_id: str, user_id: str = "alice") -> Principal:
    return Principal(user_id=user_id, email="", tenant_id=tenant_id, roles=["volundr:developer"])


def _snapshot() -> dict:
    return {
        "revision": "r1",
        "nodes": [
            {"id": "cluster", "label": "ymir"},
            {"id": "regin", "parentId": "cluster", "tenantId": "default"},
            {"id": "alice-session", "parentId": "cluster", "tenantId": "org-a"},
            {"id": "alice-mimir", "parentId": "alice-session", "tenantId": "org-a"},
            {"id": "carol-session", "parentId": "cluster", "tenantId": "org-b"},
        ],
        "edges": [
            {"sourceId": "alice-session", "targetId": "alice-mimir"},
            {"sourceId": "alice-session", "targetId": "cluster"},
            {"sourceId": "carol-session", "targetId": "alice-mimir"},
        ],
        "pendingEdges": [
            {"sourceId": "alice-session", "targetId": "somewhere"},
            {"sourceId": "regin", "targetId": "somewhere"},
        ],
    }


def _ids(view: dict) -> list[str]:
    return [node["id"] for node in view["nodes"]]


def test_an_org_sees_only_what_it_owns() -> None:
    view = topology_for(_snapshot(), _caller("org-a"), platform_tenant_id=PLATFORM)

    assert _ids(view) == ["alice-session", "alice-mimir"]
    assert view["edges"] == [{"sourceId": "alice-session", "targetId": "alice-mimir"}]
    assert [edge["sourceId"] for edge in view["pendingEdges"]] == ["alice-session"]


def test_a_node_naming_no_org_belongs_to_the_platform_org() -> None:
    view = topology_for(_snapshot(), _caller(PLATFORM), platform_tenant_id=PLATFORM)

    assert _ids(view) == ["cluster", "regin"]


def test_a_kept_node_loses_a_parent_its_org_cannot_see() -> None:
    view = topology_for(_snapshot(), _caller("org-a"), platform_tenant_id=PLATFORM)

    parents = {node["id"]: node.get("parentId") for node in view["nodes"]}
    assert parents == {"alice-session": None, "alice-mimir": "alice-session"}


def test_a_private_node_stays_with_its_owner_inside_the_org() -> None:
    snapshot = {
        "nodes": [
            {"id": "mine", "tenantId": "org-a", "ownerId": "alice", "visibility": "user"},
            {"id": "bobs", "tenantId": "org-a", "ownerId": "bob", "visibility": "user"},
        ]
    }

    view = topology_for(snapshot, _caller("org-a"), platform_tenant_id=PLATFORM)

    assert _ids(view) == ["mine"]


def test_a_caller_with_no_org_sees_nothing() -> None:
    view = topology_for(_snapshot(), _caller(""), platform_tenant_id=PLATFORM)

    assert view["nodes"] == []


def test_the_signal_log_stays_with_the_platform_org() -> None:
    events = [{"id": "e1"}]

    assert events_for(events, _caller(PLATFORM), platform_tenant_id=PLATFORM) == events
    assert events_for(events, _caller("org-a"), platform_tenant_id=PLATFORM) == []


def test_a_workload_tagged_with_an_org_belongs_to_that_org() -> None:
    entity = DiscoveredEntity(
        id="brain",
        kind="service",
        name="brain",
        source_adapter="kubernetes",
        source_kind="pod",
        source_uid="uid",
        annotations={"niuu.world/tenant-id": "org-a"},
    )

    node = _entity_to_node(entity, parent_id=None, known_type_ids={"service"})

    assert node["tenantId"] == "org-a"


def test_a_shared_agent_naming_no_org_belongs_to_the_platform_org() -> None:
    def visible_to(tenant_id: str) -> bool:
        return is_agent_scope_visible(
            owner_id=None,
            tenant_id=None,
            visibility="system",
            environment_member_ids=[],
            principal=_caller(tenant_id),
            platform_tenant_id=PLATFORM,
        )

    assert visible_to(PLATFORM)
    assert not visible_to("org-a")
