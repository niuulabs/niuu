"""Tests for Guild-side topology aggregation.

The properties that matter: one unreachable source must not empty the graph,
every source's state is reported rather than hidden, and a pushed fragment is
indistinguishable from a pulled one once merged.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from niuu.adapters.memory_observatory_fragments import InMemoryObservatoryFragmentRepository
from niuu.domain.models import InstanceKind, InstanceVisibility, RegisteredInstance
from niuu.domain.observatory import (
    FragmentMeta,
    ObservatoryFragment,
    TopologyEdge,
    TopologyNode,
)
from niuu.domain.services.observatory_fragments import ObservatoryFragmentInboxService
from niuu.domain.services.observatory_topology import ObservatoryTopologyAggregationService

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)


def _instance(instance_id: str, *, cluster: str = "ymir") -> RegisteredInstance:
    return RegisteredInstance(
        id=instance_id,
        kind=InstanceKind.OBSERVATORY,
        slug=instance_id,
        name=f"Observatory {instance_id}",
        base_url=f"https://{instance_id}.example.test",
        visibility=InstanceVisibility.SYSTEM,
        owner_id=None,
        tenant_id=None,
        enabled=True,
        is_default=False,
        config={"cluster": cluster},
        tags=[],
        created_at=NOW,
        updated_at=NOW,
    )


def _fragment(source_id: str, *, node_ids: list[str]) -> ObservatoryFragment:
    return ObservatoryFragment(
        nodes=[TopologyNode(id=node_id, type_id="service", label=node_id) for node_id in node_ids],
        edges=[TopologyEdge(id=f"edge-{source_id}", source_id=node_ids[0], target_id=node_ids[0])],
        meta=FragmentMeta(source_id=source_id, cluster_id="ymir", revision="rev-1"),
    )


class _StubClient:
    """Returns a canned fragment or raises, per instance id."""

    def __init__(self, results: dict[str, ObservatoryFragment | Exception]) -> None:
        self.results = results
        self.calls: list[str] = []

    async def fetch_fragment(self, instance, *, headers):  # noqa: ANN001, ANN202
        del headers
        self.calls.append(instance.id)
        result = self.results[instance.id]
        if isinstance(result, Exception):
            raise result
        return result


def _service(
    results: dict[str, ObservatoryFragment | Exception],
    *,
    inbox: ObservatoryFragmentInboxService | None = None,
) -> ObservatoryTopologyAggregationService:
    return ObservatoryTopologyAggregationService(
        client=_StubClient(results),  # type: ignore[arg-type]
        max_concurrency=4,
        fragment_inbox=inbox,
    )


class TestPullAggregation:
    @pytest.mark.asyncio
    async def test_merges_every_reachable_source(self) -> None:
        service = _service(
            {
                "obs-a": _fragment("obs-a", node_ids=["a-1"]),
                "obs-b": _fragment("obs-b", node_ids=["b-1"]),
            }
        )

        snapshot = await service.get_snapshot([_instance("obs-a"), _instance("obs-b")], headers={})

        assert {node.id for node in snapshot.nodes} == {"a-1", "b-1"}
        assert snapshot.partial is False

    @pytest.mark.asyncio
    async def test_one_failing_source_does_not_empty_the_graph(self) -> None:
        service = _service(
            {
                "obs-a": _fragment("obs-a", node_ids=["a-1"]),
                "obs-b": RuntimeError("connection refused"),
            }
        )

        snapshot = await service.get_snapshot([_instance("obs-a"), _instance("obs-b")], headers={})

        assert {node.id for node in snapshot.nodes} == {"a-1"}
        assert snapshot.partial is True

    @pytest.mark.asyncio
    async def test_a_failed_source_is_reported_not_hidden(self) -> None:
        """An empty graph and an unreachable estate must not look identical."""
        service = _service({"obs-b": RuntimeError("connection refused")})

        snapshot = await service.get_snapshot([_instance("obs-b")], headers={})

        health = {source.source_id: source for source in snapshot.sources}
        assert health["obs-b"].status == "failed"
        assert "connection refused" in health["obs-b"].message
        assert [warning.code for warning in snapshot.warnings] == ["source_unreachable"]

    @pytest.mark.asyncio
    async def test_an_unreachable_source_says_what_went_wrong(self) -> None:
        """Several httpx transport errors stringify to "".

        That turned every unreachable source into "Observatory Ymir: " — a
        warning that names the source and then says nothing, indistinguishable
        between a DNS failure, a timeout and a refused connection. It blocked a
        real diagnosis on ymir, so the class name is the floor.
        """
        service = _service({"obs-b": ConnectionResetError()})

        snapshot = await service.get_snapshot([_instance("obs-b")], headers={})

        assert snapshot.warnings[0].message.endswith("ConnectionResetError")
        assert snapshot.sources[0].message == "ConnectionResetError"

    @pytest.mark.asyncio
    async def test_health_records_node_counts_per_source(self) -> None:
        service = _service({"obs-a": _fragment("obs-a", node_ids=["a-1", "a-2"])})

        snapshot = await service.get_snapshot([_instance("obs-a")], headers={})

        assert snapshot.sources[0].node_count == 2
        assert snapshot.sources[0].transport == "pull"

    @pytest.mark.asyncio
    async def test_no_sources_yields_an_empty_but_honest_snapshot(self) -> None:
        snapshot = await _service({}).get_snapshot([], headers={})

        assert snapshot.nodes == []
        assert snapshot.partial is False
        assert snapshot.timestamp


class TestPushedFragments:
    @pytest.mark.asyncio
    async def test_a_pushed_source_merges_like_a_pulled_one(self) -> None:
        inbox = ObservatoryFragmentInboxService(
            InMemoryObservatoryFragmentRepository(),
            ttl_seconds=180.0,
            clock=lambda: NOW,
        )
        await inbox.accept("spark-1", _fragment("spark-1", node_ids=["ivaldi"]))
        service = _service({"obs-a": _fragment("obs-a", node_ids=["a-1"])}, inbox=inbox)

        snapshot = await service.get_snapshot([_instance("obs-a")], headers={})

        assert {node.id for node in snapshot.nodes} == {"a-1", "ivaldi"}
        transports = {source.source_id: source.transport for source in snapshot.sources}
        assert transports == {"obs-a": "pull", "spark-1": "push"}

    @pytest.mark.asyncio
    async def test_a_stale_pushed_source_is_reported_and_marks_the_graph_partial(self) -> None:
        clock = {"now": NOW}
        inbox = ObservatoryFragmentInboxService(
            InMemoryObservatoryFragmentRepository(),
            ttl_seconds=60.0,
            clock=lambda: clock["now"],
        )
        await inbox.accept("spark-1", _fragment("spark-1", node_ids=["ivaldi"]))
        clock["now"] = datetime(2026, 8, 1, 12, 10, 0, tzinfo=UTC)
        service = _service({}, inbox=inbox)

        snapshot = await service.get_snapshot([], headers={})

        assert snapshot.partial is True
        assert [warning.code for warning in snapshot.warnings] == ["source_stale"]
        # Still contributing nodes: stale is not gone.
        assert {node.id for node in snapshot.nodes} == {"ivaldi"}

    @pytest.mark.asyncio
    async def test_an_unreadable_inbox_degrades_to_pull_only(self) -> None:
        class _BrokenInbox:
            async def current(self):  # noqa: ANN202
                raise RuntimeError("inbox down")

        service = _service(
            {"obs-a": _fragment("obs-a", node_ids=["a-1"])},
            inbox=_BrokenInbox(),  # type: ignore[arg-type]
        )

        snapshot = await service.get_snapshot([_instance("obs-a")], headers={})

        assert {node.id for node in snapshot.nodes} == {"a-1"}


class TestMerge:
    @pytest.mark.asyncio
    async def test_identical_nodes_from_two_sources_are_not_duplicated(self) -> None:
        shared = _fragment("obs-a", node_ids=["shared"])
        service = _service({"obs-a": shared, "obs-b": shared})

        snapshot = await service.get_snapshot([_instance("obs-a"), _instance("obs-b")], headers={})

        assert [node.id for node in snapshot.nodes] == ["shared"]
        assert snapshot.warnings == []

    @pytest.mark.asyncio
    async def test_the_cluster_that_owns_a_node_beats_a_peer_referencing_it(self) -> None:
        """A placeholder must not outrank the real thing.

        An Observatory that references a peer's cluster synthesises a stub for
        it with no placement, while the Observatory in that cluster emits the
        real node under its realm. Keeping whichever arrived first let the stub
        win, and the realm — left with no children — had no rectangle left to
        draw. On the live estate every realm but ymir's vanished this way.
        """
        stub = ObservatoryFragment(
            nodes=[
                TopologyNode(id="cluster-eitri", type_id="cluster", label="eitri"),
                TopologyNode(
                    id="cluster-ymir",
                    type_id="cluster",
                    label="ymir",
                    parent_id="realm-ginnungagap",
                ),
            ],
            meta=FragmentMeta(source_id="obs-ymir", cluster_id="ymir", revision="r"),
        )
        owner = ObservatoryFragment(
            nodes=[
                TopologyNode(
                    id="cluster-eitri",
                    type_id="cluster",
                    label="eitri",
                    parent_id="realm-svartalfheim",
                )
            ],
            meta=FragmentMeta(source_id="obs-eitri", cluster_id="eitri", revision="r"),
        )
        service = _service({"obs-ymir": stub, "obs-eitri": owner})

        snapshot = await service.get_snapshot(
            [_instance("obs-ymir"), _instance("obs-eitri")], headers={}
        )

        by_id = {node.id: node for node in snapshot.nodes}
        # The owner's placement survives even though the stub was seen first.
        assert by_id["cluster-eitri"].parent_id == "realm-svartalfheim"
        assert by_id["cluster-ymir"].parent_id == "realm-ginnungagap"
        # Not a conflict worth reporting — one source simply knows more.
        assert snapshot.warnings == []

    @pytest.mark.asyncio
    async def test_a_placed_node_beats_a_bare_reference_even_without_ownership(self) -> None:
        bare = ObservatoryFragment(
            nodes=[TopologyNode(id="host-1", type_id="host", label="host-1")],
            meta=FragmentMeta(source_id="obs-a", cluster_id="", revision="r"),
        )
        placed = ObservatoryFragment(
            nodes=[
                TopologyNode(id="host-1", type_id="host", label="host-1", parent_id="cluster-x")
            ],
            meta=FragmentMeta(source_id="obs-b", cluster_id="", revision="r"),
        )
        service = _service({"obs-a": bare, "obs-b": placed})

        snapshot = await service.get_snapshot([_instance("obs-a"), _instance("obs-b")], headers={})

        assert snapshot.nodes[0].parent_id == "cluster-x"

    @pytest.mark.asyncio
    async def test_a_contested_node_id_is_reported_rather_than_resolved_silently(self) -> None:
        first = ObservatoryFragment(
            nodes=[TopologyNode(id="contested", type_id="host", label="from-a")],
            meta=FragmentMeta(source_id="obs-a"),
        )
        second = ObservatoryFragment(
            nodes=[TopologyNode(id="contested", type_id="host", label="from-b")],
            meta=FragmentMeta(source_id="obs-b"),
        )
        service = _service({"obs-a": first, "obs-b": second})

        snapshot = await service.get_snapshot([_instance("obs-a"), _instance("obs-b")], headers={})

        assert [node.label for node in snapshot.nodes] == ["from-a"]
        assert [warning.code for warning in snapshot.warnings] == ["node_id_conflict"]
        assert "obs-b" in snapshot.warnings[0].message

    @pytest.mark.asyncio
    async def test_revision_is_stable_when_the_graph_is(self) -> None:
        service = _service({"obs-a": _fragment("obs-a", node_ids=["a-1"])})

        first = await service.get_snapshot([_instance("obs-a")], headers={})
        second = await service.get_snapshot([_instance("obs-a")], headers={})

        assert first.revision == second.revision

    @pytest.mark.asyncio
    async def test_revision_changes_when_the_graph_does(self) -> None:
        one = await _service({"obs-a": _fragment("obs-a", node_ids=["a-1"])}).get_snapshot(
            [_instance("obs-a")], headers={}
        )
        two = await _service({"obs-a": _fragment("obs-a", node_ids=["a-1", "a-2"])}).get_snapshot(
            [_instance("obs-a")], headers={}
        )

        assert one.revision != two.revision
