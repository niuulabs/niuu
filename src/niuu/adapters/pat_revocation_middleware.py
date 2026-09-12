"""Enforce PAT revocation and expire authenticated WebSocket connections.

JWT signatures are verified by Envoy. This middleware never establishes identity;
it only narrows the lifetime of credentials admitted by the authentication layer.
"""

from __future__ import annotations

import asyncio
import math
import time
from urllib.parse import parse_qs

import jwt
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from niuu.ports.identity import HeaderAuthenticationPort, InvalidTokenError


class PATRevocationMiddleware:
    """Check HTTP revocation and continuously bound WebSocket credential lifetime."""

    def __init__(self, app: ASGIApp, websocket_check_interval: float = 30.0):
        if not math.isfinite(websocket_check_interval) or websocket_check_interval <= 0:
            raise ValueError("websocket_check_interval must be positive and finite")
        self.app = app
        self._interval = websocket_check_interval

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        validator = getattr(scope["app"].state, "pat_validator", None)
        headers = Headers(scope=scope)
        identity = getattr(scope["app"].state, "identity", None)
        auth = headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        # Envoy accepts query credentials for browser clients too. Check the
        # same credential carriers for HTTP and WebSocket revocation.
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        tokens = {t for t in [token, *query.get("token", []), *query.get("access_token", [])] if t}
        if len(tokens) > 1:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            await JSONResponse(status_code=401, content={"detail": "Conflicting credentials"})(
                scope, receive, send
            )
            return
        token = next(iter(tokens), "")
        if scope["type"] == "http":
            if validator is not None and token and not await validator.is_valid(token):
                response = JSONResponse(
                    status_code=401, content={"detail": "Token has been revoked"}
                )
                await response(scope, receive, send)
                return
            await self.app(scope, receive, send)
            return

        if not token:
            await self.app(scope, receive, send)
            return
        try:
            claims = jwt.decode(token, options={"verify_signature": False})
            expiry = claims.get("exp")
            if type(expiry) not in (int, float) or not math.isfinite(expiry):
                raise ValueError("Missing or invalid expiry")
        except (jwt.InvalidTokenError, ValueError):
            await send({"type": "websocket.close", "code": 1008})
            return

        async def valid() -> bool:
            if time.time() >= expiry:
                return False
            if isinstance(identity, HeaderAuthenticationPort):
                try:
                    await identity.validate_headers(dict(headers))
                except InvalidTokenError:
                    return False
            return validator is None or await validator.is_valid(token)

        if not await valid():
            await send({"type": "websocket.close", "code": 1008})
            return

        closed = False
        closing = asyncio.Event()

        async def close():
            nonlocal closed
            if not closed:
                closed = True
                closing.set()
                await send({"type": "websocket.close", "code": 1008})

        async def guarded_send(message):
            nonlocal closed
            if closed:
                return
            if message["type"] == "websocket.close":
                closed = True
                closing.set()
            elif not await valid():
                await close()
                return
            await send(message)

        async def guarded_receive():
            message = await receive()
            if message["type"] == "websocket.disconnect":
                return message
            if closed or not await valid():
                await close()
                return {"type": "websocket.disconnect", "code": 1008}
            return message

        async def monitor():
            try:
                while not closed:
                    delay = max(0, min(self._interval, expiry - time.time()))
                    try:
                        await asyncio.wait_for(closing.wait(), timeout=delay)
                    except TimeoutError:
                        pass
                    if closed or not await valid():
                        break
            finally:
                await close()

        application = asyncio.create_task(self.app(scope, guarded_receive, guarded_send))
        watchdog = asyncio.create_task(monitor())
        try:
            done, _ = await asyncio.wait(
                {application, watchdog}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                task.result()
        finally:
            # Cancel application work as well as the socket: revoked connections
            # must not keep consuming input or producing protected output.
            closed = True
            application.cancel()
            watchdog.cancel()
            await asyncio.gather(application, watchdog, return_exceptions=True)
