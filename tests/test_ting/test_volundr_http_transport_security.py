"""VolundrHTTPAdapter routes every outbound call through guild_transport.

Before this, VolundrHTTPAdapter dialled whatever scheme/host a registered
instance's base_url carried with a bare httpx.AsyncClient — an http:// row
without an explicit opt-in leaked the bearer token in plaintext, and a
pinned, self-signed instance failed normal CA verification. These tests
prove the fix: VolundrHTTPAdapter._client() now goes through
niuu.adapters.outbound.guild_transport.build_guild_httpx_client, the same
choke point every other outbound Guild call (aggregate REST, health probe,
WebSocket proxies, SSE stream) uses, so the same https-unless-allow_plaintext
policy and optional TLS pin apply here too — and a violation raises
GuildTransportError rather than being silently skipped.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
import respx
from httpx import Response

from niuu.adapters.outbound import guild_transport
from niuu.adapters.outbound.guild_transport import (
    GuildInsecureTransportError,
    GuildTLSPinMismatchError,
    GuildTransportError,
)
from ting.adapters.volundr_http import VolundrHTTPAdapter


@pytest.mark.asyncio
async def test_a_plain_http_instance_without_the_opt_in_is_refused() -> None:
    """No request is even attempted — httpx never sees this call, so there
    is no respx mock to register; a policy refusal happens before any
    socket is touched."""
    adapter = VolundrHTTPAdapter(base_url="http://volundr.example.com", name="alpha")

    with pytest.raises(GuildInsecureTransportError, match="allow_plaintext"):
        await adapter.list_sessions()


@pytest.mark.asyncio
async def test_the_refusal_is_not_caught_and_skipped_anywhere_in_the_call_chain() -> None:
    """A policy failure must surface to the caller exactly like any other
    Volundr HTTP failure — never be swallowed into an empty result that
    reads as success (see .claude/rules/no-fallbacks.md)."""
    adapter = VolundrHTTPAdapter(base_url="http://volundr.example.com", name="alpha")

    with pytest.raises(GuildTransportError):
        await adapter.list_sessions()


@pytest.mark.asyncio
@respx.mock
async def test_a_plain_http_instance_with_the_opt_in_is_allowed() -> None:
    respx.get("http://volundr.example.com/api/v1/forge/sessions").mock(
        return_value=Response(200, json=[])
    )
    adapter = VolundrHTTPAdapter(
        base_url="http://volundr.example.com",
        name="alpha",
        config={"allow_plaintext": True},
    )

    sessions = await adapter.list_sessions()

    assert sessions == []


@pytest.mark.asyncio
@respx.mock
async def test_a_loopback_http_instance_needs_no_opt_in() -> None:
    """The same exemption every other Guild outbound call site gets: a
    loopback target never leaves the machine, so there is no wire to
    harden — this is what makes LocalVolundrAdapterFactory's always-local
    target work with an empty config."""
    respx.get("http://127.0.0.1:9999/api/v1/forge/sessions").mock(
        return_value=Response(200, json=[])
    )
    adapter = VolundrHTTPAdapter(base_url="http://127.0.0.1:9999", name="local")

    sessions = await adapter.list_sessions()

    assert sessions == []


@pytest.mark.asyncio
@respx.mock
async def test_an_in_cluster_svc_cluster_local_instance_needs_no_opt_in() -> None:
    """Ting dials Guild-registered instances directly (VolundrAdapterFactory),
    including live clusters that seed in-cluster http:// service DNS (e.g.
    http://niuu-volundr.volundr.svc.cluster.local) — the same trusted-suffix
    exemption every other outbound Guild call site gets must apply here too,
    or Ting alone would refuse targets Guild itself accepted at seed time."""
    respx.get("http://niuu-volundr.volundr.svc.cluster.local/api/v1/forge/sessions").mock(
        return_value=Response(200, json=[])
    )
    adapter = VolundrHTTPAdapter(
        base_url="http://niuu-volundr.volundr.svc.cluster.local", name="alpha"
    )

    sessions = await adapter.list_sessions()

    assert sessions == []


@pytest.mark.asyncio
async def test_a_pinned_instance_with_a_mismatched_certificate_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VolundrHTTPAdapter shares build_guild_httpx_client with the
    aggregate/probe/WS call sites, so a pinned instance's mismatched
    certificate is caught here too — a hard refusal, not a fallback to
    default CA verification."""

    def _fake_fetch(*_args: Any, **_kwargs: Any) -> bytes:
        return b"not the certificate the operator pinned"

    monkeypatch.setattr(guild_transport, "fetch_leaf_certificate_der", _fake_fetch)
    pinned_fingerprint = hashlib.sha256(b"the actual expected certificate").hexdigest()
    adapter = VolundrHTTPAdapter(
        base_url="https://volundr.example.com",
        name="alpha",
        config={"tls_fingerprint": pinned_fingerprint},
    )

    with pytest.raises(GuildTLSPinMismatchError, match="does not match"):
        await adapter.list_sessions()


@pytest.mark.asyncio
async def test_a_non_boolean_allow_plaintext_is_rejected_not_coerced() -> None:
    """Matches the write-time validation in services/instances.py: a
    truthy non-bool is a caller mistake, not a decision, and this is the
    real, call-time enforcement boundary a legacy or hand-edited row still
    has to pass."""
    adapter = VolundrHTTPAdapter(
        base_url="http://volundr.example.com",
        name="alpha",
        config={"allow_plaintext": "true"},
    )

    with pytest.raises(GuildInsecureTransportError, match="boolean"):
        await adapter.list_sessions()


@pytest.mark.asyncio
@respx.mock
async def test_subscribe_activity_also_enforces_transport_policy() -> None:
    """The one streaming call site (no_read_timeout=True) goes through the
    same _client() helper as every other method — this is not a second,
    separately-wired code path that could drift out of policy."""
    adapter = VolundrHTTPAdapter(base_url="http://volundr.example.com", name="alpha")

    with pytest.raises(GuildInsecureTransportError):
        async for _ in adapter.subscribe_activity():
            pass  # pragma: no cover - refused before any event is read
