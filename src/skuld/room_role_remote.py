"""Remote room-role resolution: ask Forge for a caller's session_participants grant.

Lets a Kubernetes/OpenShell/VM-backed session pod honour ``session_participants``
grants it cannot itself evaluate (they live in Forge's Postgres, not in this
pod). Selected via ``ws_auth.room_role_source: remote`` +
``ws_auth.room_role_remote`` (dynamic adapter, see
``.claude/rules/dynamic-adapters.md``). Authenticates to Forge with the pod's
own projected workload-identity token (grep ``niuu-workload``,
``workload_identity`` — the SAME projected-service-account-token exchange
``skuld.event_log._refresh_workload_token`` uses for chronicle/event-log
calls), requesting the ``forge:session:room-role`` scope so a leaked token
minted for this purpose alone cannot be replayed against any other endpoint
(``niuu.domain.services.token_scope.KNOWN_WORKLOAD_SCOPES``).

Fails closed, per ``.claude/rules/no-fallbacks.md``: an unreachable Forge, a
timeout, a non-2xx response, or a malformed body all raise
``RoomRoleResolutionError`` — never a default role. Callers (``skuld.broker_api``,
``skuld.websocket_lifecycle``) must deny outright on that error, exactly as
they already deny on ``identity.ports.AuthorizationEvaluationError``.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx

from skuld.room_role_port import RoomRoleResolutionError, RoomRoleResolverPort

logger = logging.getLogger("skuld.broker")

_DEFAULT_TOKEN_FILE = "/var/run/secrets/niuu-workload/token"
_DEFAULT_SCOPE = "forge:session:room-role"
_DEFAULT_AUDIENCES = ("forge",)
_VALID_ROLES = frozenset({"owner", "approver", "viewer"})
_WORKLOAD_TOKEN_EXPIRY_SKEW_SECONDS = 30


class RemoteAuthorizationAdapter(RoomRoleResolverPort):
    """Resolve room roles by calling Forge's session-participants role endpoint.

    Constructor takes plain kwargs (adapter constructors never take config
    objects — see ``.claude/rules/dynamic-adapters.md``).
    """

    def __init__(
        self,
        *,
        volundr_api_url: str,
        token_file: str = _DEFAULT_TOKEN_FILE,
        exchange_url: str = "",
        audiences: list[str] | None = None,
        scope: str = _DEFAULT_SCOPE,
        cache_ttl_seconds: float = 5.0,
        timeout_seconds: float = 5.0,
        **_extra: object,
    ) -> None:
        if not volundr_api_url.strip():
            raise ValueError(
                "RemoteAuthorizationAdapter requires volundr_api_url — set "
                "ws_auth.room_role_remote.kwargs.volundr_api_url (see "
                "charts/skuld/values.yaml's wsAuth.room_role_remote)."
            )
        self._volundr_api_url = volundr_api_url.rstrip("/")
        self._token_file = token_file
        self._exchange_url = (
            exchange_url.strip() or f"{self._volundr_api_url}/api/v1/tokens/workload/exchange"
        )
        self._audiences = list(audiences) if audiences else list(_DEFAULT_AUDIENCES)
        self._scope = scope
        self._cache_ttl_seconds = cache_ttl_seconds
        self._timeout_seconds = timeout_seconds
        self._workload_jwt: str | None = None
        self._workload_jwt_expires_at: float = 0.0
        self._role_cache: dict[tuple[str, str, str, tuple[str, ...]], tuple[float, str | None]] = {}

    async def resolve_role(
        self,
        *,
        session_id: str,
        user_id: str,
        tenant_id: str,
        roles: list[str],
    ) -> str | None:
        cache_key = (session_id, user_id, tenant_id, tuple(sorted(roles)))
        cached = self._role_cache.get(cache_key)
        now = time.time()
        if cached is not None and cached[0] > now:
            return cached[1]

        token = await self._workload_token()
        role = await self._fetch_role(
            token, session_id=session_id, user_id=user_id, tenant_id=tenant_id, roles=roles
        )
        self._role_cache[cache_key] = (now + self._cache_ttl_seconds, role)
        return role

    async def _workload_token(self) -> str:
        """Return a cached or freshly exchanged workload JWT scoped to room-role reads.

        Raises RoomRoleResolutionError on any failure — this credential is
        required for every call, so its absence is fatal here, unlike
        ``skuld.event_log``'s best-effort chronicle refresh.
        """
        now = time.time()
        fresh_until = self._workload_jwt_expires_at - _WORKLOAD_TOKEN_EXPIRY_SKEW_SECONDS
        if self._workload_jwt and fresh_until > now:
            return self._workload_jwt

        token_path = Path(self._token_file)
        if not token_path.exists():
            raise RoomRoleResolutionError(
                f"No projected workload-identity token at {self._token_file} — mount "
                "the niuu-workload service-account token volume on this pod "
                "(see WorkloadIdentityContributor) or set room_role_source back to "
                "'deployment'."
            )
        proof = token_path.read_text(encoding="utf-8").strip()
        if not proof:
            raise RoomRoleResolutionError(
                f"Projected workload-identity token at {self._token_file} is empty."
            )

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    self._exchange_url,
                    json={"token": proof, "audiences": self._audiences, "scopes": [self._scope]},
                )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise RoomRoleResolutionError(
                f"Workload token exchange with Forge ({self._exchange_url}) failed: {exc}. "
                "Verify Forge is reachable and the workload identity issuer/audience "
                "configuration matches."
            ) from exc

        token = str(payload.get("token") or "")
        if not token:
            raise RoomRoleResolutionError(
                "Workload token exchange returned no token — Forge's response did not "
                "include a 'token' field."
            )
        expires_at = payload.get("expiresAt") or payload.get("expires_at")
        self._workload_jwt = token
        self._workload_jwt_expires_at = float(expires_at) if expires_at else now + 300
        return token

    async def _fetch_role(
        self,
        token: str,
        *,
        session_id: str,
        user_id: str,
        tenant_id: str,
        roles: list[str],
    ) -> str | None:
        url = f"{self._volundr_api_url}/api/v1/forge/sessions/{session_id}/participants/role"
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.get(
                    url,
                    params={"user_id": user_id, "tenant_id": tenant_id, "roles": ",".join(roles)},
                    headers={"Authorization": f"Bearer {token}"},
                )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise RoomRoleResolutionError(
                f"Room-role lookup against Forge ({url}) failed: {exc}. Forge is "
                "unreachable or denied the request — verify network policy, the "
                "workload token's forge:session:room-role scope, and that this "
                "session still exists."
            ) from exc

        role = payload.get("role")
        if role is None:
            return None
        if role not in _VALID_ROLES:
            raise RoomRoleResolutionError(
                f"Forge returned an unrecognized room role {role!r} for session "
                f"{session_id} — expected one of {sorted(_VALID_ROLES)} or null."
            )
        return role
