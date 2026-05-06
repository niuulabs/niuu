"""Helpers for testing legacy and canonical HTTP route equivalence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RouteCallSpec:
    """Describe one HTTP call for contract comparison tests."""

    path: str
    method: str = "GET"
    params: dict[str, Any] = field(default_factory=dict)
    json_body: Any = None
    headers: dict[str, str] = field(default_factory=dict)


def _request_kwargs(spec: RouteCallSpec) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if spec.params:
        kwargs["params"] = spec.params
    if spec.headers:
        kwargs["headers"] = spec.headers
    if spec.json_body is not None:
        kwargs["json"] = spec.json_body
    return kwargs


def assert_route_equivalence(
    client: Any,
    legacy: RouteCallSpec,
    canonical: RouteCallSpec,
    *,
    expected_status: int = 200,
    normalizer: Any = None,
) -> tuple[Any, Any]:
    """Assert that legacy and canonical routes return equivalent responses."""
    legacy_response = client.request(legacy.method, legacy.path, **_request_kwargs(legacy))
    canonical_response = client.request(
        canonical.method,
        canonical.path,
        **_request_kwargs(canonical),
    )

    assert legacy_response.status_code == expected_status
    assert canonical_response.status_code == expected_status

    legacy_payload = legacy_response.json()
    canonical_payload = canonical_response.json()
    if normalizer is not None:
        legacy_payload = normalizer(legacy_payload)
        canonical_payload = normalizer(canonical_payload)

    assert legacy_payload == canonical_payload
    return legacy_response, canonical_response
