"""Tests for the HTTP-backed reachability probe."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import respx
from fastapi import FastAPI
from httpx import Response

from niuu.adapters.outbound.http_instance_probe import (
    DEFAULT_HEALTH_PATHS,
    HttpInstanceProbeAdapter,
)
from niuu.domain.models import InstanceKind, InstanceVisibility, RegisteredInstance

_VOLUNDR_HEALTH_PATH = DEFAULT_HEALTH_PATHS[InstanceKind.VOLUNDR]


def _instance(
    *,
    base_url: str = "https://volundr-1.example.com",
    kind: InstanceKind = InstanceKind.VOLUNDR,
    config: dict | None = None,
) -> RegisteredInstance:
    now = datetime.now(UTC)
    return RegisteredInstance(
        id="instance-1",
        kind=kind,
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
    respx.get(f"https://volundr-1.example.com{_VOLUNDR_HEALTH_PATH}").mock(
        return_value=Response(200)
    )
    adapter = HttpInstanceProbeAdapter(timeout_seconds=5.0)

    result = await adapter.probe(_instance())

    assert result.ok is True
    assert result.status_code == 200
    assert result.message == "Volundr One is reachable"


@pytest.mark.asyncio
@respx.mock
async def test_probe_reports_not_ok_on_an_error_status() -> None:
    respx.get(f"https://volundr-1.example.com{_VOLUNDR_HEALTH_PATH}").mock(
        return_value=Response(503)
    )
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
    respx.get(f"https://volundr-1.example.com{_VOLUNDR_HEALTH_PATH}").mock(
        side_effect=ConnectionError()
    )
    adapter = HttpInstanceProbeAdapter(timeout_seconds=5.0)

    result = await adapter.probe(_instance())

    assert result.ok is False
    assert result.status_code is None
    assert result.message  # never blank — see observatory_topology.py's own note on this


@pytest.mark.asyncio
async def test_embedded_transport_probes_the_asgi_app_directly() -> None:
    embedded = FastAPI()

    @embedded.get(_VOLUNDR_HEALTH_PATH)
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


# ── Per-kind health path resolution ──────────────────────────────────────


@pytest.mark.parametrize(
    ("kind", "expected_path"),
    [
        (InstanceKind.VOLUNDR, "/api/v1/forge/health"),
        (InstanceKind.TING, "/api/v1/ting/health"),
        (InstanceKind.MIMIR, "/api/v1/mimir/health"),
        (InstanceKind.BIFROST, "/api/v1/bifrost/health"),
        (InstanceKind.RAVN, "/api/v1/ravn/health"),
        (InstanceKind.OBSERVATORY, "/api/v1/observatory/health"),
        (InstanceKind.GENERIC, "/health"),
    ],
)
def test_default_health_path_hits_the_service_under_its_own_api_prefix(
    kind: InstanceKind, expected_path: str
) -> None:
    """A bare /health either 404s behind an ingress that only routes the
    service's own API prefix (the standalone Observatories), or is silently
    answered by a co-located web-next nginx instead of the real backend
    (containers/niuu-web/nginx.conf) — every default must resolve under the
    service's own API prefix to dodge both."""
    adapter = HttpInstanceProbeAdapter(timeout_seconds=5.0)

    assert adapter._resolve_health_path(_instance(kind=kind)) == expected_path


@pytest.mark.asyncio
@respx.mock
async def test_configured_health_paths_override_the_kind_default() -> None:
    """niuu.health.probe.health_paths (per .claude/rules/config-first.md)
    only needs to carry the kind being changed — the rest keep their
    built-in default."""
    respx.get("https://mimir-1.example.com/mimir/health").mock(return_value=Response(200))
    adapter = HttpInstanceProbeAdapter(timeout_seconds=5.0, health_paths={"mimir": "/mimir/health"})

    result = await adapter.probe(
        _instance(base_url="https://mimir-1.example.com", kind=InstanceKind.MIMIR)
    )

    assert result.ok is True
    # The override did not clobber other kinds' defaults.
    assert adapter._resolve_health_path(_instance(kind=InstanceKind.RAVN)) == (
        "/api/v1/ravn/health"
    )


@pytest.mark.asyncio
@respx.mock
async def test_per_instance_health_path_wins_over_both_the_kind_default_and_config() -> None:
    """An instance whose base_url doesn't follow its kind's usual convention
    (e.g. it already ends in a version prefix) sets config.health_path —
    the one thing an operator controls per-registration, without redeploying
    Guild."""
    respx.get("https://mimir-yggdrasil.example.com/api/v1/mimir/health").mock(
        return_value=Response(200)
    )
    adapter = HttpInstanceProbeAdapter(timeout_seconds=5.0, health_paths={"mimir": "/mimir/health"})

    result = await adapter.probe(
        _instance(
            base_url="https://mimir-yggdrasil.example.com",
            kind=InstanceKind.MIMIR,
            config={"health_path": "/api/v1/mimir/health"},
        )
    )

    assert result.ok is True


def test_per_instance_health_path_is_normalized_to_start_with_a_slash() -> None:
    adapter = HttpInstanceProbeAdapter(timeout_seconds=5.0)

    resolved = adapter._resolve_health_path(_instance(config={"health_path": "custom/health"}))

    assert resolved == "/custom/health"


def test_an_unrecognized_kind_falls_back_to_the_documented_default() -> None:
    """A kind DEFAULT_HEALTH_PATHS doesn't cover (e.g. a future InstanceKind
    added without updating the map) falls back to the same conservative
    /health GENERIC uses — a documented default, not a silent guess."""
    adapter = HttpInstanceProbeAdapter(timeout_seconds=5.0)

    resolved = adapter._resolve_health_path(_instance(kind="future-kind"))  # type: ignore[arg-type]

    assert resolved == "/health"
