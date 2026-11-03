"""HttpNotificationImageFetcher: the broker path for file_id and guarded https for url."""

from __future__ import annotations

import socket
from uuid import uuid4

import httpx
import pytest

from niuu.domain.notifications import NotificationImageRef
from volundr.adapters.outbound.notification_image_fetcher import (
    HttpNotificationImageFetcher,
    resolve_host,
)
from volundr.domain.models import Session
from volundr.domain.notifications import (
    FetchedImage,
    NotificationImageError,
    PendingNotificationImage,
)

FILE_ID = "pf_" + "c" * 32
PNG = b"\x89PNG-bytes"
GATEWAY = "http://gateway.internal:8080"


def _pending(ref: dict, session_id=None) -> PendingNotificationImage:
    return PendingNotificationImage(
        notification_id=uuid4(),
        session_id=session_id,
        ref=NotificationImageRef.model_validate(ref),
        attempts=1,
    )


def _fetcher(
    handler, *, sessions=None, addresses=None, **overrides
) -> HttpNotificationImageFetcher:
    sessions = sessions or {}
    resolved: list[tuple[str, int]] = []

    async def lookup(session_id):
        return sessions.get(session_id)

    async def resolver(host, port):
        resolved.append((host, port))
        return (addresses or {}).get(host, ["93.184.216.34"])

    values = {
        "session_lookup": lookup,
        "gateway_url": GATEWAY,
        "timeout_seconds": 5,
        "max_redirects": 2,
        "allow_private_networks": False,
        "resolver": resolver,
        "transport": httpx.MockTransport(handler),
    }
    values.update(overrides)
    fetcher = HttpNotificationImageFetcher(**values)
    fetcher.resolved = resolved  # type: ignore[attr-defined]
    return fetcher


def _image(request: httpx.Request, content_type="image/png", body=PNG) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": content_type}, content=body)


class TestPresentedFile:
    async def test_reads_the_presented_file_route_on_the_sessions_broker(self):
        session = Session(name="s", chat_endpoint="ws://10.0.0.5:9100/session")
        seen: list[httpx.Request] = []

        def handler(request):
            seen.append(request)
            return _image(request, "image/png; charset=binary")

        fetcher = _fetcher(handler, sessions={session.id: session})
        fetched = await fetcher.fetch(_pending({"file_id": FILE_ID}, session.id))
        assert fetched == FetchedImage(content=PNG, content_type="image/png")
        assert str(seen[0].url) == f"http://10.0.0.5:9100/api/files/presented/{FILE_ID}"
        assert fetcher.resolved == []  # the broker is a trusted target, never DNS-checked

    async def test_openshell_brokers_go_through_the_gateway(self):
        endpoint = "wss://forge-1--skuld.openshell.localhost:8080/s/abc/session"
        session = Session(name="s", chat_endpoint=endpoint)
        seen: list[httpx.Request] = []

        def handler(request):
            seen.append(request)
            return _image(request)

        await _fetcher(handler, sessions={session.id: session}).fetch(
            _pending({"file_id": FILE_ID}, session.id)
        )
        assert str(seen[0].url) == f"{GATEWAY}/s/abc/api/files/presented/{FILE_ID}"
        assert seen[0].headers["host"] == "forge-1--skuld.openshell.localhost:8080"

    async def test_follows_broker_redirects(self):
        session = Session(name="s", chat_endpoint="ws://broker:1/session")

        def handler(request):
            if request.url.path.startswith("/api/files/presented/"):
                return httpx.Response(307, headers={"location": "http://broker:1/moved"})
            return _image(request)

        fetched = await _fetcher(handler, sessions={session.id: session}).fetch(
            _pending({"file_id": FILE_ID}, session.id)
        )
        assert fetched.content == PNG

    @pytest.mark.parametrize(
        ("sessions", "session_id", "message"),
        [
            ({}, None, "needs the notification's session"),
            ({}, "missing", "no longer exists"),
            ("stopped", "stopped", "is not running"),
        ],
    )
    async def test_refuses_without_a_live_session(self, sessions, session_id, message):
        stopped = Session(name="s", chat_endpoint=None)
        known = {stopped.id: stopped} if sessions == "stopped" else {}
        sid = {None: None, "missing": uuid4(), "stopped": stopped.id}[session_id]
        fetcher = _fetcher(lambda r: _image(r), sessions=known)
        with pytest.raises(NotificationImageError, match=message):
            await fetcher.fetch(_pending({"file_id": FILE_ID}, sid))

    @pytest.mark.parametrize(
        ("response", "message"),
        [
            (httpx.Response(404, text="unknown file_id"), "answered HTTP 404"),
            (httpx.Response(410), "answered HTTP 410"),
            (
                httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF"),
                "sent application/pdf, not an accepted image type",
            ),
            (httpx.Response(200, content=b"?"), "sent no content type"),
        ],
    )
    async def test_refuses_bad_broker_answers(self, response, message):
        session = Session(name="s", chat_endpoint="ws://broker:1/session")
        fetcher = _fetcher(lambda r: response, sessions={session.id: session})
        with pytest.raises(NotificationImageError, match=message):
            await fetcher.fetch(_pending({"file_id": FILE_ID}, session.id))

    async def test_an_unreachable_broker_is_reported(self):
        session = Session(name="s", chat_endpoint="ws://broker:1/session")

        def handler(request):
            raise httpx.ConnectError("refused", request=request)

        fetcher = _fetcher(handler, sessions={session.id: session})
        with pytest.raises(NotificationImageError, match="could not reach the session broker"):
            await fetcher.fetch(_pending({"file_id": FILE_ID}, session.id))


