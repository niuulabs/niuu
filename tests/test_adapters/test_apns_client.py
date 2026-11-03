"""APNs client: host per environment, headers per push type, the JWT and the key checks."""

from __future__ import annotations

import json

import httpx
import jwt
import pytest

from tests.helpers.apns_keys import (
    generate_p8,
    generate_p384_pem,
    generate_rsa_pem,
    public_pem,
    write_p8,
)
from volundr.adapters.outbound.apns_client import (
    ApnsClient,
    ApnsConfigurationError,
    check_signing_key,
    load_signing_key,
)
from volundr.domain.push import ApnsPush, ApnsPushType, PushEnvironment

PRODUCTION = "https://api.push.apple.com"
SANDBOX = "https://api.sandbox.push.apple.com"
KEY = generate_p8()


class Apns:
    """A stand-in APNs: records requests and answers from a queue."""

    def __init__(self, *answers: httpx.Response | Exception) -> None:
        self.requests: list[httpx.Request] = []
        self._answers = list(answers)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._answers:
            return httpx.Response(200, headers={"apns-id": f"id-{len(self.requests)}"})
        answer = self._answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def client_for(apns: Apns, *, clock=None, ttl: float = 3000) -> ApnsClient:
    kwargs = {"clock": clock} if clock is not None else {}
    return ApnsClient(
        team_id="TEAM123456",
        key_id="KEY1234567",
        private_key=KEY,
        production_url=PRODUCTION + "/",
        sandbox_url=SANDBOX,
        token_ttl_seconds=ttl,
        request_timeout_seconds=5,
        client=httpx.AsyncClient(transport=httpx.MockTransport(apns.handler)),
        **kwargs,
    )


def push(**overrides) -> ApnsPush:
    values = {
        "token": "abc123",
        "environment": PushEnvironment.PRODUCTION,
        "topic": "com.xteo.lexi.chat",
        "push_type": ApnsPushType.ALERT,
        "priority": 10,
        "payload": {"aps": {"alert": {"title": "t", "body": "b"}}},
    }
    values.update(overrides)
    return ApnsPush(**values)


class TestRouting:
    async def test_production_token_goes_to_the_production_host(self):
        apns = Apns()
        result = await client_for(apns).send(push())
        assert str(apns.requests[0].url) == f"{PRODUCTION}/3/device/abc123"
        assert result.accepted and result.apns_id == "id-1"

    async def test_sandbox_token_goes_to_the_sandbox_host(self):
        apns = Apns()
        await client_for(apns).send(push(environment=PushEnvironment.SANDBOX))
        assert str(apns.requests[0].url) == f"{SANDBOX}/3/device/abc123"

    async def test_alert_headers_and_body(self):
        apns = Apns()
        await client_for(apns).send(push(collapse_id="n-1", expiration=1_790_926_814))
        request = apns.requests[0]
        assert request.headers["apns-topic"] == "com.xteo.lexi.chat"
        assert request.headers["apns-push-type"] == "alert"
        assert request.headers["apns-priority"] == "10"
        assert request.headers["apns-collapse-id"] == "n-1"
        assert request.headers["apns-expiration"] == "1790926814"
        assert request.headers["content-type"] == "application/json"
        assert json.loads(request.content) == {"aps": {"alert": {"title": "t", "body": "b"}}}

    async def test_optional_headers_are_absent_when_unset(self):
        apns = Apns()
        await client_for(apns).send(
            push(
                push_type=ApnsPushType.WIDGETS,
                topic="com.xteo.lexi.chat.push-type.widgets",
                priority=5,
                payload={"aps": {"content-changed": True}},
            )
        )
        request = apns.requests[0]
        assert request.headers["apns-push-type"] == "widgets"
        assert request.headers["apns-topic"] == "com.xteo.lexi.chat.push-type.widgets"
        assert request.headers["apns-priority"] == "5"
        assert "apns-collapse-id" not in request.headers
        assert "apns-expiration" not in request.headers
        assert request.content == b'{"aps":{"content-changed":true}}'


class TestProviderToken:
    async def test_jwt_is_es256_with_team_and_key_id(self):
        apns = Apns()
        await client_for(apns, clock=lambda: 1_790_923_214.9).send(push())
        scheme, token = apns.requests[0].headers["authorization"].split(" ")
        assert scheme == "bearer"
        assert jwt.get_unverified_header(token) == {
            "alg": "ES256",
            "kid": "KEY1234567",
            "typ": "JWT",
        }
        claims = jwt.decode(
            token, public_pem(KEY), algorithms=["ES256"], options={"verify_iat": False}
        )
        assert claims == {"iss": "TEAM123456", "iat": 1_790_923_214}

    async def test_jwt_is_reused_within_the_ttl_and_resigned_after(self):
        now = [1000.0]
        apns = Apns()
        client = client_for(apns, clock=lambda: now[0], ttl=3000)
        await client.send(push())
        now[0] += 2999
        await client.send(push())
        now[0] += 2
        await client.send(push())
        tokens = [request.headers["authorization"] for request in apns.requests]
        assert tokens[0] == tokens[1]
        assert tokens[2] != tokens[0]

    async def test_an_expired_provider_token_is_resigned_once(self):
        now = [1000.0]
        expired = httpx.Response(403, json={"reason": "ExpiredProviderToken"})
        apns = Apns(expired)

        def clock() -> float:
            now[0] += 1
            return now[0]

        result = await client_for(apns, clock=clock).send(push())
        assert result.accepted
        first, second = (request.headers["authorization"] for request in apns.requests)
        assert first != second

    async def test_a_second_expired_answer_is_reported_not_looped(self):
        expired = {"reason": "ExpiredProviderToken"}
        apns = Apns(httpx.Response(403, json=expired), httpx.Response(403, json=expired))
        result = await client_for(apns).send(push())
        assert len(apns.requests) == 2
        assert (result.status_code, result.reason) == (403, "ExpiredProviderToken")


