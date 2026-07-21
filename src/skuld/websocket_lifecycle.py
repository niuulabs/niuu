"""WebSocket authentication and connection lifecycle for Skuld."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import asdict
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from niuu.domain.transcript_reducer import PER_CONNECT_MARKER
from skuld.channels import WebSocketChannel, _is_expected_ws_disconnect
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

    def _authorize_websocket(self, websocket: WebSocket, *, endpoint: str) -> bool:
        """Enforce session ownership on an inbound WebSocket connection.

        Authorization only — token signatures are validated upstream (Envoy /
        API gateway). The verdict mirrors Volundr's
        ``SimpleRoleAuthorizationAdapter``: tenant scoping first, admin
        bypass, then owner match. Sessions without an ``owner_id`` (legacy or
        unauthenticated dev sessions) are not restricted. Unauthenticated
        loopback peers (the in-pod CLI, flock ravn daemons) are trusted when
        ``ws_auth.allow_loopback`` is set — they share the pod trust boundary.
        """
        cfg = self._settings.ws_auth
        if not cfg.enforce_ownership:
            return True

        owner_id = (self._settings.session.owner_id or "").strip()
        if not owner_id:
            return True

        principal = _resolve_ws_principal(websocket)
        if principal is None:
            if cfg.allow_loopback and _is_loopback_ws_client(websocket):
                return True
            logger.warning(
                "%s: rejecting unauthenticated WebSocket (session owner enforced)",
                endpoint,
            )
            return False

        session_tenant = (self._settings.session.tenant_id or "").strip()
        if session_tenant and principal.tenant_id and principal.tenant_id != session_tenant:
            logger.warning(
                "%s: rejecting cross-tenant WebSocket (user=%s)",
                endpoint,
                _sanitize_log(principal.user_id),
            )
            return False

        if any(role in principal.roles for role in cfg.admin_roles):
            return True

        if principal.user_id == owner_id:
            return True

        logger.warning(
            "%s: rejecting WebSocket from non-owner (user=%s)",
            endpoint,
            _sanitize_log(principal.user_id),
        )
        return False

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
        if not self._authorize_websocket(websocket, endpoint="handle_websocket"):
            await websocket.close(code=1008, reason="Not authorized for this session")
            return

        # Extract JWT before accepting — headers are available pre-accept
        self._update_jwt_from_websocket(websocket)

        await websocket.accept()
        # Internal-visibility default comes from the ONE configured source (SRD
        # FR-7 / INV-10) — NOT the hardcoded WebSocketChannel default — so the live
        # channel, the replay tail, and the cold-read all read the same default and
        # move together when it is flipped.
        channel = WebSocketChannel(websocket, show_internal=self._settings.default_show_internal)
        self._channels.add(channel)
        conn_count = self._channels.count
        logger.info("WebSocket connected, total channels: %d", conn_count)

        try:
            if not self._transport:
                logger.error("handle_websocket: transport not initialized")
                _transport_err = {"type": "error", "content": "Transport not initialized"}
                self._enqueue_event_log(_transport_err)
                await self._safe_browser_send_json(websocket, _transport_err)
                return

            # Lazy-start transport on first browser connection
            if not self._transport.is_alive:
                if self._is_room_routed_session():
                    logger.info(
                        "handle_websocket: room-routed session detected; "
                        "skipping transport lazy-start"
                    )
                else:
                    logger.info("handle_websocket: transport not alive, starting...")
                    try:
                        await self._transport.start()
                        logger.info("handle_websocket: transport started successfully")
                    except Exception as e:
                        logger.error(
                            "handle_websocket: transport.start() failed: %r",
                            e,
                            exc_info=True,
                        )
                        _start_err = {
                            "type": "error",
                            "content": f"Transport start failed: {e}",
                        }
                        self._enqueue_event_log(_start_err)
                        await self._safe_browser_send_json(websocket, _start_err)
                        return
            else:
                logger.debug("handle_websocket: transport already alive")

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
                if not await self._safe_send_broker_frame_to(websocket, caps):
                    return
                logger.debug("handle_websocket: capabilities sent")

            # Replay conversation history so late-joining browsers see
            # earlier messages (including the initial prompt)
            # Whole-truth unification: replay completed turns PLUS the in-flight turn so a
            # reconnect mid-run reconstructs the FULL truth (not just new frames from now on).
            in_progress_turn = self._serialize_in_progress_turn()
            if self._conversation_turns or in_progress_turn is not None:
                replay_turns = [asdict(t) for t in self._conversation_turns]
                if in_progress_turn is not None:
                    replay_turns.append(in_progress_turn)
                logger.info(
                    "Replaying %d conversation turn(s) to new browser",
                    len(replay_turns),
                )
                if not await self._safe_browser_send_json(
                    websocket,
                    {
                        "type": "conversation_history",
                        "turns": replay_turns,
                        # SRD FR-6: the durable-log head seq at reconnect time. The
                        # client loads this state, then resumes the live tail from
                        # head_seq+1 with no gap and no duplicate (the broker keeps
                        # appending to the SAME monotonic seq it broadcasts from).
                        "head_seq": self._event_log_seq,
                    },
                ):
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

            # Plan + running agents: a late-joining client should immediately know
            # the current plan and the running fleet without waiting for the next
            # change — same guarantee questions/permissions get above.
            if self._current_plan is not None:
                if not await self._safe_browser_send_json(websocket, self._current_plan):
                    return
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
                    _dispatch_err = {"type": "error", "content": str(e)}
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
                _ws_err = {"type": "error", "content": str(e)}
                self._enqueue_event_log(_ws_err)
                await websocket.send_json(_ws_err)
            except Exception:
                logger.debug("Failed to send error response to WebSocket", exc_info=True)
        finally:
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

        if not self._authorize_websocket(websocket, endpoint="handle_cli_websocket"):
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

        if not self._authorize_websocket(websocket, endpoint="handle_ravn_websocket"):
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
