"""Tests for Mímir's auth_mode contract (host without Envoy).

Covers:
- MimirRouter._require_deploy_auth / enforce_instance_tenant read identity
  from the configured HeaderAuthenticationPort, never x-auth-* headers
  directly — a spoofed header must not work once a verifying adapter
  (JWKS) is configured, and a verified bearer token must.
- The startup guard (niuu.service_runtime._validate_identity_adapter_class)
  refuses an Envoy-trusting adapter under auth_mode: oidc/none.
- auth_mode: none (AllowAllHeaderAuthenticationAdapter) behaves as an
  explicit, unrestricted no-auth host, same as before this change.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock

import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from jwt.algorithms import RSAAlgorithm

from identity.adapters.identity import (
    AllowAllHeaderAuthenticationAdapter,
    EnvoyHeaderAuthenticationAdapter,
)
from mimir.adapters.markdown import MarkdownMimirAdapter
from mimir.app import create_app
from mimir.config import MimirServiceConfig
from mimir.router import MimirRouter

ISSUER = "https://idp.test/realms/niuu"
AUDIENCE = "mimir"
JWKS_URI = f"{ISSUER}/jwks"
KID = "mimir-test-key"
PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC_JWK = json.loads(RSAAlgorithm.to_jwk(PRIVATE_KEY.public_key()))
PUBLIC_JWK.update({"kid": KID, "use": "sig", "alg": "RS256"})

_OIDC_ISSUER_KWARGS = {
    "issuers": [{"issuer": ISSUER, "audiences": [AUDIENCE], "jwks_uri": JWKS_URI}],
}


def _bearer_token(*, sub: str = "alice", tenant_id: str = "acme", roles: str = "") -> str:
    now = int(time.time())
    claims = {
        "sub": sub,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": now + 300,
        "iat": now,
        "tenant_id": tenant_id,
        "resource_access": {"volundr": {"roles": roles.split(",") if roles else []}},
    }
    return jwt.encode(claims, PRIVATE_KEY, algorithm="RS256", headers={"kid": KID})


ADMIN_SPOOF_HEADERS = {
    "x-auth-user-id": "admin",
    "x-auth-roles": "volundr:admin",
    "x-auth-tenant": "tenant-a",
}


def _jwks_adapter():
    from identity.adapters.jwks import JwksBearerAuthenticationAdapter

    return JwksBearerAuthenticationAdapter(**_OIDC_ISSUER_KWARGS)


def _jwks_adapter_with_role_mapping():
    """Matches what the CLI actually wires for MIMIR_AUTH__KWARGS under

    oidc — cli.config._oidc_adapter_kwargs()'s role_mapping, same as
    Völundr's chart default (charts/volundr/values.yaml identity.roleMapping).
    """
    from identity.adapters.jwks import JwksBearerAuthenticationAdapter

    return JwksBearerAuthenticationAdapter(
        **_OIDC_ISSUER_KWARGS,
        role_mapping={
            "admin": "volundr:admin",
            "developer": "volundr:developer",
            "viewer": "volundr:viewer",
        },
    )


# ---------------------------------------------------------------------------
# MimirRouter._require_deploy_auth (deploy-mount admin routes)
# ---------------------------------------------------------------------------


class TestRequireDeployAuth:
    def _app(self, tmp_path: Path, *, auth) -> FastAPI:
        deployment = AsyncMock()
        deployment.list_deployments.return_value = {"releases": []}
        app = FastAPI()
        app.include_router(
            MimirRouter(
                MarkdownMimirAdapter(root=tmp_path),
                deployment=deployment,
                auth=auth,
            ).router
        )
        return app

    def test_envoy_adapter_rejects_spoofed_admin_without_real_gateway(self, tmp_path: Path) -> None:
        """Baseline: an explicitly-wired EnvoyHeaderAuthenticationAdapter trusts

        x-auth-* headers like today — `auth` is a required constructor
        argument (no implicit default), so every caller must wire this
        explicitly; see test_router_construction_requires_an_explicit_auth
        below for the "no default" contract itself.
        """
        app = self._app(tmp_path, auth=EnvoyHeaderAuthenticationAdapter())
        with TestClient(app) as client:
            assert client.get("/deployments").status_code == 403
            assert client.get("/deployments", headers=ADMIN_SPOOF_HEADERS).status_code == 200

    @respx.mock
    def test_oidc_adapter_ignores_spoofed_x_auth_headers(self, tmp_path: Path) -> None:
        """A caller cannot forge admin access by sending x-auth-* headers once

        the router is wired to a signature-verifying adapter (auth_mode: oidc).
        """
        respx.get(JWKS_URI).mock(return_value=Response(200, json={"keys": [PUBLIC_JWK]}))
        app = self._app(tmp_path, auth=_jwks_adapter())
        with TestClient(app) as client:
            resp = client.get("/deployments", headers=ADMIN_SPOOF_HEADERS)
            assert resp.status_code == 403

    @respx.mock
    def test_oidc_adapter_accepts_verified_admin_bearer(self, tmp_path: Path) -> None:
        respx.get(JWKS_URI).mock(return_value=Response(200, json={"keys": [PUBLIC_JWK]}))
        app = self._app(tmp_path, auth=_jwks_adapter())
        token = _bearer_token(roles="volundr:admin")
        with TestClient(app) as client:
            resp = client.get("/deployments", headers={"authorization": f"Bearer {token}"})
            assert resp.status_code == 200

    @respx.mock
    def test_oidc_adapter_rejects_verified_non_admin_bearer(self, tmp_path: Path) -> None:
        respx.get(JWKS_URI).mock(return_value=Response(200, json={"keys": [PUBLIC_JWK]}))
        app = self._app(tmp_path, auth=_jwks_adapter())
        token = _bearer_token(roles="volundr:viewer")
        with TestClient(app) as client:
            resp = client.get("/deployments", headers={"authorization": f"Bearer {token}"})
            assert resp.status_code == 403

    def test_none_mode_allow_all_grants_admin_unconditionally(self, tmp_path: Path) -> None:
        app = self._app(tmp_path, auth=AllowAllHeaderAuthenticationAdapter())
        with TestClient(app) as client:
            assert client.get("/deployments").status_code == 200


# ---------------------------------------------------------------------------
# _request_scope's write-gate: a verified-but-tenant-less caller (router.py's
# "An authenticated tenant is required for knowledge changes") is refused on
# writes but not on reads — distinct from the anonymous case.
# ---------------------------------------------------------------------------


class TestRequestScopeTenantlessWriteGate:
    @respx.mock
    def test_tenantless_verified_token_rejected_on_write(self, tmp_path: Path) -> None:
        respx.get(JWKS_URI).mock(return_value=Response(200, json={"keys": [PUBLIC_JWK]}))
        app = FastAPI()
        app.include_router(
            MimirRouter(MarkdownMimirAdapter(root=tmp_path), auth=_jwks_adapter()).router
        )
        # A write role is granted so _require_write_auth's separate role
        # check cannot also explain the 403 — this isolates _request_scope's
        # own "no tenant" rule (router.py's WRITE_ROLES check requires a
        # tenant too, but for a different reason; both independently refuse
        # a tenant-less writer, which is the point).
        token = _bearer_token(tenant_id="", roles="volundr:developer")
        with TestClient(app) as client:
            resp = client.put(
                "/page",
                headers={"authorization": f"Bearer {token}"},
                json={"path": "x.md", "content": "hi"},
            )
            assert resp.status_code == 403

    @respx.mock
    def test_tenantless_verified_token_still_allowed_on_read(self, tmp_path: Path) -> None:
        respx.get(JWKS_URI).mock(return_value=Response(200, json={"keys": [PUBLIC_JWK]}))
        app = FastAPI()
        app.include_router(
            MimirRouter(MarkdownMimirAdapter(root=tmp_path), auth=_jwks_adapter()).router
        )
        token = _bearer_token(tenant_id="")
        with TestClient(app) as client:
            resp = client.get("/stats", headers={"authorization": f"Bearer {token}"})
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# _require_write_auth's WRITE_ROLES gate applies under auth_mode: oidc only.
# ymir's knowledge warden and valhalla's Muninn write to their Mímir
# instances directly today with no credential at all (auth_mode: envoy, no
# Envoy actually in front of them there), and Keycloak web-UI users reach
# Mímir with Envoy's *raw* resource_access roles (Mímir's own
# EnvoyHeaderAuthenticationAdapter never gets Völundr's role_mapping) — so
# enforcing WRITE_ROLES under envoy today would 403 all of them. See
# MimirRouter._require_write_auth's docstring for the full rationale and the
# tracked follow-up (Envoy + a workload credential for the warden/Muninn,
# plus role_mapping for Mímir's own Envoy identity adapter).
# ---------------------------------------------------------------------------


class TestWriteRoleGateScopedToOidc:
    def _app(self, tmp_path: Path, *, auth, auth_mode: str) -> FastAPI:
        app = FastAPI()
        app.include_router(
            MimirRouter(MarkdownMimirAdapter(root=tmp_path), auth=auth, auth_mode=auth_mode).router
        )
        return app

    def test_envoy_mode_write_allowed_with_no_headers_at_all(self, tmp_path: Path) -> None:
        """Matches ymir's warden / valhalla's Muninn today: no credential,

        envoy mode, write still succeeds — _require_write_auth is a no-op
        here, same as before this round's role gate existed.
        """
        app = self._app(tmp_path, auth=EnvoyHeaderAuthenticationAdapter(), auth_mode="envoy")
        with TestClient(app) as client:
            resp = client.put("/page", json={"path": "x.md", "content": "hi"})
            assert resp.status_code == 204

    def test_envoy_mode_write_allowed_with_authenticated_but_role_less_caller(
        self, tmp_path: Path
    ) -> None:
        """A Keycloak user whose raw role is 'developer' (not mapped to

        'volundr:developer' for Mímir's own Envoy identity adapter) must
        keep writing — this is literally 'no role WRITE_ROLES recognises'
        from Mímir's point of view, and envoy mode must not 403 it.
        """
        app = self._app(tmp_path, auth=EnvoyHeaderAuthenticationAdapter(), auth_mode="envoy")
        with TestClient(app) as client:
            resp = client.put(
                "/page",
                headers={
                    "x-auth-user-id": "keycloak-user",
                    "x-auth-tenant": "tenant-a",
                    "x-auth-roles": "developer",  # raw claim, unmapped
                },
                json={"path": "x.md", "content": "hi"},
            )
            assert resp.status_code == 204

    @respx.mock
    def test_oidc_mode_rejects_a_tenanted_caller_with_no_write_role(self, tmp_path: Path) -> None:
        respx.get(JWKS_URI).mock(return_value=Response(200, json={"keys": [PUBLIC_JWK]}))
        app = self._app(tmp_path, auth=_jwks_adapter(), auth_mode="oidc")
        token = _bearer_token()  # tenant set, no roles claim
        with TestClient(app) as client:
            resp = client.put(
                "/page",
                headers={"authorization": f"Bearer {token}"},
                json={"path": "x.md", "content": "hi"},
            )
            assert resp.status_code == 403

    @respx.mock
    def test_oidc_mode_accepts_the_raw_keycloak_developer_role(self, tmp_path: Path) -> None:
        """The CLI's MIMIR_AUTH__KWARGS carries the same role_mapping as

        Völundr's chart default (cli.config._DEFAULT_OIDC_ROLE_MAPPING) —
        so under oidc, a raw 'developer' claim already arrives here mapped
        to 'volundr:developer' and satisfies WRITE_ROLES; no extra raw-role
        entry is needed for the oidc path (unlike the envoy follow-up).
        """
        respx.get(JWKS_URI).mock(return_value=Response(200, json={"keys": [PUBLIC_JWK]}))
        mapped_adapter = _jwks_adapter_with_role_mapping()
        app = self._app(tmp_path, auth=mapped_adapter, auth_mode="oidc")
        token = _bearer_token(roles="developer")  # raw claim, as Keycloak sends it
        with TestClient(app) as client:
            resp = client.put(
                "/page",
                headers={"authorization": f"Bearer {token}"},
                json={"path": "x.md", "content": "hi"},
            )
            assert resp.status_code == 204


# ---------------------------------------------------------------------------
# mimir.app.create_app — startup guard + enforce_instance_tenant middleware
# ---------------------------------------------------------------------------


class TestCreateAppAuthModeGuard:
    def test_default_envoy_config_starts_fine(self, tmp_path: Path) -> None:
        create_app(MimirServiceConfig(path=str(tmp_path / "mimir")))  # must not raise

    def test_oidc_mode_with_envoy_trusting_adapter_refuses_to_start(self, tmp_path: Path) -> None:
        config = MimirServiceConfig(
            path=str(tmp_path / "mimir"),
            auth_mode="oidc",
            identity_adapter=("identity.adapters.identity.EnvoyHeaderAuthenticationAdapter"),
        )
        with pytest.raises(ValueError, match="trusts x-auth-\\* headers"):
            create_app(config)

    def test_none_mode_with_envoy_trusting_adapter_refuses_to_start(self, tmp_path: Path) -> None:
        config = MimirServiceConfig(
            path=str(tmp_path / "mimir"),
            auth_mode="none",
            identity_adapter=("identity.adapters.identity.EnvoyHeaderAuthenticationAdapter"),
        )
        with pytest.raises(ValueError, match="trusts x-auth-\\* headers"):
            create_app(config)

    def test_oidc_mode_with_allow_all_adapter_refuses_to_start(self, tmp_path: Path) -> None:
        config = MimirServiceConfig(
            path=str(tmp_path / "mimir"),
            auth_mode="oidc",
            identity_adapter="identity.adapters.identity.AllowAllHeaderAuthenticationAdapter",
        )
        with pytest.raises(ValueError, match="verifies a bearer"):
            create_app(config)

    def test_oidc_mode_with_jwks_adapter_starts_fine(self, tmp_path: Path) -> None:
        config = MimirServiceConfig(
            path=str(tmp_path / "mimir"),
            auth_mode="oidc",
            identity_adapter="identity.adapters.jwks.JwksBearerAuthenticationAdapter",
            identity_kwargs=_OIDC_ISSUER_KWARGS,
        )
        create_app(config)  # must not raise

    def test_none_mode_with_allow_all_adapter_starts_fine(self, tmp_path: Path) -> None:
        config = MimirServiceConfig(
            path=str(tmp_path / "mimir"),
            auth_mode="none",
            identity_adapter="identity.adapters.identity.AllowAllHeaderAuthenticationAdapter",
        )
        create_app(config)  # must not raise


class TestEnforceInstanceTenant:
    def test_default_envoy_config_matches_today_header_based_behaviour(
        self, tmp_path: Path
    ) -> None:
        config = MimirServiceConfig(path=str(tmp_path / "mimir"), tenant_id="tenant-a")
        app = create_app(config)
        with TestClient(app) as client:
            assert client.get("/mimir/stats").status_code == 403
            ok = client.get(
                "/mimir/stats",
                headers={"x-auth-user-id": "u", "x-auth-tenant": "tenant-a"},
            )
            assert ok.status_code == 200
            wrong_tenant = client.get(
                "/mimir/stats",
                headers={"x-auth-user-id": "u", "x-auth-tenant": "tenant-b"},
            )
            assert wrong_tenant.status_code == 403

    @respx.mock
    def test_oidc_mode_rejects_spoofed_tenant_header(self, tmp_path: Path) -> None:
        respx.get(JWKS_URI).mock(return_value=Response(200, json={"keys": [PUBLIC_JWK]}))
        config = MimirServiceConfig(
            path=str(tmp_path / "mimir"),
            tenant_id="tenant-a",
            auth_mode="oidc",
            identity_adapter="identity.adapters.jwks.JwksBearerAuthenticationAdapter",
            identity_kwargs=_OIDC_ISSUER_KWARGS,
        )
        app = create_app(config)
        with TestClient(app) as client:
            # No real credential — just the spoofed header from the old attack.
            # Rejected as unauthenticated (401) now, before tenant-matching
            # even runs — the app-wide oidc gate (enforce_identity) requires
            # a verified principal for every non-health path; see
            # TestAppLevelOidcGate for that gate's own dedicated coverage.
            resp = client.get("/mimir/stats", headers={"x-auth-tenant": "tenant-a"})
            assert resp.status_code == 401

    @respx.mock
    def test_oidc_mode_accepts_verified_matching_tenant(self, tmp_path: Path) -> None:
        respx.get(JWKS_URI).mock(return_value=Response(200, json={"keys": [PUBLIC_JWK]}))
        config = MimirServiceConfig(
            path=str(tmp_path / "mimir"),
            tenant_id="acme",
            auth_mode="oidc",
            identity_adapter="identity.adapters.jwks.JwksBearerAuthenticationAdapter",
            identity_kwargs=_OIDC_ISSUER_KWARGS,
        )
        app = create_app(config)
        token = _bearer_token(tenant_id="acme")
        with TestClient(app) as client:
            resp = client.get("/mimir/stats", headers={"authorization": f"Bearer {token}"})
            assert resp.status_code == 200

    @respx.mock
    def test_oidc_mode_rejects_verified_mismatched_tenant(self, tmp_path: Path) -> None:
        respx.get(JWKS_URI).mock(return_value=Response(200, json={"keys": [PUBLIC_JWK]}))
        config = MimirServiceConfig(
            path=str(tmp_path / "mimir"),
            tenant_id="acme",
            auth_mode="oidc",
            identity_adapter="identity.adapters.jwks.JwksBearerAuthenticationAdapter",
            identity_kwargs=_OIDC_ISSUER_KWARGS,
        )
        app = create_app(config)
        token = _bearer_token(tenant_id="someone-else")
        with TestClient(app) as client:
            resp = client.get("/mimir/stats", headers={"authorization": f"Bearer {token}"})
            assert resp.status_code == 403

    def test_none_mode_has_no_tenant_owner_configured_by_default(self, tmp_path: Path) -> None:
        """auth_mode: none deployments typically leave tenant_id unset, so the

        tenant middleware no-ops entirely — same as today's mini/local dev.
        """
        config = MimirServiceConfig(
            path=str(tmp_path / "mimir"),
            auth_mode="none",
            identity_adapter="identity.adapters.identity.AllowAllHeaderAuthenticationAdapter",
        )
        app = create_app(config)
        with TestClient(app) as client:
            assert client.get("/mimir/stats").status_code == 200


# ---------------------------------------------------------------------------
# auth_mode: none must keep MimirRouter's tenant "" (host operator), not the
# allow-all adapter's fixed "default" tenant — otherwise local-mount writes
# start getting tenant-scoped (422) and entries created under the old,
# real "" tenant become unreachable for update/delete (404) purely because
# an identity adapter now exists where none did before.
# ---------------------------------------------------------------------------


class TestNoneModeRegistryTenantScoping:
    def _app(self, tmp_path: Path) -> FastAPI:
        config = MimirServiceConfig(
            path=str(tmp_path / "mimir"),
            auth_mode="none",
            identity_adapter="identity.adapters.identity.AllowAllHeaderAuthenticationAdapter",
        )
        return create_app(config)

    def test_local_mount_registry_create_succeeds(self, tmp_path: Path) -> None:
        with TestClient(self._app(tmp_path)) as client:
            resp = client.post(
                "/mimir/registry/mounts",
                json={"name": "other", "kind": "local", "path": str(tmp_path / "other-wiki")},
            )
            assert resp.status_code == 200

    def test_global_entry_update_and_delete_still_find_it(self, tmp_path: Path) -> None:
        with TestClient(self._app(tmp_path)) as client:
            created = client.post(
                "/mimir/registry/mounts",
                json={"name": "other", "kind": "local", "path": str(tmp_path / "other-wiki")},
            ).json()
            entry_id = created["id"]

            updated = client.put(
                f"/mimir/registry/mounts/{entry_id}",
                json={
                    "name": "renamed",
                    "kind": "local",
                    "path": str(tmp_path / "other-wiki"),
                },
            )
            assert updated.status_code == 200
            assert updated.json()["name"] == "renamed"

            deleted = client.delete(f"/mimir/registry/mounts/{entry_id}")
            assert deleted.status_code == 204


# ---------------------------------------------------------------------------
# App-wide oidc gate (mimir.app.create_app's enforce_identity middleware):
# every non-health path — REST and MCP alike — requires a verified principal.
# Regression coverage for a real vulnerability: with no gate, an anonymous
# caller under oidc could read /mimir/stats, register a LOCAL registry mount
# naming an arbitrary host path (e.g. "/etc"), and call MCP write tools —
# all with zero credential, because _verified_principal/_request_scope treat
# a missing/invalid token as merely anonymous rather than refused, and
# _require_write_auth is a no-op.
# ---------------------------------------------------------------------------


class TestAppLevelOidcGate:
    def _app(self, tmp_path: Path) -> FastAPI:
        """A single-tenant local Mímir under oidc — no config.tenant_id, the

        shape the vulnerable probe used (an operator's own local instance,
        not a Guild-managed multi-tenant one).
        """
        config = MimirServiceConfig(
            path=str(tmp_path / "mimir"),
            auth_mode="oidc",
            identity_adapter="identity.adapters.jwks.JwksBearerAuthenticationAdapter",
            identity_kwargs=_OIDC_ISSUER_KWARGS,
        )
        return create_app(config)

    @respx.mock
    def test_anonymous_stats_read_is_rejected(self, tmp_path: Path) -> None:
        with TestClient(self._app(tmp_path)) as client:
            resp = client.get("/mimir/stats")
            assert resp.status_code == 401

    @respx.mock
    def test_anonymous_local_mount_registry_write_is_rejected(self, tmp_path: Path) -> None:
        """The exact probe that used to 200 and create a host-path mount."""
        with TestClient(self._app(tmp_path)) as client:
            resp = client.post("/mimir/registry/mounts", json={"kind": "local", "path": "/etc"})
            assert resp.status_code == 401

    @respx.mock
    def test_anonymous_mcp_write_call_is_rejected(self, tmp_path: Path) -> None:
        with TestClient(self._app(tmp_path)) as client:
            resp = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "mimir_write",
                        "arguments": {"path": "x.md", "content": "hi"},
                    },
                },
            )
            assert resp.status_code == 401

    @respx.mock
    def test_invalid_bearer_is_401_never_treated_as_anonymous(self, tmp_path: Path) -> None:
        with TestClient(self._app(tmp_path)) as client:
            resp = client.get("/mimir/stats", headers={"authorization": "Bearer not-a-jwt"})
            assert resp.status_code == 401

    @respx.mock
    def test_valid_token_stats_read_succeeds(self, tmp_path: Path) -> None:
        respx.get(JWKS_URI).mock(return_value=Response(200, json={"keys": [PUBLIC_JWK]}))
        token = _bearer_token()
        with TestClient(self._app(tmp_path)) as client:
            resp = client.get("/mimir/stats", headers={"authorization": f"Bearer {token}"})
            assert resp.status_code == 200

    @respx.mock
    def test_valid_token_mcp_write_call_reaches_the_tool(self, tmp_path: Path) -> None:
        """Authentication passes (no 401) — the tool call itself is handled,

        distinct from the anonymous case which never reaches MCP dispatch.
        """
        respx.get(JWKS_URI).mock(return_value=Response(200, json={"keys": [PUBLIC_JWK]}))
        token = _bearer_token(roles="volundr:developer")
        with TestClient(self._app(tmp_path)) as client:
            resp = client.post(
                "/mcp",
                headers={"authorization": f"Bearer {token}"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "mimir_write",
                        "arguments": {"path": "x.md", "content": "hi"},
                    },
                },
            )
            assert resp.status_code == 200
            assert "error" not in resp.json()

    @respx.mock
    def test_valid_tenanted_token_registry_write_passes_auth_but_local_mount_still_scoped(
        self, tmp_path: Path
    ) -> None:
        """A valid credential clears the new authentication gate (no 401);

        the same malicious local-path payload is then still correctly
        rejected by Mímir's existing tenant-scoping (a tenant-scoped caller
        cannot register a host-path mount) — a 422, not a 401. Authentication
        and authorization are separate failures here and this pins both.
        """
        respx.get(JWKS_URI).mock(return_value=Response(200, json={"keys": [PUBLIC_JWK]}))
        token = _bearer_token(tenant_id="acme", roles="volundr:developer")
        with TestClient(self._app(tmp_path)) as client:
            resp = client.post(
                "/mimir/registry/mounts",
                headers={"authorization": f"Bearer {token}"},
                json={"kind": "local", "path": "/etc"},
            )
            assert resp.status_code == 422


# ---------------------------------------------------------------------------
# auth has no implicit default — every caller must wire it explicitly.
# ---------------------------------------------------------------------------


def test_router_construction_requires_an_explicit_auth(tmp_path: Path) -> None:
    """`auth` is keyword-only with no default: an Envoy-trusting adapter must

    never be silently assumed for a caller that hasn't said what its host's
    auth_mode actually is.
    """
    with pytest.raises(TypeError, match="auth"):
        MimirRouter(MarkdownMimirAdapter(root=tmp_path))  # type: ignore[call-arg]
