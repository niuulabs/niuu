"""Tests for bifrost auth port and adapters (open, pat, mesh, oidc)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from fastapi import HTTPException

from bifrost.adapters.auth import build_auth_adapter
from bifrost.adapters.auth.mesh import MeshAuthAdapter, _parse_spiffe_workload
from bifrost.adapters.auth.oidc import OidcAuthAdapter
from bifrost.adapters.auth.open import OpenAuthAdapter
from bifrost.adapters.auth.pat import PATAuthAdapter
from bifrost.auth import AgentIdentity, AuthMode
from niuu.domain.models import Principal
from niuu.ports.identity import InvalidTokenError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SECRET = "test-secret-key-that-is-at-least-32-bytes-long!"


def _make_token(payload: dict) -> str:
    return jwt.encode(payload, _SECRET, algorithm="HS256")


def _req(headers: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.headers = headers or {}
    return r


# ---------------------------------------------------------------------------
# OpenAuthAdapter
# ---------------------------------------------------------------------------


class TestOpenAuthAdapter:
    async def test_anonymous_defaults(self):
        adapter = OpenAuthAdapter()
        identity = await adapter.extract(_req())
        assert identity.agent_id == "anonymous"
        assert identity.tenant_id == "default"
        assert identity.session_id == ""
        assert identity.saga_id == ""

    async def test_reads_all_headers(self):
        adapter = OpenAuthAdapter()
        identity = await adapter.extract(
            _req(
                {
                    "x-agent-id": "my-agent",
                    "x-tenant-id": "my-tenant",
                    "x-session-id": "sess-1",
                    "x-saga-id": "saga-2",
                }
            )
        )
        assert identity.agent_id == "my-agent"
        assert identity.tenant_id == "my-tenant"
        assert identity.session_id == "sess-1"
        assert identity.saga_id == "saga-2"

    async def test_returns_agent_identity(self):
        adapter = OpenAuthAdapter()
        assert isinstance(await adapter.extract(_req()), AgentIdentity)


# ---------------------------------------------------------------------------
# PATAuthAdapter
# ---------------------------------------------------------------------------


class TestPATAuthAdapter:
    async def test_valid_token_extracts_claims(self):
        token = _make_token({"sub": "agent-1", "tenant_id": "tenant-1"})
        adapter = PATAuthAdapter(_SECRET)
        identity = await adapter.extract(_req({"authorization": f"Bearer {token}"}))
        assert identity.agent_id == "agent-1"
        assert identity.tenant_id == "tenant-1"

    async def test_missing_bearer_raises_401(self):
        adapter = PATAuthAdapter(_SECRET)
        with pytest.raises(HTTPException) as exc:
            await adapter.extract(_req({}))
        assert exc.value.status_code == 401

    async def test_invalid_token_raises_401(self):
        adapter = PATAuthAdapter(_SECRET)
        with pytest.raises(HTTPException) as exc:
            await adapter.extract(_req({"authorization": "Bearer not-a-jwt"}))
        assert exc.value.status_code == 401

    async def test_wrong_secret_raises_401(self):
        token = _make_token({"sub": "agent-1"})
        adapter = PATAuthAdapter("wrong-secret-that-is-at-least-32-bytes!")
        with pytest.raises(HTTPException) as exc:
            await adapter.extract(_req({"authorization": f"Bearer {token}"}))
        assert exc.value.status_code == 401

    async def test_expired_token_raises_401(self):
        import time

        token = _make_token({"sub": "agent-1", "exp": int(time.time()) - 10})
        adapter = PATAuthAdapter(_SECRET)
        with pytest.raises(HTTPException) as exc:
            await adapter.extract(_req({"authorization": f"Bearer {token}"}))
        assert exc.value.status_code == 401
        assert "expired" in exc.value.detail.lower()

    async def test_defaults_when_claims_absent(self):
        token = _make_token({})
        adapter = PATAuthAdapter(_SECRET)
        identity = await adapter.extract(_req({"authorization": f"Bearer {token}"}))
        assert identity.agent_id == "anonymous"
        assert identity.tenant_id == "default"

    async def test_reads_attribution_headers(self):
        token = _make_token({"sub": "ag"})
        adapter = PATAuthAdapter(_SECRET)
        identity = await adapter.extract(
            _req(
                {
                    "authorization": f"Bearer {token}",
                    "x-session-id": "sess",
                    "x-saga-id": "saga",
                }
            )
        )
        assert identity.session_id == "sess"
        assert identity.saga_id == "saga"

    async def test_case_insensitive_bearer_prefix(self):
        token = _make_token({"sub": "agent-x"})
        adapter = PATAuthAdapter(_SECRET)
        # uppercase BEARER
        identity = await adapter.extract(_req({"authorization": f"BEARER {token}"}))
        assert identity.agent_id == "agent-x"

    async def test_no_revocation_validator_configured_accepts_token(self):
        """revocation_validator=None reproduces the old (revocation-blind) behaviour."""
        token = _make_token({"sub": "agent-1", "type": "pat"})
        adapter = PATAuthAdapter(_SECRET, revocation_validator=None)
        identity = await adapter.extract(_req({"authorization": f"Bearer {token}"}))
        assert identity.agent_id == "agent-1"

    async def test_revoked_token_rejected(self):
        token = _make_token({"sub": "agent-1", "type": "pat"})
        validator = AsyncMock()
        validator.is_valid.return_value = False
        adapter = PATAuthAdapter(_SECRET, revocation_validator=validator)
        with pytest.raises(HTTPException) as exc:
            await adapter.extract(_req({"authorization": f"Bearer {token}"}))
        assert exc.value.status_code == 401
        assert "revoked" in exc.value.detail.lower()
        validator.is_valid.assert_awaited_once_with(token)

    async def test_valid_unrevoked_token_accepted(self):
        token = _make_token({"sub": "agent-1", "type": "pat"})
        validator = AsyncMock()
        validator.is_valid.return_value = True
        adapter = PATAuthAdapter(_SECRET, revocation_validator=validator)
        identity = await adapter.extract(_req({"authorization": f"Bearer {token}"}))
        assert identity.agent_id == "agent-1"


# ---------------------------------------------------------------------------
# MeshAuthAdapter
# ---------------------------------------------------------------------------


class TestMeshAuthAdapter:
    async def test_extracts_spiffe_workload_from_xfcc(self):
        xfcc = "By=spiffe://cluster.local/ns/default/sa/bifrost;URI=spiffe://cluster.local/ns/prod/sa/volundr"
        adapter = MeshAuthAdapter()
        identity = await adapter.extract(
            _req(
                {
                    "x-forwarded-client-cert": xfcc,
                    "x-tenant-id": "prod",
                }
            )
        )
        assert identity.agent_id == "volundr"
        assert identity.tenant_id == "prod"

    async def test_xfcc_wins_even_with_x_agent_id_present(self):
        xfcc = "URI=spiffe://cluster.local/ns/default/sa/ting"
        adapter = MeshAuthAdapter()
        identity = await adapter.extract(
            _req({"x-forwarded-client-cert": xfcc, "x-agent-id": "should-be-ignored"})
        )
        assert identity.agent_id == "ting"

    async def test_missing_xfcc_is_rejected_not_x_agent_id_fallback(self):
        """A caller-supplied X-Agent-Id must never assert identity without XFCC.

        This is the fix for the spoofable fallback: previously, no XFCC meant
        `read_agent_id(request)` (the plain X-Agent-Id header) was trusted
        instead, letting any caller claim an arbitrary agent identity.
        """
        adapter = MeshAuthAdapter()
        with pytest.raises(HTTPException) as exc:
            await adapter.extract(_req({"x-agent-id": "spoofed-agent"}))
        assert exc.value.status_code == 401

    async def test_unparseable_xfcc_is_rejected_not_x_agent_id_fallback(self):
        adapter = MeshAuthAdapter()
        with pytest.raises(HTTPException) as exc:
            await adapter.extract(
                _req({"x-forwarded-client-cert": "By=hash;Hash=abc", "x-agent-id": "spoofed"})
            )
        assert exc.value.status_code == 401

    async def test_reads_attribution_headers(self):
        xfcc = "URI=spiffe://cluster.local/ns/default/sa/skuld"
        adapter = MeshAuthAdapter()
        identity = await adapter.extract(
            _req(
                {
                    "x-forwarded-client-cert": xfcc,
                    "x-session-id": "s1",
                    "x-saga-id": "sg1",
                }
            )
        )
        assert identity.session_id == "s1"
        assert identity.saga_id == "sg1"


# ---------------------------------------------------------------------------
# _parse_spiffe_workload helper
# ---------------------------------------------------------------------------


class TestParseSpiffeWorkload:
    def test_extracts_last_path_segment(self):
        xfcc = "URI=spiffe://cluster.local/ns/default/sa/volundr"
        assert _parse_spiffe_workload(xfcc) == "volundr"

    def test_handles_trailing_slash(self):
        xfcc = "URI=spiffe://cluster.local/ns/default/sa/ting/"
        assert _parse_spiffe_workload(xfcc) == "ting"

    def test_returns_none_when_no_uri_field(self):
        assert _parse_spiffe_workload("By=spiffe://…;Hash=abc123") is None

    def test_case_insensitive_match(self):
        xfcc = "uri=spiffe://cluster.local/ns/default/sa/skuld"
        assert _parse_spiffe_workload(xfcc) == "skuld"

    def test_multipart_xfcc_header(self):
        # Multiple cert entries separated by commas
        xfcc = "By=x;Hash=y,URI=spiffe://cluster.local/ns/default/sa/niuu"
        assert _parse_spiffe_workload(xfcc) == "niuu"


# ---------------------------------------------------------------------------
# OidcAuthAdapter
# ---------------------------------------------------------------------------


def _fake_bearer_token(**claims) -> str:
    """A structurally-real JWT (unsigned-verification only matters —
    OidcAuthAdapter's own scope-check decode doesn't verify signature,
    matching the already-verified-by-`bearer`-above posture).
    """
    return jwt.encode(
        {"sub": "x", **claims}, "irrelevant-but-at-least-32-bytes-long!", algorithm="HS256"
    )


class TestOidcAuthAdapter:
    async def test_verified_bearer_becomes_identity(self):
        bearer = AsyncMock()
        bearer.validate_headers.return_value = Principal("alice", "a@test", "acme", ["viewer"])
        adapter = OidcAuthAdapter(bearer)
        token = _fake_bearer_token()
        identity = await adapter.extract(_req({"authorization": f"Bearer {token}"}))
        assert identity.agent_id == "alice"
        assert identity.tenant_id == "acme"
        bearer.validate_headers.assert_awaited_once()

    async def test_invalid_token_rejected(self):
        bearer = AsyncMock()
        bearer.validate_headers.side_effect = InvalidTokenError("bad signature")
        adapter = OidcAuthAdapter(bearer)
        with pytest.raises(HTTPException) as exc:
            await adapter.extract(_req({"authorization": "Bearer forged"}))
        assert exc.value.status_code == 401

    async def test_spoofed_x_agent_id_header_is_never_consulted(self):
        """Only the verified bearer's claims decide identity in oidc mode."""
        bearer = AsyncMock()
        bearer.validate_headers.return_value = Principal("real-user", "", "acme", [])
        adapter = OidcAuthAdapter(bearer)
        token = _fake_bearer_token()
        identity = await adapter.extract(
            _req({"authorization": f"Bearer {token}", "x-agent-id": "spoofed-agent"})
        )
        assert identity.agent_id == "real-user"

    async def test_missing_tenant_claim_is_rejected_not_pooled_into_default(self):
        """A tenant-less verified token must not share Bifröst's 'default'

        quota/usage bucket with every other tenant-less caller — that would
        let one caller read or exhaust another's budget through a shared
        pseudo-tenant. Refuse it instead.
        """
        bearer = AsyncMock()
        bearer.validate_headers.return_value = Principal("alice", "", "", [])
        adapter = OidcAuthAdapter(bearer)
        token = _fake_bearer_token()
        with pytest.raises(HTTPException) as exc:
            await adapter.extract(_req({"authorization": f"Bearer {token}"}))
        assert exc.value.status_code == 403

    async def test_scoped_workload_token_denied_model_access(self):
        bearer = AsyncMock()
        bearer.validate_headers.return_value = Principal("alice", "", "acme", [])
        adapter = OidcAuthAdapter(bearer)
        token = _fake_bearer_token(token_use="valkyrie_build")
        with pytest.raises(HTTPException) as exc:
            await adapter.extract(_req({"authorization": f"Bearer {token}"}))
        assert exc.value.status_code == 403

    async def test_revoked_pat_bearer_rejected(self):
        bearer = AsyncMock()
        bearer.validate_headers.return_value = Principal("alice", "", "acme", [])
        validator = AsyncMock()
        validator.is_valid.return_value = False
        adapter = OidcAuthAdapter(bearer, revocation_validator=validator)
        token = _fake_bearer_token(type="pat")
        with pytest.raises(HTTPException) as exc:
            await adapter.extract(_req({"authorization": f"Bearer {token}"}))
        assert exc.value.status_code == 401
        validator.is_valid.assert_awaited_once_with(token)

    async def test_non_revoked_bearer_accepted(self):
        bearer = AsyncMock()
        bearer.validate_headers.return_value = Principal("alice", "", "acme", [])
        validator = AsyncMock()
        validator.is_valid.return_value = True
        adapter = OidcAuthAdapter(bearer, revocation_validator=validator)
        token = _fake_bearer_token(type="pat")
        identity = await adapter.extract(_req({"authorization": f"Bearer {token}"}))
        assert identity.agent_id == "alice"


