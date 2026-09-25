"""Shared outbound transport for calls this process makes to another Guild instance.

Every Guild remote call — Forge/Ravn aggregate REST requests, the Forge session
SSE stream, the reachability probe, both WebSocket proxy legs (resident/session
chat and Forge replay), and the Observatory/agent-directory HTTP clients —
resolves its transport trust through this one module: it is the single choke
point that enforces the LAN-transport policy (https:// unless
``config.allow_plaintext``) and an optional per-instance
``config.tls_fingerprint`` pin against whichever URL a caller actually dials,
instead of either being reimplemented (and possibly forgotten) at each call
site or trusted only once at registration time.

Callers must always pass the *dial_url* they are actually about to connect
to — an instance's ``base_url`` for most calls, but a split-service target's
``config.ravn_base_url`` for the Ravn aggregate/owner-probe legs (see
``rest_ravn._ravn_base_url``). Passing the wrong URL here would check and pin
the wrong host's certificate.

Embedded/in-process transports never reach this module: callers already
branch on ``config.transport == "embedded"`` before calling in (an embedded
instance's own ``base_url`` uses the ``embedded://`` scheme, which this
module's policy also exempts, for a caller that dials it directly).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import hashlib
import socket
import ssl
import time
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from niuu.domain.tls_fingerprint import normalize_tls_fingerprint
from niuu.domain.transport_security import insecure_transport_reason, normalize_allow_plaintext


class GuildTransportTarget(Protocol):
    """The two fields this module actually needs from a Guild instance.

    Structural, not ``niuu.domain.models.RegisteredInstance`` itself: Ting's
    ``VolundrHTTPAdapter`` (``src/ting/adapters/volundr_http.py``) routes its
    own per-instance calls through this same module without needing to
    fabricate a full ``RegisteredInstance`` (id, kind, visibility, owner_id,
    timestamps, ...) it has no use for and no values for.
    """

    name: str
    config: dict[str, Any]


# The default TLS port when an instance's https:// base_url omits one.
_DEFAULT_TLS_PORT = 443
# Ceiling for the connect leg of an outbound Guild call when the caller does
# not split connect/read timeouts itself; also used as the timeout for the
# bare handshake that fetches a pinned instance's live certificate. A module
# default, overridden once at process startup by the composition root via
# configure_default_timeouts() from Settings.guild_transport_connect_timeout_seconds
# — every call site shares this one value rather than each needing its own
# config plumbing (see the module docstring: this module is the one choke
# point for outbound Guild transport policy).
_connect_timeout_ceiling_seconds = 5.0
# How long a successfully verified (host, port, fingerprint) pin is trusted
# before the next call re-fetches and re-verifies the live certificate. A
# process-wide ceiling, not yet an operator-configurable setting — bounds how
# stale a cached verification can be without paying a fresh TLS handshake on
# every single outbound call.
_PIN_CACHE_TTL_SECONDS = 300.0
# How long a failed certificate fetch (unreachable host, timed-out handshake)
# is remembered before the next call retries the network. Short and
# deliberately much shorter than _PIN_CACHE_TTL_SECONDS: an offline instance
# coming back should be noticed quickly, but a caller that retries on a tight
# loop (the merged SSE stream retries every forge_stream_retry_seconds, by
# default 5s) must not spawn a fresh blocking DNS/connect attempt — and a
# fresh worker thread that may itself never return — on every single retry.
_UNREACHABLE_CACHE_TTL_SECONDS = 10.0
# A small, dedicated pool for the blocking certificate-fetch handshake,
# deliberately separate from asyncio's shared default executor: a stalled DNS
# lookup is not bounded by the socket-level timeout (see
# fetch_leaf_certificate_der's docstring), so the worker thread can outlive
# the asyncio.wait_for() that gave up on it. Isolating that leak to a small
# pool of its own keeps a stuck lookup from starving unrelated blocking work
# elsewhere in the process that shares the default executor.
_PIN_FETCH_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="guild-tls-pin-fetch"
)


def configure_default_timeouts(*, connect_timeout_ceiling_seconds: float) -> None:
    """Set the process-wide connect/pin-handshake timeout ceiling.

    Called once by the composition root (``guild/app.py``) from
    ``Settings.guild_transport_connect_timeout_seconds`` — see the module
    docstring for why this is a module-level default rather than a
    per-call-site parameter.
    """
    global _connect_timeout_ceiling_seconds
    if connect_timeout_ceiling_seconds <= 0:
        raise ValueError("connect_timeout_ceiling_seconds must be positive")
    _connect_timeout_ceiling_seconds = connect_timeout_ceiling_seconds


def default_connect_timeout_ceiling_seconds() -> float:
    """The current process-wide connect/pin-handshake timeout ceiling."""
    return _connect_timeout_ceiling_seconds


class GuildTransportError(RuntimeError):
    """Base for a Guild outbound call refused before any request was sent.

    Callers catch this one type to map any transport-policy or TLS-pin
    refusal to the same outcome (502 for an aggregate REST call, a closed
    WebSocket with the reason logged) without needing to enumerate every
    subclass.
    """


class GuildInsecureTransportError(GuildTransportError):
    """Raised when a dialled URL violates the https-unless-allow_plaintext policy."""


class GuildTLSPinMismatchError(GuildTransportError):
    """Raised when a remote instance's live certificate does not match its pin."""


