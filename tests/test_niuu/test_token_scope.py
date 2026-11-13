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
    EXCHANGE_GRANTABLE_SCOPES,
    FORGE_NOTIFY_SCOPE,
    FORGE_SESSION_LIFECYCLE_SCOPE,
    FORGE_SESSION_MESSAGE_SCOPE,
    FORGE_SESSION_PREVIEW_SCOPE,
    FORGE_SESSION_READ_SCOPE,
    FORGE_SESSION_SCOPES,
    FORGE_SESSION_SECRET_SCOPE,
    FORGE_SESSION_TOKEN_USE,
    ISSUER_ONLY_SCOPES,
    KNOWN_WORKLOAD_SCOPES,
    SESSION_PREVIEW_PATH,
    VALKYRIE_BUILD_SCOPES,
    VALKYRIE_BUILD_TOKEN_USE,
    bound_workload_scopes,
    claims_have_scope,
    credential_allows_route,
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
    # token_has_scope(token, SCOPE) names its scope second, after the credential.
    token_call = re.compile(
        r"""token_has_scope\(\s*[^,()]*(?:\([^()]*\))?[^,()]*,\s*([A-Za-z_][\w.]*|["'][^"']+["'])"""
    )
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
        for raw in [*call.findall(text), *token_call.findall(text)]:
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
                "forge:mesh:identity",
                "forge:memory",
                "forge:session:create",
                "forge:session:room-role",
                "ting:workflow:coordinate",
                "ting:workflow:launch",
                "observatory:topology:push",
                "node_join",
                "compute_host_join",
                "forge:notify",
                "forge:session:read",
                "forge:session:secret",
                "forge:session:message",
                "forge:session:lifecycle",
                "forge:notifications:deliver",
                "forge:push:call",
                "forge:session:preview",
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


class TestSessionCredentialAtBifrost:
    """A session credential is not a model credential: Bifröst's routes never admit it."""

    @pytest.mark.parametrize(
        "path",
        [
            "/v1/messages",
            "/v1/chat/completions",
            "/v1/responses",
            "/api/v1/bifrost/v1/messages",
            "/api/v1/bifrost/v1/chat/completions",
            "/api/v1/bifrost/v1/responses",
        ],
    )
    def test_refused_on_the_model_routes_with_every_session_scope(self, path: str) -> None:
        token = _session_token(sorted(FORGE_SESSION_SCOPES))
        assert credential_allows_route(token, "POST", path) is False


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

    def test_execution_coordinator_can_call_delivery_surface(self) -> None:
        token = _build_token(["ting:workflow:coordinate"])
        assert credential_allows_route(
            token,
            "POST",
            "/api/v1/forge/delivery/workspaces/verify",
        )
        assert not credential_allows_route(
            token,
            "POST",
            "/api/v1/forge/sessions",
        )

    def test_workflow_launcher_can_call_a2a_jsonrpc_only_with_launch_scope(self) -> None:
        launcher = _build_token(["ting:workflow:launch"])
        unrelated = _build_token(["forge:session:create"])

        assert credential_allows_route(launcher, "POST", "/api/v1/ting/a2a")
        assert not credential_allows_route(unrelated, "POST", "/api/v1/ting/a2a")
        assert not credential_allows_route(launcher, "GET", "/api/v1/ting/a2a")

    @pytest.mark.parametrize("suffix", ["expansions", "messages", "reconcile", "cancel", "waits"])
    def test_execution_coordinator_can_call_generic_execution_mutations(self, suffix: str) -> None:
        token = _build_token(["ting:workflow:coordinate"])

        assert credential_allows_route(
            token,
            "POST",
            f"/api/v1/ting/workflow-executions/execution-1/{suffix}",
        )

    @pytest.mark.parametrize(
        "suffix",
        ["expansions", "integration-candidate", "complete", "delivery-authorizations"],
    )
    def test_execution_coordinator_can_call_delivery_execution_mutations(self, suffix: str) -> None:
        token = _build_token(["ting:workflow:coordinate"])

        assert credential_allows_route(
            token,
            "POST",
            f"/api/v1/ting/delivery-executions/execution-1/{suffix}",
        )

    def test_execution_coordinator_retry_is_post_only_and_gets_remain_denied(self) -> None:
        token = _build_token(["ting:workflow:coordinate"])
        retry = "/api/v1/ting/workflow-executions/execution-1/children/api/retry"
        wait = "/api/v1/ting/workflow-executions/execution-1/waits"

        assert credential_allows_route(token, "POST", retry)
        assert credential_allows_route(token, "POST", wait)
        assert not credential_allows_route(token, "GET", retry)
        assert not credential_allows_route(token, "GET", wait)
        assert not credential_allows_route(
            token, "GET", "/api/v1/ting/delivery-executions/execution-1/evidence"
        )

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


