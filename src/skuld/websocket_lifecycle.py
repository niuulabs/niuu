"""WebSocket authentication and connection lifecycle for Skuld."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import asdict
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from identity.models import Principal, Resource
from identity.ports import AuthorizationEvaluationError
from niuu.domain.history_control import history_gap
from niuu.domain.text_projection import projection_revision
from niuu.domain.transcript_reducer import PER_CONNECT_MARKER
from niuu.room_access import ROOM_ROLE_RANK
from skuld.channels import WebSocketChannel, _is_expected_ws_disconnect
from skuld.control_errors import control_error_frame
from skuld.conversation_read import conversation_rows, wait_history_quiet
from skuld.conversation_snapshot import (
    ConversationSnapshotTooLargeError,
    prepare_conversation_snapshot,
    prepare_history_page,
    prepare_recent_snapshot,
)
from skuld.room_role_port import RoomRoleResolutionError
from skuld.websocket_auth import (
    _decode_jwt_claims,
    _extract_token_from_websocket,
    _is_loopback_ws_client,
    _resolve_ws_principal,
)

logger = logging.getLogger("skuld.broker")


def _sanitize_log(value: object) -> str:
    return str(value).replace("\n", "\\n").replace("\r", "\\r")


class WebSocketLifecycleMixin:
    """Own browser, CLI, and Ravn WebSocket connection handling."""

    async def _authorize_websocket(self, websocket: WebSocket, *, endpoint: str) -> bool:
        """Enforce configured ownership using only proxy-verified identity."""
        cfg = self._settings.ws_auth
        if not cfg.enforce_ownership:
            return True

        owner_id = (self._settings.session.owner_id or "").strip()
        session_tenant = (self._settings.session.tenant_id or "").strip()
        if not owner_id or not session_tenant:
            return False

        principal = _resolve_ws_principal(
            websocket,
            user_id_header=cfg.user_id_header,
            tenant_header=cfg.tenant_header,
            roles_header=cfg.roles_header,
        )
        if principal is None:
            if (
                cfg.allow_loopback
                and endpoint in ("handle_cli_websocket", "handle_ravn_websocket")
                and _is_loopback_ws_client(websocket)
                and not websocket.headers.get("x-forwarded-for")
            ):
                return True
            logger.warning(
                "%s: rejecting unauthenticated WebSocket (session owner enforced)",
                endpoint,
            )
            return False

        if principal.tenant_id != session_tenant:
            logger.warning(
                "%s: rejecting cross-tenant WebSocket (user=%s)",
                endpoint,
                _sanitize_log(principal.user_id),
            )
            return False

        mapped_roles = [cfg.role_mapping.get(r, r) for r in principal.roles]
        roles = [r for r in mapped_roles if r != "volundr:admin" and r not in cfg.admin_roles]
        if any(r in cfg.admin_roles for r in mapped_roles):
            roles.append("volundr:admin")
        actor = Principal(principal.user_id, "", principal.tenant_id, roles)
        identity = getattr(self, "_ws_identity", None)
        if identity is not None:
            from niuu.ports.identity import InvalidTokenError

            headers = dict(websocket.headers)
            query_token = websocket.query_params.get("token") or websocket.query_params.get(
                "access_token"
            )
            if query_token and "authorization" not in headers:
                headers["authorization"] = f"Bearer {query_token}"
            try:
                actor = await identity.validate_headers(headers)
            except (InvalidTokenError, AuthorizationEvaluationError):
                return False
            if actor.user_id != principal.user_id or actor.tenant_id != principal.tenant_id:
                return False
        resource = Resource(
            "session",
            self.session_id,
            {
                "owner_id": owner_id,
                "tenant_id": session_tenant,
            },
        )
        try:
            return await self._ws_authorization.is_allowed(actor, "start", resource)
        except AuthorizationEvaluationError:
            logger.exception("WebSocket authorization failed")
            return False

    _VALID_ROOM_ROLES = frozenset({"owner", "approver", "viewer"})

    async def _resolve_room_role(self, websocket: WebSocket) -> str | None:
        """Resolve this connection's room role for per-message authorization.

        A valid ``room_role_header`` always wins outright — it is the
        session-proxy-verified value, which already encodes the dev-identity
        default (session_proxy stamps "owner" under dev identity with no
        resolver configured).

        Otherwise this defers entirely to ``ws_auth.room_role_source``
        (mirrors ``skuld.broker_api._effective_room_role`` exactly — the two
        must never diverge, or the same caller gets a different room role on
        the HTTP and WebSocket legs of the same session):

        - "deployment" (the default — Kubernetes, OpenShell, VM, and any
          backend other than a proxy-fronted process): this pod's own auth
          boundary (ext_authz / enforce_ownership / the deployment's Gateway)
          already gates every caller who reaches this pod at all, and
          participants are not supported on these backends (invites are
          refused with 409), so a missing header simply means owner —
          identical to this pod's behavior before session_participants
          existed.
        - "proxy" (rendered only for the process backend): the session proxy
          resolves and stamps the header itself from session_participants
          grants, so trust it — a missing header means viewer, except a
          loopback caller carrying no x-forwarded-for (same-pod tooling a
          reverse proxy could never present as).
        - "remote" (Kubernetes/OpenShell/VM pods deliberately opted in): asks
          Forge for this caller's grant via ``self._room_role_resolver``
          (``skuld.room_role_remote.RemoteAuthorizationAdapter``). Returns
          ``None`` — a real "no grant" answer, never a default role — when
          the caller has no verified identity headers or no active grant.
          Raises ``RoomRoleResolutionError`` when Forge cannot be reached;
          ``handle_websocket`` treats that as a deny, never a fallback role.
        """
        cfg = self._settings.ws_auth
        header_role = websocket.headers.get(cfg.room_role_header, "").strip().lower()
        if header_role in self._VALID_ROOM_ROLES:
            return header_role
        if cfg.room_role_source == "deployment":
            return "owner"
        if cfg.room_role_source == "remote":
            principal = _resolve_ws_principal(
                websocket,
                user_id_header=cfg.user_id_header,
                tenant_header=cfg.tenant_header,
                roles_header=cfg.roles_header,
            )
            if principal is None:
                # Same-pod tooling exception "proxy" mode already carries —
                # only an in-pod caller can present as loopback with no XFF.
                if _is_loopback_ws_client(websocket) and not websocket.headers.get(
                    "x-forwarded-for"
                ):
                    return "owner"
                return None
            if self._room_role_resolver is None:
                raise RoomRoleResolutionError(
                    "ws_auth.room_role_source is 'remote' but no room_role_remote adapter "
                    "was constructed — this should be unreachable (WsAuthConfig validates "
                    "this at load time); check skuld broker startup logs."
                )
            return await self._room_role_resolver.resolve_role(
                session_id=self.session_id,
                user_id=principal.user_id,
                tenant_id=principal.tenant_id,
                roles=list(principal.roles),
            )
        if _is_loopback_ws_client(websocket) and not websocket.headers.get("x-forwarded-for"):
            return "owner"
        return "viewer"

    async def _revalidate_remote_room_role(self, websocket: WebSocket, original_role: str) -> bool:
        """Re-check a 'remote'-mode connection's room role; False closes it.

        A grant revoked (or demoted) after connect must not leave the live
        socket usable at its original privilege until the browser happens to
        reconnect. Raises ``RoomRoleResolutionError`` on a resolution
        failure — the caller (``_room_role_revalidation_loop``) decides how
        much of that failure to tolerate before closing, rather than this
        method silently absorbing it (see the loop's own docstring for why
        that grace must be bounded, not unlimited).
        """
        current_role = await self._resolve_room_role(websocket)
        if current_role is None:
            return False
        return ROOM_ROLE_RANK[current_role] >= ROOM_ROLE_RANK[original_role]

    async def _room_role_revalidation_loop(self, websocket: WebSocket, original_role: str) -> None:
        """Periodically re-check a 'remote'-mode room role; close on revoke/demotion.

        Mirrors ``niuu.session_proxy._revalidate_loop``'s asymmetric failure
        handling — a *transient* ``RoomRoleResolutionError`` (a momentary
        Forge blip) does not immediately close an otherwise-healthy
        connection — but that grace is BOUNDED, not unlimited: an authority
        that never recovers must not keep a socket open forever on stale
        authorization. After ``room_role_revalidate_max_consecutive_failures``
        in a row, or ``room_role_revalidate_max_staleness_seconds`` since the
        first one (whichever comes first), the socket closes (1011) just
        like any other unexpected loop failure. A single successful
        revalidation (allowed OR explicitly denied — anything that isn't
        this typed error) resets both counters.

        Any OTHER exception reaching this function is an unexpected failure
        of the loop itself (not a routine revalidation outcome) and must not
        be swallowed: a silently-dead loop leaves an already-open socket at
        its original privilege forever — the exact gap this loop exists to
        close. Fail closed and loud instead.
        """
        cfg = self._settings.ws_auth
        consecutive_failures = 0
        first_failure_at: float | None = None
        try:
            while True:
                await asyncio.sleep(cfg.room_role_revalidate_interval_seconds)
                try:
                    allowed = await self._revalidate_remote_room_role(websocket, original_role)
                except RoomRoleResolutionError:
                    consecutive_failures += 1
                    now = time.monotonic()
                    if first_failure_at is None:
                        first_failure_at = now
                    stale_for = now - first_failure_at
                    if (
                        consecutive_failures >= cfg.room_role_revalidate_max_consecutive_failures
                        or stale_for >= cfg.room_role_revalidate_max_staleness_seconds
                    ):
                        logger.error(
                            "Room role revalidation failed %d times over %.1fs; closing "
                            "the socket rather than trusting a stale authorization "
                            "indefinitely",
                            consecutive_failures,
                            stale_for,
                        )
                        await websocket.close(
                            code=1011, reason="Room role authorization unavailable"
                        )
                        return
                    logger.warning(
                        "Room role revalidation failed transiently (%d/%d); keeping the "
                        "connection open",
                        consecutive_failures,
                        cfg.room_role_revalidate_max_consecutive_failures,
                        exc_info=True,
                    )
                    continue
                consecutive_failures = 0
                first_failure_at = None
                if not allowed:
                    await websocket.close(code=1008, reason="Access revoked or downgraded")
                    return
        except Exception:
            logger.error(
                "Room role revalidation loop failed unexpectedly; closing the socket "
                "rather than leaving it un-revalidated for the rest of its life",
                exc_info=True,
            )
            with contextlib.suppress(Exception):
                await websocket.close(code=1011, reason="Revalidation failed")

    def _update_jwt_from_websocket(self, websocket: WebSocket) -> None:
        """Extract and store JWT from an incoming WebSocket connection.

        Prefers the Authorization header (set by Envoy or reverse proxy),
        then falls back to the access_token query parameter (browser).
        Updates the stored JWT on each connection so token refreshes
        propagate automatically.
        """
        try:
            token = _extract_token_from_websocket(websocket)
        except Exception:
            logger.debug("Failed to extract JWT from WebSocket", exc_info=True)
            return
        if not token:
            if self._user_jwt is None:
                logger.warning("No JWT found on WebSocket connection")
            return

        self._user_jwt = token
        self._user_claims = _decode_jwt_claims(token)

        user_id = self._user_claims.get("sub", "unknown")
        logger.info("JWT updated from WebSocket connection (sub=%s)", _sanitize_log(user_id))

        # Propagate new auth headers to the chronicle watcher
        if self._chronicle_watcher is not None:
            self._chronicle_watcher.update_headers(self._build_auth_headers())

    async def _safe_browser_send_json(self, websocket: WebSocket, payload: dict[str, Any]) -> bool:
        """Send a browser frame unless the client has already disconnected."""
        try:
            await websocket.send_json(payload)
            return True
        except Exception as exc:
            if _is_expected_ws_disconnect(exc):
                logger.info("WebSocket disconnected")
                return False
            raise

    async def handle_websocket(self, websocket: WebSocket) -> None:
        """Handle a browser WebSocket connection at /session."""
        # Ownership check first — a rejected caller must not overwrite the
        # broker's stored JWT or reach any session frames.
        if not await self._authorize_websocket(websocket, endpoint="handle_websocket"):
            await websocket.close(code=1008, reason="Not authorized for this session")
            return

        # Pre-accept: the room-role header is only trustworthy from the raw
        # request headers.
        try:
            room_role = await self._resolve_room_role(websocket)
        except RoomRoleResolutionError:
            logger.exception("Room role resolution failed")
            await websocket.close(code=1011, reason="Room role authorization unavailable")
            return
        if room_role is None:
            await websocket.close(
                code=1008, reason="No active session_participants grant for this session"
            )
            return
        # Extract JWT before accepting — headers are available pre-accept.
        # Only an OWNER connection may update the broker's single stored
        # _user_jwt/_user_claims: they are read later for actions taken "as
        # the user" (e.g. the chronicle watcher's auth headers). A viewer or
        # approver connecting after the owner must not silently swap the
        # broker's notion of who it is acting as.
        if room_role == "owner":
            self._update_jwt_from_websocket(websocket)

        await websocket.accept()
        # Internal-visibility default comes from the ONE configured source (SRD
        # FR-7 / INV-10) — NOT the hardcoded WebSocketChannel default — so the live
        # channel, the replay tail, and the cold-read all read the same default and
        # move together when it is flipped.
        protocol2 = websocket.query_params.get("history_protocol") == "2"
        no_history = protocol2 and websocket.query_params.get("history_delivery") == "none"
        channel = WebSocketChannel(
            websocket,
            show_internal=self._settings.default_show_internal,
            max_frame_bytes=self._settings.live_frame_max_bytes,
            history_protocol=2 if protocol2 else 0,
            history_bootstrap_max_frames=self._settings.history_bootstrap_max_frames,
            room_role=room_role,
        )
        if not protocol2:
            self._channels.add(channel)
        conn_count = self._channels.count
        logger.info("WebSocket connected, total channels: %d", conn_count)

        # A revoked or demoted session_participants grant must not leave
        # this already-open socket usable at its original privilege until
        # the browser happens to reconnect (see docs/operator/session
        # -participants.md's "within a few seconds" claim) — only "remote"
        # mode needs this: "deployment" and "proxy" never change mid
        # -connection (the pod's own auth boundary, or the session proxy's
        # OWN revalidation loop upstream of this pod, already cover those).
        revalidate_task: asyncio.Task | None = None
        if self._settings.ws_auth.room_role_source == "remote":
            revalidate_task = asyncio.create_task(
                self._room_role_revalidation_loop(websocket, room_role)
            )

        try:
            if not self._transport:
                logger.error("handle_websocket: transport not initialized")
                _transport_err = control_error_frame(
                    "Transport not initialized", code="transport_not_ready"
                )
                self._enqueue_event_log(_transport_err)
                await self._safe_browser_send_json(websocket, _transport_err)
                return

            # Join background resume even when the transport reports alive before
            # completing its handshake. Every start path uses the same lock.
            if not self._is_room_routed_session():
                try:
                    await self._ensure_transport_started()
                except Exception as e:
                    logger.error("handle_websocket: transport start failed: %r", e, exc_info=True)
                    _start_err = {"type": "error", "content": f"Transport start failed: {e}"}
                    self._enqueue_event_log(_start_err)
                    await self._safe_browser_send_json(websocket, _start_err)
                    return

            # Report session start to timeline (once, on first connection)
            asyncio.create_task(self._report_session_start())

            # Send welcome message (broker-originated first-connect frame: log first).
            # PER-CONNECT handshake: addressed to THIS socket only, not the canonical
            # shared stream. Mark it so the RAW read paths drop HISTORICAL welcomes
            # (a connecting client always gets its own) — the system kind is shared
            # with genuine CLI system frames, so it can't be excluded by kind alone
            # (SRD INV-5). The marker is inert on the wire.
            if not await self._safe_send_broker_frame_to(
                websocket,
                {
                    "type": "system",
                    "content": f"Connected to session {self.session_id}",
                    PER_CONNECT_MARKER: True,
                },
            ):
                return
            logger.debug("handle_websocket: welcome message sent")

            # Send transport capabilities so the frontend knows which
            # controls to render. Broker-originated first-connect frame: log first.
            if self._transport:
                caps = {"type": "capabilities", **asdict(self._transport.capabilities)}
                caps["room_prompt_resend"] = self._room_bridge is not None
                caps["history_protocol"] = 2
                if not await self._safe_send_broker_frame_to(websocket, caps):
                    return
                logger.debug("handle_websocket: capabilities sent")

            # Protocol2 readers wait for a coherent state, without locking or
            # delaying writers. Register only AFTER synchronous capture so events
            # included in history cannot also be buffered as post-snapshot deltas.
            snapshot = None
            gap = None
            if not no_history:
                try:
                    if protocol2:
                        await wait_history_quiet(self)
                    replay_turns = conversation_rows(self)
                    recent_requested = (
                        protocol2 or websocket.query_params.get("history") == "recent"
                    )
                    frame = {
                        "type": "conversation_history",
                        "turns": replay_turns,
                        "projection_revision": projection_revision(replay_turns),
                        "head_seq": self._event_log_seq,
                    }
                    logger.info(
                        "Replaying %d recent=%s conversation turns",
                        len(replay_turns),
                        recent_requested,
                    )
                    if protocol2:
                        snapshot = prepare_history_page(
                            {**frame, "history_source": "gateway"},
                            session_id=self.session_id,
                            max_bytes=min(
                                self._settings.conversation_recent_max_bytes,
                                self._settings.conversation_snapshot_max_bytes,
                            ),
                            max_turns=self._settings.conversation_recent_max_turns,
                        )
                    elif replay_turns and recent_requested:
                        snapshot = prepare_recent_snapshot(
                            frame,
                            max_bytes=min(
                                self._settings.conversation_recent_max_bytes,
                                self._settings.conversation_snapshot_max_bytes,
                            ),
                            max_turns=self._settings.conversation_recent_max_turns,
                        )
                    elif replay_turns:
                        snapshot = prepare_conversation_snapshot(
                            frame, max_bytes=self._settings.conversation_snapshot_max_bytes
                        )
                except (ConversationSnapshotTooLargeError, TimeoutError) as exc:
                    logger.warning("WebSocket conversation replay requires REST: %s", exc)
                    if protocol2:
                        gap = history_gap(
                            "snapshot_race"
                            if isinstance(exc, TimeoutError)
                            else "snapshot_too_large",
                            head_seq=self._event_log_seq,
                        )
                    else:
                        gap = {
                            "type": "error",
                            "code": "conversation_history_too_large",
                            "content": (
                                "Conversation history is too large for WebSocket replay. "
                                "Reload history through REST."
                            ),
                            PER_CONNECT_MARKER: True,
                        }
            if protocol2:
                self._channels.add(channel)
            if snapshot is not None:
                if not await self._safe_browser_send_json(websocket, snapshot):
                    return
            if gap is not None:
                if not await self._safe_send_broker_frame_to(websocket, gap):
                    return

            # Send current room state to late-joining browsers when room mode active
            if self._room_bridge is not None:
                if not await self._safe_browser_send_json(
                    websocket,
                    self._room_bridge.get_room_state_event(),
                ):
                    return

            # Permission requests are transport RPCs, not conversation turns.
            # Replay outstanding approvals so a browser that reconnects after
            # the event was emitted still sees the allow/deny callout.
            if self._pending_permission_requests:
                logger.info(
                    "Replaying %d pending permission request(s) to new browser",
                    len(self._pending_permission_requests),
                )
                for permission_request in list(self._pending_permission_requests.values()):
                    if not await self._safe_browser_send_json(websocket, permission_request):
                        return

            # Same for ask_user_question: these are CLI events (not control_request
            # RPCs), so they're not in the permission set above. Re-surface any
            # outstanding question so a client reconnecting WHILE the agent is blocked
            # gets the answerable card instead of a frozen/"dead" session — the core
            # tmux-interactive reconnect bug.
            if self._pending_ask_user_questions:
                logger.info(
                    "Replaying %d pending ask_user_question(s) to new browser",
                    len(self._pending_ask_user_questions),
                )
                for ask_question in list(self._pending_ask_user_questions.values()):
                    if not await self._safe_browser_send_json(websocket, ask_question):
                        return

            # Persisted question text is evidence, not a usable RPC address after
            # the native process restarts. Show the retained card honestly and
            # never route its answer onto a different live prompt.
            for recovered in [
                *self._unrestored_questions.values(),
                *self._unrestored_permissions.values(),
            ]:
                if not await self._safe_browser_send_json(websocket, recovered):
                    return
                if not await self._safe_browser_send_json(
                    websocket,
                    control_error_frame(
                        "This pending control survived in history, but the native process "
                        "must reissue it before an answer can be delivered.",
                        recovered,
                        code="question_recovery_required",
                    ),
                ):
                    return

            # Plan + running agents: a late-joining client should immediately know
            # the current plan and the running fleet without waiting for the next
            # change — same guarantee questions/permissions get above.
            if self._current_plan is not None:
                if not await self._safe_browser_send_json(websocket, self._current_plan):
                    return
            self._reap_dead_teammates()
            if self._running_agents:
                logger.info(
                    "Replaying %d running agent(s) to new browser",
                    len(self._running_agents),
                )
                for agent in list(self._running_agents.values()):
                    frame = {
                        "type": "agent_update",
                        "event_type": "claude.agent",
                        "action": "started",
                        "agent": agent,
                        "metadata": {"source": "reconnect_replay"},
                    }
                    if not await self._safe_browser_send_json(websocket, frame):
                        return

            if protocol2:
                await channel.finish_history_bootstrap()

            # Handle messages from browser
            while True:
                # INV-7: a malformed / non-JSON inbound frame must NOT tear down the
                # socket or drop every subsequent valid message. receive_json() is
                # INSIDE the per-message try so a bad frame is logged + surfaced as an
                # error frame and the loop CONTINUES. A genuine disconnect still raises
                # WebSocketDisconnect (and the expected-disconnect classifier below),
                # which we re-raise to exit the loop cleanly.
                try:
                    data = await websocket.receive_json()
                except (ValueError, UnicodeDecodeError, TypeError) as e:
                    # A genuinely MALFORMED inbound payload (non-JSON / wrong type):
                    # log + surface an error frame and CONTINUE so one bad frame never
                    # tears down the socket or drops the subsequent valid messages
                    # (INV-7, second clause). ``json.JSONDecodeError`` is a ``ValueError``.
                    # NOTE: this is deliberately NARROW — a transport-level failure
                    # (WebSocketDisconnect, a connection RuntimeError, any other Exception)
                    # is NOT a malformed frame and must propagate to tear the loop down,
                    # so a permanently-failing receive can never spin forever.
                    logger.warning(
                        "handle_websocket: malformed inbound frame ignored: %s",
                        _sanitize_log(str(e)),
                    )
                    _bad_frame_err = {
                        "type": "error",
                        "code": "malformed_message",
                        "content": f"malformed message ignored: {e}",
                    }
                    self._enqueue_event_log(_bad_frame_err)
                    with contextlib.suppress(Exception):
                        await websocket.send_json(_bad_frame_err)
                    continue
                logger.debug(
                    "handle_websocket: browser msg: %s",
                    _sanitize_log(json.dumps(data)[:500]),
                )
                try:
                    await self._dispatch_browser_message(data, sender_ws=websocket)
                except Exception as e:
                    logger.exception("Error processing browser message: %s", _sanitize_log(data))
                    _dispatch_err = control_error_frame(e, data)
                    self._enqueue_event_log(_dispatch_err)
                    with contextlib.suppress(Exception):
                        await websocket.send_json(_dispatch_err)

        except WebSocketDisconnect:
            logger.info("WebSocket disconnected")
        except Exception as e:
            if _is_expected_ws_disconnect(e):
                logger.info("WebSocket disconnected")
                return
            logger.exception("WebSocket error")
            try:
                _ws_err = control_error_frame(e, code="websocket_error")
                self._enqueue_event_log(_ws_err)
                await websocket.send_json(_ws_err)
            except Exception:
                logger.debug("Failed to send error response to WebSocket", exc_info=True)
        finally:
            if revalidate_task is not None:
                revalidate_task.cancel()
            self._channels.remove(channel)
            remaining = self._channels.count
            logger.info("Connection closed, remaining channels: %d", remaining)

    async def handle_cli_websocket(self, websocket: WebSocket, session_id: str) -> None:
        """Handle the CLI WebSocket connection at /ws/cli/{session_id}.

        Only used by the SdkWebSocketTransport. The CLI process connects
        back to this endpoint after being spawned with --sdk-url.
        """
        logger.info(
            "handle_cli_websocket: incoming CLI connection for session=%s (transport=%s)",
            _sanitize_log(session_id),
            type(self._transport).__name__ if self._transport else None,
        )

        if not await self._authorize_websocket(websocket, endpoint="handle_cli_websocket"):
            await websocket.close(code=1008, reason="Not authorized for this session")
            return

        if not self._transport or not self._transport.capabilities.cli_websocket:
            logger.warning(
                "CLI WebSocket received but transport %s does not support SDK WebSocket protocol",
                type(self._transport).__name__ if self._transport else "None",
            )
            await websocket.close(code=1008, reason="SDK transport not active")
            return

        if session_id != self.session_id:
            logger.warning(
                "CLI WebSocket session mismatch: expected %s, got %s",
                _sanitize_log(self.session_id),
                _sanitize_log(session_id),
            )
            await websocket.close(code=1008, reason="Session ID mismatch")
            return

        logger.info("handle_cli_websocket: attaching CLI websocket to transport")
        await self._transport.attach_cli_websocket(websocket)

        # Block until the receive loop finishes (CLI disconnects)
        logger.info("handle_cli_websocket: waiting for CLI disconnect")
        await self._transport.wait_for_cli_disconnect()
        logger.info("handle_cli_websocket: CLI disconnected, handler returning")

    async def handle_ravn_websocket(self, websocket: WebSocket, peer_id: str) -> None:
        """Handle a Ravn WebSocket connection at /ws/ravn/{peer_id}.

        Accepts NDJSON collaboration frames projected by Ravn and forwards
        them to the room adapter. Only active when room mode is enabled.
        """
        if self._room_bridge is None:
            logger.warning(
                "handle_ravn_websocket: room mode disabled, rejecting peer_id=%s",
                _sanitize_log(peer_id),
            )
            await websocket.close(code=1008, reason="Room mode is not enabled")
            return

        if not await self._authorize_websocket(websocket, endpoint="handle_ravn_websocket"):
            await websocket.close(code=1008, reason="Not authorized for this session")
            return

        await websocket.accept()
        logger.info("handle_ravn_websocket: Ravn connected peer_id=%s", _sanitize_log(peer_id))

        # Register with peer_id as initial persona; enriched on first frame
        await self._room_bridge.register(
            peer_id=peer_id,
            persona=peer_id,
            websocket=websocket,
        )
        _registered_with_metadata = False

        try:
            while True:
                raw = await websocket.receive_text()
                for line in raw.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        frame = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning(
                            "handle_ravn_websocket: invalid JSON from peer_id=%s",
                            _sanitize_log(peer_id),
                        )
                        continue

                    # Enrich participant on first frame with persona metadata
                    if not _registered_with_metadata and (
                        frame.get("persona") or frame.get("subscribes_to")
                    ):
                        _registered_with_metadata = True
                        await self._room_bridge.register(
                            peer_id=peer_id,
                            persona=frame.get("persona", peer_id),
                            websocket=websocket,
                            display_name=frame.get("display_name", ""),
                            subscribes_to=frame.get("subscribes_to"),
                            emits=frame.get("emits"),
                            tools=frame.get("tools"),
                        )

                    await self._room_bridge.handle_collaboration_frame(peer_id, frame)

        except WebSocketDisconnect:
            logger.info(
                "handle_ravn_websocket: Ravn disconnected peer_id=%s",
                _sanitize_log(peer_id),
            )
        except Exception:
            logger.exception(
                "handle_ravn_websocket: error from peer_id=%s",
                _sanitize_log(peer_id),
            )
        finally:
            await self._room_bridge.unregister(peer_id)
