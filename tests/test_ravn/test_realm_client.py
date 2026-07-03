"""RealmClient: resolve a Valkyrie's tool-build trust grant over fake HTTP.

Phase 3 — per-Valkyrie tool-build config from realm trust grants. Ravn reaches
the realm governance API at the same Volundr base_url it uses for Forge
sessions and never imports niuu/volundr.
"""

from __future__ import annotations

from typing import Any

import pytest

from ravn.adapters.realm.client import (
    BuildGrant,
    RealmClient,
    autonomy_mode_for_trust_level,
    build_realm_client_kwargs,
    workflow_selector_from_grant,
)
from ravn.adapters.tool_build.http import HttpResponse


class _FakeHttpClient:
    """Scripted AsyncJsonHttpClient: maps a url-suffix -> HttpResponse."""

    def __init__(
        self,
        routes: dict[str, HttpResponse] | None = None,
        *,
        raise_on_get: Exception | None = None,
    ) -> None:
        self._routes = dict(routes or {})
        self._raise_on_get = raise_on_get
        self.calls: list[str] = []

    async def get(self, url: str) -> HttpResponse:
        self.calls.append(url)
        if self._raise_on_get is not None:
            raise self._raise_on_get
        for suffix, response in self._routes.items():
            if url.endswith(suffix):
                return response
        raise AssertionError(f"no scripted response for GET {url}")

    async def post(self, url: str, json_body: dict[str, Any]) -> HttpResponse:
        raise AssertionError("RealmClient must never POST — it is read-only")


def _client(routes: dict[str, HttpResponse] | None = None, **kwargs: Any) -> RealmClient:
    return RealmClient(base_url="http://volundr", client=_FakeHttpClient(routes or {}), **kwargs)


def _grants_path(slug: str) -> str:
    return f"/api/v1/realms/{slug}/trust-grants"


# ---------------------------------------------------------------------------
# resolve_build_grant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_build_grant_picks_highest_level_build_grant() -> None:
    body = [
        {"action_class": "read", "level": 9, "limits": {}, "target": "mimir"},
        {"action_class": "build", "level": 2, "limits": {"workflow": "old"}, "target": "t2"},
        {
            "action_class": "build",
            "level": 5,
            "limits": {"workflow": "tool-builder"},
            "target": "t5",
        },
    ]
    client = _client({_grants_path("payments"): HttpResponse(status_code=200, body=body)})

    grant = await client.resolve_build_grant("payments")

    assert grant == BuildGrant(level=5, limits={"workflow": "tool-builder"}, target="t5")


@pytest.mark.asyncio
async def test_resolve_build_grant_returns_none_without_a_build_grant() -> None:
    body = [{"action_class": "read", "level": 9, "limits": {}, "target": "mimir"}]
    client = _client({_grants_path("payments"): HttpResponse(status_code=200, body=body)})

    assert await client.resolve_build_grant("payments") is None


@pytest.mark.asyncio
async def test_resolve_build_grant_returns_none_for_unknown_realm_404() -> None:
    client = _client({_grants_path("ghost"): HttpResponse(status_code=404, body={"error": "no"})})

    assert await client.resolve_build_grant("ghost") is None


@pytest.mark.asyncio
async def test_resolve_build_grant_returns_none_for_empty_slug() -> None:
    client = _client({})

    assert await client.resolve_build_grant("") is None


@pytest.mark.asyncio
async def test_resolve_build_grant_raises_on_non_list_body() -> None:
    client = _client(
        {_grants_path("payments"): HttpResponse(status_code=200, body={"grants": []})}
    )

    with pytest.raises(ValueError, match="non-list body"):
        await client.resolve_build_grant("payments")


@pytest.mark.asyncio
async def test_resolve_build_grant_raises_when_build_grant_missing_level() -> None:
    body = [{"action_class": "build", "limits": {}, "target": "t"}]
    client = _client({_grants_path("payments"): HttpResponse(status_code=200, body=body)})

    with pytest.raises(ValueError, match="missing a numeric 'level'"):
        await client.resolve_build_grant("payments")


@pytest.mark.asyncio
async def test_resolve_build_grant_raises_on_non_object_limits() -> None:
    body = [{"action_class": "build", "level": 3, "limits": "nope", "target": "t"}]
    client = _client({_grants_path("payments"): HttpResponse(status_code=200, body=body)})

    with pytest.raises(ValueError, match="non-object 'limits'"):
        await client.resolve_build_grant("payments")