class TestUrl:
    async def test_fetches_a_public_https_image(self):
        seen: list[httpx.Request] = []

        def handler(request):
            seen.append(request)
            return _image(request, "IMAGE/JPEG")

        fetcher = _fetcher(handler)
        fetched = await fetcher.fetch(_pending({"url": "https://cdn.example.com:8443/a.jpg"}))
        assert fetched.content_type == "image/jpeg"
        assert fetcher.resolved == [("cdn.example.com", 8443)]
        assert str(seen[0].url) == "https://cdn.example.com:8443/a.jpg"

    @pytest.mark.parametrize(
        "address", ["127.0.0.1", "10.1.2.3", "100.101.102.103", "169.254.169.254", "fe80::1%eth0"]
    )
    async def test_refuses_hosts_on_private_networks(self, address):
        fetcher = _fetcher(lambda r: _image(r), addresses={"intranet.example": [address]})
        with pytest.raises(NotificationImageError, match="resolves to a non-public address"):
            await fetcher.fetch(_pending({"url": "https://intranet.example/a.png"}))

    async def test_refuses_when_any_address_is_private_or_none_resolve(self):
        mixed = _fetcher(
            lambda r: _image(r), addresses={"mixed.example": ["93.184.216.34", "10.0.0.1"]}
        )
        with pytest.raises(NotificationImageError, match="non-public"):
            await mixed.fetch(_pending({"url": "https://mixed.example/a.png"}))
        empty = _fetcher(lambda r: _image(r), addresses={"none.example": []})
        with pytest.raises(NotificationImageError, match="non-public"):
            await empty.fetch(_pending({"url": "https://none.example/a.png"}))

    async def test_private_networks_can_be_allowed(self):
        fetcher = _fetcher(
            lambda r: _image(r),
            addresses={"nas.tailnet": ["100.64.1.2"]},
            allow_private_networks=True,
        )
        assert (await fetcher.fetch(_pending({"url": "https://nas.tailnet/a.png"}))).content == PNG
        assert fetcher.resolved == []

    async def test_follows_checked_redirects(self):
        def handler(request):
            if request.url.path == "/start":
                return httpx.Response(302, headers={"location": "/final.png"})
            return _image(request)

        fetcher = _fetcher(handler)
        fetched = await fetcher.fetch(_pending({"url": "https://a.example/start"}))
        assert fetched.content == PNG
        assert fetcher.resolved == [("a.example", 443), ("a.example", 443)]

    @pytest.mark.parametrize(
        ("location", "message"),
        [
            ("http://a.example/final.png", "must be https"),
            (None, "redirected without a Location"),
        ],
    )
    async def test_refuses_bad_redirects(self, location, message):
        headers = {"location": location} if location else {}
        fetcher = _fetcher(lambda r: httpx.Response(301, headers=headers))
        with pytest.raises(NotificationImageError, match=message):
            await fetcher.fetch(_pending({"url": "https://a.example/start"}))

    async def test_redirects_are_bounded(self):
        fetcher = _fetcher(lambda r: httpx.Response(302, headers={"location": "/again"}))
        with pytest.raises(NotificationImageError, match="redirected more than 2 times"):
            await fetcher.fetch(_pending({"url": "https://a.example/start"}))

    async def test_a_redirect_to_a_private_host_is_refused(self):
        fetcher = _fetcher(
            lambda r: httpx.Response(302, headers={"location": "https://internal.example/x"}),
            addresses={"internal.example": ["192.168.1.10"]},
        )
        with pytest.raises(NotificationImageError, match="refusing internal.example"):
            await fetcher.fetch(_pending({"url": "https://a.example/start"}))

    async def test_an_invalid_port_is_refused(self):
        fetcher = _fetcher(lambda r: httpx.Response(302, headers={"location": "https://a:99999/"}))
        with pytest.raises(NotificationImageError, match="invalid port"):
            await fetcher.fetch(_pending({"url": "https://a.example/start"}))

    async def test_transport_failures_are_reported(self):
        def handler(request):
            raise httpx.ReadTimeout("slow", request=request)

        with pytest.raises(
            NotificationImageError, match=r"could not fetch the image \(ReadTimeout"
        ):
            await _fetcher(handler).fetch(_pending({"url": "https://a.example/a.png"}))