def _session_token(scopes: list[str]) -> str:
    return _encode({"token_use": FORGE_SESSION_TOKEN_USE, "scopes": scopes})


class TestForgeSessionScopes:
    def test_session_scopes_are_known_and_separate_from_build_scopes(self) -> None:
        assert FORGE_SESSION_SCOPES == {
            FORGE_NOTIFY_SCOPE,
            FORGE_SESSION_READ_SCOPE,
            FORGE_SESSION_SECRET_SCOPE,
            FORGE_SESSION_MESSAGE_SCOPE,
            FORGE_SESSION_LIFECYCLE_SCOPE,
            "forge:mesh:identity",
            "forge:memory",
        }
        assert FORGE_SESSION_SCOPES <= KNOWN_WORKLOAD_SCOPES
        assert not FORGE_SESSION_SCOPES & VALKYRIE_BUILD_SCOPES
        assert FORGE_SESSION_TOKEN_USE == "forge_session"

    def test_session_token_requires_scope_check(self) -> None:
        assert token_requires_scope_check({"token_use": FORGE_SESSION_TOKEN_USE}) is True

    def test_session_token_with_scope_allowed(self) -> None:
        token = _session_token([FORGE_NOTIFY_SCOPE, FORGE_SESSION_READ_SCOPE])
        assert token_has_scope(token, FORGE_SESSION_READ_SCOPE) is True

    def test_secret_scope_admits_only_the_owned_secret_routes(self) -> None:
        sid = "0b9e1f7a-1c2d-4e5f-8a9b-0c1d2e3f4a5b"
        base = f"/api/v1/forge/sessions/{sid}/secret-requests"
        with_scope = _session_token([FORGE_SESSION_SECRET_SCOPE])
        without = _session_token([FORGE_NOTIFY_SCOPE, FORGE_SESSION_READ_SCOPE])
        owned = [
            ("POST", base),
            ("GET", base),
            ("GET", f"{base}/r-1"),
            ("POST", f"{base}/r-1/claim"),
            ("POST", f"{base}/r-1/cancel"),
        ]
        for method, path in owned:
            assert credential_allows_route(with_scope, method, path) is True
            assert credential_allows_route(without, method, path) is False
        # Supplying or declining belongs to the owner: no scope opens it to a token.
        for action in ("fulfill", "decline"):
            assert credential_allows_route(with_scope, "POST", f"{base}/r-1/{action}") is False

    def test_session_token_missing_grant_denied(self) -> None:
        token = _session_token([FORGE_NOTIFY_SCOPE, FORGE_SESSION_READ_SCOPE])
        assert token_has_scope(token, FORGE_SESSION_MESSAGE_SCOPE) is False

    def test_session_token_denied_outside_its_family(self) -> None:
        # Deny-by-default: a build-only entry point never admits a session token.
        token = _session_token(sorted(FORGE_SESSION_SCOPES))
        assert token_has_scope(token, "forge:session:create") is False
        assert token_has_scope(token, "observatory:topology:push") is False

    def test_session_token_cannot_smuggle_build_scopes(self) -> None:
        token = _session_token(["forge:session:create"])
        assert token_has_scope(token, "forge:session:create") is False

    def test_build_token_is_unaffected_by_session_scope_checks(self) -> None:
        # Session scopes guard routes a build token already used; it keeps working.
        token = _build_token(["forge:session:create"])
        assert token_has_scope(token, FORGE_SESSION_READ_SCOPE) is True

    def test_claims_have_scope_any_of(self) -> None:
        claims = {"token_use": FORGE_SESSION_TOKEN_USE, "scopes": [FORGE_SESSION_LIFECYCLE_SCOPE]}
        assert claims_have_scope(claims, ("forge:session:create", FORGE_SESSION_LIFECYCLE_SCOPE))
        build = {"token_use": VALKYRIE_BUILD_TOKEN_USE, "scopes": ["forge:session:create"]}
        assert claims_have_scope(build, ("forge:session:create", FORGE_SESSION_LIFECYCLE_SCOPE))
        assert claims_have_scope({"type": "pat"}, (FORGE_NOTIFY_SCOPE,))

    def test_malformed_session_scopes_grant_nothing(self) -> None:
        claims = {"token_use": FORGE_SESSION_TOKEN_USE, "scopes": FORGE_NOTIFY_SCOPE}
        assert claims_have_scope(claims, (FORGE_NOTIFY_SCOPE,)) is False

    def test_session_token_admitted_only_on_the_forge_allow_list(self) -> None:
        session_id = "0b8f5e5a-6f5c-4a3e-9d59-3f1c2b7a9e10"
        token = _session_token([FORGE_NOTIFY_SCOPE, FORGE_SESSION_READ_SCOPE])

        assert credential_allows_route(token, "GET", "/api/v1/forge/sessions")
        assert credential_allows_route(
            token, "POST", f"/api/v1/forge/sessions/{session_id}/notifications"
        )
        assert credential_allows_route(token, "POST", "/api/v1/forge/mcp")
        assert credential_allows_route(
            token, "PUT", f"/api/v1/forge/sessions/{session_id}/description"
        )
        assert not credential_allows_route(token, "PUT", f"/api/v1/forge/sessions/{session_id}")
        # Missing grant, a route off the allow-list, and the identity route.
        assert not credential_allows_route(
            token, "POST", f"/api/v1/forge/sessions/{session_id}/messages"
        )
        assert not credential_allows_route(token, "DELETE", f"/api/v1/forge/sessions/{session_id}")
        assert not credential_allows_route(token, "GET", "/api/v1/identity/me")
        assert not credential_allows_route(token, "POST", "/api/v1/ting/a2a")

    def test_session_token_websocket_upgrade_is_checked_as_get(self) -> None:
        token = _session_token([FORGE_SESSION_READ_SCOPE])
        assert credential_allows_route(token, "WEBSOCKET", "/api/v1/forge/notifications")
        assert not credential_allows_route(
            _session_token([]), "WEBSOCKET", "/api/v1/forge/sessions"
        )

    def test_pats_cannot_carry_session_scopes(self) -> None:
        assert token_scope.validate_pat_scopes(["forge:session:create"]) == (
            "forge:session:create",
        )
        with pytest.raises(ValueError, match="PAT scopes"):
            token_scope.validate_pat_scopes([FORGE_NOTIFY_SCOPE])

    def test_scoped_pat_follows_the_build_family(self) -> None:
        pat = {"type": "pat", "scope": "ting:workflow:launch"}
        assert token_requires_scope_check(pat) is True
        assert claims_have_scope(pat, ("ting:workflow:launch",))
        assert not claims_have_scope(pat, ("forge:session:create",))
        # A session-only check does not apply to the build family.
        assert claims_have_scope(pat, (FORGE_SESSION_READ_SCOPE,))

    def test_bound_scopes_to_one_family(self) -> None:
        requested = ["forge:session:create", FORGE_NOTIFY_SCOPE]
        assert bound_workload_scopes(requested, allowed=VALKYRIE_BUILD_SCOPES) == [
            "forge:session:create"
        ]
        assert bound_workload_scopes(requested, allowed=FORGE_SESSION_SCOPES) == [
            FORGE_NOTIFY_SCOPE
        ]