class TestAnswers:
    async def test_unregistered_token(self):
        apns = Apns(httpx.Response(410, json={"reason": "Unregistered", "timestamp": 1}))
        result = await client_for(apns).send(push())
        assert (result.status_code, result.reason) == (410, "Unregistered")
        assert result.token_is_dead and not result.retryable

    async def test_bad_device_token(self):
        apns = Apns(httpx.Response(400, json={"reason": "BadDeviceToken"}))
        result = await client_for(apns).send(push())
        assert result.token_is_dead

    async def test_throttled_with_retry_after(self):
        apns = Apns(
            httpx.Response(429, json={"reason": "TooManyRequests"}, headers={"retry-after": "30"})
        )
        result = await client_for(apns).send(push())
        assert result.retryable and result.retry_after == 30.0

    async def test_unreadable_retry_after_and_body(self):
        apns = Apns(httpx.Response(503, content=b"<html>", headers={"retry-after": "soon"}))
        result = await client_for(apns).send(push())
        assert (result.status_code, result.reason, result.retry_after) == (503, "", None)
        assert result.retryable

    async def test_a_non_object_body_has_no_reason(self):
        apns = Apns(httpx.Response(500, json=["nope"]))
        assert (await client_for(apns).send(push())).reason == ""

    async def test_unreachable_apns_is_an_answer_not_an_exception(self):
        apns = Apns(httpx.ConnectError("no route"))
        result = await client_for(apns).send(push())
        assert result.status_code is None and result.reason == "ConnectError"
        assert result.retryable and not result.token_is_dead


class TestSigningKey:
    def test_loads_a_p8_file(self, tmp_path):
        path = write_p8(tmp_path)
        assert load_signing_key(str(path)).startswith("-----BEGIN PRIVATE KEY-----")

    def test_empty_path_names_the_setting(self):
        with pytest.raises(ApnsConfigurationError, match="push.apns.private_key_path is empty"):
            load_signing_key("")

    def test_missing_file_names_the_path_and_the_remedy(self, tmp_path):
        missing = tmp_path / "AuthKey_NONE.p8"
        with pytest.raises(ApnsConfigurationError) as excinfo:
            load_signing_key(str(missing))
        assert str(missing) in str(excinfo.value)
        assert "FileNotFoundError" in str(excinfo.value)
        assert "push.apns.private_key_path" in str(excinfo.value)

    def test_binary_file_is_not_a_pem(self, tmp_path):
        path = tmp_path / "key.p8"
        path.write_bytes(b"\xff\xfe\x00\x01")
        with pytest.raises(ApnsConfigurationError, match="not a PEM"):
            load_signing_key(str(path))

    def test_text_that_is_not_a_key(self, tmp_path):
        path = tmp_path / "key.p8"
        path.write_text("not a key")
        with pytest.raises(ApnsConfigurationError, match="not an unencrypted PEM private key"):
            load_signing_key(str(path))

    @pytest.mark.parametrize("wrong", [generate_rsa_pem, generate_p384_pem])
    def test_wrong_key_type_or_curve(self, wrong):
        with pytest.raises(ApnsConfigurationError, match="not an EC P-256 key"):
            check_signing_key(wrong(), source="a test")

    def test_error_never_contains_the_key(self, tmp_path):
        path = tmp_path / "key.p8"
        path.write_text(generate_rsa_pem())
        with pytest.raises(ApnsConfigurationError) as excinfo:
            load_signing_key(str(path))
        assert "BEGIN" not in str(excinfo.value)


class TestConstruction:
    def _kwargs(self, **overrides) -> dict:
        values = {
            "team_id": "TEAM123456",
            "key_id": "KEY1234567",
            "private_key": KEY,
            "production_url": PRODUCTION,
            "sandbox_url": SANDBOX,
            "token_ttl_seconds": 3000,
            "request_timeout_seconds": 5,
        }
        values.update(overrides)
        return values

    @pytest.mark.parametrize("field", ["team_id", "key_id"])
    def test_ids_are_required(self, field):
        with pytest.raises(ApnsConfigurationError, match="team_id and push.apns.key_id"):
            ApnsClient(**self._kwargs(**{field: ""}))

    @pytest.mark.parametrize("field", ["production_url", "sandbox_url"])
    def test_hosts_are_required(self, field):
        with pytest.raises(
            ApnsConfigurationError, match="production_url and push.apns.sandbox_url"
        ):
            ApnsClient(**self._kwargs(**{field: ""}))

    def test_a_wrong_key_is_refused_at_construction(self):
        with pytest.raises(ApnsConfigurationError):
            ApnsClient(**self._kwargs(private_key="garbage"))

    async def test_builds_its_own_http2_client_and_closes_it(self):
        client = ApnsClient(**self._kwargs())
        assert client._client._transport._pool._http2 is True
        await client.close()
        assert client._client.is_closed

    async def test_an_injected_client_is_left_open(self):
        shared = httpx.AsyncClient(transport=httpx.MockTransport(Apns().handler))
        client = ApnsClient(**self._kwargs(client=shared))
        await client.close()
        assert not shared.is_closed
        await shared.aclose()
