"""What every discovery adapter carries of a peer's role, description, activity and topics.

A fact a discovery did not receive is named in ``MeshPeer.not_announced``; it is never an empty
value that reads like "this peer has no role".
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from niuu.adapters.discovery.k8s import K8sDiscoveryAdapter
from niuu.adapters.discovery.mdns import MdnsDiscoveryAdapter
from niuu.adapters.discovery.sleipnir import SleipnirDiscoveryAdapter
from niuu.adapters.discovery.static import StaticDiscoveryAdapter
from niuu.mesh.config import DiscoveryConfig, DiscoveryMdnsConfig
from niuu.mesh.identity import CHOOSER_FACTS, MeshIdentity, MeshPeer, unannounced_facts

LIVE = ["activity_state", "busy"]


def _identity(peer_id: str = "me", **fields) -> MeshIdentity:
    return MeshIdentity(
        peer_id=peer_id,
        realm_id="realm-xyz",
        persona="reviewer",
        capabilities=["review"],
        permission_mode="read_only",
        version="1",
        **fields,
    )


def _reviewer(**overrides) -> MeshIdentity:
    fields = {
        "display_name": "reviewer-1",
        "participant_type": "skuld",
        "role": "reviewer",
        "description": "Reviews pull requests for correctness.",
        "consumes_event_types": ["review.requested"],
        "emits_event_types": ["review.done"],
        "activity_state": "awaiting_input",
        "busy": False,
    }
    return _identity("reviewer-1", **{**fields, **overrides})


def _config() -> DiscoveryConfig:
    return DiscoveryConfig(mdns=DiscoveryMdnsConfig(handshake_timeout_s=5.0))


def _facts(peer: MeshPeer) -> dict:
    return {
        "role": peer.role,
        "description": peer.description,
        "consumes": peer.consumes_event_types,
        "emits": peer.emits_event_types,
        "activity": peer.activity_state,
    }


REVIEWER_FACTS = {
    "role": "reviewer",
    "description": "Reviews pull requests for correctness.",
    "consumes": ["review.requested"],
    "emits": ["review.done"],
    "activity": "awaiting_input",
}


class TestUnannouncedFacts:
    def test_a_fact_is_announced_when_its_key_is_present_even_when_empty(self) -> None:
        raw = {"role": "", "description": "d", "consumes_event_types": []}

        assert unannounced_facts(raw) == [
            "activity_state",
            "busy",
            "emits_event_types",
        ]

    def test_a_fact_the_discovery_cannot_carry_is_unannounced_whatever_the_raw_says(self) -> None:
        raw = {fact: "x" for fact in CHOOSER_FACTS}

        assert unannounced_facts(raw, carries=["role"]) == [
            fact for fact in CHOOSER_FACTS if fact != "role"
        ]

    def test_busy_can_travel_with_the_key_of_another_fact(self) -> None:
        assert unannounced_facts({"activity_state": "idle"}, keys={"busy": "activity_state"}) == [
            "role",
            "description",
            "consumes_event_types",
            "emits_event_types",
        ]

    def test_nothing_at_all_announces_nothing(self) -> None:
        assert unannounced_facts(None) == list(CHOOSER_FACTS)


class TestMdns:
    def _adapter(self, identity: MeshIdentity | None = None) -> MdnsDiscoveryAdapter:
        return MdnsDiscoveryAdapter(config=_config(), own_identity=identity or _identity())

    def test_the_handshake_carries_the_whole_identity(self) -> None:
        # What the responder sends is the dataclass as JSON; the initiator parses it.
        raw = dataclasses.asdict(_reviewer(busy=True))

        peer = self._adapter()._peer_from_identity_dict(raw, candidate=None)

        assert _facts(peer) == REVIEWER_FACTS
        assert (peer.display_name, peer.participant_type) == ("reviewer-1", "skuld")
        assert (peer.status, peer.not_announced) == ("busy", [])

    def test_an_older_handshake_with_none_of_the_new_fields_names_them_unannounced(self) -> None:
        raw = {
            key: value
            for key, value in dataclasses.asdict(_reviewer()).items()
            if key not in CHOOSER_FACTS
        }

        peer = self._adapter()._peer_from_identity_dict(raw, candidate=None)

        assert peer.not_announced == list(CHOOSER_FACTS)

    def test_the_txt_records_carry_the_live_facts_and_leave_the_description_to_the_handshake(
        self,
    ) -> None:
        txt = self._adapter(_reviewer(busy=True))._build_txt_records()

        assert (txt["activity"], txt["busy"]) == ("awaiting_input", "1")
        assert txt["consumes"] == "review.requested" and txt["emits"] == "review.done"
        assert "description" not in txt and "role" not in txt

    def test_a_known_peers_activity_follows_its_re_registered_records(self) -> None:
        adapter = self._adapter()
        peer = adapter._peer_from_identity_dict(dataclasses.asdict(_reviewer()), candidate=None)
        peer.last_heartbeat = datetime(2020, 1, 1, tzinfo=UTC)

        MdnsDiscoveryAdapter._apply_txt_refresh(
            peer,
            {"activity": "tool_executing", "busy": "1", "consumes": "a,b", "emits": ""},
        )

        assert (peer.activity_state, peer.status) == ("tool_executing", "busy")
        assert peer.consumes_event_types == ["a", "b"] and peer.emits_event_types == []
        assert peer.last_heartbeat.year > 2020
        assert peer.role == "reviewer"  # the handshake's snapshot is not touched

    def test_records_from_an_older_peer_change_nothing(self) -> None:
        adapter = self._adapter()
        peer = adapter._peer_from_identity_dict(dataclasses.asdict(_reviewer()), candidate=None)

        MdnsDiscoveryAdapter._apply_txt_refresh(peer, {"peer_id": "reviewer-1", "persona": "x"})

        assert peer.activity_state == "awaiting_input"


class TestAmqpSleipnir:
    def _adapter(self, identity: MeshIdentity | None = None) -> SleipnirDiscoveryAdapter:
        return SleipnirDiscoveryAdapter(config=_config(), own_identity=identity or _identity())

    def _announce(self, identity: dict, action: str = "join", status: str = "idle") -> dict:
        return {
            "event_type": "ravn.mesh.announce",
            "source": f"ravn:{identity['peer_id']}",
            "payload": {
                "identity": identity,
                "action": action,
                "status": status,
                "task_count": 0,
            },
        }

    async def test_an_announcement_round_trips_every_fact(self) -> None:
        sender, receiver = self._adapter(_reviewer(busy=True)), self._adapter()

        await receiver._handle_announce(self._announce(sender._identity_dict(), status="busy"))

        peer = receiver.peers()["reviewer-1"]
        assert _facts(peer) == REVIEWER_FACTS
        assert (peer.display_name, peer.status, peer.not_announced) == ("reviewer-1", "busy", [])

    async def test_an_older_announcement_names_the_new_facts_unannounced(self) -> None:
        old = {
            key: value
            for key, value in self._adapter(_reviewer())._identity_dict().items()
            if key not in CHOOSER_FACTS
        }
        receiver = self._adapter()

        await receiver._handle_announce(self._announce(old))

        assert receiver.peers()["reviewer-1"].not_announced == list(CHOOSER_FACTS)

    async def test_a_heartbeat_refreshes_the_facts_of_a_known_peer(self) -> None:
        sender, receiver = self._adapter(_reviewer()), self._adapter()
        await receiver._handle_announce(self._announce(sender._identity_dict()))
        sender._identity.activity_state = "idle"
        sender._identity.consumes_event_types = ["other"]

        await receiver._handle_announce(self._announce(sender._identity_dict(), "heartbeat"))

        peer = receiver.peers()["reviewer-1"]
        assert (peer.activity_state, peer.consumes_event_types) == ("idle", ["other"])


class TestKubernetes:
    ANNOTATED = {
        "ravn.niuu.world/peer-id": "pod-1",
        "ravn.niuu.world/persona": "reviewer",
        "ravn.niuu.world/role": "reviewer",
        "ravn.niuu.world/description": "Reviews pull requests.",
        "ravn.niuu.world/consumes": "review.requested, code.changed",
        "ravn.niuu.world/emits": "review.done",
    }

    def _scan(self, annotations: dict) -> tuple[K8sDiscoveryAdapter, list]:
        adapter = K8sDiscoveryAdapter(config=_config(), own_identity=_identity())
        pod = MagicMock()
        pod.metadata.annotations = annotations
        pod.status.pod_ip = "10.0.0.9"
        v1 = MagicMock()
        v1.list_pod_for_all_namespaces.return_value.items = [pod]
        with patch("niuu.adapters.discovery.k8s.k8s_client") as k8s:
            k8s.CoreV1Api.return_value = v1
            candidates = adapter._list_candidates()
        adapter._list_candidates = lambda: candidates  # type: ignore[method-assign]
        return adapter, candidates

    async def test_the_annotations_carry_what_a_deployer_can_say_and_not_the_live_facts(
        self,
    ) -> None:
        adapter, _ = self._scan(self.ANNOTATED)

        await adapter._do_scan()

        peer = adapter.peers()["pod-1"]
        assert (peer.role, peer.description) == ("reviewer", "Reviews pull requests.")
        assert peer.consumes_event_types == ["review.requested", "code.changed"]
        assert peer.emits_event_types == ["review.done"]
        assert peer.not_announced == LIVE

    async def test_an_annotation_that_is_absent_is_unannounced_not_empty(self) -> None:
        adapter, _ = self._scan(
            {k: v for k, v in self.ANNOTATED.items() if "role" not in k and "emits" not in k}
        )

        await adapter._do_scan()

        assert adapter.peers()["pod-1"].not_announced == [
            "role",
            "activity_state",
            "busy",
            "emits_event_types",
        ]


class TestStatic:
    async def _peers(self, entries: list[dict]) -> dict[str, MeshPeer]:
        adapter = StaticDiscoveryAdapter(_identity(), peers=entries, poll_interval_s=0)
        await adapter.start()
        try:
            return adapter.peers()
        finally:
            await adapter.stop()

    async def test_an_entry_carries_what_a_file_can_say_and_never_the_live_facts(self) -> None:
        peers = await self._peers(
            [
                {
                    "peer_id": "listed",
                    "role": "reviewer",
                    "description": "Reviews pull requests.",
                    "consumes_event_types": ["review.requested"],
                    "emits_event_types": ["review.done"],
                }
            ]
        )

        peer = peers["listed"]
        assert (peer.role, peer.description) == ("reviewer", "Reviews pull requests.")
        assert peer.consumes_event_types == ["review.requested"]
        assert peer.not_announced == LIVE

    async def test_an_entry_that_leaves_a_fact_out_is_unannounced_on_it(self) -> None:
        peers = await self._peers([{"peer_id": "bare", "persona": "worker"}])

        assert peers["bare"].not_announced == [
            "role",
            "description",
            "activity_state",
            "busy",
            "consumes_event_types",
            "emits_event_types",
        ]

    async def test_an_entry_that_says_it_has_no_role_is_not_unannounced(self) -> None:
        peers = await self._peers([{"peer_id": "plain", "role": "", "description": ""}])

        assert "role" not in peers["plain"].not_announced
        assert "description" not in peers["plain"].not_announced


async def test_event_bus_names_what_an_older_announcement_left_out() -> None:
    from niuu.adapters.discovery.event_bus import EventBusDiscoveryAdapter
    from sleipnir.adapters.in_process import InProcessBus
    from sleipnir.domain.events import SleipnirEvent

    bus = InProcessBus()
    watcher = EventBusDiscoveryAdapter(
        _identity("watcher"),
        bus,
        bus,
        manage_transport_lifecycle=False,
    )
    await watcher.start()
    for identity in (
        {"peer_id": "old", "realm_id": "realm-xyz", "persona": "coder"},
        {
            "peer_id": "new",
            "realm_id": "realm-xyz",
            "persona": "coder",
            **{"role": "", "description": "", "activity_state": "idle"},
            "consumes_event_types": [],
            "emits_event_types": [],
        },
    ):
        await bus.publish(
            SleipnirEvent(
                event_type=watcher._announce_event_type,
                source=f"ravn:{identity['peer_id']}",
                payload={"identity": identity, "action": "join", "status": "idle"},
                summary="peer join",
                domain="code",
                urgency=0.3,
                timestamp=datetime.now(UTC),
            )
        )
    await bus.flush()

    assert watcher.peers()["old"].not_announced == list(CHOOSER_FACTS)
    assert watcher.peers()["new"].not_announced == []
    await watcher.stop()