class GuildTransportUnreachableError(GuildTransportError):
    """Raised when the pin-verification handshake to a dialled URL could not complete.

    Distinct from ``GuildTLSPinMismatchError``: this is "we could not check",
    not "we checked and it does not match". A caller iterating candidate
    instances (e.g. the Ravn owner-probe loop) should treat this exactly like
    an ordinary ``httpx.HTTPError`` — skip to the next candidate — while a
    genuine mismatch is a hard, unconditional refusal.
    """


# (host, port, expected_fingerprint) -> (context, expires_at_monotonic)
_PIN_CACHE: dict[tuple[str, int, str], tuple[ssl.SSLContext, float]] = {}
# (host, port) -> (error message, expires_at_monotonic) for a recent failed fetch.
_UNREACHABLE_CACHE: dict[tuple[str, int], tuple[str, float]] = {}


def _require_call_time_transport_security(dial_url: str, instance: GuildTransportTarget) -> None:
    """Enforce the https-unless-allow_plaintext policy at the moment of dialling.

    The write-time check in ``services.instances`` is a fail-fast convenience
    only — this is the real enforcement boundary. A row written before that
    validation existed (or before ``ravn_base_url`` was added, or that was
    only ever touched by an update that didn't change its transport fields)
    is still stopped here, on every call, never silently sent a bearer token
    over plaintext.
    """
    try:
        allow_plaintext = normalize_allow_plaintext(instance.config)
    except ValueError as exc:
        raise GuildInsecureTransportError(f"{instance.name}: {exc}") from exc
    reason = insecure_transport_reason(dial_url, allow_plaintext=allow_plaintext)
    if reason:
        raise GuildInsecureTransportError(f"{instance.name}: {reason}")


