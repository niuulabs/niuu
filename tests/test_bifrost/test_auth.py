"""Tests for bifrost.auth — core identity types and integration."""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import jwt
import respx
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from httpx import Response
from jwt.algorithms import RSAAlgorithm

from bifrost.app import create_app
from bifrost.auth import AgentIdentity, AuthMode
from bifrost.config import BifrostConfig, ProviderConfig
from niuu.domain.services.pat_validator import PATValidator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SECRET = "test-secret-key-that-is-at-least-32-bytes-long!"

_OIDC_ISSUER = "https://idp.test"
_OIDC_AUDIENCE = "bifrost"
_OIDC_JWKS_URI = f"{_OIDC_ISSUER}/jwks"
_OIDC_KID = "bifrost-test-key"
_OIDC_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OIDC_PUBLIC_JWK = json.loads(RSAAlgorithm.to_jwk(_OIDC_PRIVATE_KEY.public_key()))
_OIDC_PUBLIC_JWK.update({"kid": _OIDC_KID, "use": "sig", "alg": "RS256"})


def _make_token(payload: dict) -> str:
    return jwt.encode(payload, _SECRET, algorithm="HS256")


def _make_oidc_token(
    *, sub: str = "resident-1", tenant_id: str = "acme", roles: list[str] | None = None
) -> str:
    now = int(time.time())
    claims = {
        "sub": sub,
        "iss": _OIDC_ISSUER,
        "aud": _OIDC_AUDIENCE,
        "exp": now + 300,
        "iat": now,
        "tenant_id": tenant_id,
    }
    if roles is not None:
        claims["resource_access"] = {"volundr": {"roles": roles}}
    return jwt.encode(claims, _OIDC_PRIVATE_KEY, algorithm="RS256", headers={"kid": _OIDC_KID})


def _make_config(
    auth_mode: AuthMode = AuthMode.OPEN,
    secret: str = "",
    *,
    pat_revocation: dict[str, Any] | None = None,
    oidc_kwargs: dict[str, Any] | None = None,
) -> BifrostConfig:
    # auth_mode: pat requires an explicit revocation decision (BifrostConfig.
    # _pat_mode_requires_revocation_decision) — default tests that aren't
    # exercising revocation to an explicit opt-out, so they keep testing only
    # what they say they test.
    if pat_revocation is None and auth_mode == AuthMode.PAT:
        pat_revocation = {"enabled": False}
    return BifrostConfig(
        providers={"anthropic": ProviderConfig(models=["claude-sonnet-4-6"])},
        auth_mode=auth_mode,
        pat_secret=secret,
        pat_revocation=pat_revocation or {},
        oidc_kwargs=oidc_kwargs or {},
    )


class FakeRevocationValidator(PATValidator):
    """Revokes every PAT whose ``jti`` is in ``REVOKED_JTIS`` — a test double.

    Dynamic-adapter-loaded by dotted path (tests.test_bifrost.test_auth.
    FakeRevocationValidator) to prove BifrostConfig.pat_revocation actually
    threads through to a real revocation decision, without needing a live
    identity authority or a database pool.
    """

    REVOKED_JTIS: set[str] = set()

    async def is_valid(self, raw_token: str) -> bool:
        payload = jwt.decode(raw_token, options={"verify_signature": False})
        return payload.get("jti") not in self.REVOKED_JTIS


# ---------------------------------------------------------------------------
# AgentIdentity defaults
# ---------------------------------------------------------------------------


class TestAgentIdentity:
    def test_default_values(self):
        identity = AgentIdentity()
        assert identity.agent_id == "anonymous"
        assert identity.tenant_id == "default"
        assert identity.session_id == ""
        assert identity.saga_id == ""

    def test_custom_values(self):
        identity = AgentIdentity(
            agent_id="my-agent",
            tenant_id="my-tenant",
            session_id="s1",
            saga_id="sg1",
        )
        assert identity.agent_id == "my-agent"
        assert identity.tenant_id == "my-tenant"


# ---------------------------------------------------------------------------
# Integration: auth mode wired into the app via TestClient
# ---------------------------------------------------------------------------


