"""Tests for niuu.mesh.transport_builder."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from niuu.mesh.transport_builder import (
    TRANSPORT_ALIASES,
    TransportBuildError,
    build_nng_transport,
    build_transport,
)


class TestTransportAliases:
    def test_nng_alias_present(self):
        assert "nng" in TRANSPORT_ALIASES
        assert "NngTransport" in TRANSPORT_ALIASES["nng"]

    def test_nats_alias_present(self):
        assert "nats" in TRANSPORT_ALIASES

    def test_in_process_alias_present(self):
        assert "in_process" in TRANSPORT_ALIASES
        assert "InProcessBus" in TRANSPORT_ALIASES["in_process"]

    @pytest.mark.parametrize("alias", ["sleipnir", "rabbitmq", "redis"])
    def test_at_most_once_broker_aliases_are_not_mesh_transports(self, alias):
        # The RabbitMQ and Redis Streams subscribers ack before dispatch, so
        # the mesh must not be able to select them.
        assert alias not in TRANSPORT_ALIASES


class TestBuildTransport:
    def test_unimportable_class_path_raises_with_remedy(self):
        with pytest.raises(TransportBuildError, match="could not be imported") as exc_info:
            build_transport("nonexistent.module.Class")
        assert "Install the package" in str(exc_info.value)
        assert isinstance(exc_info.value.__cause__, ImportError)

    def test_missing_class_in_module_raises(self):
        with pytest.raises(TransportBuildError, match="could not be imported"):
            build_transport("sleipnir.adapters.in_process.NoSuchTransport")

    @pytest.mark.parametrize("adapter", ["", "rabbitmq", "redis"])
    def test_unknown_alias_raises_with_valid_choices(self, adapter):
        with pytest.raises(TransportBuildError, match="unknown mesh transport") as exc_info:
            build_transport(adapter)
        assert "in_process, nats, nng" in str(exc_info.value)

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

    def test_instantiation_failure_raises_with_cause(self):
        with patch("niuu.mesh.transport_builder.import_class") as mock_import:
            mock_cls = MagicMock(side_effect=TypeError("unexpected keyword argument 'amqp_url'"))
            mock_import.return_value = mock_cls

            with pytest.raises(TransportBuildError, match="could not be constructed") as exc_info:
                build_transport("nng", address="bad")

        assert "amqp_url" in str(exc_info.value)
        assert "mesh.nng" in str(exc_info.value)
        assert isinstance(exc_info.value.__cause__, TypeError)


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