# ---------------------------------------------------------------------------
# build_auth_adapter factory
# ---------------------------------------------------------------------------


class TestBuildAuthAdapter:
    def test_open_mode(self):
        adapter = build_auth_adapter(AuthMode.OPEN)
        assert isinstance(adapter, OpenAuthAdapter)

    def test_pat_mode(self):
        adapter = build_auth_adapter(AuthMode.PAT, pat_secret=_SECRET)
        assert isinstance(adapter, PATAuthAdapter)

    def test_pat_mode_threads_revocation_validator(self):
        validator = AsyncMock()
        adapter = build_auth_adapter(
            AuthMode.PAT, pat_secret=_SECRET, pat_revocation_validator=validator
        )
        assert isinstance(adapter, PATAuthAdapter)
        assert adapter._revocation_validator is validator

    def test_mesh_mode(self):
        adapter = build_auth_adapter(AuthMode.MESH)
        assert isinstance(adapter, MeshAuthAdapter)

    def test_oidc_mode(self):
        adapter = build_auth_adapter(
            AuthMode.OIDC,
            oidc_kwargs={
                "issuers": [
                    {
                        "issuer": "https://idp.test",
                        "audiences": ["bifrost"],
                        "jwks_uri": "https://idp.test/jwks",
                    }
                ]
            },
        )
        assert isinstance(adapter, OidcAuthAdapter)

    def test_oidc_mode_requires_issuers(self):
        with pytest.raises(ValueError, match="issuers"):
            build_auth_adapter(AuthMode.OIDC, oidc_kwargs={})

    def test_unknown_mode_raises_never_falls_back_to_open(self):
        with pytest.raises(ValueError, match="Unknown bifrost auth_mode"):
            build_auth_adapter("unknown_mode")  # type: ignore[arg-type]