class TestRequireScopeAlternatives:
    def _app(self) -> FastAPI:
        app = FastAPI()

        @app.post("/sessions")
        async def create(
            _: None = Depends(require_scope("forge:session:create", FORGE_SESSION_LIFECYCLE_SCOPE)),
        ) -> dict:
            return {"ok": True}

        return app

    def test_rejects_an_unknown_alternative(self) -> None:
        with pytest.raises(ValueError, match="Unknown workload scope"):
            require_scope("forge:session:create", "forge:session:delete")

    def test_each_family_needs_its_own_scope(self) -> None:
        client = TestClient(self._app())

        def post(token: str) -> int:
            return client.post(
                "/sessions", headers={"Authorization": f"Bearer {token}"}
            ).status_code

        assert post(_build_token(["forge:session:create"])) == 200
        assert post(_build_token(["ting:workflow:launch"])) == 403
        assert post(_session_token([FORGE_SESSION_LIFECYCLE_SCOPE])) == 200
        response = client.post(
            "/sessions",
            headers={"Authorization": f"Bearer {_session_token([FORGE_NOTIFY_SCOPE])}"},
        )
        assert response.status_code == 403
        assert "forge:session:create or forge:session:lifecycle" in response.json()["detail"]


class TestTrackerIssueRoutes:
    """Ravn platform tools exchange workload tokens without requesting scopes, so
    residents reach the tracker API with an unscoped token; scoped credentials stay
    confined to their own entry points and gain no tracker access."""

    def test_unscoped_workload_token_may_create_and_read_issues(self) -> None:
        token = _encode({"workload_subject": "system:serviceaccount:nats:regin"})
        assert credential_allows_route(token, "POST", "/api/v1/tracker/issues")
        assert credential_allows_route(token, "GET", "/api/v1/tracker/issues/NIU-1092")

    def test_scoped_workload_token_cannot_create_issues(self) -> None:
        token = _build_token(["forge:session:create"])
        assert not credential_allows_route(token, "POST", "/api/v1/tracker/issues")