class TestAuthIntegration:
    """Verify the FastAPI app enforces the configured auth mode end-to-end."""

    @contextmanager
    def _client_with_pat(self) -> TestClient:
        cfg = _make_config(AuthMode.PAT, _SECRET)
        app = create_app(cfg)
        with patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock) as m:
            from bifrost.translation.models import AnthropicResponse, TextBlock, UsageInfo

            m.return_value = AnthropicResponse(
                id="msg",
                content=[TextBlock(text="hi")],
                model="claude-sonnet-4-6",
                stop_reason="end_turn",
                usage=UsageInfo(input_tokens=5, output_tokens=2),
            )
            with TestClient(app, raise_server_exceptions=False) as c:
                yield c

    def test_pat_mode_rejects_missing_token(self):
        with self._client_with_pat() as client:
            resp = client.post(
                "/api/v1/bifrost/v1/messages",
                json={
                    "model": "claude-sonnet-4-6",
                    "max_tokens": 10,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
            assert resp.status_code == 401

    def test_pat_mode_accepts_valid_token(self):
        token = _make_token({"sub": "agent-test"})
        with self._client_with_pat() as client:
            resp = client.post(
                "/api/v1/bifrost/v1/messages",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "model": "claude-sonnet-4-6",
                    "max_tokens": 10,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
            assert resp.status_code == 200

    def test_pat_mode_rejects_admin_endpoint_without_token(self):
        with self._client_with_pat() as client:
            resp = client.post("/api/v1/bifrost/admin/reload-keys")
            assert resp.status_code == 401

    def test_pat_mode_accepts_admin_endpoint_with_valid_token(self):
        token = _make_token({"sub": "operator", "roles": ["admin"]})
        with self._client_with_pat() as client:
            resp = client.post(
                "/api/v1/bifrost/admin/reload-keys",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            assert resp.json() == {"status": "ok"}

    def test_pat_mode_rejects_admin_endpoint_without_admin_role(self):
        """A valid-but-non-admin PAT must not disrupt the adapter cache —

        pat/oidc mode requires the admin role in addition to a valid
        credential (unlike open/mesh, where a per-caller role concept
        doesn't exist)."""
        token = _make_token({"sub": "operator"})  # no roles claim
        with self._client_with_pat() as client:
            resp = client.post(
                "/api/v1/bifrost/admin/reload-keys",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Integration: PAT revocation check wired through BifrostConfig.pat_revocation
# ---------------------------------------------------------------------------


class TestPatRevocationIntegration:
    """Confirm BifrostConfig.pat_revocation actually reaches PATAuthAdapter."""

    @contextmanager
    def _client(self) -> TestClient:
        cfg = _make_config(
            AuthMode.PAT,
            _SECRET,
            pat_revocation={
                "adapter": "tests.test_bifrost.test_auth.FakeRevocationValidator",
            },
        )
        app = create_app(cfg)
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c

    def setup_method(self) -> None:
        FakeRevocationValidator.REVOKED_JTIS = set()

    def test_explicit_opt_out_has_no_revocation_check(self):
        """pat_revocation.enabled: false (an explicit decision) must not break

        create_app — see test_pat_mode_requires_an_explicit_revocation_decision
        in TestBifrostConfig for the case this is opting out of.
        """
        cfg = _make_config(AuthMode.PAT, _SECRET)  # _make_config defaults to enabled: False
        app = create_app(cfg)  # must not raise
        assert app is not None

    def test_revoked_pat_rejected(self):
        FakeRevocationValidator.REVOKED_JTIS = {"revoked-1"}
        token = _make_token({"sub": "operator", "jti": "revoked-1"})
        with self._client() as client:
            resp = client.post(
                "/api/v1/bifrost/admin/reload-keys",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 401

    def test_unrevoked_pat_accepted(self):
        FakeRevocationValidator.REVOKED_JTIS = {"revoked-1"}
        token = _make_token({"sub": "operator", "jti": "still-valid", "roles": ["admin"]})
        with self._client() as client:
            resp = client.post(
                "/api/v1/bifrost/admin/reload-keys",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Integration: OIDC bearer verification wired through BifrostConfig.oidc_kwargs
# ---------------------------------------------------------------------------


class TestOidcIntegration:
    @contextmanager
    def _client(self) -> TestClient:
        cfg = _make_config(
            AuthMode.OIDC,
            oidc_kwargs={
                "issuers": [
                    {
                        "issuer": _OIDC_ISSUER,
                        "audiences": [_OIDC_AUDIENCE],
                        "jwks_uri": _OIDC_JWKS_URI,
                    }
                ],
            },
        )
        app = create_app(cfg)
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c

    def test_missing_bearer_rejected(self):
        with self._client() as client:
            resp = client.post("/api/v1/bifrost/admin/reload-keys")
            assert resp.status_code == 401

    def test_unverifiable_token_rejected(self):
        with self._client() as client:
            resp = client.post(
                "/api/v1/bifrost/admin/reload-keys",
                headers={"Authorization": "Bearer not-a-jwt"},
            )
            assert resp.status_code == 401

    def test_x_agent_id_spoof_without_bearer_rejected(self):
        """oidc mode must never fall back to a caller-supplied identity header."""
        with self._client() as client:
            resp = client.post(
                "/api/v1/bifrost/admin/reload-keys",
                headers={"X-Agent-Id": "spoofed-admin"},
            )
            assert resp.status_code == 401

    @respx.mock
    def test_signature_verified_bearer_accepted(self):
        respx.get(_OIDC_JWKS_URI).mock(
            return_value=Response(200, json={"keys": [_OIDC_PUBLIC_JWK]})
        )
        token = _make_oidc_token(roles=["admin"])
        with self._client() as client:
            resp = client.post(
                "/api/v1/bifrost/admin/reload-keys",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200

    @respx.mock
    def test_signature_verified_bearer_without_admin_role_rejected(self):
        respx.get(_OIDC_JWKS_URI).mock(
            return_value=Response(200, json={"keys": [_OIDC_PUBLIC_JWK]})
        )
        token = _make_oidc_token()  # no roles claim
        with self._client() as client:
            resp = client.post(
                "/api/v1/bifrost/admin/reload-keys",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 403

    @respx.mock
    def test_wrong_key_signed_bearer_rejected(self):
        respx.get(_OIDC_JWKS_URI).mock(
            return_value=Response(200, json={"keys": [_OIDC_PUBLIC_JWK]})
        )
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = int(time.time())
        forged = jwt.encode(
            {
                "sub": "attacker",
                "iss": _OIDC_ISSUER,
                "aud": _OIDC_AUDIENCE,
                "exp": now + 300,
                "iat": now,
            },
            other_key,
            algorithm="RS256",
            headers={"kid": _OIDC_KID},
        )
        with self._client() as client:
            resp = client.post(
                "/api/v1/bifrost/admin/reload-keys",
                headers={"Authorization": f"Bearer {forged}"},
            )
            assert resp.status_code == 401
