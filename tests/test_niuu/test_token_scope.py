"""Tests for least-privilege workload-token scope enforcement."""

from __future__ import annotations

import re
import time
from pathlib import Path

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from niuu.domain.services import token_scope
from niuu.domain.services.token_scope import (
    KNOWN_WORKLOAD_SCOPES,
    VALKYRIE_BUILD_TOKEN_USE,
    bound_workload_scopes,
    require_scope,
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


SRC_ROOT = Path(__file__).resolve().parents[2] / "src"


def _enforced_scopes() -> set[str]:
    """Every scope string some route or guard actually checks.

    Read out of the source rather than a registry, because enforcement *is*
    code: `require_scope("x")` on a route is the only thing that gives "x"
    meaning.

    Call sites pass either a literal or a named constant, so bare identifiers
    are resolved against this module's own constants and the caller's — a
    guard that only saw literals would report a false gap.
    """
    call = re.compile(r"""(?:require_scope|token_has_scope)\(\s*([A-Za-z_][\w.]*|["'][^"']+["'])""")
    assignment = re.compile(r"""^([A-Z][A-Z0-9_]*)\s*=\s*["']([^"']+)["']""", re.MULTILINE)
    known = {
        name: value
        for name, value in vars(token_scope).items()
        if name.isupper() and isinstance(value, str)
    }

    found: set[str] = set()
    for path in SRC_ROOT.rglob("*.py"):
        if path.samefile(token_scope.__file__):
            continue  # the definition, not an enforcement point
        text = path.read_text(encoding="utf-8")
        if "require_scope(" not in text and "token_has_scope(" not in text:
            continue
        local = dict(assignment.findall(text))
        for raw in call.findall(text):
            if raw[:1] in {'"', "'"}:
                found.add(raw[1:-1])
                continue
            name = raw.rsplit(".", 1)[-1]
            resolved = local.get(name) or known.get(name)
            if resolved:
                found.add(resolved)
    return found


class TestKnownWorkloadScopes:
    def test_known_workload_scopes_constant(self) -> None:
        """Pinned deliberately: widening what a workload credential may ever be
        granted should be a visible, reviewed change, not a silent one."""
        assert KNOWN_WORKLOAD_SCOPES == frozenset(
            {
                "forge:session:create",
                "ting:workflow:launch",
                "observatory:topology:push",
            }
        )

    def test_every_granted_scope_is_enforced_somewhere(self) -> None:
        """A scope nothing checks is a credential that reads as restricted and
        protects nothing — the drift this guard exists to catch."""
        unenforced = KNOWN_WORKLOAD_SCOPES - _enforced_scopes()

        assert not unenforced, (
            "These scopes can be granted but no route enforces them: "
            f"{sorted(unenforced)}. Either add a require_scope(...) dependency "
            "at the entry point, or remove the scope from KNOWN_WORKLOAD_SCOPES."
        )

    def test_nothing_enforces_a_scope_that_cannot_be_granted(self) -> None:
        """The mirror image: a route demanding a scope issuance always drops
        would 403 every scoped credential that reaches it."""
        ungrantable = _enforced_scopes() - KNOWN_WORKLOAD_SCOPES

        assert not ungrantable, (
            "These scopes are enforced but can never be granted: "
            f"{sorted(ungrantable)}. Add them to KNOWN_WORKLOAD_SCOPES."
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


class TestBoundWorkloadScopes:
    def test_none_returns_empty(self) -> None:
        assert bound_workload_scopes(None) == []

    def test_empty_returns_empty(self) -> None:
        assert bound_workload_scopes([]) == []

    def test_known_scopes_pass_through(self) -> None:
        assert bound_workload_scopes(["forge:session:create"]) == ["forge:session:create"]

    def test_unknown_scopes_dropped(self) -> None:
        result = bound_workload_scopes(
            ["forge:session:create", "forge:session:delete", "*", "admin:everything"]
        )
        assert result == ["forge:session:create"]

    def test_all_unknown_scopes_dropped_to_empty(self) -> None:
        assert bound_workload_scopes(["nope", "also-nope"]) == []

    def test_duplicates_and_whitespace_collapsed(self) -> None:
        result = bound_workload_scopes(
            [
                " forge:session:create ",
                "forge:session:create",
                "ting:workflow:launch",
                "",
            ]
        )
        assert result == ["forge:session:create", "ting:workflow:launch"]


class TestRequireScopeFactory:
    def test_rejects_unknown_scope(self) -> None:
        with pytest.raises(ValueError, match="Unknown workload scope"):
            require_scope("forge:session:delete")


def _app_with_scope(scope: str) -> FastAPI:
    app = FastAPI()

    @app.post("/build")
    async def build(_: None = Depends(require_scope(scope))) -> dict:
        return {"ok": True}

    return app


class TestRequireScopeDependency:
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
