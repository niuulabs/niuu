"""Tests for the shared Guild outbound TLS-pinning transport factory.

Two tiers:

- Unit tests mock ``fetch_leaf_certificate_der`` to a canned result and
  exercise the pure comparison/caching/policy logic in isolation.
- End-to-end tests run a real loopback TLS server (stdlib ``http.server`` +
  ``ssl``, certificates generated with ``cryptography``) and exercise
  ``fetch_leaf_certificate_der`` and the full pinned ``httpx.AsyncClient``
  unmocked, proving the returned ``SSLContext`` is actually usable — not just
  a mock that happens to satisfy the interface. No network beyond loopback.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import hashlib
import http.server
import os
import ssl
import tempfile
import threading
from collections.abc import Iterator
from datetime import UTC
from typing import Any

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from cryptography.x509.oid import NameOID

from niuu.adapters.outbound import guild_transport
from niuu.adapters.outbound.guild_transport import (
    GuildInsecureTransportError,
    GuildTLSPinMismatchError,
    GuildTransportUnreachableError,
    build_guild_httpx_client,
    resolve_guild_httpx_verify,
    resolve_guild_ssl_context,
    resolve_guild_ws_ssl,
)
from niuu.domain.models import InstanceKind, InstanceVisibility, RegisteredInstance
from niuu.domain.tls_fingerprint import normalize_tls_fingerprint
from niuu.domain.transport_security import (
    _DEFAULT_TRUSTED_PLAINTEXT_HOST_SUFFIXES,
    configure_trusted_plaintext_host_suffixes,
)


@pytest.fixture(autouse=True)
def _clear_pin_cache() -> Iterator[None]:
    """Keep the module-global pin/unreachable caches, and the trusted-plaintext
    host-suffix list, from leaking across tests."""
    guild_transport._PIN_CACHE.clear()
    guild_transport._UNREACHABLE_CACHE.clear()
    configure_trusted_plaintext_host_suffixes(_DEFAULT_TRUSTED_PLAINTEXT_HOST_SUFFIXES)
    yield
    guild_transport._PIN_CACHE.clear()
    guild_transport._UNREACHABLE_CACHE.clear()
    configure_trusted_plaintext_host_suffixes(_DEFAULT_TRUSTED_PLAINTEXT_HOST_SUFFIXES)


def _self_signed(
    *, common_name: str = "guild-test.local"
) -> tuple[EllipticCurvePrivateKey, x509.Certificate]:
    """A self-signed leaf (issuer == subject), CA:FALSE — a normal dev cert."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _private_ca() -> tuple[EllipticCurvePrivateKey, x509.Certificate]:
    """A self-signed root, CA:TRUE — used only to issue a leaf below, never
    installed as a trust anchor (the whole point of the leaf-pinning test)."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Guild Test Root CA")])
    now = datetime.datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _leaf_issued_by(
    ca_key: EllipticCurvePrivateKey, ca_cert: x509.Certificate, *, common_name: str = "127.0.0.1"
) -> tuple[EllipticCurvePrivateKey, x509.Certificate]:
    """A CA-issued leaf (issuer == the CA's subject), CA:FALSE — a real server cert."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    return key, cert


def _der(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(Encoding.DER)


def _fingerprint(cert: x509.Certificate) -> str:
    return hashlib.sha256(_der(cert)).hexdigest()


class _QuietHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib method name
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:  # keep test output quiet
        pass


@contextlib.contextmanager
def _loopback_tls_server(
    key: EllipticCurvePrivateKey,
    cert: x509.Certificate,
    *,
    chain_cert: x509.Certificate | None = None,
) -> Iterator[int]:
    """Start a real loopback HTTPS server presenting *cert*; yields its port.

    Standard-library only (``http.server`` + ``ssl``): the listening socket
    is wrapped in an ``SSLContext``, so every accepted connection is
    automatically a TLS connection presenting exactly this certificate. When
    *chain_cert* is given, the server presents *cert* followed by
    *chain_cert* — a server sending its full chain, the normal production
    setup — rather than the leaf alone; ``getpeercert(binary_form=True)``
    (and so the pin) always sees only the first certificate in that chain.
    """
    cert_pem = cert.public_bytes(Encoding.PEM)
    key_pem = key.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption())
    fd, cert_path = tempfile.mkstemp(suffix=".pem")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(cert_pem)
            if chain_cert is not None:
                handle.write(chain_cert.public_bytes(Encoding.PEM))
            handle.write(key_pem)
        server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_ctx.load_cert_chain(cert_path)
    finally:
        os.unlink(cert_path)

    httpd = http.server.HTTPServer(("127.0.0.1", 0), _QuietHandler)
    httpd.socket = server_ctx.wrap_socket(httpd.socket, server_side=True)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        httpd.shutdown()
        thread.join(timeout=2)
        httpd.server_close()


_LEAF_KEY, _LEAF_CERT = _self_signed()
_LEAF_CERT_DER = _der(_LEAF_CERT)
_LEAF_FINGERPRINT = _fingerprint(_LEAF_CERT)


def _instance(*, base_url: str = "https://node.test", config: dict[str, Any] | None = None):
    now = datetime.datetime.now(UTC)
    return RegisteredInstance(
        id="node",
        kind=InstanceKind.VOLUNDR,
        slug="node",
        name="Node",
        base_url=base_url,
        visibility=InstanceVisibility.SYSTEM,
        owner_id=None,
        tenant_id=None,
        enabled=True,
        is_default=False,
        config=config or {},
        created_at=now,
        updated_at=now,
        tags=[],
    )


class TestConfigureDefaultTimeouts:
    def test_updates_the_process_wide_ceiling(self) -> None:
        original = guild_transport.default_connect_timeout_ceiling_seconds()
        try:
            guild_transport.configure_default_timeouts(connect_timeout_ceiling_seconds=1.5)
            assert guild_transport.default_connect_timeout_ceiling_seconds() == 1.5
        finally:
            guild_transport.configure_default_timeouts(connect_timeout_ceiling_seconds=original)

    def test_rejects_a_non_positive_value(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            guild_transport.configure_default_timeouts(connect_timeout_ceiling_seconds=0)


class TestNormalizeTlsFingerprint:
    def test_accepts_colon_separated_hex(self) -> None:
        colonized = ":".join(_LEAF_FINGERPRINT[i : i + 2] for i in range(0, 64, 2))
        assert normalize_tls_fingerprint(colonized.upper()) == _LEAF_FINGERPRINT

    def test_accepts_bare_lowercase_hex(self) -> None:
        assert normalize_tls_fingerprint(_LEAF_FINGERPRINT) == _LEAF_FINGERPRINT

    def test_rejects_wrong_length(self) -> None:
        with pytest.raises(ValueError, match="64 hex characters"):
            normalize_tls_fingerprint("ab" * 10)

    def test_rejects_non_hex_characters(self) -> None:
        with pytest.raises(ValueError, match="sha256 hex digest"):
            normalize_tls_fingerprint("zz" * 32)


class TestCallTimeTransportPolicy:
    """Enforced on every call, regardless of write-time validation — a row
    that predates that validation (or an update that never touched its
    transport fields) must still be stopped here."""

    async def test_rejects_plain_http_without_allow_plaintext(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _unexpected_fetch(*_a: Any, **_k: Any) -> bytes:
            raise AssertionError("must not fetch a certificate for a call refused on policy")

        monkeypatch.setattr(guild_transport, "fetch_leaf_certificate_der", _unexpected_fetch)
        instance = _instance(base_url="http://node.test")

        with pytest.raises(GuildInsecureTransportError, match="allow_plaintext"):
            await resolve_guild_ssl_context(instance, dial_url="http://node.test", timeout=1.0)

    async def test_allows_plain_http_with_allow_plaintext(self) -> None:
        instance = _instance(base_url="http://node.test", config={"allow_plaintext": True})
        assert (
            await resolve_guild_ssl_context(instance, dial_url="http://node.test", timeout=1.0)
            is None
        )

    async def test_exempts_localhost_without_allow_plaintext(self) -> None:
        instance = _instance(base_url="http://127.0.0.1:9")
        assert (
            await resolve_guild_ssl_context(instance, dial_url="http://127.0.0.1:9", timeout=1.0)
            is None
        )

    async def test_exempts_an_in_cluster_svc_cluster_local_host_by_default(self) -> None:
        """The same choke point insecure_transport_reason feeds — a
        ymir-style seed's outbound calls must not be refused at call time
        either, matching the write-time seed check in test_instance_service.py."""
        instance = _instance(base_url="http://niuu-volundr.volundr.svc.cluster.local")
        dial_url = "http://niuu-volundr.volundr.svc.cluster.local"
        assert await resolve_guild_ssl_context(instance, dial_url=dial_url, timeout=1.0) is None

    async def test_the_in_cluster_exemption_is_gone_once_the_suffix_list_is_emptied(self) -> None:
        configure_trusted_plaintext_host_suffixes([])
        instance = _instance(base_url="http://niuu-volundr.volundr.svc.cluster.local")
        dial_url = "http://niuu-volundr.volundr.svc.cluster.local"

        with pytest.raises(GuildInsecureTransportError, match="allow_plaintext"):
            await resolve_guild_ssl_context(instance, dial_url=dial_url, timeout=1.0)

    async def test_tls_pinning_still_requires_https_on_a_trusted_plaintext_suffix(self) -> None:
        """The suffix exemption only ever relaxes the https-unless-
        allow_plaintext policy — config.tls_fingerprint keeps requiring
        https:// regardless of hostname, in-cluster or not."""
        instance = _instance(
            base_url="http://niuu-volundr.volundr.svc.cluster.local",
            config={"tls_fingerprint": "ab" * 32},
        )
        dial_url = "http://niuu-volundr.volundr.svc.cluster.local"

        with pytest.raises(GuildTLSPinMismatchError, match="https"):
            await resolve_guild_ssl_context(instance, dial_url=dial_url, timeout=1.0)

    async def test_exempts_embedded_scheme_regardless_of_config_transport(self) -> None:
        """The exemption is keyed on the dial URL's own scheme, not the
        user-editable config.transport flag — see the should-fix this locks
        in: a caller dialling base_url directly must get the same answer."""
        instance = _instance(base_url="embedded://local-forge", config={})
        assert (
            await resolve_guild_ssl_context(
                instance, dial_url="embedded://local-forge", timeout=1.0
            )
            is None
        )

    async def test_rejects_a_non_boolean_allow_plaintext(self) -> None:
        instance = _instance(base_url="http://node.test", config={"allow_plaintext": "true"})

        with pytest.raises(GuildInsecureTransportError, match="boolean"):
            await resolve_guild_ssl_context(instance, dial_url="http://node.test", timeout=1.0)

    async def test_enforces_policy_against_dial_url_not_instance_base_url(self) -> None:
        """The URL actually dialled governs — not instance.base_url. This is
        exactly the split-service (ravn_base_url) case: base_url may be
        https while the URL a caller is really connecting to is a plain,
        unauthorized http:// address."""
        instance = _instance(base_url="https://node.test")

        with pytest.raises(GuildInsecureTransportError):
            await resolve_guild_ssl_context(instance, dial_url="http://ravn.node.test", timeout=1.0)


class TestResolveGuildSslContext:
    async def test_returns_none_without_a_pinned_fingerprint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _unexpected_fetch(*_a: Any, **_k: Any) -> bytes:
            raise AssertionError("must not fetch a certificate when no fingerprint is pinned")

        monkeypatch.setattr(guild_transport, "fetch_leaf_certificate_der", _unexpected_fetch)
        instance = _instance()

        assert (
            await resolve_guild_ssl_context(instance, dial_url=instance.base_url, timeout=1.0)
            is None
        )
        assert (
            await resolve_guild_httpx_verify(instance, dial_url=instance.base_url, timeout=1.0)
            is True
        )
        assert await resolve_guild_ws_ssl(instance, dial_url=instance.base_url, timeout=1.0) is None

    async def test_requires_https_for_a_pinned_fingerprint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _unexpected_fetch(*_a: Any, **_k: Any) -> bytes:
            raise AssertionError("must not fetch a certificate over plain http")

        monkeypatch.setattr(guild_transport, "fetch_leaf_certificate_der", _unexpected_fetch)
        instance = _instance(
            base_url="http://node.test",
            config={"allow_plaintext": True, "tls_fingerprint": _LEAF_FINGERPRINT},
        )

        with pytest.raises(GuildTLSPinMismatchError, match="https"):
            await resolve_guild_ssl_context(instance, dial_url="http://node.test", timeout=1.0)

    async def test_rejects_a_malformed_fingerprint(self) -> None:
        instance = _instance(config={"tls_fingerprint": "not-a-fingerprint"})

        with pytest.raises(GuildTLSPinMismatchError, match="sha256 hex digest"):
            await resolve_guild_ssl_context(instance, dial_url=instance.base_url, timeout=1.0)

    async def test_trusts_a_matching_certificate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[tuple[str, int]] = []

        def _fake_fetch(host: str, port: int, *, timeout: float) -> bytes:
            calls.append((host, port))
            assert timeout == 3.0
            return _LEAF_CERT_DER

        monkeypatch.setattr(guild_transport, "fetch_leaf_certificate_der", _fake_fetch)
        instance = _instance(
            base_url="https://node.test:8443", config={"tls_fingerprint": _LEAF_FINGERPRINT}
        )

        context = await resolve_guild_ssl_context(instance, dial_url=instance.base_url, timeout=3.0)

        assert calls == [("node.test", 8443)]
        assert isinstance(context, ssl.SSLContext)
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is False
        assert context.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN

        verify = await resolve_guild_httpx_verify(instance, dial_url=instance.base_url, timeout=3.0)
        assert isinstance(verify, ssl.SSLContext)
        ws_ssl = await resolve_guild_ws_ssl(instance, dial_url=instance.base_url, timeout=3.0)
        assert isinstance(ws_ssl, ssl.SSLContext)

    async def test_fails_closed_on_a_mismatched_certificate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        other_fingerprint = hashlib.sha256(b"a different certificate entirely").hexdigest()
        monkeypatch.setattr(
            guild_transport, "fetch_leaf_certificate_der", lambda *a, **k: _LEAF_CERT_DER
        )
        instance = _instance(config={"tls_fingerprint": other_fingerprint})

        with pytest.raises(GuildTLSPinMismatchError, match="does not match"):
            await resolve_guild_ssl_context(instance, dial_url=instance.base_url, timeout=1.0)

    async def test_wraps_a_connection_failure_as_unreachable_not_a_mismatch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A connection failure is "we could not check", distinct from
        GuildTLSPinMismatchError's "we checked and it does not match" — see
        GuildTransportUnreachableError."""

        def _refused(*_a: Any, **_k: Any) -> bytes:
            raise ConnectionRefusedError("refused")

        monkeypatch.setattr(guild_transport, "fetch_leaf_certificate_der", _refused)
        instance = _instance(config={"tls_fingerprint": _LEAF_FINGERPRINT})

        with pytest.raises(GuildTransportUnreachableError, match="unreachable"):
            await resolve_guild_ssl_context(instance, dial_url=instance.base_url, timeout=1.0)

    async def test_caches_a_verified_pin_by_host_port_fingerprint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = 0

        def _counting_fetch(*_a: Any, **_k: Any) -> bytes:
            nonlocal calls
            calls += 1
            return _LEAF_CERT_DER

        monkeypatch.setattr(guild_transport, "fetch_leaf_certificate_der", _counting_fetch)
        instance = _instance(config={"tls_fingerprint": _LEAF_FINGERPRINT})

        first = await resolve_guild_ssl_context(instance, dial_url=instance.base_url, timeout=1.0)
        second = await resolve_guild_ssl_context(instance, dial_url=instance.base_url, timeout=1.0)

        assert calls == 1
        assert first is second

    async def test_cache_key_includes_fingerprint_so_a_changed_pin_refetches(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = 0

        def _counting_fetch(*_a: Any, **_k: Any) -> bytes:
            nonlocal calls
            calls += 1
            return _LEAF_CERT_DER

        monkeypatch.setattr(guild_transport, "fetch_leaf_certificate_der", _counting_fetch)
        instance = _instance(config={"tls_fingerprint": _LEAF_FINGERPRINT})
        await resolve_guild_ssl_context(instance, dial_url=instance.base_url, timeout=1.0)

        other_fingerprint = hashlib.sha256(b"a different certificate entirely").hexdigest()
        # A different pinned fingerprint for the same host/port is a
        # different cache key, so it must re-fetch (and here, mismatch).
        other_instance = _instance(config={"tls_fingerprint": other_fingerprint})
        with pytest.raises(GuildTLSPinMismatchError):
            await resolve_guild_ssl_context(other_instance, dial_url=instance.base_url, timeout=1.0)

        assert calls == 2

    async def test_ttl_expiry_forces_a_refetch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = 0

        def _counting_fetch(*_a: Any, **_k: Any) -> bytes:
            nonlocal calls
            calls += 1
            return _LEAF_CERT_DER

        monkeypatch.setattr(guild_transport, "fetch_leaf_certificate_der", _counting_fetch)
        instance = _instance(config={"tls_fingerprint": _LEAF_FINGERPRINT})

        fake_now = 1_000.0
        monkeypatch.setattr(guild_transport.time, "monotonic", lambda: fake_now)
        await resolve_guild_ssl_context(instance, dial_url=instance.base_url, timeout=1.0)
        assert calls == 1

        # Still within the TTL: served from cache, no refetch.
        fake_now += guild_transport._PIN_CACHE_TTL_SECONDS - 1
        await resolve_guild_ssl_context(instance, dial_url=instance.base_url, timeout=1.0)
        assert calls == 1

        # Past the TTL: refetches and re-verifies.
        fake_now += 2
        await resolve_guild_ssl_context(instance, dial_url=instance.base_url, timeout=1.0)
        assert calls == 2

    async def test_a_rotated_certificate_is_refused_once_the_cache_expires(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The cache must not keep trusting a stale verification forever: once
        it expires, a certificate that no longer matches the pin (rotated
        without updating config.tls_fingerprint) is refused, not silently
        served from the old, now-stale cached context."""
        results = iter([_LEAF_CERT_DER, b"a rotated certificate with different bytes"])

        def _rotating_fetch(*_a: Any, **_k: Any) -> bytes:
            return next(results)

        monkeypatch.setattr(guild_transport, "fetch_leaf_certificate_der", _rotating_fetch)
        instance = _instance(config={"tls_fingerprint": _LEAF_FINGERPRINT})

        fake_now = 2_000.0
        monkeypatch.setattr(guild_transport.time, "monotonic", lambda: fake_now)
        context = await resolve_guild_ssl_context(instance, dial_url=instance.base_url, timeout=1.0)
        assert context is not None

        fake_now += guild_transport._PIN_CACHE_TTL_SECONDS + 1
        with pytest.raises(GuildTLSPinMismatchError, match="does not match"):
            await resolve_guild_ssl_context(instance, dial_url=instance.base_url, timeout=1.0)


class TestUnreachableCache:
    """The short negative cache for a failed certificate fetch."""

    async def test_a_failure_is_cached_and_expires(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = 0

        def _refused(*_a: Any, **_k: Any) -> bytes:
            nonlocal calls
            calls += 1
            raise ConnectionRefusedError("refused")

        monkeypatch.setattr(guild_transport, "fetch_leaf_certificate_der", _refused)
        instance = _instance(config={"tls_fingerprint": _LEAF_FINGERPRINT})

        fake_now = 3_000.0
        monkeypatch.setattr(guild_transport.time, "monotonic", lambda: fake_now)
        with pytest.raises(GuildTransportUnreachableError):
            await resolve_guild_ssl_context(instance, dial_url=instance.base_url, timeout=1.0)
        assert calls == 1

        # Still within the negative-cache TTL: no new attempt at all.
        fake_now += guild_transport._UNREACHABLE_CACHE_TTL_SECONDS - 1
        with pytest.raises(GuildTransportUnreachableError):
            await resolve_guild_ssl_context(instance, dial_url=instance.base_url, timeout=1.0)
        assert calls == 1

        # Past the negative-cache TTL: retries the network.
        fake_now += 2
        with pytest.raises(GuildTransportUnreachableError):
            await resolve_guild_ssl_context(instance, dial_url=instance.base_url, timeout=1.0)
        assert calls == 2

    async def test_a_success_after_a_cached_failure_is_not_blocked_forever(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        results = iter([ConnectionRefusedError("refused"), _LEAF_CERT_DER])

        def _flaky_fetch(*_a: Any, **_k: Any) -> bytes:
            result = next(results)
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr(guild_transport, "fetch_leaf_certificate_der", _flaky_fetch)
        instance = _instance(config={"tls_fingerprint": _LEAF_FINGERPRINT})

        fake_now = 4_000.0
        monkeypatch.setattr(guild_transport.time, "monotonic", lambda: fake_now)
        with pytest.raises(GuildTransportUnreachableError):
            await resolve_guild_ssl_context(instance, dial_url=instance.base_url, timeout=1.0)

        fake_now += guild_transport._UNREACHABLE_CACHE_TTL_SECONDS + 1
        context = await resolve_guild_ssl_context(instance, dial_url=instance.base_url, timeout=1.0)
        assert context is not None


class TestBuildGuildHttpxClient:
    async def test_defaults_to_platform_trust(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _unexpected_fetch(*_a: Any, **_k: Any) -> bytes:
            raise AssertionError("must not fetch a certificate when no fingerprint is pinned")

        monkeypatch.setattr(guild_transport, "fetch_leaf_certificate_der", _unexpected_fetch)
        instance = _instance()

        client = await build_guild_httpx_client(
            instance, dial_url=instance.base_url, timeout_seconds=10.0
        )
        try:
            assert isinstance(client, httpx.AsyncClient)
        finally:
            await client.aclose()

    async def test_fails_closed_before_any_request_on_pin_mismatch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            guild_transport, "fetch_leaf_certificate_der", lambda *a, **k: _LEAF_CERT_DER
        )
        other_fingerprint = hashlib.sha256(b"not the pinned certificate").hexdigest()
        instance = _instance(config={"tls_fingerprint": other_fingerprint})

        with pytest.raises(GuildTLSPinMismatchError):
            await build_guild_httpx_client(
                instance, dial_url=instance.base_url, timeout_seconds=10.0
            )

    async def test_fails_closed_on_an_insecure_dial_url_before_any_request(self) -> None:
        instance = _instance(base_url="http://node.test")

        with pytest.raises(GuildInsecureTransportError):
            await build_guild_httpx_client(
                instance, dial_url="http://node.test", timeout_seconds=10.0
            )

    async def test_a_pinned_client_disables_trust_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            guild_transport, "fetch_leaf_certificate_der", lambda *a, **k: _LEAF_CERT_DER
        )
        instance = _instance(config={"tls_fingerprint": _LEAF_FINGERPRINT})

        client = await build_guild_httpx_client(
            instance, dial_url=instance.base_url, timeout_seconds=10.0
        )
        try:
            assert client.trust_env is False
        finally:
            await client.aclose()

    async def test_an_unpinned_client_keeps_the_default_trust_env(self) -> None:
        instance = _instance()

        client = await build_guild_httpx_client(
            instance, dial_url=instance.base_url, timeout_seconds=10.0
        )
        try:
            assert client.trust_env is True
        finally:
            await client.aclose()

    async def test_a_pinned_client_disables_redirects_even_when_requested(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            guild_transport, "fetch_leaf_certificate_der", lambda *a, **k: _LEAF_CERT_DER
        )
        instance = _instance(config={"tls_fingerprint": _LEAF_FINGERPRINT})

        client = await build_guild_httpx_client(
            instance, dial_url=instance.base_url, timeout_seconds=10.0, follow_redirects=True
        )
        try:
            assert client.follow_redirects is False
        finally:
            await client.aclose()

    async def test_an_unpinned_client_honours_the_requested_follow_redirects(self) -> None:
        instance = _instance()

        client = await build_guild_httpx_client(
            instance, dial_url=instance.base_url, timeout_seconds=10.0, follow_redirects=True
        )
        try:
            assert client.follow_redirects is True
        finally:
            await client.aclose()

    async def test_rejects_a_custom_transport_for_a_pinned_instance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """httpx ignores verify= once a custom transport is set — combining
        the two would silently build a client whose pin does nothing."""
        monkeypatch.setattr(
            guild_transport, "fetch_leaf_certificate_der", lambda *a, **k: _LEAF_CERT_DER
        )
        instance = _instance(config={"tls_fingerprint": _LEAF_FINGERPRINT})

        with pytest.raises(ValueError, match="transport"):
            await build_guild_httpx_client(
                instance,
                dial_url=instance.base_url,
                timeout_seconds=10.0,
                transport=httpx.MockTransport(lambda request: httpx.Response(200)),
            )

    async def test_allows_a_custom_transport_for_an_unpinned_instance(self) -> None:
        instance = _instance()

        client = await build_guild_httpx_client(
            instance,
            dial_url=instance.base_url,
            timeout_seconds=10.0,
            transport=httpx.MockTransport(lambda request: httpx.Response(200)),
        )
        try:
            assert isinstance(client, httpx.AsyncClient)
        finally:
            await client.aclose()


class TestRealLoopbackServer:
    """No monkeypatch anywhere in this class: fetch_leaf_certificate_der runs
    for real against a genuine loopback TLS server."""

    async def test_fetch_leaf_certificate_der_returns_the_real_presented_certificate(self) -> None:
        with _loopback_tls_server(_LEAF_KEY, _LEAF_CERT) as port:
            der = await asyncio.to_thread(
                guild_transport.fetch_leaf_certificate_der, "127.0.0.1", port, timeout=3.0
            )
        assert hashlib.sha256(der).hexdigest() == _LEAF_FINGERPRINT

    async def test_a_matching_pin_completes_a_real_get(self) -> None:
        with _loopback_tls_server(_LEAF_KEY, _LEAF_CERT) as port:
            instance = _instance(
                base_url=f"https://127.0.0.1:{port}",
                config={"tls_fingerprint": _LEAF_FINGERPRINT},
            )
            client = await build_guild_httpx_client(
                instance, dial_url=instance.base_url, timeout_seconds=5.0
            )
            async with client:
                response = await client.get(f"https://127.0.0.1:{port}/")
        assert response.status_code == 200
        assert response.text == "ok"

    async def test_a_different_certificate_on_the_real_connection_is_rejected(self) -> None:
        """Pin fingerprint A but dial a server presenting certificate B: the
        pin-fetch itself (against the server actually being dialled) sees B,
        computes B's hash, and refuses before any request is sent."""
        other_key, other_cert = _self_signed(common_name="not-the-pinned-cert")
        with _loopback_tls_server(other_key, other_cert) as port:
            instance = _instance(
                base_url=f"https://127.0.0.1:{port}",
                config={"tls_fingerprint": _LEAF_FINGERPRINT},
            )
            with pytest.raises(GuildTLSPinMismatchError, match="does not match"):
                await build_guild_httpx_client(
                    instance, dial_url=instance.base_url, timeout_seconds=5.0
                )

    async def test_pins_a_ca_issued_leaf_via_verify_x509_partial_chain(self) -> None:
        """The server presents only its leaf (the pinned-leaf scenario), and
        that leaf was issued by a private CA never installed as a trust
        anchor. Without VERIFY_X509_PARTIAL_CHAIN this fails chain
        verification even though the leaf itself is exactly what we trust —
        see test_verify_x509_partial_chain_is_required_for_a_ca_issued_leaf
        for the negative control proving the flag is load-bearing."""
        ca_key, ca_cert = _private_ca()
        leaf_key, leaf_cert = _leaf_issued_by(ca_key, ca_cert)
        leaf_fingerprint = _fingerprint(leaf_cert)
        with _loopback_tls_server(leaf_key, leaf_cert) as port:
            instance = _instance(
                base_url=f"https://127.0.0.1:{port}",
                config={"tls_fingerprint": leaf_fingerprint},
            )
            client = await build_guild_httpx_client(
                instance, dial_url=instance.base_url, timeout_seconds=5.0
            )
            async with client:
                response = await client.get(f"https://127.0.0.1:{port}/")
        assert response.status_code == 200

    async def test_verify_x509_partial_chain_is_required_for_a_ca_issued_leaf(self) -> None:
        """Negative control: without VERIFY_X509_PARTIAL_CHAIN, pinning a
        CA-issued leaf as the sole trust anchor fails to verify — proving
        the flag in resolve_guild_ssl_context is load-bearing, not
        decorative."""
        ca_key, ca_cert = _private_ca()
        leaf_key, leaf_cert = _leaf_issued_by(ca_key, ca_cert)
        with _loopback_tls_server(leaf_key, leaf_cert) as port:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_REQUIRED
            context.load_verify_locations(
                cadata=leaf_cert.public_bytes(Encoding.PEM).decode("ascii")
            )
            # Deliberately no VERIFY_X509_PARTIAL_CHAIN here.
            async with httpx.AsyncClient(verify=context) as client:
                with pytest.raises(httpx.ConnectError):
                    await client.get(f"https://127.0.0.1:{port}/")

    async def test_a_server_sending_the_full_chain_also_works(self) -> None:
        """A server presenting its leaf plus the issuing CA (the normal
        production setup, not just the bare leaf) still pins correctly: the
        pin only ever checks the first certificate in the chain."""
        ca_key, ca_cert = _private_ca()
        leaf_key, leaf_cert = _leaf_issued_by(ca_key, ca_cert)
        leaf_fingerprint = _fingerprint(leaf_cert)
        with _loopback_tls_server(leaf_key, leaf_cert, chain_cert=ca_cert) as port:
            instance = _instance(
                base_url=f"https://127.0.0.1:{port}",
                config={"tls_fingerprint": leaf_fingerprint},
            )
            client = await build_guild_httpx_client(
                instance, dial_url=instance.base_url, timeout_seconds=5.0
            )
            async with client:
                response = await client.get(f"https://127.0.0.1:{port}/")
        assert response.status_code == 200