def _delivery_token(scopes: list[str] | None = None) -> str:
    return _encode(
        {
            "token_use": token_scope.NOTIFICATION_DELIVERY_TOKEN_USE,
            "scopes": scopes if scopes is not None else [token_scope.NOTIFICATION_DELIVERY_SCOPE],
        }
    )


class TestNotificationDeliveryScope:
    """The push gateway's cross-tenant reader is admitted on its two reads and nowhere else."""

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/api/v1/forge/notifications"),
            ("GET", "/api/v1/niuu/instances"),
            ("GET", "/api/v1/identity/me"),
        ],
    )
    def test_admitted_on_its_entry_points(self, method, path) -> None:
        assert credential_allows_route(_delivery_token(), method, path) is True

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("PUT", "/api/v1/forge/notifications/read-state"),
            ("GET", "/api/v1/forge/notifications/abc/image"),
            ("GET", "/api/v1/forge/sessions"),
            ("GET", "/api/v1/forge/sessions/stream"),
            ("POST", "/api/v1/forge/devices"),
            ("GET", "/api/v1/integrations"),
            ("DELETE", "/api/v1/niuu/instances/x"),
        ],
    )
    def test_denied_everywhere_else(self, method, path) -> None:
        assert credential_allows_route(_delivery_token(), method, path) is False

    def test_the_marker_without_the_scope_is_admitted_nowhere(self) -> None:
        token = _delivery_token(scopes=[])
        assert credential_allows_route(token, "GET", "/api/v1/forge/notifications") is False
        assert token_scope.is_notification_delivery_token(token) is False

    def test_it_is_recognised(self) -> None:
        assert token_scope.is_notification_delivery_token(_delivery_token()) is True
        assert token_scope.is_notification_delivery_token(_build_token(["node_join"])) is False
        assert token_scope.is_notification_delivery_token("") is False

    def test_a_build_token_cannot_carry_it(self) -> None:
        token = _build_token([token_scope.NOTIFICATION_DELIVERY_SCOPE])
        assert token_scope.is_notification_delivery_token(token) is False
        assert credential_allows_route(token, "GET", "/api/v1/forge/notifications") is False

    def test_a_pat_cannot_carry_it(self) -> None:
        with pytest.raises(ValueError):
            token_scope.validate_pat_scopes([token_scope.NOTIFICATION_DELIVERY_SCOPE])

    def test_require_scope_admits_it_only_where_named(self) -> None:
        feed = require_scope(FORGE_SESSION_READ_SCOPE, token_scope.NOTIFICATION_DELIVERY_SCOPE)
        app = FastAPI()

        @app.get("/feed")
        async def read_feed(_: None = Depends(feed)) -> dict:
            return {"ok": True}

        @app.get("/sessions")
        async def sessions(_: None = Depends(require_scope(FORGE_SESSION_READ_SCOPE))) -> dict:
            return {"ok": True}

        client = TestClient(app)
        headers = {"Authorization": f"Bearer {_delivery_token()}"}
        assert client.get("/feed", headers=headers).status_code == 200
        # Deny by default: a check naming no scope of its family refuses it.
        assert client.get("/sessions", headers=headers).status_code == 403
        # A session credential still reads the feed with its own scope.
        session = {"Authorization": f"Bearer {_session_token([FORGE_SESSION_READ_SCOPE])}"}
        assert client.get("/feed", headers=session).status_code == 200


