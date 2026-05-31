"""Shared Niuu auth dependency for co-hosted local and browser flows."""

from __future__ import annotations

from fastapi import Request

from niuu.domain.models import Principal


def _split_roles(raw: str) -> list[str]:
    return [role.strip() for role in raw.split(",") if role.strip()]


async def extract_principal(request: Request) -> Principal:
    """Extract a principal from trusted headers or fall back to local dev defaults.

    The shared Niuu surfaces are commonly used from the in-app browser against
    a local shell. When no forwarded identity headers are present, we provide a
    stable local developer principal so browsing the shell in anonymous-dev mode
    stays safe by default. Explicit `x-auth-*` headers always take precedence
    and are used by the guild proof to validate tenancy behavior. Non-production
    browser flows may also supply the same values through `dev*` query params.
    """

    user_id = request.headers.get("x-auth-user-id", "").strip()
    if user_id:
        return Principal(
            user_id=user_id,
            email=request.headers.get("x-auth-email", ""),
            tenant_id=request.headers.get("x-auth-tenant", ""),
            roles=_split_roles(request.headers.get("x-auth-roles", "volundr:developer")),
        )

    dev_user_id = request.query_params.get("devUserId", "").strip()
    if dev_user_id:
        return Principal(
            user_id=dev_user_id,
            email=request.query_params.get("devEmail", "").strip(),
            tenant_id=request.query_params.get("devTenantId", "").strip(),
            roles=_split_roles(request.query_params.get("devRoles", "volundr:developer")),
        )

    return Principal(
        user_id="dev-user",
        email="",
        tenant_id="default",
        roles=["volundr:developer"],
    )
