"""Tests for transport builder and _resolve_transport_kwargs (NIU-629, NIU-634).

_build_sleipnir_transport was deleted in NIU-634; the logic now lives in
_build_mesh via niuu.mesh.transport_builder.build_transport directly.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from niuu.mesh.transport_builder import TRANSPORT_ALIASES as _TRANSPORT_ALIASES
from ravn.cli.commands import _resolve_transport_kwargs
from ravn.config import Settings


def _default_nats_kwargs(servers: list[str] | None = None) -> dict[str, Any]:
    return {
        "servers": servers or ["nats://localhost:4222"],
        "stream_name": "ravn_environment",
        "jetstream_domain": "",
        "subject_prefix": "ravn.environment",
        "retention": "limits",
        "max_age_seconds": 604800,
        "max_bytes": 1073741824,
        "ring_buffer_depth": 1000,
        "connect_timeout_s": 10.0,
        "max_reconnect_attempts": 60,
        "ensure_stream": True,
        "publish_timeout_s": 10.0,
        "tls_ca_file": "",
        "tls_ca_pem": "",
        "tls_cert_file": "",
        "tls_key_file": "",
        "tls_hostname": "",
        "tls_handshake_first": False,
        "tls_legacy_ca": False,
        "tls_insecure_skip_verify": False,
        "user": "",
        "password": "",
        "token": "",
        "nkeys_seed_file": "",
        "nkeys_seed": "",
        "extra_subscriptions": [],
        "core_subscriptions": [],
    }


def _make_settings(**overrides: Any) -> Settings:
    """Create a minimal Settings with sensible defaults for transport tests."""
    s = Settings()
    # Wire up mesh.nng defaults
    s.mesh.nng.pub_sub_address = "ipc:///tmp/test.ipc"
    s.mesh.own_peer_id = "test-peer"
    for key, val in overrides.items():
        parts = key.split(".")
        obj = s
        for part in parts[:-1]:
            obj = getattr(obj, part)
        setattr(obj, parts[-1], val)
    return s


class TestTransportAliases:
    """Verify the alias map covers all known transport short names."""

    def test_all_short_names_present(self):
        expected = {"nng", "sleipnir", "rabbitmq", "nats", "redis", "in_process"}
        assert set(_TRANSPORT_ALIASES.keys()) == expected

    def test_aliases_are_fully_qualified(self):
        for alias, fq in _TRANSPORT_ALIASES.items():
            parts = fq.rsplit(".", 1)
            assert len(parts) == 2, f"alias {alias!r} is not a dotted class path"

    def test_sleipnir_and_rabbitmq_resolve_to_same_class(self):
        assert _TRANSPORT_ALIASES["sleipnir"] == _TRANSPORT_ALIASES["rabbitmq"]


class TestResolveTransportKwargs:
    """Verify _resolve_transport_kwargs returns correct kwargs per adapter."""

    def test_nng_kwargs(self):
        settings = _make_settings()
        kwargs = _resolve_transport_kwargs(settings, "nng")
        assert kwargs["address"] == "ipc:///tmp/test.ipc"
        assert kwargs["service_id"] == "ravn:test-peer"
        assert "peer_addresses" in kwargs

    def test_rabbitmq_kwargs_with_env(self):
        settings = _make_settings()
        with patch.dict("os.environ", {"SLEIPNIR_AMQP_URL": "amqp://host"}):
            kwargs = _resolve_transport_kwargs(settings, "rabbitmq")
        assert kwargs == {"amqp_url": "amqp://host"}

    def test_rabbitmq_kwargs_empty_when_env_missing(self):
        settings = _make_settings()
        with patch.dict("os.environ", {}, clear=True):
            kwargs = _resolve_transport_kwargs(settings, "rabbitmq")
        assert kwargs == {}

    def test_sleipnir_alias_same_as_rabbitmq(self):
        settings = _make_settings()
        with patch.dict("os.environ", {"SLEIPNIR_AMQP_URL": "amqp://host"}):
            kwargs = _resolve_transport_kwargs(settings, "sleipnir")
        assert kwargs == {"amqp_url": "amqp://host"}

    def test_nats_kwargs(self):
        with patch.dict("os.environ", {"NATS_URL": "nats://custom:4222"}):
            settings = _make_settings()
            kwargs = _resolve_transport_kwargs(settings, "nats")
        assert kwargs == _default_nats_kwargs(["nats://custom:4222"])

    def test_nats_kwargs_default(self):
        settings = _make_settings()
        with patch.dict("os.environ", {}, clear=True):
            kwargs = _resolve_transport_kwargs(settings, "nats")
        assert kwargs == _default_nats_kwargs()

    def test_nats_kwargs_supports_multiple_servers_and_replay(self):
        settings = _make_settings(
            **{
                "mesh.nats.servers": ["nats://a:4222", "nats://b:4222"],
                "mesh.nats.stream_name": "valkyrie_signals",
                "mesh.nats.subject_prefix": "odin.valkyrie",
                "mesh.nats.consumer_group": "k8s-watchers",
                "mesh.nats.replay_from_sequence": 42,
                "mesh.nats.ring_buffer_depth": 2048,
            }
        )
        kwargs = _resolve_transport_kwargs(settings, "nats")
        assert kwargs == {
            **_default_nats_kwargs(["nats://a:4222", "nats://b:4222"]),
            "stream_name": "valkyrie_signals",
            "subject_prefix": "odin.valkyrie",
            "consumer_group": "k8s-watchers",
            "replay_from_sequence": 42,
            "ring_buffer_depth": 2048,
        }

    def test_nats_kwargs_supports_gitops_managed_tls_and_auth(self):
        settings = _make_settings(
            **{
                "mesh.nats.servers": ["tls://nats-valhalla.nats.svc.cluster.local:4222"],
                "mesh.nats.ensure_stream": False,
                "mesh.nats.tls_ca_file": "/etc/nats/ca.crt",
                "mesh.nats.tls_ca_pem": "certificate-pem",
                "mesh.nats.tls_hostname": "nats-valhalla.nats.svc.cluster.local",
                "mesh.nats.tls_handshake_first": True,
                "mesh.nats.tls_legacy_ca": True,
                "mesh.nats.tls_insecure_skip_verify": True,
                "mesh.nats.user": "valkyrie-valhalla",
                "mesh.nats.password_env": "VALKYRIE_NATS_PASSWORD",
                "mesh.nats.nkeys_seed_file": "/etc/nats/user.nk",
            }
        )
        with patch.dict(
            "os.environ",
            {
                "VALKYRIE_NATS_PASSWORD": "secret",
            },
            clear=True,
        ):
            kwargs = _resolve_transport_kwargs(settings, "nats")

        assert kwargs == {
            **_default_nats_kwargs(["tls://nats-valhalla.nats.svc.cluster.local:4222"]),
            "ensure_stream": False,
            "tls_ca_file": "/etc/nats/ca.crt",
            "tls_ca_pem": "certificate-pem",
            "tls_hostname": "nats-valhalla.nats.svc.cluster.local",
            "tls_handshake_first": True,
            "tls_legacy_ca": True,
            "tls_insecure_skip_verify": True,
            "user": "valkyrie-valhalla",
            "password": "secret",
            "nkeys_seed_file": "/etc/nats/user.nk",
        }

    def test_redis_kwargs(self):
        settings = _make_settings()
        with patch.dict("os.environ", {"REDIS_URL": "redis://custom:6379"}):
            kwargs = _resolve_transport_kwargs(settings, "redis")
        assert kwargs == {"redis_url": "redis://custom:6379"}

    def test_redis_kwargs_default(self):
        settings = _make_settings()
        with patch.dict("os.environ", {}, clear=True):
            kwargs = _resolve_transport_kwargs(settings, "redis")
        assert kwargs == {"redis_url": "redis://localhost:6379"}

    def test_in_process_kwargs_empty(self):
        settings = _make_settings()
        kwargs = _resolve_transport_kwargs(settings, "in_process")
        assert kwargs == {}


class TestBuildMesh:
    """Verify _build_mesh creates the correct mesh adapter (legacy + list paths)."""

    def test_legacy_nng_returns_sleipnir_adapter(self):
        """Legacy nng adapter (default) → SleipnirMeshAdapter is constructed."""
        from ravn.cli.commands import _build_mesh

        settings = _make_settings()
        with (
            patch("niuu.mesh.transport_builder.build_transport", return_value=MagicMock()),
            patch("ravn.adapters.mesh.sleipnir_mesh.SleipnirMeshAdapter") as mock_cls,
            patch("niuu.mesh.cluster.read_cluster_pub_addresses", return_value=[]),
        ):
            _build_mesh(settings)
        mock_cls.assert_called_once()

    def test_legacy_rabbitmq_no_env_returns_none(self):
        """Legacy rabbitmq adapter with no AMQP_URL env → returns None (guard fires)."""
        from ravn.cli.commands import _build_mesh

        settings = _make_settings()
        settings.mesh.adapter = "rabbitmq"
        with patch.dict("os.environ", {}, clear=True):
            result = _build_mesh(settings)
        assert result is None

    def test_adapters_list_exercises_closure_body(self):
        """When mesh.adapters list is set, _sleipnir_tb closure body is exercised."""
        from ravn.cli.commands import _build_mesh

        settings = _make_settings()
        settings.mesh.adapters = [{"role": "pub_sub", "transport": "nng"}]
        settings.discovery.realm_id = "flock-a"

        captured: list = []

        def _fake_build_mesh(
            adapters,
            own_peer_id,
            rpc_timeout_s,
            discovery,
            sleipnir_transport_builder,
            environment_id,
        ):
            assert environment_id == "flock-a"
            captured.append(sleipnir_transport_builder)
            return MagicMock()

        with (
            patch("niuu.mesh.build_mesh_from_adapters_list", side_effect=_fake_build_mesh),
            patch("niuu.mesh.transport_builder.build_transport", return_value=MagicMock()),
            patch("niuu.mesh.cluster.read_cluster_pub_addresses", return_value=[]),
        ):
            _build_mesh(settings)
            assert captured, "sleipnir_transport_builder was not passed to helper"
            nng_result = captured[0]({"transport": "nng"})
        assert nng_result is not None

    def test_adapters_list_closure_rabbitmq_no_env_returns_none(self):
        """_sleipnir_tb closure returns None when rabbitmq env is missing."""
        from ravn.cli.commands import _build_mesh

        settings = _make_settings()
        settings.mesh.adapters = [{"role": "pub_sub", "transport": "rabbitmq"}]

        captured: list = []

        def _fake_build_mesh(
            adapters,
            own_peer_id,
            rpc_timeout_s,
            discovery,
            sleipnir_transport_builder,
            environment_id,
        ):
            captured.append(sleipnir_transport_builder)
            return MagicMock()

        with (
            patch("niuu.mesh.build_mesh_from_adapters_list", side_effect=_fake_build_mesh),
            patch("niuu.mesh.transport_builder.build_transport", return_value=MagicMock()),
            patch.dict("os.environ", {}, clear=True),
        ):
            _build_mesh(settings)
            assert captured
            result = captured[0]({"transport": "rabbitmq"})
        assert result is None


class TestBuildDiscovery:
    """Verify _build_discovery delegates to niuu.mesh.discovery_builder."""

    def test_returns_discovery_adapter(self):
        """_build_discovery calls build_discovery_adapters and returns its result."""
        from ravn.cli.commands import _build_discovery

        settings = _make_settings()
        mock_discovery = MagicMock()
        with (
            patch(
                "ravn.adapters.discovery._identity.load_or_create_peer_id",
                return_value="p1",
            ),
            patch(
                "ravn.adapters.discovery._identity.load_or_create_realm_key",
                return_value=b"key",
            ),
            patch(
                "ravn.adapters.discovery._identity.realm_id_from_key",
                return_value="realm-1",
            ),
            patch("importlib.metadata.version", return_value="0.0.0"),
            patch(
                "niuu.mesh.discovery_builder.build_discovery_adapters",
                return_value=mock_discovery,
            ),
        ):
            result = _build_discovery(settings)
        assert result is mock_discovery


class TestRunPeers:
    """Verify _run_peers calls build_discovery_adapters and exits early when None."""

    @pytest.mark.asyncio
    async def test_run_peers_no_discovery_returns_early(self):
        """_run_peers exits early (no exception) when discovery is None."""
        from ravn.cli.commands import _run_peers

        settings = _make_settings()
        with (
            patch(
                "ravn.adapters.discovery._identity.load_or_create_peer_id",
                return_value="p1",
            ),
            patch(
                "ravn.adapters.discovery._identity.load_or_create_realm_key",
                return_value=b"key",
            ),
            patch(
                "ravn.adapters.discovery._identity.realm_id_from_key",
                return_value="realm-1",
            ),
            patch("importlib.metadata.version", return_value="0.0.0"),
            patch(
                "niuu.mesh.discovery_builder.build_discovery_adapters",
                return_value=None,
            ),
        ):
            await _run_peers(settings, verbose=False, force_scan=False)

    def test_unknown_adapter_returns_empty(self):
        settings = _make_settings()
        kwargs = _resolve_transport_kwargs(settings, "unknown_thing")
        assert kwargs == {}


class TestBuildMeshTransport:
    """Verify _resolve_transport_kwargs + niuu.mesh.transport_builder.build_transport
    together produce the correct kwargs for each transport type.

    _build_sleipnir_transport was deleted in NIU-634; these tests cover
    _resolve_transport_kwargs, which is still used by _build_mesh.
    """

    def test_nng_kwargs_with_cluster(self):
        """NNG kwargs resolve peer addresses from cluster.yaml."""
        settings = _make_settings()
        with patch("niuu.mesh.cluster.read_cluster_pub_addresses", return_value=["tcp://p:7480"]):
            kwargs = _resolve_transport_kwargs(settings, "nng")
            assert kwargs["address"] == "ipc:///tmp/test.ipc"
            assert kwargs["service_id"] == "ravn:test-peer"
            assert kwargs["peer_addresses"]() == ["tcp://p:7480"]

    def test_nng_peer_addresses_are_re_read(self):
        """The provider is re-invoked so peers that join later are dialed."""
        settings = _make_settings()
        roster = ["tcp://p:7480"]
        with patch(
            "niuu.mesh.cluster.read_cluster_pub_addresses",
            side_effect=lambda _cfg: list(roster),
        ):
            provider = _resolve_transport_kwargs(settings, "nng")["peer_addresses"]

            assert provider() == ["tcp://p:7480"]

            roster.append("tcp://q:7482")

            assert provider() == ["tcp://p:7480", "tcp://q:7482"]

    def test_rabbitmq_returns_none_when_env_missing(self):
        settings = _make_settings()
        with patch.dict("os.environ", {}, clear=True):
            kwargs = _resolve_transport_kwargs(settings, "rabbitmq")
        assert kwargs == {}

    def test_rabbitmq_kwargs_with_env(self):
        settings = _make_settings()
        with patch.dict("os.environ", {"SLEIPNIR_AMQP_URL": "amqp://host"}):
            kwargs = _resolve_transport_kwargs(settings, "rabbitmq")
        assert kwargs == {"amqp_url": "amqp://host"}

    def test_in_process_kwargs_empty(self):
        settings = _make_settings()
        kwargs = _resolve_transport_kwargs(settings, "in_process")
        assert kwargs == {}