class TestSizeCap:
    async def test_a_declared_length_over_the_cap_is_refused_unread(self):
        def handler(request):
            return httpx.Response(
                200, headers={"content-type": "image/png", "content-length": "11"}, content=PNG
            )

        fetcher = _fetcher(handler, max_bytes=10)
        with pytest.raises(NotificationImageError, match="larger than 10 bytes"):
            await fetcher.fetch(_pending({"url": "https://a.example/a.png"}))

    async def test_a_streamed_body_over_the_cap_is_refused(self):
        async def chunks():
            for _ in range(4):
                yield b"x" * 4

        def handler(request):
            return httpx.Response(200, headers={"content-type": "image/png"}, content=chunks())

        fetcher = _fetcher(handler, max_bytes=10)
        with pytest.raises(NotificationImageError, match="larger than 10 bytes"):
            await fetcher.fetch(_pending({"url": "https://a.example/a.png"}))


class TestConstruction:
    @pytest.mark.parametrize(
        ("overrides", "message"),
        [({"timeout_seconds": 0}, "timeout_seconds"), ({"max_redirects": -1}, "max_redirects")],
    )
    def test_rejects_impossible_settings(self, overrides, message):
        with pytest.raises(ValueError, match=message):
            _fetcher(lambda r: _image(r), **overrides)


class TestResolveHost:
    async def test_returns_unique_addresses(self, monkeypatch):
        async def fake_getaddrinfo(host, port, type):
            assert (host, port, type) == ("x.example", 443, socket.SOCK_STREAM)
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
                (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:2800::1", 443, 0, 0)),
            ]

        import asyncio

        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fake_getaddrinfo)
        assert await resolve_host("x.example", 443) == ["2606:2800::1", "93.184.216.34"]

    async def test_resolution_failure_is_an_image_error(self, monkeypatch):
        async def failing(host, port, type):
            raise socket.gaierror(-2, "Name or service not known")

        import asyncio

        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", failing)
        with pytest.raises(NotificationImageError, match="could not resolve nowhere.invalid"):
            await resolve_host("nowhere.invalid", 443)
