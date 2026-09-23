"""Tests for niuu.mesh.discovery_builder."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from niuu.mesh.discovery_builder import (
    DISCOVERY_ALIASES,
    DiscoveryBuildError,
    build_discovery_adapters,
)
from niuu.mesh.identity import MeshIdentity
from niuu.mesh.transport_builder import TransportBuildError


def _make_identity() -> MeshIdentity:
    return MeshIdentity(
        peer_id="test-peer",
        realm_id="realm-123",
        persona="coder",
        capabilities=["git"],
        permission_mode="full_access",
        version="1.0.0",
    )


def _adapter_cls(*, requires_sleipnir_transport: bool = False, **kwargs) -> MagicMock:
    """A fake discovery adapter class; MagicMock attributes are truthy by default."""
    cls = MagicMock(**kwargs)
    cls.requires_sleipnir_transport = requires_sleipnir_transport
    return cls


class TestDiscoveryAliases:
    def test_mdns_alias_present(self):
        assert "mdns" in DISCOVERY_ALIASES
        assert "MdnsDiscoveryAdapter" in DISCOVERY_ALIASES["mdns"]

    def test_static_alias_present(self):
        assert "static" in DISCOVERY_ALIASES
        assert "StaticDiscoveryAdapter" in DISCOVERY_ALIASES["static"]

    def test_k8s_alias_present(self):
        assert "k8s" in DISCOVERY_ALIASES

    def test_sleipnir_alias_present(self):
        assert "sleipnir" in DISCOVERY_ALIASES


class TestBuildDiscoveryAdapters:
    def test_empty_config_returns_none(self):
        result = build_discovery_adapters([], _make_identity())
        assert result is None

    def test_missing_adapter_key_raises(self):
        with pytest.raises(DiscoveryBuildError, match="entry 0 has no 'adapter' key"):
            build_discovery_adapters([{"no_adapter": "foo"}], _make_identity())

    def test_unknown_alias_raises(self):
        with pytest.raises(DiscoveryBuildError, match="unknown discovery adapter 'gossip'"):
            build_discovery_adapters([{"adapter": "gossip"}], _make_identity())

    def test_bad_import_raises_with_remedy(self):
        with pytest.raises(DiscoveryBuildError, match="could not be imported") as excinfo:
            build_discovery_adapters([{"adapter": "nonexistent.module.Class"}], _make_identity())

        assert "remove the entry" in str(excinfo.value)
        assert isinstance(excinfo.value.__cause__, ImportError)

    def test_one_unbuildable_entry_fails_the_whole_config(self):
        """A working adapter next to a broken one must not hide the broken one."""
        good_cls = _adapter_cls()

        def _import(path: str):
            if "Composite" in path:
                return MagicMock()
            if path == "fake.Good":
                return good_cls
            raise ImportError(f"No module named {path!r}")

        with patch("niuu.mesh.discovery_builder.import_class", side_effect=_import):
            with pytest.raises(DiscoveryBuildError, match="entry 1"):
                build_discovery_adapters(
                    [{"adapter": "fake.Good"}, {"adapter": "fake.Missing"}],
                    _make_identity(),
                )

    def test_own_identity_passed_to_constructor(self):
        identity = _make_identity()
        with patch("niuu.mesh.discovery_builder.import_class") as mock_import:
            mock_cls = _adapter_cls()
            mock_import.return_value = mock_cls
            mock_cls.return_value = MagicMock()

            build_discovery_adapters(
                [{"adapter": "fake.Adapter"}],
                identity,
            )

            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["own_identity"] is identity

    def test_default_heartbeat_and_ttl_injected(self):
        with patch("niuu.mesh.discovery_builder.import_class") as mock_import:
            mock_cls = _adapter_cls()
            mock_import.return_value = mock_cls
            mock_cls.return_value = MagicMock()

            build_discovery_adapters([{"adapter": "fake.Adapter"}], _make_identity())

            call_kwargs = mock_cls.call_args[1]
            assert "heartbeat_interval_s" in call_kwargs
            assert "peer_ttl_s" in call_kwargs

    def test_custom_heartbeat_and_ttl_forwarded(self):
        with patch("niuu.mesh.discovery_builder.import_class") as mock_import:
            mock_cls = _adapter_cls()
            mock_import.return_value = mock_cls
            mock_cls.return_value = MagicMock()

            build_discovery_adapters(
                [{"adapter": "fake.Adapter"}],
                _make_identity(),
                heartbeat_interval_s=2.5,
                peer_ttl_s=15.0,
            )

            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["heartbeat_interval_s"] == 2.5
            assert call_kwargs["peer_ttl_s"] == 15.0

    def test_single_adapter_returned_directly(self):
        with patch("niuu.mesh.discovery_builder.import_class") as mock_import:
            fake_instance = MagicMock()
            mock_cls = _adapter_cls(return_value=fake_instance)
            mock_import.return_value = mock_cls

            result = build_discovery_adapters([{"adapter": "fake.Adapter"}], _make_identity())

            assert result is fake_instance

    def test_multiple_adapters_wrapped_in_composite(self):
        fake_composite_cls = MagicMock()
        fake_adapter_cls = _adapter_cls(return_value=MagicMock())

        def _side_effect(path: str):
            if "Composite" in path:
                return fake_composite_cls
            return fake_adapter_cls

        with patch("niuu.mesh.discovery_builder.import_class", side_effect=_side_effect):
            build_discovery_adapters(
                [{"adapter": "fake.A"}, {"adapter": "fake.B"}],
                _make_identity(),
            )

            fake_composite_cls.assert_called_once()
            backends = fake_composite_cls.call_args[1]["backends"]
            assert len(backends) == 2

    def test_per_adapter_kwargs_forwarded(self):
        with patch("niuu.mesh.discovery_builder.import_class") as mock_import:
            mock_cls = _adapter_cls()
            mock_import.return_value = mock_cls
            mock_cls.return_value = MagicMock()

            build_discovery_adapters(
                [{"adapter": "fake.Adapter", "cluster_file": "/tmp/cluster.yaml"}],
                _make_identity(),
            )

            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["cluster_file"] == "/tmp/cluster.yaml"
            assert "adapter" not in call_kwargs

    def test_failed_instantiation_raises_with_remedy(self):
        with patch("niuu.mesh.discovery_builder.import_class") as mock_import:
            mock_import.return_value = _adapter_cls(side_effect=TypeError("bad args"))

            with pytest.raises(DiscoveryBuildError, match="could not be constructed") as excinfo:
                build_discovery_adapters([{"adapter": "bad.Adapter"}], _make_identity())

        assert "Fix that entry's settings" in str(excinfo.value)
        assert isinstance(excinfo.value.__cause__, TypeError)


class TestSleipnirTransportInjection:
    def test_transport_requiring_adapter_gets_built_transport(self):
        transport = MagicMock()
        builder = MagicMock(return_value=transport)
        entry = {"adapter": "fake.EventBus", "transport": "nats"}
        with patch("niuu.mesh.discovery_builder.import_class") as mock_import:
            mock_cls = _adapter_cls(requires_sleipnir_transport=True)
            mock_import.return_value = mock_cls

            build_discovery_adapters(
                [entry],
                _make_identity(),
                sleipnir_transport_builder=builder,
            )

        builder.assert_called_once_with(entry)
        call_kwargs = mock_cls.call_args[1]
        assert call_kwargs["publisher"] is transport
        assert call_kwargs["subscriber"] is transport
        assert "transport" not in call_kwargs

    def test_transport_requiring_adapter_without_builder_raises(self):
        with patch("niuu.mesh.discovery_builder.import_class") as mock_import:
            mock_cls = _adapter_cls(requires_sleipnir_transport=True)
            mock_import.return_value = mock_cls

            with pytest.raises(DiscoveryBuildError, match="needs a Sleipnir transport"):
                build_discovery_adapters([{"adapter": "event_bus"}], _make_identity())

        mock_cls.assert_not_called()

    def test_transport_build_failure_propagates(self):
        builder = MagicMock(side_effect=TransportBuildError("unknown mesh transport 'amqp'"))
        with patch("niuu.mesh.discovery_builder.import_class") as mock_import:
            mock_cls = _adapter_cls(requires_sleipnir_transport=True)
            mock_import.return_value = mock_cls

            with pytest.raises(TransportBuildError, match="amqp"):
                build_discovery_adapters(
                    [{"adapter": "event_bus", "transport": "amqp"}],
                    _make_identity(),
                    sleipnir_transport_builder=builder,
                )

        mock_cls.assert_not_called()
