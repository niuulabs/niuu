"""Tests for bifrost auth port and adapters (open, pat, mesh)."""

from __future__ import annotations

from unittest.mock import MagicMock

import jwt
import pytest
from fastapi import HTTPException

from bifrost.adapters.auth import build_auth_adapter
from bifrost.adapters.auth.mesh import MeshAuthAdapter, _parse_spiffe_workload
from bifrost.adapters.auth.open import OpenAuthAdapter
from bifrost.adapters.auth.pat import PATAuthAdapter
from bifrost.auth import AgentIdentity, AuthMode

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
    def test_anonymous_defaults(self):
        adapter = OpenAuthAdapter()
        identity = adapter.extract(_req())
        assert identity.agent_id == "anonymous"
        assert identity.tenant_id == "default"
        assert identity.session_id == ""
        assert identity.saga_id == ""

    def test_reads_all_headers(self):
        adapter = OpenAuthAdapter()
        identity = adapter.extract(
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

    def test_returns_agent_identity(self):
        adapter = OpenAuthAdapter()
        assert isinstance(adapter.extract(_req()), AgentIdentity)


# ---------------------------------------------------------------------------
# PATAuthAdapter
# ---------------------------------------------------------------------------


class TestPATAuthAdapter:
    def test_valid_token_extracts_claims(self):
        token = _make_token({"sub": "agent-1", "tenant_id": "tenant-1"})
        adapter = PATAuthAdapter(_SECRET)
        identity = adapter.extract(_req({"authorization": f"Bearer {token}"}))
        assert identity.agent_id == "agent-1"
        assert identity.tenant_id == "tenant-1"

    def test_missing_bearer_raises_401(self):
        adapter = PATAuthAdapter(_SECRET)
        with pytest.raises(HTTPException) as exc:
            adapter.extract(_req({}))
        assert exc.value.status_code == 401

    def test_invalid_token_raises_401(self):
        adapter = PATAuthAdapter(_SECRET)
        with pytest.raises(HTTPException) as exc:
            adapter.extract(_req({"authorization": "Bearer not-a-jwt"}))
        assert exc.value.status_code == 401

    def test_wrong_secret_raises_401(self):
        token = _make_token({"sub": "agent-1"})
        adapter = PATAuthAdapter("wrong-secret-that-is-at-least-32-bytes!")
        with pytest.raises(HTTPException) as exc:
            adapter.extract(_req({"authorization": f"Bearer {token}"}))
        assert exc.value.status_code == 401

    def test_expired_token_raises_401(self):
        import time

        token = _make_token({"sub": "agent-1", "exp": int(time.time()) - 10})
        adapter = PATAuthAdapter(_SECRET)
        with pytest.raises(HTTPException) as exc:
            adapter.extract(_req({"authorization": f"Bearer {token}"}))
        assert exc.value.status_code == 401
        assert "expired" in exc.value.detail.lower()

    def test_defaults_when_claims_absent(self):
        token = _make_token({})
        adapter = PATAuthAdapter(_SECRET)
        identity = adapter.extract(_req({"authorization": f"Bearer {token}"}))
        assert identity.agent_id == "anonymous"
        assert identity.tenant_id == "default"

    def test_reads_attribution_headers(self):
        token = _make_token({"sub": "ag"})
        adapter = PATAuthAdapter(_SECRET)
        identity = adapter.extract(
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

    def test_case_insensitive_bearer_prefix(self):
        token = _make_token({"sub": "agent-x"})
        adapter = PATAuthAdapter(_SECRET)
        # uppercase BEARER
        identity = adapter.extract(_req({"authorization": f"BEARER {token}"}))
        assert identity.agent_id == "agent-x"


# ---------------------------------------------------------------------------
# MeshAuthAdapter
# ---------------------------------------------------------------------------


class TestMeshAuthAdapter:
    def test_reads_standard_headers_without_xfcc(self):
        adapter = MeshAuthAdapter()
        identity = adapter.extract(_req({"x-agent-id": "mesh-agent", "x-tenant-id": "mesh-tenant"}))
        assert identity.agent_id == "mesh-agent"
        assert identity.tenant_id == "mesh-tenant"

    def test_defaults_when_headers_absent(self):
        adapter = MeshAuthAdapter()
        identity = adapter.extract(_req())
        assert identity.agent_id == "anonymous"
        assert identity.tenant_id == "default"

    def test_extracts_spiffe_workload_from_xfcc(self):
        xfcc = "By=spiffe://cluster.local/ns/default/sa/bifrost;URI=spiffe://cluster.local/ns/prod/sa/volundr"
        adapter = MeshAuthAdapter()
        identity = adapter.extract(
            _req(
                {
                    "x-forwarded-client-cert": xfcc,
                    "x-tenant-id": "prod",
                }
            )
        )
        assert identity.agent_id == "volundr"
        assert identity.tenant_id == "prod"

    def test_xfcc_takes_priority_over_x_agent_id(self):
        xfcc = "URI=spiffe://cluster.local/ns/default/sa/ting"
        adapter = MeshAuthAdapter()
        identity = adapter.extract(
            _req({"x-forwarded-client-cert": xfcc, "x-agent-id": "should-be-ignored"})
        )
        assert identity.agent_id == "ting"

    def test_falls_back_to_x_agent_id_when_xfcc_unparseable(self):
        adapter = MeshAuthAdapter()
        identity = adapter.extract(
            _req({"x-forwarded-client-cert": "By=hash;Hash=abc", "x-agent-id": "fallback-agent"})
        )
        assert identity.agent_id == "fallback-agent"

    def test_reads_attribution_headers(self):
        adapter = MeshAuthAdapter()
        identity = adapter.extract(_req({"x-session-id": "s1", "x-saga-id": "sg1"}))
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
# build_auth_adapter factory
# ---------------------------------------------------------------------------


class TestBuildAuthAdapter:
    def test_open_mode(self):
        adapter = build_auth_adapter(AuthMode.OPEN)
        assert isinstance(adapter, OpenAuthAdapter)

    def test_pat_mode(self):
        adapter = build_auth_adapter(AuthMode.PAT, pat_secret=_SECRET)
        assert isinstance(adapter, PATAuthAdapter)

    def test_mesh_mode(self):
        adapter = build_auth_adapter(AuthMode.MESH)
        assert isinstance(adapter, MeshAuthAdapter)

    def test_default_falls_back_to_open(self):
        # Any unrecognised mode string should default to open
        adapter = build_auth_adapter("unknown_mode")  # type: ignore[arg-type]
        assert isinstance(adapter, OpenAuthAdapter)
