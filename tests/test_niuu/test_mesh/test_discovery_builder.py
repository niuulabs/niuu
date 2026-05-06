"""Tests for niuu.mesh.discovery_builder."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from niuu.mesh.discovery_builder import DISCOVERY_ALIASES, build_discovery_adapters
from niuu.mesh.identity import MeshIdentity


def _make_identity() -> MeshIdentity:
    return MeshIdentity(
        peer_id="test-peer",
        realm_id="realm-123",
        persona="coder",
        capabilities=["git"],
        permission_mode="full_access",
        version="1.0.0",
    )


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

    def test_missing_adapter_key_skipped(self):
        result = build_discovery_adapters([{"no_adapter": "foo"}], _make_identity())
        assert result is None

    def test_bad_import_returns_none(self):
        result = build_discovery_adapters(
            [{"adapter": "nonexistent.module.Class"}], _make_identity()
        )
        assert result is None

    def test_own_identity_passed_to_constructor(self):
        identity = _make_identity()
        with patch("niuu.mesh.discovery_builder.import_class") as mock_import:
            mock_cls = MagicMock()
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
            mock_cls = MagicMock()
            mock_import.return_value = mock_cls
            mock_cls.return_value = MagicMock()

            build_discovery_adapters([{"adapter": "fake.Adapter"}], _make_identity())

            call_kwargs = mock_cls.call_args[1]
            assert "heartbeat_interval_s" in call_kwargs
            assert "peer_ttl_s" in call_kwargs

    def test_custom_heartbeat_and_ttl_forwarded(self):
        with patch("niuu.mesh.discovery_builder.import_class") as mock_import:
            mock_cls = MagicMock()
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
            mock_cls = MagicMock(return_value=fake_instance)
            mock_import.return_value = mock_cls

            result = build_discovery_adapters([{"adapter": "fake.Adapter"}], _make_identity())

            assert result is fake_instance

    def test_multiple_adapters_wrapped_in_composite(self):
        fake_composite_cls = MagicMock()
        fake_adapter_cls = MagicMock(return_value=MagicMock())

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
            mock_cls = MagicMock()
            mock_import.return_value = mock_cls
            mock_cls.return_value = MagicMock()

            build_discovery_adapters(
                [{"adapter": "fake.Adapter", "cluster_file": "/tmp/cluster.yaml"}],
                _make_identity(),
            )

            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["cluster_file"] == "/tmp/cluster.yaml"
            assert "adapter" not in call_kwargs

    def test_failed_instantiation_skipped(self):
        with patch("niuu.mesh.discovery_builder.import_class") as mock_import:
            mock_cls = MagicMock(side_effect=TypeError("bad args"))
            mock_import.return_value = mock_cls

            result = build_discovery_adapters([{"adapter": "bad.Adapter"}], _make_identity())
            assert result is None
