"""Tests for niuu.mesh.identity.MeshIdentity."""

from __future__ import annotations

from niuu.mesh.identity import MeshIdentity


class TestMeshIdentity:
    def test_required_fields(self):
        identity = MeshIdentity(
            peer_id="peer-1",
            realm_id="realm-abc",
            persona="coder",
            capabilities=["git", "terminal"],
            permission_mode="full_access",
            version="1.0.0",
        )
        assert identity.peer_id == "peer-1"
        assert identity.realm_id == "realm-abc"
        assert identity.persona == "coder"
        assert identity.capabilities == ["git", "terminal"]
        assert identity.permission_mode == "full_access"
        assert identity.version == "1.0.0"

    def test_optional_fields_default_to_none(self):
        identity = MeshIdentity(
            peer_id="p",
            realm_id="",
            persona="skuld",
            capabilities=[],
            permission_mode="full_access",
            version="0.1.0",
        )
        assert identity.rep_address is None
        assert identity.pub_address is None
        assert identity.spiffe_id is None
        assert identity.sleipnir_routing_key is None

    def test_consumes_event_types_defaults_to_empty(self):
        identity = MeshIdentity(
            peer_id="p",
            realm_id="",
            persona="skuld",
            capabilities=[],
            permission_mode="full_access",
            version="0.1.0",
        )
        assert identity.consumes_event_types == []

    def test_optional_fields_can_be_set(self):
        identity = MeshIdentity(
            peer_id="p",
            realm_id="r",
            persona="reviewer",
            capabilities=["review"],
            permission_mode="workspace_write",
            version="2.0.0",
            consumes_event_types=["code.changed"],
            rep_address="tcp://127.0.0.1:6001",
            pub_address="tcp://127.0.0.1:6000",
        )
        assert identity.consumes_event_types == ["code.changed"]
        assert identity.rep_address == "tcp://127.0.0.1:6001"
        assert identity.pub_address == "tcp://127.0.0.1:6000"

    def test_has_same_fields_as_ravn_identity(self):
        """MeshIdentity must have all fields that RavnIdentity has so it can
        substitute for it in discovery adapters (duck typing)."""
        import dataclasses

        from ravn.domain.models import RavnIdentity

        mesh_fields = {f.name for f in dataclasses.fields(MeshIdentity)}
        ravn_fields = {f.name for f in dataclasses.fields(RavnIdentity)}

        missing = ravn_fields - mesh_fields
        assert not missing, f"MeshIdentity is missing fields: {missing}"
