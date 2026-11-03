"""Fetching the bytes of a notification image, once, with its protocol limits.

* ``file_id`` — read through the session's Skuld broker at its presented-file route
  (``GET {broker}/api/files/presented/{file_id}``), resolved exactly like Forge's
  presented-file proxy (:mod:`volundr.domain.session_broker`). The broker is a
  target Forge already trusts, so redirects are followed as the proxy does.
* ``url`` — an ``https://`` GET. Every hop (redirects are followed by hand, up to
  ``max_redirects``) must be https and, unless ``allow_private_networks`` is set,
  resolve only to public addresses, so an agent cannot point Forge at its own
  private network.

Both paths refuse a response that is not an accepted image type or exceeds
``MAX_IMAGE_BYTES`` (declared or streamed). Every refusal or transport failure
raises :class:`NotificationImageError` with a reader-facing reason.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin, urlsplit
from uuid import UUID

import httpx

from niuu.domain.notifications import MAX_IMAGE_BYTES, NOTIFICATION_IMAGE_MIME_TYPES
from volundr.domain.models import Session
from volundr.domain.notification_ports import NotificationImageFetcher
from volundr.domain.notifications import (
    FetchedImage,
    NotificationImageError,
    PendingNotificationImage,
)
from volundr.domain.session_broker import (
    broker_base_url,
    presented_file_url,
    server_side_http_proxy_target,
)

SessionLookup = Callable[[UUID], Awaitable[Session | None]]
HostResolver = Callable[[str, int], Awaitable[list[str]]]

_HTTPS = "https"
_HTTPS_PORT = 443
_BROKER_SOURCE = "the session broker"
_ACCEPTED_TYPES = ", ".join(sorted(NOTIFICATION_IMAGE_MIME_TYPES))


async def resolve_host(host: str, port: int) -> list[str]:
    """Every address ``host`` resolves to (the system resolver)."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as exc:
        raise NotificationImageError(f"could not resolve {host}") from exc
    return sorted({str(info[4][0]) for info in infos})


def _media_type(header: str | None) -> str:
    return (header or "").split(";", 1)[0].strip().lower()


def _is_public(address: str) -> bool:
    return ipaddress.ip_address(address.split("%", 1)[0]).is_global


class HttpNotificationImageFetcher(NotificationImageFetcher):
    """Fetches notification images over HTTP (see the module docstring)."""

    def __init__(
        self,
        *,
        session_lookup: SessionLookup,
        gateway_url: str,
        timeout_seconds: float,
        max_redirects: int,
        allow_private_networks: bool,
        max_bytes: int = MAX_IMAGE_BYTES,
        resolver: HostResolver = resolve_host,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_redirects < 0:
            raise ValueError("max_redirects must not be negative")
        self._session_lookup = session_lookup
        self._gateway_url = gateway_url
        self._timeout = timeout_seconds
        self._max_redirects = max_redirects
        self._allow_private_networks = allow_private_networks
        self._max_bytes = max_bytes
        self._resolver = resolver
        self._transport = transport

    async def fetch(self, pending: PendingNotificationImage) -> FetchedImage:
        ref = pending.ref
        if ref.file_id is not None:
            return await self._fetch_presented(pending.session_id, ref.file_id)
        return await self._fetch_url(str(ref.url))

    def _client(self, *, follow_redirects: bool) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._timeout, follow_redirects=follow_redirects, transport=self._transport
        )

    # -- file_id: the session broker ------------------------------------------

    async def _fetch_presented(self, session_id: UUID | None, file_id: str) -> FetchedImage:
        if session_id is None:
            raise NotificationImageError("a present-file image needs the notification's session")
        session = await self._session_lookup(session_id)
        if session is None:
            raise NotificationImageError(
                f"session {session_id} no longer exists, so its presented file cannot be read"
            )
        if not session.chat_endpoint:
            raise NotificationImageError(
                f"session {session_id} is not running, so its presented file cannot be read"
            )
        url, headers = server_side_http_proxy_target(
            presented_file_url(broker_base_url(session.chat_endpoint), file_id),
            gateway_url=self._gateway_url,
        )
        try:
            async with self._client(follow_redirects=True) as client:
                async with client.stream("GET", url, headers=headers) as response:
                    return await self._read(response, source=_BROKER_SOURCE)
        except httpx.HTTPError as exc:
            raise NotificationImageError(
                f"could not reach {_BROKER_SOURCE} ({type(exc).__name__})"
            ) from exc

    # -- url: public https ----------------------------------------------------

    async def _fetch_url(self, url: str) -> FetchedImage:
        current = url
        try:
            async with self._client(follow_redirects=False) as client:
                for _ in range(self._max_redirects + 1):
                    host = await self._checked_host(current)
                    async with client.stream("GET", current) as response:
                        if not response.is_redirect:
                            return await self._read(response, source=host)
                        location = response.headers.get("location")
                        if not location:
                            raise NotificationImageError(f"{host} redirected without a Location")
                        current = urljoin(current, location)
        except httpx.HTTPError as exc:
            raise NotificationImageError(
                f"could not fetch the image ({type(exc).__name__})"
            ) from exc
        raise NotificationImageError(
            f"the image URL redirected more than {self._max_redirects} times"
        )

    async def _checked_host(self, url: str) -> str:
        """The URL's host after checking scheme and (by default) that it is public."""
        parsed = urlsplit(url)
        if parsed.scheme != _HTTPS or not parsed.hostname:
            raise NotificationImageError("image URLs must be https:// (including redirects)")
        host = parsed.hostname
        if self._allow_private_networks:
            return host
        try:
            port = parsed.port or _HTTPS_PORT
        except ValueError as exc:
            raise NotificationImageError(f"invalid port in image URL for {host}") from exc
        addresses = await self._resolver(host, port)
        if not addresses or not all(_is_public(address) for address in addresses):
            raise NotificationImageError(
                f"refusing {host}: it resolves to a non-public address "
                "(notifications.images.allow_private_networks permits that)"
            )
        return host

    # -- both -----------------------------------------------------------------

    async def _read(self, response: httpx.Response, *, source: str) -> FetchedImage:
        if response.status_code != httpx.codes.OK:
            raise NotificationImageError(f"{source} answered HTTP {response.status_code}")
        content_type = _media_type(response.headers.get("content-type"))
        if content_type not in NOTIFICATION_IMAGE_MIME_TYPES:
            raise NotificationImageError(
                f"{source} sent {content_type or 'no content type'}, "
                f"not an accepted image type ({_ACCEPTED_TYPES})"
            )
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > self._max_bytes:
            raise NotificationImageError(self._too_large(source))
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > self._max_bytes:
                raise NotificationImageError(self._too_large(source))
            chunks.append(chunk)
        return FetchedImage(content=b"".join(chunks), content_type=content_type)

    def _too_large(self, source: str) -> str:
        return f"the image from {source} is larger than {self._max_bytes} bytes"
