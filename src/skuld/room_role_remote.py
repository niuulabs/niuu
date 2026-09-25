"""Remote room-role resolution: ask Forge for a caller's session_participants grant.

Lets a Kubernetes-backed session pod honour ``session_participants`` grants it
cannot itself evaluate (they live in Forge's Postgres, not in this pod).
Selected via ``ws_auth.room_role_source: remote`` + ``ws_auth.room_role_remote``
(dynamic adapter, see ``.claude/rules/dynamic-adapters.md``). Authenticates to
Forge with the pod's own projected workload-identity token (grep
``niuu-workload``, ``workload_identity`` — the SAME projected-service-account
-token exchange ``skuld.event_log._refresh_workload_token`` uses for
chronicle/event-log calls), requesting the ``forge:session:room-role`` scope
so a leaked token minted for this purpose alone cannot be replayed against any
other Forge endpoint (``niuu.domain.services.token_scope.KNOWN_WORKLOAD_SCOPES``).

Fails closed, per ``.claude/rules/no-fallbacks.md``: an unreachable Forge, a
timeout, a non-2xx response, a malformed body (bad JSON, a non-dict payload,
an unusable ``expiresAt``), or a token-file read error all raise
``RoomRoleResolutionError`` — never a default role, never a silently-long
token lifetime. Callers (``skuld.broker_api``, ``skuld.websocket_lifecycle``)
must deny outright on that error, exactly as they already deny on
``identity.ports.AuthorizationEvaluationError``.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from pathlib import Path

import httpx

from skuld.room_role_port import RoomRoleResolutionError, RoomRoleResolverPort

logger = logging.getLogger("skuld.broker")

_DEFAULT_TOKEN_FILE = "/var/run/secrets/niuu-workload/token"
_DEFAULT_SCOPE = "forge:session:room-role"
_DEFAULT_AUDIENCES = ("forge",)
_VALID_ROLES = frozenset({"owner", "approver", "viewer"})

_RoleCacheKey = tuple[str, str, str, tuple[str, ...]]


class RemoteAuthorizationAdapter(RoomRoleResolverPort):
    """Resolve room roles by calling Forge's session-participants role endpoint.

    Constructor takes plain kwargs (adapter constructors never take config
    objects — see ``.claude/rules/dynamic-adapters.md``); no ``**_extra``
    catch-all, so a typo'd kwarg fails loudly at construction instead of
    being silently dropped.
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
        cache_max_entries: int = 512,
        token_expiry_skew_seconds: float = 30.0,
        timeout_seconds: float = 5.0,
    ) -> None:
        if not volundr_api_url.strip():
            raise ValueError(
                "RemoteAuthorizationAdapter requires volundr_api_url — set "
                "ws_auth.room_role_remote.kwargs.volundr_api_url (see "
                "charts/skuld/values.yaml's wsAuth.room_role_remote)."
            )
        if cache_max_entries < 1:
            raise ValueError("RemoteAuthorizationAdapter requires cache_max_entries >= 1")
        self._volundr_api_url = volundr_api_url.rstrip("/")
        self._token_file = token_file
        self._exchange_url = (
            exchange_url.strip() or f"{self._volundr_api_url}/api/v1/tokens/workload/exchange"
        )
        self._audiences = list(audiences) if audiences else list(_DEFAULT_AUDIENCES)
        self._scope = scope
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache_max_entries = cache_max_entries
        self._token_expiry_skew_seconds = token_expiry_skew_seconds
        self._timeout_seconds = timeout_seconds
        self._workload_jwt: str | None = None
        self._workload_jwt_expires_at: float = 0.0
        # Bounded, oldest-inserted-evicted-first — a pod only ever sees a
        # handful of distinct (session, user) pairs, but an unbounded dict
        # keyed on caller-influenced strings is still an easy leak to avoid.
        self._role_cache: OrderedDict[_RoleCacheKey, tuple[float, str | None]] = OrderedDict()

    async def resolve_role(
        self,
        *,
        session_id: str,
        user_id: str,
        tenant_id: str,
        roles: list[str],
    ) -> str | None:
        cache_key: _RoleCacheKey = (session_id, user_id, tenant_id, tuple(sorted(roles)))
        cached = self._role_cache.get(cache_key)
        now = time.monotonic()
        if cached is not None and cached[0] > now:
            return cached[1]

        token = await self._workload_token()
        role = await self._fetch_role(
            token, session_id=session_id, user_id=user_id, tenant_id=tenant_id, roles=roles
        )
        self._cache_role(cache_key, now, role)
        return role

    def _cache_role(self, key: _RoleCacheKey, now: float, role: str | None) -> None:
        self._role_cache[key] = (now + self._cache_ttl_seconds, role)
        self._role_cache.move_to_end(key)
        while len(self._role_cache) > self._cache_max_entries:
            self._role_cache.popitem(last=False)

    async def _workload_token(self) -> str:
        """Return a cached or freshly exchanged workload JWT scoped to room-role reads.

        Raises RoomRoleResolutionError on any failure — this credential is
        required for every call, so its absence is fatal here, unlike
        ``skuld.event_log``'s best-effort chronicle refresh.
        """
        now = time.time()
        fresh_until = self._workload_jwt_expires_at - self._token_expiry_skew_seconds
        if self._workload_jwt and fresh_until > now:
            return self._workload_jwt

        proof = self._read_workload_proof()

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    self._exchange_url,
                    json={"token": proof, "audiences": self._audiences, "scopes": [self._scope]},
                )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RoomRoleResolutionError(
                f"Workload token exchange with Forge ({self._exchange_url}) failed: {exc}. "
                "Verify Forge is reachable and the workload identity issuer/audience "
                "configuration matches."
            ) from exc

        payload = self._decode_json_object(response, context="workload token exchange")
        token = str(payload.get("token") or "")
        if not token:
            raise RoomRoleResolutionError(
                "Workload token exchange returned no token — Forge's response did not "
                "include a 'token' field."
            )
        raw_expires_at = payload.get("expiresAt", payload.get("expires_at"))
        if raw_expires_at is None:
            raise RoomRoleResolutionError(
                "Workload token exchange response is missing expiresAt/expires_at — "
                "refusing to cache a credential with an unknown lifetime."
            )
        try:
            expires_at = float(raw_expires_at)
        except (TypeError, ValueError) as exc:
            raise RoomRoleResolutionError(
                f"Workload token exchange returned a non-numeric expiresAt: {raw_expires_at!r}."
            ) from exc

        self._workload_jwt = token
        self._workload_jwt_expires_at = expires_at
        return token

    def _read_workload_proof(self) -> str:
        token_path = Path(self._token_file)
        try:
            proof = token_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError as exc:
            raise RoomRoleResolutionError(
                f"No projected workload-identity token at {self._token_file} — mount "
                "the niuu-workload service-account token volume on this pod "
                "(see WorkloadIdentityContributor) or set room_role_source back to "
                "'deployment'."
            ) from exc
        except OSError as exc:
            raise RoomRoleResolutionError(
                f"Failed to read the projected workload-identity token at "
                f"{self._token_file}: {exc}."
            ) from exc
        if not proof:
            raise RoomRoleResolutionError(
                f"Projected workload-identity token at {self._token_file} is empty."
            )
        return proof

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
        except httpx.HTTPError as exc:
            raise RoomRoleResolutionError(
                f"Room-role lookup against Forge ({url}) failed: {exc}. Forge is "
                "unreachable or denied the request — verify network policy, the "
                "workload token's forge:session:room-role scope, and that this "
                "session still exists."
            ) from exc

        payload = self._decode_json_object(response, context="room-role lookup")
        role = payload.get("role")
        if role is None:
            return None
        if role not in _VALID_ROLES:
            raise RoomRoleResolutionError(
                f"Forge returned an unrecognized room role {role!r} for session "
                f"{session_id} — expected one of {sorted(_VALID_ROLES)} or null."
            )
        return role

    @staticmethod
    def _decode_json_object(response: httpx.Response, *, context: str) -> dict:
        try:
            payload = response.json()
        except ValueError as exc:
            raise RoomRoleResolutionError(
                f"Forge's {context} response was not valid JSON."
            ) from exc
        if not isinstance(payload, dict):
            raise RoomRoleResolutionError(
                f"Forge's {context} response was {type(payload).__name__}, expected an object."
            )
        return payload
