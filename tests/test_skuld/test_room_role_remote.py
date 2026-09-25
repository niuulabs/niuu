"""RemoteAuthorizationAdapter: room-role resolution via Forge, over HTTP.

Covers role resolution per role, cache TTL / revocation, and fail-closed
behavior on an unreachable Forge or a malformed response — see
.claude/rules/no-fallbacks.md: an unreachable authority must deny, never
substitute a default role.
"""

from __future__ import annotations

import time

import httpx
import pytest
import respx

from skuld.room_role_port import RoomRoleResolutionError
from skuld.room_role_remote import RemoteAuthorizationAdapter

EXCHANGE_URL = "http://volundr.test/api/v1/tokens/workload/exchange"
ROLE_URL = "http://volundr.test/api/v1/forge/sessions/sess-1/participants/role"


def _adapter(tmp_path, **overrides) -> RemoteAuthorizationAdapter:
    token_file = tmp_path / "token"
    token_file.write_text("service-account-proof")
    kwargs = {
        "volundr_api_url": "http://volundr.test",
        "token_file": str(token_file),
        "cache_ttl_seconds": 5.0,
    }
    kwargs.update(overrides)
    return RemoteAuthorizationAdapter(**kwargs)


def test_constructor_requires_volundr_api_url():
    with pytest.raises(ValueError, match="volundr_api_url"):
        RemoteAuthorizationAdapter(volundr_api_url="")


def test_constructor_requires_non_blank_volundr_api_url():
    with pytest.raises(ValueError, match="volundr_api_url"):
        RemoteAuthorizationAdapter(volundr_api_url="   ")


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["owner", "approver", "viewer"])
async def test_resolves_each_room_role(tmp_path, role):
    adapter = _adapter(tmp_path)
    with respx.mock(assert_all_called=True) as router:
        router.post(EXCHANGE_URL).mock(
            return_value=httpx.Response(
                200, json={"token": "workload-jwt", "expiresAt": time.time() + 300}
            )
        )
        router.get(ROLE_URL).mock(return_value=httpx.Response(200, json={"role": role}))
        result = await adapter.resolve_role(
            session_id="sess-1", user_id="alice", tenant_id="acme", roles=[]
        )
    assert result == role


@pytest.mark.asyncio
async def test_no_grant_resolves_to_none_not_an_error(tmp_path):
    """A caller with no active grant is a real, expected answer — not a deny-path bug."""
    adapter = _adapter(tmp_path)
    with respx.mock(assert_all_called=True) as router:
        router.post(EXCHANGE_URL).mock(
            return_value=httpx.Response(
                200, json={"token": "workload-jwt", "expiresAt": time.time() + 300}
            )
        )
        router.get(ROLE_URL).mock(return_value=httpx.Response(200, json={"role": None}))
        result = await adapter.resolve_role(
            session_id="sess-1", user_id="alice", tenant_id="acme", roles=[]
        )
    assert result is None


@pytest.mark.asyncio
async def test_request_carries_the_configured_scope_and_target_identity(tmp_path):
    adapter = _adapter(tmp_path, scope="forge:session:room-role")
    captured = {}

    def _capture_exchange(request):
        captured["exchange_body"] = request.content
        return httpx.Response(200, json={"token": "workload-jwt", "expiresAt": time.time() + 300})

    def _capture_role(request):
        captured["role_params"] = dict(request.url.params)
        captured["auth_header"] = request.headers.get("authorization")
        return httpx.Response(200, json={"role": "viewer"})

    with respx.mock(assert_all_called=True) as router:
        router.post(EXCHANGE_URL).mock(side_effect=_capture_exchange)
        router.get(ROLE_URL).mock(side_effect=_capture_role)
        await adapter.resolve_role(
            session_id="sess-1", user_id="bob", tenant_id="acme", roles=["volundr:developer"]
        )

    assert b'"forge:session:room-role"' in captured["exchange_body"]
    assert captured["role_params"]["user_id"] == "bob"
    assert captured["role_params"]["tenant_id"] == "acme"
    assert captured["role_params"]["roles"] == "volundr:developer"
    assert captured["auth_header"] == "Bearer workload-jwt"


@pytest.mark.asyncio
async def test_repeated_calls_within_ttl_hit_the_cache_not_the_network(tmp_path):
    adapter = _adapter(tmp_path, cache_ttl_seconds=60.0)
    with respx.mock(assert_all_called=True) as router:
        exchange_route = router.post(EXCHANGE_URL).mock(
            return_value=httpx.Response(
                200, json={"token": "workload-jwt", "expiresAt": time.time() + 300}
            )
        )
        role_route = router.get(ROLE_URL).mock(
            return_value=httpx.Response(200, json={"role": "owner"})
        )
        for _ in range(3):
            result = await adapter.resolve_role(
                session_id="sess-1", user_id="alice", tenant_id="acme", roles=[]
            )
            assert result == "owner"
    assert exchange_route.call_count == 1
    assert role_route.call_count == 1


