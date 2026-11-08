"""Choosing whom to talk to: what a Skuld announces, and the tool that lists the peers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from niuu.forge_mcp.models import ForgeApiError
from niuu.mesh.identity import MeshPeer
from personas.domain.persona_document import PersonaDocumentError
from skuld.config import MeshConfig, SkuldSettings
from skuld.forge_mcp.host import BrokerForgeMcpHost
from skuld.mesh_topics import describe_peer, mesh_topics, peer_table
from tests.test_skuld.test_broker_review_regressions import _broker
from tests.test_skuld.test_mesh_publish import _persona


def _settings(persona=None, **mesh) -> SkuldSettings:
    return SkuldSettings(
        mesh=MeshConfig(enabled=True, peer_id="me", **mesh), session={"id": "s", "persona": persona}
    )


def _peer(peer_id: str, **fields) -> MeshPeer:
    base = {
        "realm_id": "r",
        "persona": "coder",
        "capabilities": ["coding"],
        "permission_mode": "full_access",
        "version": "1",
    }
    return MeshPeer(peer_id=peer_id, **{**base, **fields})


class TestWhatASkuldAnnounces:
    def test_the_role_and_description_come_from_the_persona(self) -> None:
        persona = _persona(consumes=["review.requested"], persona_id="reviewer")
        persona["definition"]["description"] = "Reviews pull requests for correctness."

        topics = mesh_topics(_settings(persona))

        assert (topics.role, topics.description) == (
            "reviewer",
            "Reviews pull requests for correctness.",
        )

    def test_the_operators_role_wins_and_their_description_fills_a_gap(self) -> None:
        topics = mesh_topics(
            _settings(_persona(persona_id="reviewer"), role="lead", description="d")
        )

        assert (topics.role, topics.description) == ("lead", "d")

    def test_a_session_without_a_persona_announces_only_what_the_operator_set(self) -> None:
        assert mesh_topics(_settings()).role == ""
        topics = mesh_topics(_settings(role=" builder ", description=" Builds. "))
        assert (topics.role, topics.description) == ("builder", "Builds.")

    def test_a_persona_description_that_is_not_text_fails_loudly(self) -> None:
        persona = _persona(persona_id="reviewer")
        persona["definition"]["description"] = ["not", "text"]

        with pytest.raises(PersonaDocumentError, match="description must be a string"):
            mesh_topics(_settings(persona))


class TestBrokerAnnouncement:
    async def test_the_identity_carries_the_declaration_and_follows_the_activity(
        self, tmp_path
    ) -> None:
        broker = _broker(tmp_path)
        broker._settings.session.name = "reviewer-1"
        broker._settings.mesh = MeshConfig(
            enabled=True,
            peer_id="me",
            transport="in_process",
            rpc_timeout_s=5.0,
            capabilities=["review"],
            consumes_event_types=[],
            # Signing is covered in test_mesh_attestation; this test is about the announcement.
            attestation={"required": False},
        )
        persona = _persona(
            consumes=["review.requested"], produces={"event_type": "review.done"}, persona_id="rev"
        )
        persona["definition"]["description"] = "Reviews."
        broker._settings.session.persona = persona
        broker._room_bridge = None

        await broker._start_mesh_adapter()
        try:
            identity = broker._mesh_identity
            assert (identity.persona, identity.role, identity.description) == (
                "rev",
                "rev",
                "Reviews.",
            )
            assert identity.display_name == "reviewer-1"
            assert identity.capabilities == ["review"]
            assert identity.consumes_event_types == ["review.requested"]
            assert identity.emits_event_types == ["code.changed", "review.done"]

            broker._set_activity_state("tool_executing")
            assert (identity.activity_state, identity.busy) == ("tool_executing", True)
            broker._set_activity_state("awaiting_input")
            assert (identity.activity_state, identity.busy) == ("awaiting_input", False)
            broker._set_activity_state("idle")
            assert identity.activity_state == "idle"
        finally:
            await broker._mesh_adapter.stop()

    def test_an_activity_change_before_the_mesh_starts_changes_nothing_else(self, tmp_path) -> None:
        broker = _broker(tmp_path)

        broker._set_activity_state("active")

        assert broker._mesh_identity is None


class TestPeerTable:
    def test_a_peer_is_described_by_what_it_announced_and_nothing_else(self) -> None:
        peer = _peer(
            "reviewer-1",
            display_name="reviewer-1",
            participant_type="skuld",
            role="reviewer",
            description="Reviews.",
            consumes_event_types=["review.requested"],
            emits_event_types=["review.done"],
            activity_state="idle",
        )

        facts = describe_peer(peer)

        assert facts == {
            "peer_id": "reviewer-1",
            "name": "reviewer-1",
            "kind": "skuld",
            "persona": "coder",
            "role": "reviewer",
            "description": "Reviews.",
            "capabilities": ["coding"],
            "consumes": ["review.requested"],
            "produces": ["review.done"],
            "activity_state": "idle",
            "status": "idle",
            "task_count": 0,
            "trust_level": "unverified",
            "last_heartbeat": peer.last_heartbeat.isoformat(),
        }

    def test_filters_are_exact_matches_that_must_all_hold_and_the_order_is_not_a_preference(
        self,
    ) -> None:
        peers = [
            _peer("b", role="reviewer", capabilities=["review", "git"]),
            _peer("a", role="reviewer", capabilities=["git"], consumes_event_types=["x"]),
            _peer("c", role="planner", capabilities=["review"], emits_event_types=["y"]),
        ]

        assert [p["peer_id"] for p in peer_table(peers)] == ["a", "b", "c"]
        assert [p["peer_id"] for p in peer_table(peers, role="reviewer")] == ["a", "b"]
        assert [p["peer_id"] for p in peer_table(peers, capability="review")] == ["b", "c"]
        assert [p["peer_id"] for p in peer_table(peers, role="reviewer", capability="review")] == [
            "b"
        ]
        assert [p["peer_id"] for p in peer_table(peers, consumes="x")] == ["a"]
        assert [p["peer_id"] for p in peer_table(peers, produces="y")] == ["c"]
        assert peer_table(peers, role="review") == []  # exact, not a prefix or a guess


class TestMeshPeersHost:
    def _host(self, tmp_path, *, peers=None, discovery=True, adapter=True):
        broker = _broker(tmp_path)
        broker._mesh_adapter = SimpleNamespace(peer_id="me") if adapter else None
        broker._mesh_discovery = (
            SimpleNamespace(peers=lambda: {p.peer_id: p for p in (peers or [])})
            if discovery
            else None
        )
        return BrokerForgeMcpHost(broker, client=SimpleNamespace())

    async def test_it_returns_the_table_with_a_count_and_a_time(self, tmp_path) -> None:
        host = self._host(
            tmp_path,
            peers=[_peer("a", role="reviewer"), _peer("b", role="planner")],
        )

        answer = await host.mesh_peers(role="reviewer", capability="", consumes="", produces="")

        assert answer["count"] == 1 and [p["peer_id"] for p in answer["peers"]] == ["a"]
        assert answer["as_of"]

    async def test_an_empty_flock_is_an_empty_table_not_an_error(self, tmp_path) -> None:
        host = self._host(tmp_path, peers=[])

        answer = await host.mesh_peers(role="", capability="", consumes="", produces="")

        assert answer["peers"] == [] and answer["count"] == 0

    @pytest.mark.parametrize("kwargs", [{"discovery": False}, {"adapter": False}])
    async def test_no_discovery_is_an_error_naming_the_remedy_and_not_an_empty_table(
        self, tmp_path, kwargs
    ) -> None:
        host = self._host(tmp_path, **kwargs)

        with pytest.raises(ForgeApiError, match="mesh.discovery_adapters") as excinfo:
            await host.mesh_peers(role="", capability="", consumes="", produces="")

        assert excinfo.value.status == 503 and "mesh_send" in excinfo.value.detail