def fetch_leaf_certificate_der(host: str, port: int, *, timeout: float) -> bytes:
    """Fetch the DER-encoded leaf certificate a host presents over TLS.

    A bare handshake with no application data — connect, read the peer
    certificate, close — so the caller can decide whether to trust it before
    any real request (carrying the caller's bearer token) is sent on a
    connection that will actually be used. A raw socket connect, deliberately
    not routed through any ``HTTPS_PROXY``/``HTTP_PROXY`` env var (unlike the
    httpx client this feeds into, which is built with ``trust_env=False`` for
    the same reason): this fetch must see the certificate the instance itself
    presents, not whatever a proxy in between would present.

    A completed client-side TLS handshake here always yields a peer
    certificate — ``context.wrap_socket(..., server_hostname=host)`` performs
    a full client handshake, and every cipher suite modern OpenSSL negotiates
    is certificate-based (anonymous/PSK-only suites are disabled by default
    and not offered), so there is no successful-handshake path that reaches
    the caller with no certificate to check.

    Blocking; callers run it off the event loop via ``_fetch_leaf_certificate_der_cached``
    (a dedicated thread pool, not the shared default executor — see that
    function and ``_PIN_FETCH_EXECUTOR``) and bound the whole call (including
    DNS resolution, which the ``timeout=`` kwarg to ``socket.create_connection``
    does not itself cover) with ``asyncio.wait_for``.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as raw_sock:
        with context.wrap_socket(raw_sock, server_hostname=host) as tls_sock:
            der = tls_sock.getpeercert(binary_form=True)
    assert der is not None  # see docstring: unreachable after a completed handshake
    return der


async def _fetch_leaf_certificate_der_cached(host: str, port: int, *, timeout: float) -> bytes:
    """Fetch a leaf certificate, short-circuiting on a recently failed fetch.

    Runs the blocking handshake on ``_PIN_FETCH_EXECUTOR`` (never the shared
    default executor) and bounds the whole call with ``asyncio.wait_for``. A
    failure (unreachable host, timed-out handshake) is cached for
    ``_UNREACHABLE_CACHE_TTL_SECONDS`` and raised as
    ``GuildTransportUnreachableError`` — distinct from a pin mismatch, and
    never silently retried on a tight loop.
    """
    cache_key = (host, port)
    cached = _UNREACHABLE_CACHE.get(cache_key)
    if cached is not None:
        message, expires_at = cached
        if expires_at > time.monotonic():
            raise GuildTransportUnreachableError(message)
        del _UNREACHABLE_CACHE[cache_key]
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(
                _PIN_FETCH_EXECUTOR,
                functools.partial(fetch_leaf_certificate_der, host, port, timeout=timeout),
            ),
            timeout=timeout,
        )
    except (OSError, TimeoutError) as exc:
        message = (
            f"{host}:{port} was unreachable for the certificate-pin handshake "
            f"(timeout={timeout}s): {exc or exc.__class__.__name__}"
        )
        _UNREACHABLE_CACHE[cache_key] = (
            message,
            time.monotonic() + _UNREACHABLE_CACHE_TTL_SECONDS,
        )
        raise GuildTransportUnreachableError(message) from exc


def _cached_pinned_context(cache_key: tuple[str, int, str]) -> ssl.SSLContext | None:
    cached = _PIN_CACHE.get(cache_key)
    if cached is None:
        return None
    context, expires_at = cached
    if expires_at <= time.monotonic():
        del _PIN_CACHE[cache_key]
        return None
    return context


async def resolve_guild_ssl_context(
    instance: GuildTransportTarget, *, dial_url: str, timeout: float
) -> ssl.SSLContext | None:
    """Resolve the TLS trust for an outbound call to *dial_url* on *instance*.

    Always enforces the https-unless-allow_plaintext policy against
    *dial_url* first (see ``_require_call_time_transport_security``), raising
    ``GuildInsecureTransportError`` on a violation.

    Returns ``None`` when the instance has no ``config.tls_fingerprint`` —
    callers should fall back to the platform's default CA trust (``verify=
    True`` for httpx, the default ``ssl`` context for ``websockets.connect``).

    When a fingerprint is configured, fetches *dial_url*'s current live leaf
    certificate (or reuses a still-fresh cached verification for the same
    host/port/fingerprint) and returns an ``SSLContext`` that trusts only
    that exact certificate — self-signed or CA-issued, since
    ``VERIFY_X509_PARTIAL_CHAIN`` lets the leaf itself serve as the trust
    anchor without needing its issuing CA in the trust store too. Anything
    else, including a mismatch or an unreachable host, raises
    ``GuildTLSPinMismatchError`` rather than silently falling back to default
    verification (see ``.claude/rules/no-fallbacks.md``).
    """
    _require_call_time_transport_security(dial_url, instance)
    fingerprint = instance.config.get("tls_fingerprint")
    if not fingerprint:
        return None
    try:
        expected = normalize_tls_fingerprint(str(fingerprint))
    except ValueError as exc:
        raise GuildTLSPinMismatchError(f"{instance.name}: {exc}") from exc
    parsed = urlsplit(dial_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise GuildTLSPinMismatchError(
            f"{instance.name}: config.tls_fingerprint requires an https:// URL, got {dial_url}"
        )
    host = parsed.hostname
    port = parsed.port or _DEFAULT_TLS_PORT
    cache_key = (host, port, expected)
    cached_context = _cached_pinned_context(cache_key)
    if cached_context is not None:
        return cached_context
    der = await _fetch_leaf_certificate_der_cached(host, port, timeout=timeout)
    actual = hashlib.sha256(der).hexdigest()
    if actual != expected:
        raise GuildTLSPinMismatchError(
            f"{instance.name} ({host}:{port}) presented certificate fingerprint {actual}, "
            f"which does not match its pinned config.tls_fingerprint {expected}; update the "
            "pin or fix the remote's certificate — refusing to connect"
        )
    pem = ssl.DER_cert_to_PEM_cert(der)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_REQUIRED
    # A pinned leaf may be CA-issued (Issuer != Subject) rather than
    # self-signed. Without this flag, OpenSSL tries to build a full chain up
    # to a root in the trust store and fails with "unable to get local
    # issuer certificate" even though we deliberately trust this exact leaf
    # and nothing else. With it, a trust-store entry is accepted as a valid
    # anchor on its own, regardless of who issued it — which is exactly what
    # "pin this leaf" means. A CA:TRUE self-signed cert (a cert that is
    # itself a CA) would also verify under this flag without it being
    # needed; the flag only matters for the CA-issued-leaf case.
    context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    context.load_verify_locations(cadata=pem)
    _PIN_CACHE[cache_key] = (context, time.monotonic() + _PIN_CACHE_TTL_SECONDS)
    return context


async def resolve_guild_httpx_verify(
    instance: GuildTransportTarget, *, dial_url: str, timeout: float
) -> bool | ssl.SSLContext:
    """Return the ``verify=`` value for an httpx client calling *dial_url* on *instance*."""
    context = await resolve_guild_ssl_context(instance, dial_url=dial_url, timeout=timeout)
    return context if context is not None else True


async def resolve_guild_ws_ssl(
    instance: GuildTransportTarget, *, dial_url: str, timeout: float
) -> ssl.SSLContext | None:
    """Return the ``ssl=`` kwarg for a ``websockets.connect`` call to *dial_url* on *instance*."""
    return await resolve_guild_ssl_context(instance, dial_url=dial_url, timeout=timeout)


async def build_guild_httpx_client(
    instance: GuildTransportTarget,
    *,
    dial_url: str,
    timeout_seconds: float,
    connect_timeout_seconds: float | None = None,
    follow_redirects: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """Construct the httpx client for one outbound, non-embedded Guild call.

    The single place every such call builds its client, so transport-policy
    and TLS-pin enforcement is applied identically to aggregate REST
    requests, the health probe, and the Observatory/agent-directory clients.
    Callers with an embedded-transport instance never reach this factory —
    see the module docstring.

    A pinned instance's client uses ``trust_env=False``. This is consistency,
    not protection an intercepting proxy could bypass: the certificate
    pre-fetch (``fetch_leaf_certificate_der``) is a raw socket connect that
    never consults ``HTTPS_PROXY``/``HTTP_PROXY`` and cannot traverse an HTTP
    CONNECT proxy, so pinning already requires a direct route to the
    instance before this client is even built. A proxy that re-signs the
    connection (a TLS-intercepting/MITM proxy) would already fail the pin's
    own certificate check and be refused, ``trust_env`` or not — that
    protection is the pin itself. Keeping the real request on the same
    direct path the pre-fetch used is what ``trust_env=False`` buys; its
    real-world consequence is a known limitation: an instance reachable only
    through a corporate/cluster egress proxy, with no direct route at all,
    cannot be pinned, because the pre-fetch cannot reach it either. An
    unpinned instance keeps httpx's normal proxy-env-var behavior, since
    there is no pin for a proxy hop to interact with.

    A pinned instance's client also forces ``follow_redirects=False``
    regardless of *follow_redirects*: httpx re-uses the same ``verify=``
    context for a redirect target, so a same-scheme redirect to a different
    host would already fail closed on the pin mismatch — but a redirect to
    plain ``http://`` bypasses TLS (and the pin) entirely, sending the
    caller's bearer wherever the redirect points. Refusing to follow means a
    misbehaving pinned instance shows up as a 3xx response, never a silent
    hop off the pinned connection. Raises ``ValueError`` if *transport* is
    also given for a pinned instance: httpx ignores ``verify=`` once a custom
    ``transport`` is set, so combining the two would silently build a client
    whose pin does nothing.

    *connect_timeout_seconds* overrides the shared connect-leg ceiling for
    this one call (e.g. the SSE stream's own configured
    ``forge_stream_remote_connect_timeout_seconds``) — still capped at
    *timeout_seconds* so the connect leg can never exceed the whole request's
    own timeout.
    """
    connect_timeout = min(
        timeout_seconds,
        connect_timeout_seconds
        if connect_timeout_seconds is not None
        else _connect_timeout_ceiling_seconds,
    )
    verify = await resolve_guild_httpx_verify(instance, dial_url=dial_url, timeout=connect_timeout)
    is_pinned = bool(instance.config.get("tls_fingerprint"))
    if is_pinned and transport is not None:
        raise ValueError(
            f"{instance.name}: a custom transport= was passed for a pinned instance; "
            "httpx ignores verify= once transport= is set, so the pin would silently "
            "do nothing — pass transport= only for an unpinned instance"
        )
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_seconds, connect=connect_timeout),
        follow_redirects=False if is_pinned else follow_redirects,
        verify=verify,
        transport=transport,
        trust_env=not is_pinned,
    )
