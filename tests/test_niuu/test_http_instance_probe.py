"""Tests for the HTTP-backed reachability probe."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

import pytest
import respx
from fastapi import FastAPI
from httpx import Response

from niuu.adapters.outbound import guild_transport
from niuu.adapters.outbound.http_instance_probe import HttpInstanceProbeAdapter
from niuu.domain.models import InstanceKind, InstanceVisibility, RegisteredInstance


def _instance(
    *,
    base_url: str = "https://volundr-1.example.com",
    config: dict | None = None,
) -> RegisteredInstance:
    now = datetime.now(UTC)
    return RegisteredInstance(
        id="instance-1",
        kind=InstanceKind.VOLUNDR,
        slug="volundr-1",
        name="Volundr One",
        base_url=base_url,
        visibility=InstanceVisibility.SYSTEM,
        owner_id=None,
        tenant_id=None,
        enabled=True,
        is_default=False,
        config=config or {},
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
@respx.mock
async def test_probe_reports_ok_on_a_healthy_response() -> None:
    respx.get("https://volundr-1.example.com/health").mock(return_value=Response(200))
    adapter = HttpInstanceProbeAdapter(timeout_seconds=5.0)

    result = await adapter.probe(_instance())

    assert result.ok is True
    assert result.status_code == 200
    assert result.message == "Volundr One is reachable"


@pytest.mark.asyncio
@respx.mock
async def test_probe_reports_not_ok_on_an_error_status() -> None:
    respx.get("https://volundr-1.example.com/health").mock(return_value=Response(503))
    adapter = HttpInstanceProbeAdapter(timeout_seconds=5.0)

    result = await adapter.probe(_instance())

    assert result.ok is False
    assert result.status_code == 503
    assert "Volundr One" in result.message


@pytest.mark.asyncio
@respx.mock
async def test_probe_never_raises_on_a_transport_failure_and_never_reports_a_blank_message() -> (
    None
):
    respx.get("https://volundr-1.example.com/health").mock(side_effect=ConnectionError())
    adapter = HttpInstanceProbeAdapter(timeout_seconds=5.0)

    result = await adapter.probe(_instance())

    assert result.ok is False
    assert result.status_code is None
    assert result.message  # never blank — see observatory_topology.py's own note on this


@pytest.mark.asyncio
async def test_embedded_transport_probes_the_asgi_app_directly() -> None:
    embedded = FastAPI()

    @embedded.get("/health")
    async def _health() -> dict[str, str]:
        return {"status": "healthy"}

    adapter = HttpInstanceProbeAdapter(timeout_seconds=5.0, embedded_app=embedded)

    result = await adapter.probe(_instance(config={"transport": "embedded"}))

    assert result.ok is True
    assert result.status_code == 200


@pytest.mark.asyncio
async def test_embedded_transport_without_an_app_is_reported_as_unreachable() -> None:
    adapter = HttpInstanceProbeAdapter(timeout_seconds=5.0, embedded_app=None)

    result = await adapter.probe(_instance(config={"transport": "embedded"}))

    assert result.ok is False
    assert result.status_code == 502
    assert "not available" in result.message


@pytest.mark.asyncio
async def test_probe_uses_the_shared_guild_transport_factory_and_fails_closed_on_a_pin_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe shares build_guild_httpx_client with the aggregate/WS call
    sites, so a pinned instance's mismatched certificate is caught here too
    — reported as unreachable, never as a request that silently skipped
    verification."""

    def _fake_fetch(*_args: Any, **_kwargs: Any) -> bytes:
        return b"not the certificate the operator pinned"

    monkeypatch.setattr(guild_transport, "fetch_leaf_certificate_der", _fake_fetch)
    adapter = HttpInstanceProbeAdapter(timeout_seconds=5.0)
    pinned_fingerprint = hashlib.sha256(b"the actual expected certificate").hexdigest()

    result = await adapter.probe(_instance(config={"tls_fingerprint": pinned_fingerprint}))

    assert result.ok is False
    assert "does not match" in result.message
