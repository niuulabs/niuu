"""``ForgeCallPushClient``: the resident rings its owner through the Guild push gateway."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from ravn.adapters.voice.call_push import ForgeCallPushClient
from ravn.ports.call_push import CallPushPort, CallPushResult

BASE = "https://yggdrasil.niuu.world"
FRAME = {
    "type": "call.request",
    "id": "req-1",
    "payload": {
        "id": "req-1",
        "source": "resident",
        "session": {"host": "valhalla", "id": "0b6f8c1e-sidekick", "name": "Sidekick"},
        "text": "The deploy finished.",
        "notificationId": "01177db3-f589-59a9-bf46-c3e6c106e6e6",
        "priority": "now",
        "expiresAt": "2026-11-03T15:04:05Z",
    },
}


class StaticAuth:
    """An ``HttpAuthPort`` with a fixed bearer that counts renewals."""

    def __init__(self, token: str = "tok-1", *, fails: bool = False) -> None:
        self.token = token
        self.fails = fails
        self.invalidations = 0

    def headers(self) -> dict[str, str]:
        if self.fails:
            raise RuntimeError("workload identity proof token not found")
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def invalidate(self) -> bool:
        self.invalidations += 1
        self.token = f"tok-{self.invalidations + 1}"
        return True


def gateway(*responses: httpx.Response, seen: list[httpx.Request]) -> httpx.MockTransport:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return httpx.MockTransport(handler)


def client(*responses: httpx.Response, seen: list, auth=None) -> ForgeCallPushClient:
    return ForgeCallPushClient(
        base_url=BASE + "/", auth=auth or StaticAuth(), transport=gateway(*responses, seen=seen)
    )


def test_it_implements_the_port():
    assert isinstance(client(httpx.Response(200), seen=[]), CallPushPort)


def test_it_needs_the_gateway_origin():
    with pytest.raises(ValueError, match="base_url"):
        ForgeCallPushClient(base_url=" ", auth=StaticAuth())


async def test_a_ring_posts_the_frame_with_the_bearer():
    seen: list[httpx.Request] = []
    push = client(
        httpx.Response(
            200,
            json={
                "sent": True,
                "tokens": 1,
                "results": [{"fingerprint": "abc123", "status": 200, "reason": ""}],
            },
        ),
        seen=seen,
    )

    result = await push.ring(request_id="req-1", frame=FRAME)

    assert result == CallPushResult(sent=True, tokens=1, reason="")
    (request,) = seen
    assert request.method == "POST"
    assert str(request.url) == BASE + "/api/v1/forge/push/call"
    assert request.headers["Authorization"] == "Bearer tok-1"
    assert json.loads(request.content) == {"request_id": "req-1", "frame": FRAME}


async def test_the_exchange_asks_for_the_ring_scope(tmp_path: Path):
    proof = tmp_path / "token"
    proof.write_text("k8s-proof\n", encoding="utf-8")
    exchanges: list[httpx.Request] = []

    def exchange(request: httpx.Request) -> httpx.Response:
        exchanges.append(request)
        return httpx.Response(200, json={"token": "exchanged", "expires_in": 300})

    seen: list[httpx.Request] = []
    push = ForgeCallPushClient(
        base_url=BASE,
        exchange_url=BASE + "/api/v1/tokens/workload/exchange",
        token_file=str(proof),
        transport=gateway(httpx.Response(200, json={"sent": True, "tokens": 1}), seen=seen),
        exchange_transport=httpx.MockTransport(exchange),
    )

    await push.ring(request_id="req-1", frame=FRAME)

    (request,) = exchanges
    assert str(request.url) == BASE + "/api/v1/tokens/workload/exchange"
    assert json.loads(request.content) == {
        "token": "k8s-proof",
        "audiences": ["volundr-api"],
        "scopes": ["forge:push:call"],
    }
    assert seen[0].headers["Authorization"] == "Bearer exchanged"


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            httpx.Response(200, json={"sent": False, "tokens": 0, "results": []}),
            CallPushResult(sent=False, tokens=0, reason="no_voip_token"),
        ),
        (
            httpx.Response(
                200,
                json={
                    "sent": False,
                    "tokens": 1,
                    "results": [{"fingerprint": "abc", "status": 410, "reason": "Unregistered"}],
                },
            ),
            CallPushResult(sent=False, tokens=1, reason="apns_refused: Unregistered"),
        ),
        (httpx.Response(429), CallPushResult(sent=False, tokens=0, reason="rate_limited")),
        (httpx.Response(403), CallPushResult(sent=False, tokens=0, reason="http_403")),
        (httpx.Response(422), CallPushResult(sent=False, tokens=0, reason="http_422")),
        (httpx.Response(404), CallPushResult(sent=False, tokens=0, reason="http_404")),
        (httpx.Response(200, text="<html>"), CallPushResult(False, 0, "bad_response")),
    ],
)
async def test_every_answer_is_a_result(response, expected):
    assert await client(response, seen=[]).ring(request_id="req-1", frame=FRAME) == expected


async def test_a_stale_bearer_is_renewed_once():
    seen: list[httpx.Request] = []
    auth = StaticAuth()
    push = client(
        httpx.Response(401),
        httpx.Response(200, json={"sent": True, "tokens": 1}),
        seen=seen,
        auth=auth,
    )

    result = await push.ring(request_id="req-1", frame=FRAME)

    assert result.sent
    assert [r.headers["Authorization"] for r in seen] == ["Bearer tok-1", "Bearer tok-2"]
    assert auth.invalidations == 1


async def test_a_bearer_refused_twice_gives_up():
    seen: list[httpx.Request] = []
    result = await client(httpx.Response(401), seen=seen).ring(request_id="req-1", frame=FRAME)

    assert result == CallPushResult(sent=False, tokens=0, reason="http_401")
    assert len(seen) == 2


@pytest.mark.parametrize("auth", [StaticAuth(fails=True), StaticAuth(token="")])
async def test_no_credential_sends_nothing(auth):
    seen: list[httpx.Request] = []

    result = await client(httpx.Response(200), seen=seen, auth=auth).ring(
        request_id="req-1", frame=FRAME
    )

    assert result == CallPushResult(sent=False, tokens=0, reason="no_credential")
    assert seen == []


async def test_an_unreachable_gateway_is_a_result():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    push = ForgeCallPushClient(
        base_url=BASE, auth=StaticAuth(), transport=httpx.MockTransport(handler)
    )

    assert await push.ring(request_id="req-1", frame=FRAME) == CallPushResult(
        sent=False, tokens=0, reason="unreachable"
    )


async def test_logs_carry_no_token_and_no_text(caplog):
    caplog.set_level("DEBUG")
    push = client(httpx.Response(200, json={"sent": True, "tokens": 1}), seen=[])

    await push.ring(request_id="req-1", frame=FRAME)

    assert "req-1" in caplog.text
    assert "tok-1" not in caplog.text and "deploy finished" not in caplog.text
