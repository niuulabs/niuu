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

from identity.ports import AuthorizationEvaluationError
from niuu.ports.identity import HeaderAuthenticationPort, InvalidTokenError


class PATRevocationMiddleware:
    """Check HTTP revocation and continuously bound WebSocket credential lifetime."""

    def __init__(
        self,
        app: ASGIApp,
        websocket_check_interval: float = 30.0,
        enabled: bool = True,
        authenticate_http: bool = False,
    ):
        if not math.isfinite(websocket_check_interval) or websocket_check_interval <= 0:
            raise ValueError("websocket_check_interval must be positive and finite")
        self._authenticate_http = authenticate_http
        self.app = app
        self._enabled = enabled
        self._interval = websocket_check_interval

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        from identity.adapters.identity import (
            AllowAllHeaderAuthenticationAdapter,
            AllowAllIdentityAdapter,
        )

        identity = getattr(scope["app"].state, "identity", None)
        if not self._enabled or isinstance(
            identity, (AllowAllIdentityAdapter, AllowAllHeaderAuthenticationAdapter)
        ):
            await self.app(scope, receive, send)
            return
        validator = getattr(scope["app"].state, "pat_validator", None)
        headers = Headers(scope=scope)
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
        from niuu.domain.services.token_scope import credential_allows_route

        if not credential_allows_route(token, scope.get("method", "WEBSOCKET"), scope["path"]):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            await JSONResponse(status_code=403, content={"detail": "Credential scope denied"})(
                scope, receive, send
            )
            return
        if scope["type"] == "http":
            if self._authenticate_http and not (
                scope.get("method") in ("GET", "HEAD")
                and scope["path"]
                in (
                    "/health",
                    "/api/v1/tokens/workload/jwks",
                )
            ):
                if not isinstance(identity, HeaderAuthenticationPort):
                    await JSONResponse(
                        status_code=503, content={"detail": "Identity is not configured"}
                    )(scope, receive, send)
                    return
                validation_headers = dict(headers)
                if token:
                    validation_headers["authorization"] = f"Bearer {token}"
                try:
                    await identity.validate_headers(validation_headers)
                except (InvalidTokenError, AuthorizationEvaluationError) as exc:
                    code = 401 if isinstance(exc, InvalidTokenError) else 503
                    await JSONResponse(
                        status_code=code, content={"detail": "Identity validation failed"}
                    )(scope, receive, send)
                    return
            try:
                valid_token = validator is None or not token or await validator.is_valid(token)
            except AuthorizationEvaluationError:
                await JSONResponse(
                    status_code=503, content={"detail": "Identity authority unavailable"}
                )(scope, receive, send)
                return
            if not valid_token:
                response = JSONResponse(
                    status_code=401, content={"detail": "Token has been revoked"}
                )
                await response(scope, receive, send)
                return
            await self._http_response(scope, receive, send, token, identity, validator, headers)
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

        initial_principal = None

        async def valid() -> bool:
            nonlocal initial_principal
            if time.time() >= expiry:
                return False
            if isinstance(identity, HeaderAuthenticationPort):
                try:
                    validation_headers = dict(headers)
                    validation_headers["authorization"] = f"Bearer {token}"
                    principal = await identity.validate_headers(validation_headers)
                    if initial_principal is None:
                        initial_principal = principal
                    elif principal != initial_principal:
                        # Reconnect under the new authority instead of retaining
                        # subscriptions/actions granted to the previous role.
                        return False
                except (InvalidTokenError, AuthorizationEvaluationError):
                    return False
            try:
                return validator is None or await validator.is_valid(token)
            except AuthorizationEvaluationError:
                return False

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

    async def _http_response(self, scope, receive, send, token, identity, validator, headers):
        """Bound idle SSE streams to the same live identity as WebSockets."""
        if not token:
            await self.app(scope, receive, send)
            return
        streaming = asyncio.Event()
        closed = False
        denied = False
        initial_principal = None

        async def valid():
            nonlocal initial_principal
            try:
                claims = jwt.decode(token, options={"verify_signature": False})
                expiry = claims.get("exp")
                if (
                    type(expiry) not in (int, float)
                    or not math.isfinite(expiry)
                    or time.time() >= expiry
                ):
                    return False
                if isinstance(identity, HeaderAuthenticationPort):
                    validation_headers = dict(headers)
                    validation_headers["authorization"] = f"Bearer {token}"
                    principal = await identity.validate_headers(validation_headers)
                    if initial_principal is None:
                        initial_principal = principal
                    elif principal != initial_principal:
                        return False
                return validator is None or await validator.is_valid(token)
            except (jwt.InvalidTokenError, InvalidTokenError, AuthorizationEvaluationError):
                return False

        async def guarded_send(message):
            nonlocal closed, denied
            if closed:
                return
            if message["type"] == "http.response.start":
                content_type = Headers(raw=message.get("headers", [])).get("content-type", "")
                if content_type.split(";", 1)[0] == "text/event-stream":
                    streaming.set()
            if streaming.is_set() and message["type"] == "http.response.body":
                if not await valid():
                    closed = True
                    denied = True
                    await send({"type": "http.response.body", "body": b"", "more_body": False})
                    return
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                closed = True
            await send(message)

        application = asyncio.create_task(self.app(scope, receive, guarded_send))
        started = asyncio.create_task(streaming.wait())
        try:
            await asyncio.wait([application, started], return_when=asyncio.FIRST_COMPLETED)
            while not application.done() and streaming.is_set() and not closed:
                if not await valid():
                    closed = True
                    await send({"type": "http.response.body", "body": b"", "more_body": False})
                    application.cancel()
                    break
                await asyncio.wait([application], timeout=self._interval)
            if denied and not application.done():
                application.cancel()
            if not application.cancelled() and not application.cancelling():
                await application
        finally:
            started.cancel()
            if not application.done():
                application.cancel()
            await asyncio.gather(application, started, return_exceptions=True)
