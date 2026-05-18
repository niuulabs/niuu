"""Identity adapters for authentication and JIT provisioning.

All adapters accept **kwargs (dynamic adapter pattern).
The ``user_repository`` kwarg is injected at runtime by main.py.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

from volundr.domain.models import Principal, StorageQuota, TenantRole, User, UserStatus
from volundr.domain.ports import (
    IdentityPort,
    InvalidTokenError,
    StoragePort,
    UserRepository,
)

logger = logging.getLogger(__name__)


def _parse_roles_header(raw: str) -> list[str]:
    """Parse roles from an Envoy header value.

    Envoy base64-encodes non-string JWT claims (e.g. arrays).
    This handles both plain comma-separated strings and base64-encoded
    JSON arrays.
    """
    if not raw:
        return []

    # Try base64 decode → JSON array (Envoy encodes array claims this way)
    try:
        decoded = base64.b64decode(raw).decode("utf-8")
        parsed = json.loads(decoded)
        if isinstance(parsed, list):
            return [str(r) for r in parsed]
    except Exception:
        pass  # Expected: not base64/JSON, fall back to comma-separated parsing

    # Fall back to comma-separated plain text
    return [r.strip() for r in raw.split(",") if r.strip()]


class AllowAllIdentityAdapter(IdentityPort):
    """Development adapter that accepts any token and returns a default principal.

    For local dev only — skips JWT validation entirely.
    """

    def __init__(
        self,
        *,
        user_repository: UserRepository,
        storage: StoragePort | None = None,
        default_tenant_id: str = "default",
        **_extra: object,
    ) -> None:
        self._user_repository = user_repository
        self._storage = storage
        self._default_tenant_id = default_tenant_id

    async def validate_token(self, raw_token: str) -> Principal:
        if not raw_token:
            raise InvalidTokenError("Empty token")
        return Principal(
            user_id="dev-user",
            email="dev@localhost",
            tenant_id=self._default_tenant_id,
            roles=["volundr:admin"],
        )

    async def get_or_provision_user(self, principal: Principal) -> User:
        user = await self._user_repository.get(principal.user_id)
        if user is not None:
            return user

        user = User(
            id=principal.user_id,
            email=principal.email,
            display_name=principal.email.split("@")[0],
            status=UserStatus.ACTIVE,
        )
        return await self._user_repository.create(user)


class EnvoyHeaderIdentityAdapter(IdentityPort):
    """Identity adapter that trusts headers set by an Envoy sidecar.

    In production, Envoy's ext_authz or jwt_authn filter validates
    the JWT against the IDP (Keycloak) and forwards verified claims
    as trusted headers. This adapter simply reads those headers.

    Expected headers (configurable):
        x-auth-user-id:  The subject (sub) claim
        x-auth-email:    The email claim
        x-auth-tenant:   The tenant claim
        x-auth-roles:    Comma-separated list of roles
    """

    def __init__(
        self,
        *,
        user_repository: UserRepository,
        storage: StoragePort | None = None,
        tenant_service: Any | None = None,
        user_id_header: str = "x-auth-user-id",
        email_header: str = "x-auth-email",
        tenant_header: str = "x-auth-tenant",
        roles_header: str = "x-auth-roles",
        default_tenant_id: str = "default",
        role_mapping: dict[str, str] | None = None,
        **_extra: object,
    ) -> None:
        self._user_repository = user_repository
        self._storage = storage
        self._tenant_service = tenant_service
        self._user_id_header = user_id_header
        self._email_header = email_header
        self._tenant_header = tenant_header
        self._roles_header = roles_header
        self._default_tenant_id = default_tenant_id
        self._role_mapping = role_mapping

    async def validate_token(self, raw_token: str) -> Principal:
        """Validate by reading Envoy-injected headers from the raw token.

        The raw_token here is the full Authorization header value passed
        by the auth dependency. In envoy mode we also need the request
        headers — so validate_token is only called as a fallback.
        Use validate_headers() directly from the auth dependency.
        """
        if not raw_token:
            raise InvalidTokenError("Empty token")
        raise InvalidTokenError(
            "EnvoyHeaderIdentityAdapter requires headers, not a raw token. "
            "Ensure the auth dependency calls validate_headers()."
        )

    async def validate_headers(self, headers: dict[str, str]) -> Principal:
        """Extract principal from Envoy-injected headers."""
        user_id = headers.get(self._user_id_header, "")
        if not user_id:
            raise InvalidTokenError(f"Missing required header: {self._user_id_header}")

        email = headers.get(self._email_header, "")
        tenant_id = headers.get(self._tenant_header, "") or self._default_tenant_id

        roles_raw = headers.get(self._roles_header, "")
        raw_roles = _parse_roles_header(roles_raw)
        if self._role_mapping:
            roles = [self._role_mapping.get(r, r) for r in raw_roles]
        else:
            roles = raw_roles
        if not roles:
            roles = ["volundr:developer"]

        return Principal(
            user_id=user_id,
            email=email,
            tenant_id=tenant_id,
            roles=roles,
        )

    async def get_or_provision_user(self, principal: Principal) -> User:
        user = await self._user_repository.get(principal.user_id)
        if user is not None:
            if user.status == UserStatus.PROVISIONING:
                from volundr.domain.ports import UserProvisioningError

                raise UserProvisioningError("User provisioning in progress, retry later")

            # Sync tenant membership from IDP on every login
            if self._tenant_service is not None:
                await self._sync_tenant(principal)

            return user

        logger.info("JIT provisioning user: sub=%s email=%s", principal.user_id, principal.email)

        # Create user in PROVISIONING state
        user = User(
            id=principal.user_id,
            email=principal.email,
            display_name=principal.email.split("@")[0],
            status=UserStatus.PROVISIONING,
        )
        user = await self._user_repository.create(user)

        try:
            # TODO: Provision OpenBao paths (NIU-99)

            # Provision home PVC (NIU-101)
            if self._storage is not None:
                pvc_ref = await self._storage.provision_user_storage(
                    principal.user_id,
                    StorageQuota(),
                )
                from dataclasses import replace as dc_replace

                user = dc_replace(user, home_pvc=pvc_ref.name)
                user = await self._user_repository.update(user)

            # Mark as active
            from dataclasses import replace

            user = replace(user, status=UserStatus.ACTIVE)
            user = await self._user_repository.update(user)
            logger.info("JIT provisioning complete: sub=%s", principal.user_id)

            # Sync tenant membership from IDP on every login
            if self._tenant_service is not None:
                await self._sync_tenant(principal)

            return user
        except Exception:
            logger.exception("JIT provisioning failed: sub=%s", principal.user_id)
            from dataclasses import replace

            user = replace(user, status=UserStatus.FAILED)
            await self._user_repository.update(user)
            from volundr.domain.ports import UserProvisioningError

            raise UserProvisioningError("User provisioning failed")

    async def _sync_tenant(self, principal: Principal) -> None:
        """Sync tenant membership from IDP claims."""
        if not principal.tenant_id:
            return
        try:
            await self._tenant_service.sync_tenant_from_principal(principal)
            role = self._resolve_tenant_role(principal.roles)
            await self._tenant_service.add_member(principal.tenant_id, principal.user_id, role)
        except Exception:
            logger.warning(
                "Failed to sync tenant membership for user %s",
                principal.user_id,
                exc_info=True,
            )

    def _resolve_tenant_role(self, roles: list[str]) -> TenantRole:
        """Determine TenantRole from principal roles."""
        if "volundr:admin" in roles:
            return TenantRole.ADMIN
        if "volundr:viewer" in roles:
            return TenantRole.VIEWER
        return TenantRole.DEVELOPER
