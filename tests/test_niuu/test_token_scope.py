"""Tests for least-privilege Valkyrie build-token scope enforcement."""

from __future__ import annotations

import time

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from niuu.domain.services.token_scope import (
    KNOWN_BUILD_SCOPES,
    VALKYRIE_BUILD_TOKEN_USE,
    bound_build_scopes,
    require_build_scope,
    token_has_scope,
    token_requires_scope_check,
)

_SIGNING_KEY = "test-only-signing-key-32-bytes-long!"


def _encode(payload: dict) -> str:
    """Sign a JWT the way Envoy-fronted tokens arrive (signature ignored here)."""
    now = int(time.time())
    base = {"sub": "user-1", "iat": now, "exp": now + 600}
    base.update(payload)
    return jwt.encode(base, _SIGNING_KEY, algorithm="HS256")


def _build_token(scopes: list[str]) -> str:
    return _encode({"token_use": VALKYRIE_BUILD_TOKEN_USE, "scopes": scopes})


class TestKnownBuildScopes:
    def test_known_build_scopes_constant(self) -> None:
        """Pinned deliberately: widening what a workload credential may ever be
        granted should be a visible, reviewed change, not a silent one."""
        assert KNOWN_BUILD_SCOPES == frozenset(
            {
                "forge:session:create",
                "ting:workflow:launch",
                "observatory:topology:push",
            }
        )


class TestTokenRequiresScopeCheck:
    def test_build_token_requires_check(self) -> None:
        assert token_requires_scope_check({"token_use": VALKYRIE_BUILD_TOKEN_USE}) is True

    def test_non_build_token_does_not_require_check(self) -> None:
        assert token_requires_scope_check({"type": "pat"}) is False
        assert token_requires_scope_check({}) is False


class TestTokenHasScope:
    def test_build_token_with_scope_allowed(self) -> None:
        token = _build_token(["forge:session:create"])
        assert token_has_scope(token, "forge:session:create") is True

    def test_build_token_missing_scope_denied(self) -> None:
        token = _build_token(["ting:workflow:launch"])
        assert token_has_scope(token, "forge:session:create") is False

    def test_build_token_empty_scopes_denied(self) -> None:
        token = _build_token([])
        assert token_has_scope(token, "forge:session:create") is False

    def test_build_token_with_non_list_scopes_denied(self) -> None:
        token = _encode({"token_use": VALKYRIE_BUILD_TOKEN_USE, "scopes": "forge:session:create"})
        # Fail closed: a malformed scopes claim on a build token grants nothing.
        assert token_has_scope(token, "forge:session:create") is False

    def test_pat_token_always_allowed(self) -> None:
        token = _encode({"type": "pat"})
        assert token_has_scope(token, "forge:session:create") is True

    def test_plain_workload_token_always_allowed(self) -> None:
        token = _encode({"typ": "Bearer", "resource_access": {"volundr": {"roles": ["admin"]}}})
        assert token_has_scope(token, "ting:workflow:launch") is True

    def test_empty_token_allowed(self) -> None:
        # No token present -> not a build token -> pass through (Envoy/anon path).
        assert token_has_scope("", "forge:session:create") is True

    def test_malformed_token_handled_and_allowed(self) -> None:
        # A non-JWT string is handled without raising and passes through.
        assert token_has_scope("not-a-jwt", "forge:session:create") is True
        assert token_has_scope("a.b.c.d.e", "ting:workflow:launch") is True


class TestBoundBuildScopes:
    def test_none_returns_empty(self) -> None:
        assert bound_build_scopes(None) == []

    def test_empty_returns_empty(self) -> None:
        assert bound_build_scopes([]) == []

    def test_known_scopes_pass_through(self) -> None:
        assert bound_build_scopes(["forge:session:create"]) == ["forge:session:create"]

    def test_unknown_scopes_dropped(self) -> None:
        result = bound_build_scopes(
            ["forge:session:create", "forge:session:delete", "*", "admin:everything"]
        )
        assert result == ["forge:session:create"]

    def test_all_unknown_scopes_dropped_to_empty(self) -> None:
        assert bound_build_scopes(["nope", "also-nope"]) == []

    def test_duplicates_and_whitespace_collapsed(self) -> None:
        result = bound_build_scopes(
            [
                " forge:session:create ",
                "forge:session:create",
                "ting:workflow:launch",
                "",
            ]
        )
        assert result == ["forge:session:create", "ting:workflow:launch"]


class TestRequireBuildScopeFactory:
    def test_rejects_unknown_scope(self) -> None:
        with pytest.raises(ValueError, match="Unknown build scope"):
            require_build_scope("forge:session:delete")


def _app_with_scope(scope: str) -> FastAPI:
    app = FastAPI()

    @app.post("/build")
    async def build(_: None = Depends(require_build_scope(scope))) -> dict:
        return {"ok": True}

    return app


class TestRequireBuildScopeDependency:
    def test_build_token_with_scope_admitted(self) -> None:
        client = TestClient(_app_with_scope("forge:session:create"))
        token = _build_token(["forge:session:create"])
        response = client.post("/build", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json() == {"ok": True}

    def test_build_token_missing_scope_403(self) -> None:
        client = TestClient(_app_with_scope("forge:session:create"))
        token = _build_token(["ting:workflow:launch"])
        response = client.post("/build", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 403
        assert "forge:session:create" in response.json()["detail"]

    def test_non_build_token_admitted(self) -> None:
        client = TestClient(_app_with_scope("forge:session:create"))
        token = _encode({"type": "pat"})
        response = client.post("/build", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200

    def test_no_auth_header_admitted(self) -> None:
        # Scope enforcement is additive; missing auth is handled by the real
        # auth dependency, not this one.
        client = TestClient(_app_with_scope("ting:workflow:launch"))
        response = client.post("/build")
        assert response.status_code == 200

    def test_non_bearer_header_admitted(self) -> None:
        client = TestClient(_app_with_scope("ting:workflow:launch"))
        response = client.post("/build", headers={"Authorization": "Basic abc"})
        assert response.status_code == 200
