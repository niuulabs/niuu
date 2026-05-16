"""Shared Niuu auth dependency for co-hosted local and browser flows."""

from __future__ import annotations

from fastapi import Request

from niuu.domain.models import Principal


async def extract_principal(request: Request) -> Principal:
    """Extract a principal from trusted headers or fall back to local dev defaults.

    The shared Niuu surfaces are commonly used from the in-app browser against
    a local shell. When no forwarded identity headers are present, we provide a
    stable local admin principal so instance registration and inspection remain
    usable in anonymous-dev mode. Explicit `x-auth-*` headers always take
    precedence and are used by the guild proof to validate tenancy behavior.
    """

    user_id = request.headers.get("x-auth-user-id", "").strip()
    if user_id:
        return Principal(
            user_id=user_id,
            email=request.headers.get("x-auth-email", ""),
            tenant_id=request.headers.get("x-auth-tenant", ""),
            roles=request.headers.get("x-auth-roles", "volundr:developer").split(","),
        )

    return Principal(
        user_id="dev-user",
        email="",
        tenant_id="default",
        roles=["volundr:admin", "volundr:developer"],
    )
