"""Tests for the header policy applied to every outbound Guild call.

``forward_identity_headers`` is the single choke point for what a genuinely
remote Guild instance ever sees of the caller's identity — these are direct,
isolated tests of that policy, independent of the larger router integration
tests that also assert it end to end.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers

from niuu.adapters.inbound.remote_urls import (
    build_remote_url,
    forward_identity_headers,
    forward_local_identity_headers,
)


def _conn(headers: dict[str, str]) -> SimpleNamespace:
    """A minimal stand-in for a fastapi Request/WebSocket: both only need `.headers`."""
    return SimpleNamespace(headers=Headers(headers))


_SPOOFED_HEADERS = {
    "authorization": "Bearer real-token",
    "x-auth-user-id": "attacker-supplied",
    "x-auth-email": "attacker@example.com",
    "x-auth-tenant": "attacker-tenant",
    "x-auth-roles": "volundr:admin",
    "cookie": "session=abc",
}


class TestForwardIdentityHeaders:
    """The policy for a genuinely remote Guild instance: bearer only."""

    def test_forwards_only_the_authorization_header(self) -> None:
        result = forward_identity_headers(_conn(_SPOOFED_HEADERS))
        assert result == {"authorization": "Bearer real-token"}

    def test_never_forwards_spoofed_x_auth_headers(self) -> None:
        result = forward_identity_headers(_conn(_SPOOFED_HEADERS))
        for name in ("x-auth-user-id", "x-auth-email", "x-auth-tenant", "x-auth-roles"):
            assert name not in result

    def test_never_forwards_the_cookie(self) -> None:
        result = forward_identity_headers(_conn(_SPOOFED_HEADERS))
        assert "cookie" not in result

    def test_omits_authorization_entirely_when_absent(self) -> None:
        result = forward_identity_headers(_conn({"x-auth-user-id": "someone"}))
        assert result == {}

    def test_case_insensitive_like_any_http_header(self) -> None:
        result = forward_identity_headers(_conn({"Authorization": "Bearer mixed-case"}))
        assert result == {"authorization": "Bearer mixed-case"}


class TestForwardLocalIdentityHeaders:
    """The policy for a same-process embedded target: no wire, no stripping."""

    def test_forwards_the_full_resolved_identity(self) -> None:
        result = forward_local_identity_headers(_conn(_SPOOFED_HEADERS))
        assert result == {
            "authorization": "Bearer real-token",
            "x-auth-user-id": "attacker-supplied",
            "x-auth-email": "attacker@example.com",
            "x-auth-tenant": "attacker-tenant",
            "x-auth-roles": "volundr:admin",
        }

    def test_still_never_forwards_the_cookie(self) -> None:
        result = forward_local_identity_headers(_conn(_SPOOFED_HEADERS))
        assert "cookie" not in result


class TestBuildRemoteUrl:
    def test_joins_base_prefix_and_path(self) -> None:
        assert build_remote_url("https://node.test", "/api/v1/forge", "/sessions") == (
            "https://node.test/api/v1/forge/sessions"
        )

    def test_rejects_a_non_http_scheme(self) -> None:
        with pytest.raises(ValueError, match="http or https"):
            build_remote_url("embedded://local", "/api/v1/forge", "/sessions")

    def test_rejects_embedded_credentials(self) -> None:
        with pytest.raises(ValueError, match="credentials"):
            build_remote_url("https://user:pass@node.test", "/api/v1/forge", "/sessions")

    def test_rejects_a_non_relative_path(self) -> None:
        with pytest.raises(ValueError, match="relative"):
            build_remote_url("https://node.test", "/api/v1/forge", "https://evil.test/x")
