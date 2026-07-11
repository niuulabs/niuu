"""WebSocket identity extraction for the Skuld broker.

Envoy verifies signatures. This module owns transport-specific credential
extraction and normalises validated identity claims for broker ownership checks.
"""

import inspect
import urllib.parse
from dataclasses import dataclass

from fastapi import WebSocket

from niuu.ws_identity import claims_to_identity, decode_jwt_claims

_AUTH_HEADER = "authorization"
_BEARER_PREFIX = "bearer "


def _decode_jwt_claims(token: str) -> dict:
    """Decode a JWT payload without verification; Envoy verifies signatures."""
    return decode_jwt_claims(token)


def _extract_bearer_token(headers: dict[str, str]) -> str | None:
    """Extract a bearer token from an Authorization header value."""
    auth = headers.get(_AUTH_HEADER, "")
    if auth.lower().startswith(_BEARER_PREFIX):
        return auth[len(_BEARER_PREFIX) :].strip()
    return None


def _header_mapping(websocket: WebSocket) -> dict[str, str]:
    """Read headers defensively for lightweight WebSocket test doubles."""
    header_items = websocket.headers.items()
    if inspect.iscoroutine(header_items):
        header_items.close()
        header_items = ()
    elif inspect.isawaitable(header_items):
        header_items = ()
    return {key.lower(): value for key, value in header_items}


def _extract_token_from_websocket(websocket: WebSocket) -> str | None:
    """Extract a JWT from supported WebSocket credential carriers."""
    headers = _header_mapping(websocket)
    token = _extract_bearer_token(headers)
    if token:
        return token

    protocol_header = headers.get("sec-websocket-protocol", "")
    for protocol in protocol_header.split(","):
        protocol = protocol.strip()
        if protocol.startswith("volundr.bearer."):
            token = protocol.removeprefix("volundr.bearer.").strip()
            if token:
                return urllib.parse.unquote(token)

    query_get = getattr(websocket.query_params, "get", None)
    if not callable(query_get):
        return None
    query_token = query_get("token") or query_get("access_token")
    if inspect.iscoroutine(query_token):
        query_token.close()
        return None
    if inspect.isawaitable(query_token):
        return None
    return query_token


@dataclass(frozen=True)
class WsPrincipal:
    """Identity resolved from an inbound WebSocket connection."""

    user_id: str
    tenant_id: str = ""
    roles: tuple[str, ...] = ()


def _split_roles(raw: str) -> tuple[str, ...]:
    return tuple(role.strip() for role in raw.split(",") if role.strip())


def _ws_query_param(websocket: WebSocket, name: str) -> str:
    """Read one query parameter defensively."""
    query_get = getattr(websocket.query_params, "get", None)
    if not callable(query_get):
        return ""
    value = query_get(name)
    if inspect.iscoroutine(value):
        value.close()
        return ""
    if inspect.isawaitable(value):
        return ""
    return str(value or "").strip()


def _claims_to_ws_principal(claims: dict) -> WsPrincipal | None:
    """Build a principal from decoded JWT claims."""
    user_id, tenant, roles = claims_to_identity(claims)
    if not user_id:
        return None
    return WsPrincipal(user_id=user_id, tenant_id=tenant, roles=roles)


def _resolve_ws_principal(websocket: WebSocket) -> WsPrincipal | None:
    """Resolve forwarded, development, or bearer-token identity."""
    headers = _header_mapping(websocket)
    forwarded_user_id = headers.get("x-auth-user-id", "").strip()
    if forwarded_user_id:
        return WsPrincipal(
            user_id=forwarded_user_id,
            tenant_id=headers.get("x-auth-tenant", "").strip(),
            roles=_split_roles(headers.get("x-auth-roles", "volundr:developer")),
        )

    dev_user_id = _ws_query_param(websocket, "devUserId")
    if dev_user_id:
        return WsPrincipal(
            user_id=dev_user_id,
            tenant_id=_ws_query_param(websocket, "devTenantId"),
            roles=_split_roles(_ws_query_param(websocket, "devRoles") or "volundr:developer"),
        )

    token = _extract_token_from_websocket(websocket)
    if token:
        return _claims_to_ws_principal(_decode_jwt_claims(token))
    return None


def _is_loopback_ws_client(websocket: WebSocket) -> bool:
    """Return whether the WebSocket peer connected from a loopback address."""
    client = getattr(websocket, "client", None)
    host = getattr(client, "host", None)
    return host in ("127.0.0.1", "::1", "localhost")
