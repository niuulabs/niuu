"""Tests for niuu.mesh.transport_builder."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from niuu.mesh.transport_builder import TRANSPORT_ALIASES, build_nng_transport, build_transport


class TestTransportAliases:
    def test_nng_alias_present(self):
        assert "nng" in TRANSPORT_ALIASES
        assert "NngTransport" in TRANSPORT_ALIASES["nng"]

    def test_rabbitmq_aliases_present(self):
        assert "sleipnir" in TRANSPORT_ALIASES
        assert "rabbitmq" in TRANSPORT_ALIASES
        assert TRANSPORT_ALIASES["sleipnir"] == TRANSPORT_ALIASES["rabbitmq"]

    def test_nats_alias_present(self):
        assert "nats" in TRANSPORT_ALIASES

    def test_redis_alias_present(self):
        assert "redis" in TRANSPORT_ALIASES

    def test_in_process_alias_present(self):
        assert "in_process" in TRANSPORT_ALIASES
        assert "InProcessBus" in TRANSPORT_ALIASES["in_process"]


class TestBuildTransport:
    def test_unknown_adapter_returns_none_on_import_error(self):
        result = build_transport("nonexistent.module.Class")
        assert result is None

    def test_missing_adapter_field_returns_none(self):
        result = build_transport("")
        assert result is None

    def test_in_process_bus_built_successfully(self):
        result = build_transport("in_process")
        assert result is not None

    def test_fq_class_path_resolves(self):
        result = build_transport("sleipnir.adapters.in_process.InProcessBus")
        assert result is not None

    def test_kwargs_forwarded_to_constructor(self):
        with patch("niuu.mesh.transport_builder.import_class") as mock_import:
            mock_cls = MagicMock(return_value="transport_instance")
            mock_import.return_value = mock_cls

            result = build_transport("nng", address="tcp://0.0.0.0:6000", service_id="test")

            mock_cls.assert_called_once_with(address="tcp://0.0.0.0:6000", service_id="test")
            assert result == "transport_instance"

    def test_instantiation_failure_returns_none(self):
        with patch("niuu.mesh.transport_builder.import_class") as mock_import:
            mock_cls = MagicMock(side_effect=RuntimeError("bad args"))
            mock_import.return_value = mock_cls

            result = build_transport("nng", address="bad")
            assert result is None


class TestBuildNngTransport:
    """Tests for build_nng_transport — mocked so pynng is not required."""

    def test_calls_build_transport_with_nng_alias(self):
        with patch("niuu.mesh.transport_builder.import_class") as mock_import:
            fake_cls = MagicMock(return_value="nng_instance")
            mock_import.return_value = fake_cls

            result = build_nng_transport(
                address="tcp://127.0.0.1:0",
                service_id="test:peer",
            )

            assert result == "nng_instance"
            call_kwargs = fake_cls.call_args[1]
            assert call_kwargs["address"] == "tcp://127.0.0.1:0"
            assert call_kwargs["service_id"] == "test:peer"

    def test_peer_addresses_forwarded(self):
        with patch("niuu.mesh.transport_builder.import_class") as mock_import:
            fake_cls = MagicMock(return_value="nng_instance")
            mock_import.return_value = fake_cls

            build_nng_transport(
                address="tcp://127.0.0.1:0",
                service_id="test:peer",
                peer_addresses=["tcp://10.0.0.1:6000"],
            )

            call_kwargs = fake_cls.call_args[1]
            assert call_kwargs["peer_addresses"] == ["tcp://10.0.0.1:6000"]

    def test_none_peer_addresses_passed_as_none(self):
        with patch("niuu.mesh.transport_builder.import_class") as mock_import:
            fake_cls = MagicMock(return_value="nng_instance")
            mock_import.return_value = fake_cls

            build_nng_transport(
                address="tcp://127.0.0.1:0",
                service_id="test:peer",
                peer_addresses=None,
            )

            call_kwargs = fake_cls.call_args[1]
            assert call_kwargs["peer_addresses"] is None

    def test_empty_peer_addresses_normalised_to_none(self):
        with patch("niuu.mesh.transport_builder.import_class") as mock_import:
            fake_cls = MagicMock(return_value="nng_instance")
            mock_import.return_value = fake_cls

            build_nng_transport(
                address="tcp://127.0.0.1:0",
                service_id="test:peer",
                peer_addresses=[],
            )

            call_kwargs = fake_cls.call_args[1]
            # Empty list is normalised to None inside build_nng_transport
            assert call_kwargs["peer_addresses"] is None
