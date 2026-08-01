"""Contract tests for Observatory topology fragments.

These guard the two properties the rest of the design leans on: the wire format
is camelCase, and kind-specific fields survive the round trip.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from niuu.domain.observatory import (
    FragmentMeta,
    ObservatoryFragment,
    TopologyEdge,
    TopologyNode,
    TopologySnapshot,
    TopologySourceHealth,
)


class TestTopologyNode:
    def test_serialises_to_camel_case_on_the_wire(self) -> None:
        node = TopologyNode(id="n", type_id="mimir", label="mímir", parent_id="cluster-ymir")

        dumped = node.model_dump(by_alias=True)

        assert dumped["typeId"] == "mimir"
        assert dumped["parentId"] == "cluster-ymir"
        assert "type_id" not in dumped
        assert "parent_id" not in dumped

    def test_accepts_either_casing_on_input(self) -> None:
        from_camel = TopologyNode(id="n", typeId="host", label="baldr")
        from_snake = TopologyNode(id="n", type_id="host", label="baldr")

        assert from_camel.type_id == from_snake.type_id == "host"

    def test_preserves_kind_specific_fields(self) -> None:
        """Adapters carry detail in metadata, which is splatted onto the node.

        Rejecting unknown keys here would silently strip every kind-specific
        field on its way to the UI.
        """
        node = TopologyNode(
            id="mimir-ymir",
            type_id="mimir",
            label="mímir-shared",
            pages=203,
            mounts=["local", "shared"],
            mountCount=2,
        )

        dumped = node.model_dump(by_alias=True)

        assert dumped["pages"] == 203
        assert dumped["mounts"] == ["local", "shared"]
        assert dumped["mountCount"] == 2

    def test_requires_an_identity(self) -> None:
        with pytest.raises(ValidationError):
            TopologyNode(type_id="host", label="baldr")  # type: ignore[call-arg]

    def test_placement_is_optional_so_non_cluster_nodes_fit(self) -> None:
        """A bare-metal host has no cluster and no namespace."""
        node = TopologyNode(id="host-baldr", type_id="host", label="baldr", host_id="baldr")

        assert node.cluster_id == ""
        assert node.namespace == ""
        assert node.host_id == "baldr"

    def test_defaults_to_system_visibility(self) -> None:
        assert TopologyNode(id="n", type_id="service", label="s").visibility == "system"


class TestTopologyEdge:
    def test_defaults_to_declared_rather_than_observed(self) -> None:
        """An edge must not claim to be observed unless a source says so."""
        edge = TopologyEdge(id="e", source_id="a", target_id="b")

        assert edge.confidence == "declared"

    def test_rejects_an_unknown_relation_type(self) -> None:
        with pytest.raises(ValidationError):
            TopologyEdge(id="e", source_id="a", target_id="b", relation_type="befriends")

    def test_carries_evidence_for_an_observed_edge(self) -> None:
        edge = TopologyEdge(
            id="e",
            source_id="bifrost",
            target_id="model-nemotron",
            relation_type="routes_to",
            confidence="observed",
            evidence={"adapter": "bifrost", "field": "base_url"},
        )

        assert edge.model_dump(by_alias=True)["evidence"]["field"] == "base_url"


class TestObservatoryFragment:
    def test_round_trips_through_the_wire_format(self) -> None:
        fragment = ObservatoryFragment(
            nodes=[TopologyNode(id="n", type_id="host", label="sæhrímnir", gpu="GB10")],
            meta=FragmentMeta(source_id="spark-1", source_kind="host", host_id="saehrimnir"),
        )

        restored = ObservatoryFragment.model_validate(fragment.model_dump(by_alias=True))

        assert restored.meta is not None
        assert restored.meta.source_id == "spark-1"
        assert restored.nodes[0].model_dump()["gpu"] == "GB10"

    def test_is_empty_by_default_so_a_silent_source_is_not_an_error(self) -> None:
        fragment = ObservatoryFragment()

        assert fragment.nodes == []
        assert fragment.edges == []


class TestTopologySnapshot:
    def test_reports_an_unreachable_source_instead_of_hiding_it(self) -> None:
        snapshot = TopologySnapshot(
            timestamp="2026-08-01T12:00:00Z",
            sources=[
                TopologySourceHealth(source_id="valhalla", transport="pull", status="failed"),
                TopologySourceHealth(source_id="ymir", transport="pull", status="healthy"),
            ],
            partial=True,
        )

        statuses = {s.source_id: s.status for s in snapshot.sources}
        assert statuses["valhalla"] == "failed"
        assert snapshot.partial is True

    def test_distinguishes_stale_from_failed(self) -> None:
        """A source that stopped reporting is a different situation from one
        that cannot be reached at all."""
        stale = TopologySourceHealth(
            source_id="spark-1",
            transport="push",
            status="stale",
            last_seen="2026-08-01T11:56:00Z",
        )

        assert stale.status == "stale"
        assert stale.last_seen != ""

    def test_rejects_an_unknown_source_status(self) -> None:
        with pytest.raises(ValidationError):
            TopologySourceHealth(source_id="s", status="probably-fine")
