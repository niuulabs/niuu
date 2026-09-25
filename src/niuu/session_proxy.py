"""Session registry and HTTP/WebSocket proxying for Niuu-hosted Skuld sessions."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.parse
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket
from starlette.responses import JSONResponse, Response

from identity.adapters.http_auth import extract_principal
from niuu.config import NiuuSettings
from niuu.domain.models import Principal
from niuu.ports.session_proxy import SessionProxyTarget
from niuu.room_access import ROOM_ROLE_HEADER, ROOM_ROLE_RANK, required_role_for_route

logger = logging.getLogger(__name__)


def _configured_cors_origins() -> list[str]:
    """Return explicitly configured CORS origins for the unified niuu host."""

    return NiuuSettings().host.cors_origins


def _sanitize_log(value: object) -> str:
    """Sanitize a value for safe log output (prevent log injection)."""
    return str(value).replace("\n", "\\n").replace("\r", "\\r")


class SessionProxyGuardMissingError(RuntimeError):
    """A non-dev session proxy was asked to attach without an ownership guard."""


class SkuldPortRegistry:
    """Maps session IDs to their Skuld subprocess ports.

    ``dev_identity`` is set only by a local-dev composition root that runs
    without an identity provider. There the browser's asserted dev identity
    (``devUserId``/``devTenantId``/``devRoles`` query params and ``x-auth-*``
    headers) IS the identity contract, and the proxy may attach without an
    ownership guard. Everywhere else it stays ``False``: the proxy forwards
    only identity it verified itself and refuses to attach without a guard.
    """

    def __init__(
        self,
        state_file: Path | None = None,
        *,
        dev_identity: bool = False,
        revalidate_interval_seconds: float | None = None,
    ) -> None:
        self._ports: dict[str, int] = {}
        self._state_file = state_file or Path(NiuuSettings().host.forge_state_file).expanduser()
        self._dev_identity = dev_identity
        # Read once here (composition time), not per connection: _proxy_ws
        # passes this straight to bridge_websocket's _revalidate_loop
        # instead of constructing NiuuSettings() itself on every WebSocket
        # connect.
        self._revalidate_interval_seconds = (
            revalidate_interval_seconds
            if revalidate_interval_seconds is not None
            else NiuuSettings().host.session_proxy_role_check_interval_seconds
        )
        # Optional async hook invoked when the WS proxy cannot reach a live pod,
        # so the persisted Session row self-heals (status corrected, endpoint
        # cleared) instead of leaving a stale RUNNING tombstone. Set by the host
        # composition root via set_reconcile_hook(). The hook is pod-authoritative
        # and reports back whether the session is CONFIRMED dead (True) or still
        # genuinely RUNNING (False) — a transient broker-leg blip on a live pod
        # must NOT drop the port (M-8).
        self._reconcile_hook: Callable[[str], Awaitable[bool]] | None = None
        # Optional async guard invoked before proxying a browser WebSocket, so
        # the proxy (where the browser terminates) enforces session ownership.
        # The broker's own ws_auth check cannot cover the proxied path: the
        # proxy dials the broker from loopback, so identity resolution there
        # only works when Envoy x-auth-* headers are forwarded. The guard
        # receives the resolved caller identity and the session id and returns
        # True when the caller may attach. Set by the composition root.
        self._ownership_guard: (
            Callable[[str, str | None, str | None, tuple[str, ...]], Awaitable[bool]] | None
        ) = None
        self._target_resolver: Callable[[str], Awaitable[SessionProxyTarget | None]] | None = None
        # Optional async resolver returning the caller's verified room role
        # ("owner"/"approver"/"viewer") for the stamped x-niuu-room-role
        # header (see ROOM_ROLE_HEADER). Set by the composition root
        # alongside the ownership guard. Without it, a dev-identity registry
        # stamps "owner" (unchanged legacy behavior); everywhere else the
        # header is simply omitted, and the downstream broker treats its
        # absence as no elevated room privilege.
        self._room_role_resolver: (
            Callable[[str, str | None, str | None, tuple[str, ...]], Awaitable[str | None]] | None
        ) = None
        # Live proxied browser sockets, keyed by (session_id, user_id), so a
        # revoke/demotion can close an ALREADY-OPEN connection immediately
        # (see close_connections) instead of relying solely on
        # _revalidate_loop's interval poll.
        self._live_connections: dict[tuple[str, str], set[WebSocket]] = {}

    @property
    def dev_identity(self) -> bool:
        """Whether client-asserted dev identity is honoured (local dev only)."""
        return self._dev_identity

    @property
    def revalidate_interval_seconds(self) -> float:
        """Seconds between _revalidate_loop's attach/room-role re-checks."""
        return self._revalidate_interval_seconds

    def set_reconcile_hook(self, hook: Callable[[str], Awaitable[bool]]) -> None:
        """Inject the session-row reconcile callback used on a dead-pod proxy.

        The hook returns ``True`` when the pod-authoritative reconcile CONFIRMS the
        session is dead (STOPPED/FAILED) and ``False`` when it is still RUNNING.
        """
        self._reconcile_hook = hook

    def set_ownership_guard(
        self,
        guard: Callable[[str, str | None, str | None, tuple[str, ...]], Awaitable[bool]],
    ) -> None:
        """Inject the per-connection session-ownership check for the WS proxy.

        ``guard(session_id, user_id, tenant_id, roles) -> bool`` returns True
        when the caller owns (or may administer) the session.
        """
        self._ownership_guard = guard

    def set_target_resolver(
        self,
        resolver: Callable[[str], Awaitable[SessionProxyTarget | None]],
    ) -> None:
        """Inject resolution for non-local session service targets."""
        self._target_resolver = resolver

    def set_room_role_resolver(
        self,
        resolver: Callable[[str, str | None, str | None, tuple[str, ...]], Awaitable[str | None]],
    ) -> None:
        """Inject the per-connection room-role resolver for the stamped header.

        ``resolver(session_id, user_id, tenant_id, roles) -> "owner" |
        "approver" | "viewer" | None``. Called only after
        ``may_attach`` already allowed the connection; this resolves the
        FINER-grained role the downstream broker uses to gate tool-permission
        responses and gate resolution, distinct from the attach decision.
        """
        self._room_role_resolver = resolver

    def track_connection(self, session_id: str, user_id: str, websocket: WebSocket) -> None:
        """Register a live proxied socket so a later revoke can close it immediately."""
        self._live_connections.setdefault((session_id, user_id), set()).add(websocket)

    def untrack_connection(self, session_id: str, user_id: str, websocket: WebSocket) -> None:
        key = (session_id, user_id)
        sockets = self._live_connections.get(key)
        if sockets is None:
            return
        sockets.discard(websocket)
        if not sockets:
            self._live_connections.pop(key, None)

    async def close_connections(
        self, session_id: str, user_id: str, *, reason: str = "Access revoked"
    ) -> int:
        """Immediately close every currently tracked socket for (session_id, user_id).

        Best-effort: a socket that fails to close (already gone) is skipped,
        never raised — ``_revalidate_loop``'s interval poll is the fallback
        of record for anything this misses. Returns the number closed.
        """
        sockets = list(self._live_connections.get((session_id, user_id), ()))
        closed = 0
        for websocket in sockets:
            try:
                await websocket.close(code=1008, reason=reason)
                closed += 1
            except Exception:
                logger.debug(
                    "close_connections: socket already gone for session=%s user=%s",
                    _sanitize_log(session_id),
                    _sanitize_log(user_id),
                )
        return closed

    async def resolve_room_role(
        self,
        session_id: str,
        user_id: str | None,
        tenant_id: str | None,
        roles: tuple[str, ...],
    ) -> str | None:
        """Resolve the caller's verified room role, or None if it cannot be determined.

        Dev-identity registries (no verified identity boundary) treat the
        caller as owner, same as ``may_attach``. Without a resolver and
        without dev identity, returns None: the header is omitted rather than
        guessed, and the broker treats its absence as no elevated privilege.
        """
        if self._room_role_resolver is not None:
            return await self._room_role_resolver(session_id, user_id, tenant_id, roles)
        if self._dev_identity:
            return "owner"
        return None

    async def resolve_target(self, session_id: str) -> SessionProxyTarget | None:
        """Resolve an externally hosted session service, when configured."""
        if self._target_resolver is None:
            return None
        return await self._target_resolver(session_id)

    async def may_attach(
        self,
        session_id: str,
        user_id: str | None,
        tenant_id: str | None,
        roles: tuple[str, ...],
    ) -> bool:
        """Return True when the caller may attach to *session_id*'s chat.

        Without a guard only a dev-identity registry (local dev without a
        session store) attaches freely; any other registry refuses, loudly.

        Raises:
            SessionProxyGuardMissingError: No guard is wired and dev identity is off.
        """
        if self._ownership_guard is not None:
            return await self._ownership_guard(session_id, user_id, tenant_id, roles)
        if self._dev_identity:
            return True
        raise SessionProxyGuardMissingError(
            "Session proxy has no ownership guard: the composition root must call "
            "SkuldPortRegistry.set_ownership_guard(), or construct the registry with "
            "dev_identity=True for a local-dev host without an identity provider"
        )

    async def reconcile_dead(self, session_id: str) -> bool:
        """Reconcile a session whose pod the proxy could not reach (best effort).

        Pod-status authoritative on the Volundr side: if the pod is in fact still
        alive the row is left untouched. Failures must never block the proxy's
        close path, so they are swallowed.

        Returns ``True`` only when the reconcile CONFIRMS the session is dead
        (STOPPED/FAILED). On a still-RUNNING pod (transient blip), a missing hook,
        or a hook error it returns ``False`` so the caller RETAINS the live port
        rather than dropping a port it could not confirm dead (M-8).
        """
        if self._reconcile_hook is None:
            return False
        try:
            return await self._reconcile_hook(session_id)
        except Exception:
            logger.warning(
                "Reconcile hook failed for dead-pod session %s",
                _sanitize_log(session_id),
                exc_info=True,
            )
            return False

    def register(self, session_id: str, port: int) -> None:
        self._ports[session_id] = port

    def unregister(self, session_id: str) -> None:
        self._ports.pop(session_id, None)

    def get_port(self, session_id: str) -> int | None:
        port = self._ports.get(session_id)
        if port is not None:
            return port
        recovered_port = self._recover_port(session_id)
        if recovered_port is not None:
            self._ports[session_id] = recovered_port
        return recovered_port

    def _recover_port(self, session_id: str) -> int | None:
        if not self._state_file.exists():
            return None
        try:
            payload = json.loads(self._state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(payload, dict):
            return None
        info = payload.get(session_id)
        if not isinstance(info, dict):
            return None
        if info.get("state") not in {"running", "starting"}:
            return None
        port = info.get("port")
        return port if isinstance(port, int) else None


_skuld_registry: SkuldPortRegistry | None = None

# The broker replays the FULL conversation history as one frame on connect;
# long sessions exceed the websockets client's 1 MiB default (which killed the
# broker leg with 1009 "message too big" and trapped clients in a reconnect
# loop). 64 MiB headroom, shared by both the /session and /ws/ravn proxy legs.
_WS_PROXY_MAX_FRAME_BYTES = 2**26


def _bearer_token_from_ws(websocket: WebSocket) -> str:
    """Extract a bearer token from a WS: Authorization, subprotocol, or query."""
    headers = {k.lower(): v for k, v in websocket.headers.items()}
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    for proto in headers.get("sec-websocket-protocol", "").split(","):
        proto = proto.strip()
        if proto.startswith("volundr.bearer."):
            return urllib.parse.unquote(proto.removeprefix("volundr.bearer.").strip())
    return str(
        websocket.query_params.get("access_token") or websocket.query_params.get("token") or ""
    ).strip()


async def _proxy_principal(websocket: WebSocket | Request) -> Principal | None:
    """Resolve the caller through the configured identity adapter, or None."""
    try:
        return await extract_principal(websocket)
    except HTTPException:
        return None


async def _proxy_ws_identity(
    websocket: WebSocket,
) -> tuple[str | None, str | None, tuple[str, ...]]:
    """Use the configured identity adapter, including explicit no-auth mode."""
    principal = await _proxy_principal(websocket)
    if principal is None:
        return (None, None, ())
    return (principal.user_id, principal.tenant_id, tuple(principal.roles))


# Identity headers the downstream session trusts as proxy-verified.
_IDENTITY_HEADERS = frozenset(
    {
        "x-auth-user-id",
        "x-auth-email",
        "x-auth-tenant",
        "x-auth-roles",
    }
)
# The room role ("owner"/"approver"/"viewer") the proxy resolved for this
# connection, stamped so Skuld's broker can gate tool-permission responses
# and gate resolution to approver/owner. Never in the WS forward allowlist
# and always dropped from the HTTP forward set, so a client-supplied copy on
# a request that goes THROUGH THIS PROXY can never reach the broker — see
# resolve_room_role(). This guarantee is scoped to the proxy path: a request
# that reaches the broker some other way (an enforced-Kubernetes deployment
# routes the browser straight to the pod, bypassing this module entirely) is
# not covered here — the sidecar Envoy's header-stripping Lua filter
# (charts/volundr and charts/skuld envoy.headerNames) and the broker's own
# ext_authz/ws_auth checks are that path's defense, not this constant.
# Dev-identity query params mapped onto x-auth-* headers for the broker leg.
_DEV_QUERY_TO_HEADER = (
    ("devUserId", "x-auth-user-id"),
    ("devEmail", "x-auth-email"),
    ("devTenantId", "x-auth-tenant"),
    ("devRoles", "x-auth-roles"),
)
_DEV_QUERY_PARAMS = frozenset(query_key for query_key, _ in _DEV_QUERY_TO_HEADER)


def _verified_identity_headers(principal: Principal | None) -> dict[str, str]:
    """Project a principal the proxy resolved itself onto ``x-auth-*`` headers."""
    if principal is None or not principal.user_id:
        return {}
    headers = {
        "x-auth-user-id": principal.user_id,
        "x-auth-tenant": principal.tenant_id,
        "x-auth-roles": ",".join(principal.roles),
    }
    if principal.email:
        headers["x-auth-email"] = principal.email
    return headers


def _proxy_forward_headers(
    websocket: WebSocket,
    *,
    include_cookie: bool,
    dev_identity: bool,
    principal: Principal | None,
    room_role: str | None = None,
) -> dict[str, str]:
    """Build the header set forwarded from the browser leg to the broker leg.

    The downstream session treats ``x-auth-*`` as identity verified by a
    trusted proxy, so outside local dev the client's own ``x-auth-*`` headers
    and dev query params are dropped and *principal* (resolved by the
    configured identity adapter) is projected instead. With ``dev_identity``
    the browser-asserted dev identity is forwarded as-is.

    ``room_role``, when resolved, is stamped as ``ROOM_ROLE_HEADER``. It is
    never in ``allow``, so a client-supplied copy on the incoming WebSocket is
    never part of ``headers`` before this stamps the proxy's own value.
    """
    allow = {"authorization"}
    if include_cookie:
        allow.add("cookie")
    if dev_identity:
        allow |= _IDENTITY_HEADERS
    headers = {k: v for k, v in websocket.headers.items() if k.lower() in allow}
    if not dev_identity:
        headers.update(_verified_identity_headers(principal))
    else:
        for query_key, header in _DEV_QUERY_TO_HEADER:
            if value := websocket.query_params.get(query_key):
                headers[header] = value
    if room_role is not None:
        headers[ROOM_ROLE_HEADER] = room_role
    return headers


def _without_dev_params(
    params: list[tuple[str, str]], *, dev_identity: bool
) -> list[tuple[str, str]]:
    """Drop dev-identity query params unless dev identity is honoured."""
    if dev_identity:
        return params
    return [(key, value) for key, value in params if key not in _DEV_QUERY_PARAMS]


async def _revalidate_loop(
    websocket: WebSocket,
    revalidate: Callable[[], Awaitable[bool]],
    interval: float,
) -> None:
    """Periodically re-check attach/room-role and close on revoke or demotion.

    A session_participants grant revoked (or demoted) after connect must not
    leave the live socket usable at its original privilege until the browser
    happens to reconnect — this closes it within one interval. *revalidate*
    itself is responsible for treating its own transient failures (a network
    blip to the identity/authorization backend) as "still allowed" rather
    than raising — only an explicit "no longer allowed" result closes the
    socket in the ordinary path below.

    A raise reaching this function is therefore an UNEXPECTED failure of the
    loop itself, not a routine revalidation outcome, and must not be
    swallowed: a silently-dead revalidation loop leaves an already-open
    socket at its original privilege forever, which is exactly the gap this
    loop exists to close. Fail closed and loud instead — log it and close
    the socket, rather than leave it running unrevalidated with no signal.
    """
    try:
        while True:
            await asyncio.sleep(interval)
            if not await revalidate():
                await websocket.close(code=1008, reason="Access revoked or downgraded")
                return
    except Exception:
        logger.error(
            "_revalidate_loop: revalidation loop failed unexpectedly; closing the "
            "socket rather than leaving it un-revalidated for the rest of its life",
            exc_info=True,
        )
        with suppress(Exception):
            # The connection may already be gone (the reason this raised in
            # the first place); closing it is a best-effort cleanup, not the
            # signal — the log line above is.
            await websocket.close(code=1011, reason="Revalidation failed")


async def bridge_websocket(
    websocket: WebSocket,
    connect_url: str,
    *,
    headers: Mapping[str, str],
    connect_kwargs: dict[str, object] | None = None,
    on_connected: Callable[[], None] | None = None,
    revalidate: Callable[[], Awaitable[bool]] | None = None,
    revalidate_interval: float = 5.0,
) -> None:
    """Bridge one accepted browser socket to a resolved session endpoint.

    *headers* is the complete upstream header set; build it with
    ``_proxy_forward_headers`` so identity policy is applied in one place.

    *revalidate*, when given, is polled every *revalidate_interval* seconds
    for as long as the connection stays open; a ``False`` result closes it
    (see ``_revalidate_loop``) — this is how a revoked or demoted
    session_participants grant reaches an ALREADY-OPEN socket, not just new
    connection attempts.
    """
    import websockets.asyncio.client as ws_client

    forwarded_headers = dict(headers)
    await websocket.accept()
    async with ws_client.connect(
        connect_url,
        max_size=_WS_PROXY_MAX_FRAME_BYTES,
        additional_headers=forwarded_headers,
        **(connect_kwargs or {}),
    ) as broker_ws:
        if on_connected is not None:
            on_connected()

        async def browser_to_broker() -> None:
            with suppress(Exception):
                async for msg in websocket.iter_text():
                    await broker_ws.send(msg)

        async def broker_to_browser() -> None:
            with suppress(Exception):
                async for msg in broker_ws:
                    await websocket.send_text(str(msg))

        pumps = [
            asyncio.create_task(browser_to_broker()),
            asyncio.create_task(broker_to_browser()),
        ]
        if revalidate is not None:
            pumps.append(
                asyncio.create_task(_revalidate_loop(websocket, revalidate, revalidate_interval))
            )
        try:
            done, _ = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
        finally:
            # Also drain both pumps when the outer request is cancelled. A
            # reconnect must not leave an old view forwarding in the background.
            for task in pumps:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
        for task in done:
            task.result()


async def _proxy_ws(
    websocket: WebSocket,
    session_id: str,
    skuld_reg: SkuldPortRegistry,
    broker_path: str,
    *,
    log_label: str,
    include_cookie: bool = False,
    require_owner: bool = False,
) -> None:
    """Proxy a WebSocket to a session's Skuld broker (ownership-guarded).

    Shared by the browser ``/session`` and the ravn ``/ws/ravn/{peer}`` legs:
    identity guard → port lookup → bidirectional pump → M-8 self-heal on a
    never-connected broker leg. The only per-route differences are the broker
    path, whether the browser cookie is forwarded, the log label, and whether
    the leg is owner-only. Identity forwarding follows the registry's
    ``dev_identity`` policy.

    ``require_owner`` is for legs "attach" alone must not open — the ravn
    peer leg lets a resident join ANOTHER session's room with full authority
    over this one (a session_participants grant is never enough for that).
    """
    principal = await _proxy_principal(websocket)
    user_id, tenant_id, roles = (
        (principal.user_id, principal.tenant_id, tuple(principal.roles))
        if principal is not None
        else (None, None, ())
    )
    if not await skuld_reg.may_attach(session_id, user_id, tenant_id, roles):
        await websocket.close(code=1008, reason="Not authorized for this session")
        return
    room_role = await skuld_reg.resolve_room_role(session_id, user_id, tenant_id, roles)
    if room_role is None:
        # may_attach already allowed this connection, but no role resolver
        # could name a specific role for it. Stamp the least-privilege
        # value explicitly rather than omitting the header: the broker
        # (ws_auth.room_role_source="proxy" on this, the process backend)
        # treats a genuinely MISSING header from a loopback dial as owner,
        # and this proxy always dials the broker over loopback — an
        # omitted header here would silently hand out owner to a
        # connection this proxy could not actually place a role on.
        room_role = "viewer"
    if require_owner and room_role != "owner":
        await websocket.close(code=1008, reason="This connection requires the owner room role")
        return

    port = skuld_reg.get_port(session_id)
    target = None if port is not None else await skuld_reg.resolve_target(session_id)
    if port is None and target is None:
        # A newly advertised session may not have a listening broker yet.
        # Only the runtime's confirmed death makes this connection terminal.
        confirmed_dead = await skuld_reg.reconcile_dead(session_id)
        await websocket.accept()
        await websocket.close(
            code=4410 if confirmed_dead else 4411,
            reason="Session is no longer running"
            if confirmed_dead
            else "Session is starting; retry",
        )
        return

    connected = False

    def _mark_connected() -> None:
        nonlocal connected
        connected = True

    async def _revalidate() -> bool:
        """Re-check attach and room role; False closes the live socket.

        A single resolve_room_role call covers both: it is None exactly when
        may_attach would now also refuse (read_room and attach are granted to
        the identical principal set — see SessionService.attributed_resource),
        and a lower-ranked result than the connection's original room_role is
        a demotion, which must close it just as a full revoke does.
        """
        current_role = await skuld_reg.resolve_room_role(session_id, user_id, tenant_id, roles)
        if current_role is None:
            return False
        original_rank = ROOM_ROLE_RANK.get(room_role or "", -1)
        return ROOM_ROLE_RANK[current_role] >= original_rank

    tracking_key = (session_id, user_id or "")
    skuld_reg.track_connection(*tracking_key, websocket)
    try:
        connect_url = f"ws://127.0.0.1:{port}{broker_path}"
        connect_kwargs: dict[str, object] = {}
        if target is not None:
            connect_url = _session_target_url(target.service_url, broker_path, websocket=True)
            connect_kwargs = {
                "host": target.connect_host,
                "port": target.connect_port,
                "proxy": None,
            }
        # Carry the browser's supported replay negotiation across the proxy.
        # Auth query parameters are handled as headers, never copied into this URL.
        if broker_path == "/session":
            negotiation = {}
            for key, allowed in (
                ("history", {"recent"}),
                ("history_protocol", {"2"}),
                ("history_delivery", {"none"}),
            ):
                value = websocket.query_params.get(key)
                if value in allowed:
                    negotiation[key] = value
            if negotiation:
                connect_url += "?" + urllib.parse.urlencode(negotiation)
        await bridge_websocket(
            websocket,
            connect_url,
            headers=_proxy_forward_headers(
                websocket,
                include_cookie=include_cookie,
                dev_identity=skuld_reg.dev_identity,
                principal=principal,
                room_role=room_role,
            ),
            connect_kwargs=connect_kwargs,
            on_connected=_mark_connected,
            revalidate=_revalidate,
            revalidate_interval=skuld_reg.revalidate_interval_seconds,
        )
    except Exception:
        logger.debug("%s ended for session %s", log_label, _sanitize_log(session_id))
    finally:
        skuld_reg.untrack_connection(*tracking_key, websocket)
        # M-8 self-heal: if the broker leg never connected, only drop a stale
        # RUNNING port when the pod-authoritative reconcile CONFIRMS the
        # session is dead — never on a transient blip against a live pod.
        if not connected:
            confirmed_dead = await skuld_reg.reconcile_dead(session_id)
            if confirmed_dead:
                skuld_reg.unregister(session_id)
            with suppress(Exception):
                await websocket.close(
                    code=4410 if confirmed_dead else 4411,
                    reason="Session is no longer running"
                    if confirmed_dead
                    else "Session is starting; retry",
                )
        else:
            with suppress(Exception):
                await websocket.close()


def get_skuld_registry() -> SkuldPortRegistry | None:
    """Return the active SkuldPortRegistry, if any."""
    return _skuld_registry


def _install_skuld_registry(registry: SkuldPortRegistry) -> None:
    """Expose the active registry to mini-mode composition without hidden wiring."""
    global _skuld_registry  # noqa: PLW0603
    _skuld_registry = registry


def _session_target_url(
    service_url: str,
    path: str,
    *,
    websocket: bool = False,
) -> str:
    """Build a session-service URL while preserving its routing authority."""
    parsed = urllib.parse.urlsplit(service_url)
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.netloc:
        raise ValueError("Session proxy target must be an absolute URL")
    scheme = parsed.scheme
    if websocket:
        scheme = "wss" if parsed.scheme in {"https", "wss"} else "ws"
    return urllib.parse.urlunsplit((scheme, parsed.netloc, path, "", ""))


def _session_http_connect_url(target: SessionProxyTarget, path: str) -> tuple[str, str]:
    """Return the gateway URL and Host header for an HTTP session request."""
    service = urllib.parse.urlsplit(target.service_url)
    scheme = "https" if target.connect_secure else "http"
    host = target.connect_host
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:{target.connect_port}"
    url = urllib.parse.urlunsplit((scheme, netloc, path, "", ""))
    return url, service.netloc


def register_session_proxy_routes(app: FastAPI, skuld_reg: SkuldPortRegistry) -> None:
    """Register the ``/s/{session_id}`` session-proxy routes on *app*.

    Shared by the mini-mode root app and the standalone Volundr deployment
    (K8s), where no CLI root app exists to terminate the browser's session
    traffic. Sessions resolve first by local broker port, then through the
    registry's target resolver (e.g. the OpenShell gateway).
    """

    @app.websocket("/s/{session_id}/session")
    async def skuld_ws_proxy(
        websocket: WebSocket,
        session_id: str,
    ) -> None:
        """Proxy the browser chat WebSocket to the session's Skuld broker."""
        await _proxy_ws(
            websocket,
            session_id,
            skuld_reg,
            "/session",
            log_label="Skuld WS proxy",
            include_cookie=True,
        )

    @app.websocket("/s/{session_id}/ws/ravn/{peer_id}")
    async def skuld_ravn_ws_proxy(
        websocket: WebSocket,
        session_id: str,
        peer_id: str,
    ) -> None:
        """Proxy a ravn participant WebSocket to the session's broker.

        This is how a resident joins ANOTHER session's room through the
        gateway (session_join): the browser chat endpoint is proxied at
        ``/s/{id}/session``, and the sibling ravn endpoint must be proxied
        too, or cross-session joins only work in strip-prefix k8s ingress
        and fail in mini/gateway mode. Owner-only: this leg gives a peer
        full authority over the session, which no session_participants
        grant (however senior) is ever enough for.
        """
        await _proxy_ws(
            websocket,
            session_id,
            skuld_reg,
            f"/ws/ravn/{peer_id}",
            log_label="Ravn WS proxy",
            require_owner=True,
        )

    @app.api_route(
        "/s/{session_id}/api/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE"],
        include_in_schema=False,
    )
    async def skuld_http_proxy(request: Request, session_id: str, path: str) -> Response:
        """Proxy HTTP requests to the Skuld subprocess (ownership-guarded).

        Same attach guard and identity resolution as the WebSocket legs
        (``_proxy_ws``): without it, any authenticated caller who can reach
        the host could POST to a session's broker API (workflow gates, room
        join, ...) regardless of who owns the session.
        """
        principal = await _proxy_principal(request)
        user_id, tenant_id, roles = (
            (principal.user_id, principal.tenant_id, tuple(principal.roles))
            if principal is not None
            else (None, None, ())
        )
        if not await skuld_reg.may_attach(session_id, user_id, tenant_id, roles):
            return JSONResponse({"detail": "Not authorized for this session"}, status_code=403)
        room_role = await skuld_reg.resolve_room_role(session_id, user_id, tenant_id, roles)
        if room_role is None:
            # See the matching comment in _proxy_ws: stamp least privilege
            # explicitly rather than omit the header, which a loopback-dialed
            # proxy connection could otherwise have read as owner.
            room_role = "viewer"

        port = skuld_reg.get_port(session_id)
        target = None if port is not None else await skuld_reg.resolve_target(session_id)
        if port is None and target is None:
            return JSONResponse({"detail": "Session not found"}, status_code=404)

        from urllib.parse import quote

        # Skuld workflow gate ids use ":" as an internal delimiter, so the
        # session proxy needs to accept it in path segments while still
        # rejecting slashes and traversal tokens.
        allowed_segment = re.compile(r"^[A-Za-z0-9._~:-]+$")
        raw_segments = path.split("/")
        normalized_segments: list[str] = []
        for seg in raw_segments:
            if seg in ("", ".", ".."):
                return JSONResponse({"detail": "Invalid path"}, status_code=400)
            if "\\" in seg or not allowed_segment.fullmatch(seg):
                return JSONResponse({"detail": "Invalid path"}, status_code=400)
            normalized_segments.append(seg)

        unquoted_path = "/".join(normalized_segments)
        required_role = required_role_for_route(request.method, unquoted_path)
        # An unresolvable role (may_attach passed, but resolve_room_role could
        # not name one) is least privilege, "viewer" — never "no access at
        # all", which would 403 even the routes every attached caller may use.
        effective_role = room_role or "viewer"
        if ROOM_ROLE_RANK[effective_role] < ROOM_ROLE_RANK[required_role]:
            return JSONResponse(
                {"detail": f"This route requires the {required_role} room role"},
                status_code=403,
            )

        sanitized_path = "/".join(quote(seg, safe="") for seg in normalized_segments)
        proxy_path = f"/api/{sanitized_path}"
        url = f"http://127.0.0.1:{port}{proxy_path}"
        params = dict(
            _without_dev_params(
                request.query_params.multi_items(), dev_identity=skuld_reg.dev_identity
            )
        )
        # Same identity policy as the WebSocket legs: outside local dev the
        # session only ever sees x-auth-* the proxy resolved itself. The room
        # role header is always dropped from the client's own request and
        # replaced with the proxy's own resolution, never conditionally.
        dropped = {"host", "content-length", "transfer-encoding", ROOM_ROLE_HEADER}
        if not skuld_reg.dev_identity:
            dropped |= _IDENTITY_HEADERS
        headers = {k: v for k, v in request.headers.items() if k.lower() not in dropped}
        if not skuld_reg.dev_identity:
            headers.update(_verified_identity_headers(await _proxy_principal(request)))
        if room_role is not None:
            headers[ROOM_ROLE_HEADER] = room_role
        if target is not None:
            url, service_host = _session_http_connect_url(target, proxy_path)
            headers["Host"] = service_host
        body = await request.body()

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.request(
                    method=request.method,
                    url=url,
                    params=params,
                    headers=headers,
                    content=body if body else None,
                )
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers=dict(resp.headers),
            )
        except httpx.ConnectError:
            return JSONResponse(
                {"detail": "Skuld broker not ready"},
                status_code=502,
            )

    @app.get("/s/{session_id}/health", include_in_schema=False)
    async def skuld_health_proxy(request: Request, session_id: str) -> Response:
        """Proxy health check to the Skuld subprocess."""
        del request
        port = skuld_reg.get_port(session_id)
        target = None if port is not None else await skuld_reg.resolve_target(session_id)
        if port is None and target is None:
            return JSONResponse({"detail": "Session not found"}, status_code=404)

        try:
            url = f"http://127.0.0.1:{port}/health"
            headers: dict[str, str] = {}
            if target is not None:
                url, service_host = _session_http_connect_url(target, "/health")
                headers["Host"] = service_host
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(url, headers=headers)
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers=dict(resp.headers),
            )
        except httpx.ConnectError:
            return JSONResponse(
                {"detail": "Skuld broker not ready"},
                status_code=502,
            )
