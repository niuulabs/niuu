"""Streaming HTTP and WebSocket forwarding for the live-preview hops.

Both hops of a live preview — Forge's preview router (``niuu.preview_proxy``)
and a session's Skuld (``skuld.preview``) — relay an app's own traffic, so they
forward at the ASGI level: request and response bodies stream both ways, every
response header survives (several ``Set-Cookie`` included), and WebSocket text
and binary frames pass through unchanged. Header policy is each hop's own; these
helpers only move bytes.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from contextlib import suppress
from typing import Any

import httpx

logger = logging.getLogger(__name__)

Scope = dict[str, Any]
Receive = Callable[[], Any]
Send = Callable[[dict[str, Any]], Any]
RawHeaders = list[tuple[bytes, bytes]]

#: Connection-scoped headers (RFC 9110 §7.6.1) that never cross a proxy.
HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)

#: WebSocket handshake headers the upstream client library writes itself.
_WEBSOCKET_HANDSHAKE_HEADERS = frozenset(
    {
        "host",
        "sec-websocket-key",
        "sec-websocket-version",
        "sec-websocket-extensions",
        "sec-websocket-protocol",
        "sec-websocket-accept",
        "content-length",
    }
)


def scope_headers(scope: Scope) -> list[tuple[str, str]]:
    """Decode an ASGI scope's raw headers, preserving order and repeats."""
    return [(k.decode("latin-1"), v.decode("latin-1")) for k, v in scope.get("headers", [])]


def header_value(scope: Scope, name: str) -> str:
    """The first value of header *name* in *scope*, or ``""``."""
    wanted = name.lower().encode("latin-1")
    for key, value in scope.get("headers", []):
        if key.lower() == wanted:
            return value.decode("latin-1")
    return ""


def without_headers(
    headers: Iterable[tuple[str, str]],
    *,
    names: Iterable[str] = (),
    prefixes: Iterable[str] = (),
) -> list[tuple[str, str]]:
    """Drop hop-by-hop headers plus *names* and anything starting with *prefixes*."""
    dropped = HOP_BY_HOP_HEADERS | {name.lower() for name in names}
    lowered_prefixes = tuple(prefix.lower() for prefix in prefixes)
    return [
        (key, value)
        for key, value in headers
        if key.lower() not in dropped and not key.lower().startswith(lowered_prefixes)
    ]


def strip_cookie_domain(set_cookie: str) -> str:
    """Remove every ``Domain`` attribute from one ``Set-Cookie`` value.

    A cookie without ``Domain`` is host-only (RFC 6265 §5.3 step 6), so an app on
    one preview origin can never toss a cookie onto a parent domain.
    """
    name_value, *attributes = set_cookie.split(";")
    kept = [a for a in attributes if a.split("=", 1)[0].strip().lower() != "domain"]
    return ";".join([name_value, *kept])


def _has_request_body(scope: Scope) -> bool:
    return bool(header_value(scope, "content-length").strip("0 ")) or bool(
        header_value(scope, "transfer-encoding")
    )


async def _request_body(receive: Receive) -> AsyncIterator[bytes]:
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return
        chunk = message.get("body", b"")
        if chunk:
            yield chunk
        if not message.get("more_body", False):
            return


async def send_plain_response(send: Send, status: int, text: str) -> None:
    """Answer with a short ``text/plain`` body (errors and refusals)."""
    body = text.encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