@pytest.mark.asyncio
async def test_resolve_build_grant_ignores_boolean_level_when_ranking() -> None:
    # A JSON ``true`` must not be treated as level 1 — it is not a real level.
    body = [
        {"action_class": "build", "level": True, "limits": {}, "target": "bogus"},
        {"action_class": "build", "level": 4, "limits": {}, "target": "real"},
    ]
    client = _client({_grants_path("payments"): HttpResponse(status_code=200, body=body)})

    grant = await client.resolve_build_grant("payments")

    assert grant is not None
    assert grant.target == "real"
    assert grant.level == 4


@pytest.mark.asyncio
async def test_resolve_build_grant_defaults_missing_limits_to_empty() -> None:
    body = [{"action_class": "build", "level": 2, "target": "t"}]
    client = _client({_grants_path("payments"): HttpResponse(status_code=200, body=body)})

    grant = await client.resolve_build_grant("payments")

    assert grant is not None
    assert grant.limits == {}


# ---------------------------------------------------------------------------
# workflow_selector_from_grant
# ---------------------------------------------------------------------------


def test_workflow_selector_from_grant_returns_names_selector() -> None:
    grant = BuildGrant(level=3, limits={"workflow": "tool-builder"}, target="t")

    assert workflow_selector_from_grant(grant) == {"names": ["tool-builder"]}


def test_workflow_selector_from_grant_strips_whitespace() -> None:
    grant = BuildGrant(level=3, limits={"workflow": "  tool-builder  "}, target="t")

    assert workflow_selector_from_grant(grant) == {"names": ["tool-builder"]}


def test_workflow_selector_from_grant_none_when_no_workflow() -> None:
    assert workflow_selector_from_grant(BuildGrant(level=3, limits={}, target="t")) is None


def test_workflow_selector_from_grant_none_when_workflow_blank_or_wrong_type() -> None:
    blank = BuildGrant(level=3, limits={"workflow": "  "}, target="t")
    wrong_type = BuildGrant(level=3, limits={"workflow": 7}, target="t")
    assert workflow_selector_from_grant(blank) is None
    assert workflow_selector_from_grant(wrong_type) is None


# ---------------------------------------------------------------------------
# autonomy_mode_for_trust_level
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (0, "guarded"),
        (1, "guarded"),
        (2, "autonomous"),
        (3, "autonomous"),
        (4, "yolo"),
        (9, "yolo"),
    ],
)
def test_autonomy_mode_for_trust_level_table(level: int, expected: str) -> None:
    assert autonomy_mode_for_trust_level(level) == expected


# ---------------------------------------------------------------------------
# build_realm_client_kwargs
# ---------------------------------------------------------------------------


def test_build_realm_client_kwargs_prefers_realm_kwargs() -> None:
    result = build_realm_client_kwargs(
        realm_api_kwargs={"external_token_env": "REALM_TOKEN", "base_url": "ignored"},
        tool_build_kwargs={"external_token_env": "BUILD_TOKEN"},
    )

    assert result == {"external_token_env": "REALM_TOKEN"}


def test_build_realm_client_kwargs_falls_back_to_tool_build_auth() -> None:
    result = build_realm_client_kwargs(
        realm_api_kwargs={},
        tool_build_kwargs={
            "base_url": "http://volundr",
            "workload_token_file": "/var/run/token",
            "workload_audiences": ["forge"],
        },
    )

    assert result == {
        "workload_token_file": "/var/run/token",
        "workload_audiences": ["forge"],
    }


# ---------------------------------------------------------------------------
# constructor / error paths
# ---------------------------------------------------------------------------


def test_realm_client_requires_base_url() -> None:
    with pytest.raises(ValueError, match="requires a base_url"):
        RealmClient(base_url="")


def test_realm_client_base_url_is_normalized() -> None:
    client = RealmClient(base_url="http://volundr/", client=_FakeHttpClient())

    assert client.base_url == "http://volundr"


def test_realm_client_builds_default_client_from_workload_identity() -> None:
    # No client passed: it must build a real HttpxJsonClient without raising.
    client = RealmClient(base_url="http://volundr", external_token_env="REALM_TOKEN")

    assert client.base_url == "http://volundr"
