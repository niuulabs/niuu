"""Helpers for constructing validated remote service URLs."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from fastapi import Request, WebSocket


def build_remote_url(base_url: str, prefix: str, path: str) -> str:
    """Construct a remote URL from a validated base and a relative API path."""
    parsed_base = urlsplit(base_url)
    if parsed_base.scheme not in {"http", "https"}:
        raise ValueError("remote base URL must use http or https")
    if not parsed_base.netloc:
        raise ValueError("remote base URL must include a host")
    if parsed_base.username or parsed_base.password:
        raise ValueError("remote base URL must not embed credentials")
    if parsed_base.query or parsed_base.fragment:
        raise ValueError("remote base URL must not include query or fragment components")

    parsed_path = urlsplit(path)
    if parsed_path.scheme or parsed_path.netloc:
        raise ValueError("remote path must be relative")
    if not parsed_path.path.startswith("/"):
        raise ValueError("remote path must start with '/'")

    prefix_path = prefix.rstrip("/")
    base_path = parsed_base.path.rstrip("/")
    full_path = f"{base_path}{prefix_path}{parsed_path.path}"
    return urlunsplit(
        (
            parsed_base.scheme,
            parsed_base.netloc,
            full_path,
            parsed_path.query,
            "",
        )
    )


def forward_local_identity_headers(request: Request | WebSocket) -> dict[str, str]:
    """Forward the caller's full resolved identity to a SAME-PROCESS embedded target.

    Only for a ``config.transport: "embedded"`` instance, dispatched over
    ``httpx.ASGITransport`` with no network hop: it runs in this process, so
    there is no wire for ``x-auth-*`` to leak across and no separate trust
    domain asserting a different identity — it is the same request this
    process already authenticated. Never use this for anything that leaves
    the process; use ``forward_identity_headers`` for that.
    """
    headers: dict[str, str] = {}
    for name in (
        "authorization",
        "x-auth-user-id",
        "x-auth-email",
        "x-auth-tenant",
        "x-auth-roles",
    ):
        value = request.headers.get(name)
        if value:
            headers[name] = value
    return headers


def forward_identity_headers(request: Request | WebSocket) -> dict[str, str]:
    """Forward only the caller's bearer token to a remote Guild instance.

    A remote instance is a different trust domain than this process: it is a
    separate machine that re-verifies the bearer itself (in-process JWKS
    under ``host_auth.mode: oidc``, or allow-all under ``host_auth.mode:
    none`` — see ``.claude/rules/architecture.md``). Client-supplied
    ``x-auth-*`` headers are never forwarded, even when this process trusts
    them locally: they assert identity this process resolved for its own
    trust boundary, not a credential the remote can verify, and forwarding
    them would let a caller assert any identity to the remote for free.
    """
    headers: dict[str, str] = {}
    value = request.headers.get("authorization")
    if value:
        headers["authorization"] = value
    return headers