class TestSessionPreviewScope:
    """``forge:session:preview``: the live-preview router's credential for one Skuld."""

    def test_it_is_issuer_only(self) -> None:
        assert FORGE_SESSION_PREVIEW_SCOPE == "forge:session:preview"
        assert FORGE_SESSION_PREVIEW_SCOPE in VALKYRIE_BUILD_SCOPES
        assert ISSUER_ONLY_SCOPES == frozenset(
            {FORGE_SESSION_PREVIEW_SCOPE, token_scope.COMPUTE_HOST_JOIN_SCOPE}
        )
        assert EXCHANGE_GRANTABLE_SCOPES == VALKYRIE_BUILD_SCOPES - ISSUER_ONLY_SCOPES
        assert FORGE_SESSION_PREVIEW_SCOPE not in EXCHANGE_GRANTABLE_SCOPES

    def test_bounding_to_the_exchange_set_drops_it(self) -> None:
        kept = bound_workload_scopes(
            ["forge:session:create", FORGE_SESSION_PREVIEW_SCOPE],
            allowed=EXCHANGE_GRANTABLE_SCOPES,
        )
        assert kept == ["forge:session:create"]

    @pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
    @pytest.mark.parametrize(
        "path",
        ["/preview/3000", "/preview/3000/", "/preview/5173/@vite/client", "/preview/80/a/b/c"],
    )
    def test_a_scoped_token_reaches_preview_paths_with_any_method(
        self, method: str, path: str
    ) -> None:
        token = _build_token([FORGE_SESSION_PREVIEW_SCOPE])
        assert credential_allows_route(token, method, path) is True

    def test_websocket_upgrades_are_admitted(self) -> None:
        token = _build_token([FORGE_SESSION_PREVIEW_SCOPE])
        assert credential_allows_route(token, "WEBSOCKET", "/preview/3000/hmr") is True

    @pytest.mark.parametrize(
        "path",
        [
            "/",
            "/preview",
            "/preview/",
            "/preview/abc",
            "/preview/3000x",
            "/previews/3000/",
            "/api/preview/3000",
            "/s/abc/preview/3000/",
            "/api/v1/forge/sessions",
            "/api/conversation/history",
        ],
    )
    def test_it_is_refused_everywhere_else(self, path: str) -> None:
        token = _build_token([FORGE_SESSION_PREVIEW_SCOPE])
        assert credential_allows_route(token, "GET", path) is False
        assert credential_allows_route(token, "WEBSOCKET", path) is False

    def test_a_token_without_the_scope_is_refused_on_preview_paths(self) -> None:
        token = _build_token(["forge:session:create", FORGE_NOTIFY_SCOPE])
        assert credential_allows_route(token, "GET", "/preview/3000/") is False
        assert credential_allows_route(_build_token([]), "GET", "/preview/3000/") is False

    def test_a_forge_session_token_cannot_reach_preview_paths(self) -> None:
        token = _session_token(sorted(FORGE_SESSION_SCOPES))
        assert credential_allows_route(token, "GET", "/preview/3000/") is False

    def test_an_unscoped_token_is_unaffected(self) -> None:
        assert credential_allows_route(_encode({}), "GET", "/preview/3000/") is True

    def test_the_path_pattern_matches_only_whole_preview_paths(self) -> None:
        assert SESSION_PREVIEW_PATH.fullmatch("/preview/443/x")
        assert not SESSION_PREVIEW_PATH.fullmatch("/preview/443x")
        assert not SESSION_PREVIEW_PATH.fullmatch("/x/preview/443")

    def test_skuld_enforces_it_through_token_has_scope(self) -> None:
        # The enforcement scan finds skuld.preview_adapters' token_has_scope(...) call.
        assert FORGE_SESSION_PREVIEW_SCOPE in _enforced_scopes()
        assert token_has_scope(
            _build_token([FORGE_SESSION_PREVIEW_SCOPE]), FORGE_SESSION_PREVIEW_SCOPE
        )
        assert not token_has_scope(_build_token([FORGE_NOTIFY_SCOPE]), FORGE_SESSION_PREVIEW_SCOPE)