@pytest.mark.asyncio
async def test_revocation_takes_effect_once_the_cache_ttl_elapses(tmp_path, monkeypatch):
    """A short cache TTL bounds how long a revoked grant keeps working."""
    adapter = _adapter(tmp_path, cache_ttl_seconds=1.0)
    fake_now = [1_000_000.0]
    monkeypatch.setattr(time, "time", lambda: fake_now[0])

    responses = iter(
        [
            httpx.Response(200, json={"role": "owner"}),
            httpx.Response(200, json={"role": None}),
        ]
    )
    with respx.mock(assert_all_called=True) as router:
        router.post(EXCHANGE_URL).mock(
            return_value=httpx.Response(
                200, json={"token": "workload-jwt", "expiresAt": fake_now[0] + 300}
            )
        )
        role_route = router.get(ROLE_URL).mock(side_effect=lambda _request: next(responses))

        first = await adapter.resolve_role(
            session_id="sess-1", user_id="alice", tenant_id="acme", roles=[]
        )
        assert first == "owner"

        # Still within the TTL: cached, no second network call.
        cached = await adapter.resolve_role(
            session_id="sess-1", user_id="alice", tenant_id="acme", roles=[]
        )
        assert cached == "owner"
        assert role_route.call_count == 1

        # Past the TTL: revocation is now visible.
        fake_now[0] += 2.0
        revoked = await adapter.resolve_role(
            session_id="sess-1", user_id="alice", tenant_id="acme", roles=[]
        )
        assert revoked is None
        assert role_route.call_count == 2


@pytest.mark.asyncio
async def test_missing_workload_token_file_fails_closed(tmp_path):
    adapter = RemoteAuthorizationAdapter(
        volundr_api_url="http://volundr.test",
        token_file=str(tmp_path / "does-not-exist"),
    )
    with pytest.raises(RoomRoleResolutionError, match="No projected workload-identity token"):
        await adapter.resolve_role(session_id="sess-1", user_id="alice", tenant_id="acme", roles=[])


@pytest.mark.asyncio
async def test_unreachable_forge_fails_closed_not_owner_or_viewer(tmp_path):
    adapter = _adapter(tmp_path)
    with respx.mock(assert_all_called=True) as router:
        router.post(EXCHANGE_URL).mock(side_effect=httpx.ConnectError("connection refused"))
        with pytest.raises(RoomRoleResolutionError, match="Workload token exchange"):
            await adapter.resolve_role(
                session_id="sess-1", user_id="alice", tenant_id="acme", roles=[]
            )


@pytest.mark.asyncio
async def test_role_lookup_timeout_fails_closed(tmp_path):
    adapter = _adapter(tmp_path)
    with respx.mock(assert_all_called=True) as router:
        router.post(EXCHANGE_URL).mock(
            return_value=httpx.Response(
                200, json={"token": "workload-jwt", "expiresAt": time.time() + 300}
            )
        )
        router.get(ROLE_URL).mock(side_effect=httpx.ReadTimeout("timed out"))
        with pytest.raises(RoomRoleResolutionError, match="Room-role lookup"):
            await adapter.resolve_role(
                session_id="sess-1", user_id="alice", tenant_id="acme", roles=[]
            )


@pytest.mark.asyncio
async def test_exchange_returning_no_token_fails_closed(tmp_path):
    adapter = _adapter(tmp_path)
    with respx.mock(assert_all_called=True) as router:
        router.post(EXCHANGE_URL).mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(RoomRoleResolutionError, match="no token"):
            await adapter.resolve_role(
                session_id="sess-1", user_id="alice", tenant_id="acme", roles=[]
            )


@pytest.mark.asyncio
async def test_unrecognized_role_value_fails_closed(tmp_path):
    adapter = _adapter(tmp_path)
    with respx.mock(assert_all_called=True) as router:
        router.post(EXCHANGE_URL).mock(
            return_value=httpx.Response(
                200, json={"token": "workload-jwt", "expiresAt": time.time() + 300}
            )
        )
        router.get(ROLE_URL).mock(return_value=httpx.Response(200, json={"role": "admin"}))
        with pytest.raises(RoomRoleResolutionError, match="unrecognized room role"):
            await adapter.resolve_role(
                session_id="sess-1", user_id="alice", tenant_id="acme", roles=[]
            )