async def forward_http(
    scope: Scope,
    receive: Receive,
    send: Send,
    *,
    client: httpx.AsyncClient,
    url: str,
    headers: list[tuple[str, str]],
    rewrite_response_headers: Callable[[RawHeaders], RawHeaders] | None = None,
    unreachable_message: str,
) -> None:
    """Stream one HTTP request to *url* and its response back to the caller.

    A refused or failed connection answers 502 with *unreachable_message*: it
    means nothing listens there (yet), which the caller sees, never a blank page.
    """
    query = scope.get("query_string", b"").decode("latin-1")
    target = f"{url}?{query}" if query else url
    request = client.build_request(
        scope["method"],
        target,
        headers=headers,
        content=_request_body(receive) if _has_request_body(scope) else None,
    )
    try:
        response = await client.send(request, stream=True)
    except httpx.TransportError as exc:
        logger.info("Preview upstream %s unreachable: %s", url, type(exc).__name__)
        await send_plain_response(send, 502, unreachable_message)
        return
    try:
        raw = [
            (key, value)
            for key, value in response.headers.raw
            if key.decode("latin-1").lower() not in HOP_BY_HOP_HEADERS
        ]
        if rewrite_response_headers is not None:
            raw = rewrite_response_headers(raw)
        await send({"type": "http.response.start", "status": response.status_code, "headers": raw})
        async for chunk in response.aiter_raw():
            await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})
    finally:
        await response.aclose()


def websocket_forward_headers(headers: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """Drop the handshake headers the upstream WebSocket client sets itself."""
    return [(k, v) for k, v in headers if k.lower() not in _WEBSOCKET_HANDSHAKE_HEADERS]


async def bridge_websocket(
    scope: Scope,
    receive: Receive,
    send: Send,
    *,
    url: str,
    headers: list[tuple[str, str]],
    connect_kwargs: Mapping[str, Any] | None = None,
    open_timeout_seconds: float,
) -> None:
    """Bridge one browser WebSocket to *url*, frame for frame, both ways.

    The upstream handshake runs before the browser's is accepted, so the
    negotiated subprotocol (Vite's ``vite-hmr``, for one) is the upstream's.
    An upstream that refuses the handshake closes the browser leg with 1011.
    """
    import websockets.asyncio.client as ws_client
    from websockets.exceptions import ConnectionClosed, InvalidHandshake

    message = await receive()
    if message["type"] != "websocket.connect":
        return
    subprotocols = [str(p) for p in scope.get("subprotocols", []) if p]
    try:
        upstream = await ws_client.connect(
            url,
            additional_headers=websocket_forward_headers(headers),
            subprotocols=subprotocols or None,  # type: ignore[arg-type]
            max_size=None,
            open_timeout=open_timeout_seconds,
            proxy=None,
            **dict(connect_kwargs or {}),
        )
    except (OSError, InvalidHandshake, TimeoutError) as exc:
        logger.info("Preview WebSocket upstream %s refused: %s", url, type(exc).__name__)
        await send({"type": "websocket.close", "code": 1011})
        return

    await send({"type": "websocket.accept", "subprotocol": upstream.subprotocol})

    async def browser_to_upstream() -> None:
        while True:
            event = await receive()
            if event["type"] == "websocket.disconnect":
                await upstream.close(code=int(event.get("code") or 1000))
                return
            if event.get("bytes") is not None:
                await upstream.send(event["bytes"])
            elif event.get("text") is not None:
                await upstream.send(event["text"])

    async def upstream_to_browser() -> None:
        try:
            async for frame in upstream:
                if isinstance(frame, bytes):
                    await send({"type": "websocket.send", "bytes": frame})
                else:
                    await send({"type": "websocket.send", "text": frame})
        except ConnectionClosed:
            pass
        close = upstream.close_rcvd
        await send(
            {
                "type": "websocket.close",
                "code": close.code if close is not None else 1000,
                "reason": close.reason if close is not None else "",
            }
        )

    pumps = [
        asyncio.create_task(browser_to_upstream()),
        asyncio.create_task(upstream_to_browser()),
    ]
    try:
        done, _ = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in pumps:
            if not task.done():
                task.cancel()
        await asyncio.gather(*pumps, return_exceptions=True)
        with suppress(Exception):
            await upstream.close()
    for task in done:
        exc = task.exception()
        if exc is not None and not isinstance(exc, ConnectionClosed | OSError):
            raise exc
