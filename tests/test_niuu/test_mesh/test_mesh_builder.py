"""Tests for niuu.mesh.build_mesh_from_adapters_list.

A configured mesh adapter that cannot be built is fatal: the builder raises
with the remedy, and never returns a mesh made of the entries that did load.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from niuu.mesh import MeshBuildError, build_mesh_from_adapters_list
from niuu.mesh.transport_builder import TransportBuildError
from niuu.utils import import_class


class _FakeMesh:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _FakeSleipnirMesh(_FakeMesh):
    pass


class _RejectingMesh:
    def __init__(self, **kwargs: Any) -> None:
        raise TypeError("unexpected keyword argument 'secret'")


_FAKE_CLASSES: dict[str, type] = {
    "fake.mesh.Mesh": _FakeMesh,
    "fake.mesh.OtherMesh": _FakeMesh,
    "fake.sleipnir.Mesh": _FakeSleipnirMesh,
    "fake.mesh.Rejecting": _RejectingMesh,
}


def _import(path: str) -> type:
    if path in _FAKE_CLASSES:
        return _FAKE_CLASSES[path]
    return import_class(path)


@pytest.fixture(autouse=True)
def _fake_adapter_classes():
    with patch("niuu.mesh.import_class", side_effect=_import):
        yield


def _build(adapters: list[dict[str, Any]], **kwargs: Any) -> Any:
    return build_mesh_from_adapters_list(
        adapters=adapters,
        own_peer_id="peer-a",
        rpc_timeout_s=5.0,
        **kwargs,
    )


class TestBuildFailuresRaise:
    def test_unknown_alias_raises_with_remedy(self):
        with pytest.raises(MeshBuildError, match="unknown mesh adapter 'carrier_pigeon'") as exc:
            _build([{"adapter": "carrier_pigeon"}])

        assert "sleipnir, webhook" in str(exc.value)

    def test_constructor_failure_raises_with_remedy(self):
        with pytest.raises(MeshBuildError, match="could not be constructed") as exc:
            _build([{"adapter": "fake.mesh.Rejecting", "secret": "x"}])

        assert "mesh.adapters[0]" in str(exc.value)
        assert "Fix that entry's settings" in str(exc.value)
        assert isinstance(exc.value.__cause__, TypeError)

    def test_one_unbuildable_entry_fails_the_whole_config(self):
        """A mesh built from only the entries that loaded is a silent downgrade."""
        with pytest.raises(MeshBuildError, match=r"mesh.adapters\[1\]"):
            _build([{"adapter": "fake.mesh.Mesh"}, {"adapter": "nonexistent.module.Mesh"}])

    def test_transport_build_failure_propagates(self):
        builder = MagicMock(side_effect=TransportBuildError("unknown mesh transport 'amqp'"))

        with pytest.raises(TransportBuildError, match="amqp"):
            _build(
                [{"adapter": "fake.sleipnir.Mesh", "transport": "amqp"}],
                sleipnir_transport_builder=builder,
            )


class TestBuiltMesh:
    def test_single_entry_is_returned_directly(self):
        mesh = _build([{"adapter": "fake.mesh.Mesh", "listen_port": 7490}])

        assert isinstance(mesh, _FakeMesh)
        assert mesh.kwargs["listen_port"] == 7490
        assert mesh.kwargs["own_peer_id"] == "peer-a"
        assert mesh.kwargs["rpc_timeout_s"] == 5.0

    def test_sleipnir_entry_gets_the_built_transport(self):
        transport = object()
        builder = MagicMock(return_value=transport)
        entry = {"adapter": "fake.sleipnir.Mesh", "transport": "nats"}

        mesh = _build(
            [entry],
            discovery=object(),
            sleipnir_transport_builder=builder,
            environment_id="realm-1",
        )

        builder.assert_called_once_with(entry)
        assert mesh.kwargs["publisher"] is transport
        assert mesh.kwargs["subscriber"] is transport
        assert mesh.kwargs["environment_id"] == "realm-1"
        assert "discovery" not in mesh.kwargs
        assert "transport" not in mesh.kwargs

    def test_several_entries_are_wrapped_in_a_composite(self):
        mesh = _build([{"adapter": "fake.mesh.Mesh"}, {"adapter": "fake.mesh.OtherMesh"}])

        assert type(mesh).__name__ == "CompositeMeshAdapter"
        assert [type(t) for t in mesh.transports] == [_FakeMesh, _FakeMesh]
