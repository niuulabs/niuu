from __future__ import annotations

import asyncio
import base64
import json
import logging
import ssl
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID

from tests.test_guest_agent.conftest import ALLOCATION_ID, NOW, credentials_body
from volundr.domain.guest_credentials import (
    METADATA_CREDENTIALS_PATH,
    GuestCredentials,
    IssuedGuestToken,
)
from volundr.guest_agent import agent as agent_module
from volundr.guest_agent.agent import (
    CredentialState,
    Fetched,
    GuestAgent,
    refresh_delay_seconds,
    run_once,
    run_refresh_loop,
)
from volundr.guest_agent.config import AgentConfig, parse_config
from volundr.guest_agent.errors import ConfigError, FetchError, SignerError
from volundr.guest_agent.signers import (
    HEADER_ALLOCATION,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    HostKeySigner,
    RequestSigner,
    signing_input,
)

CREDENTIALS_URL = "https://forge.test:8443" + METADATA_CREDENTIALS_PATH
PATH_ONLY = METADATA_CREDENTIALS_PATH


class StaticSigner(RequestSigner):
    async def headers(self, *, method: str, path: str, body: bytes) -> dict[str, str]:
        return {"Authorization": "Bearer svid"}


def make_agent(
    config: AgentConfig,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    signer: RequestSigner | None = None,
    clock: Callable[[], datetime] = lambda: NOW,
) -> GuestAgent:
    return GuestAgent(
        config,
        signer or StaticSigner(),
        transport=httpx.MockTransport(handler),
        clock=clock,
    )


def ok(body: dict) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(200, json=body)


# --- the request ----------------------------------------------------------------------------


async def test_posts_the_allocation_to_the_metadata_path(config_dict: dict) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=credentials_body())

    agent = make_agent(parse_config(config_dict), handler)
    await agent.fetch()

    (request,) = seen
    assert request.method == "POST"
    assert str(request.url) == CREDENTIALS_URL
    assert json.loads(request.content) == {"allocation_id": ALLOCATION_ID}
    assert request.headers["Authorization"] == "Bearer svid"
    assert request.headers["Content-Type"] == "application/json"
    await agent.close()


async def test_trailing_slash_in_metadata_url_is_stripped(make_config) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=credentials_body())

    config = parse_config(make_config(metadata_url="https://forge.test/base///"))
    await make_agent(config, handler).fetch()

    assert seen == ["https://forge.test/base" + METADATA_CREDENTIALS_PATH]


async def test_host_key_headers_verify_against_the_exact_request(
    config_dict: dict, host_key: tuple[str, Ed25519PublicKey]
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=credentials_body())

    signer = HostKeySigner(key_path=host_key[0], allocation_id=ALLOCATION_ID)
    await make_agent(parse_config(config_dict), handler, signer=signer).fetch()

    (request,) = seen
    timestamp = int(request.headers[HEADER_TIMESTAMP])
    assert request.headers[HEADER_ALLOCATION] == ALLOCATION_ID
    signature = base64.b64decode(request.headers[HEADER_SIGNATURE])
    host_key[1].verify(signature, signing_input("POST", PATH_ONLY, timestamp, request.content))


async def test_a_signer_failure_sends_nothing(config_dict: dict) -> None:
    class Failing(RequestSigner):
        async def headers(self, *, method: str, path: str, body: bytes) -> dict[str, str]:
            raise SignerError("no svid")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may be sent without an identity")

    agent = make_agent(parse_config(config_dict), handler, signer=Failing())

    with pytest.raises(SignerError, match="no svid"):
        await agent.fetch()


# --- the answer -----------------------------------------------------------------------------


async def test_parses_credentials_and_keeps_the_raw_body(config_dict: dict) -> None:
    agent = make_agent(parse_config(config_dict), ok(credentials_body()))

    fetched = await agent.fetch()

    (token,) = fetched.credentials.service_account_tokens
    assert (token.volume, token.path, token.token) == ("identity", "token", "sa-token-1")
    assert token.expires_at == NOW + timedelta(seconds=100)
    assert fetched.credentials.forge_session is not None
    assert fetched.credentials.forge_session.grants == ("message",)
    assert json.loads(fetched.raw) == credentials_body()
    assert fetched.fetched_at == NOW


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (401, {"error": "unauthorized"}, "HTTP 401: unauthorized"),
        (409, {"error": "allocation has no running session"}, "HTTP 409: allocation has no"),
        (413, {"detail": "too big"}, "HTTP 413: no error reason in the body"),
        (500, None, "HTTP 500: no JSON error body"),
    ],
)
async def test_non_200_is_an_error_naming_the_status(
    config_dict: dict, status: int, body: dict | None, message: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if body is None:
            return httpx.Response(status, content=b"<html>oops</html>")
        return httpx.Response(status, json=body)

    agent = make_agent(parse_config(config_dict), handler)

    with pytest.raises(FetchError, match=message):
        await agent.fetch()


async def test_transport_failure_is_a_fetch_error(config_dict: dict) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    agent = make_agent(parse_config(config_dict), handler)

    with pytest.raises(FetchError, match="ConnectError: connection refused"):
        await agent.fetch()


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ([], "not a JSON object"),
        ({}, "no service_account_tokens list"),
        ({"service_account_tokens": ["x"], "forge_session": None}, r"\[0\] is not an object"),
        (
            {
                "service_account_tokens": [
                    {"volume": "v", "path": "p", "token": "", "expires_at": "x"}
                ]
            },
            r"\[0\]\.token is missing or empty",
        ),
        (
            {
                "service_account_tokens": [
                    {"volume": "v", "path": "p", "token": "t", "expires_at": "yesterday"}
                ]
            },
            "expires_at is not ISO-8601",
        ),
        (
            {
                "service_account_tokens": [
                    {"volume": "v", "path": "p", "token": "t", "expires_at": "2026-11-06T12:00:00"}
                ]
            },
            "expires_at has no UTC offset",
        ),
        ({"service_account_tokens": [], "forge_session": "x"}, "forge_session is not an object"),
        (
            {"service_account_tokens": [], "forge_session": {"token": "", "grants": []}},
            "forge_session.token is missing",
        ),
        (
            {"service_account_tokens": [], "forge_session": {"token": "t", "grants": "all"}},
            "forge_session.grants is not a list",
        ),
    ],
)
async def test_malformed_answers_are_rejected(
    config_dict: dict, document: object, message: str
) -> None:
    agent = make_agent(parse_config(config_dict), ok(document))  # type: ignore[arg-type]

    with pytest.raises(FetchError, match=message):
        await agent.fetch()


async def test_non_json_answer_is_rejected(config_dict: dict) -> None:
    agent = make_agent(
        parse_config(config_dict), lambda request: httpx.Response(200, content=b"nope")
    )

    with pytest.raises(FetchError, match="not JSON"):
        await agent.fetch()


# --- writing --------------------------------------------------------------------------------


async def test_writes_each_planned_token_and_the_forge_session(
    config_dict: dict, tmp_path: Path
) -> None:
    agent = make_agent(parse_config(config_dict), ok(credentials_body()))

    await run_once(agent)

    assert (tmp_path / "tokens" / "identity" / "token").read_text() == "sa-token-1"
    assert (tmp_path / "forge-session" / "token").read_text() == "forge-token-1"


async def test_tokens_are_matched_by_volume_and_path(config_dict: dict, tmp_path: Path) -> None:
    config_dict["token_volumes"] = [
        {
            "volume": "identity",
            "directory": str(tmp_path / "identity"),
            "tokens": [{"path": "token"}],
        },
        {"volume": "vault", "directory": str(tmp_path / "vault"), "tokens": [{"path": "token"}]},
    ]
    config_dict["forge_session"] = None
    body = {
        "service_account_tokens": [
            {
                "volume": "vault",
                "path": "token",
                "token": "vault-jwt",
                "expires_at": (NOW + timedelta(seconds=60)).isoformat(),
            },
            {
                "volume": "identity",
                "path": "token",
                "token": "identity-jwt",
                "expires_at": (NOW + timedelta(seconds=90)).isoformat(),
            },
        ],
        "forge_session": None,
    }

    await run_once(make_agent(parse_config(config_dict), ok(body)))

    assert (tmp_path / "identity" / "token").read_text() == "identity-jwt"
    assert (tmp_path / "vault" / "token").read_text() == "vault-jwt"


async def test_a_planned_token_missing_from_the_answer_writes_nothing(
    config_dict: dict, tmp_path: Path
) -> None:
    body = credentials_body()
    body["service_account_tokens"][0]["volume"] = "other"
    agent = make_agent(parse_config(config_dict), ok(body))

    with pytest.raises(FetchError, match="no token for volume 'identity' path 'token'"):
        await run_once(agent)

    assert not (tmp_path / "tokens").exists()
    assert not (tmp_path / "forge-session").exists()


async def test_a_planned_forge_session_missing_from_the_answer_writes_nothing(
    config_dict: dict, tmp_path: Path
) -> None:
    agent = make_agent(parse_config(config_dict), ok(credentials_body(forge_token=None)))

    with pytest.raises(FetchError, match="no forge_session"):
        await run_once(agent)

    assert not (tmp_path / "tokens").exists()


async def test_an_unplanned_forge_session_is_not_written(make_config, tmp_path: Path) -> None:
    config = parse_config(make_config(forge_session=None))

    await run_once(make_agent(config, ok(credentials_body())))

    assert not (tmp_path / "forge-session").exists()


async def test_a_token_expired_on_arrival_writes_nothing(config_dict: dict, tmp_path: Path) -> None:
    body = credentials_body(lifetime=timedelta(seconds=-5))
    agent = make_agent(parse_config(config_dict), ok(body))

    with pytest.raises(FetchError, match="fix the guest's clock"):
        await run_once(agent)

    assert not (tmp_path / "tokens").exists()


# --- TLS ------------------------------------------------------------------------------------


def test_no_ca_file_uses_default_verification() -> None:
    assert agent_module._tls_context("") is True


def test_ca_file_becomes_the_verify_bundle(tmp_path: Path) -> None:
    key = generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "forge.test")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    bundle = tmp_path / "ca.pem"
    bundle.write_bytes(certificate.public_bytes(Encoding.PEM))

    context = agent_module._tls_context(str(bundle))

    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert len(context.get_ca_certs()) == 1


def test_unreadable_ca_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="ca_file"):
        agent_module._tls_context(str(tmp_path / "absent.pem"))


def test_the_client_timeout_is_the_configured_one(make_config) -> None:
    agent = make_agent(parse_config(make_config(request_timeout_seconds=7)), ok(credentials_body()))

    assert agent._client.timeout == httpx.Timeout(7.0)


# --- refresh scheduling ---------------------------------------------------------------------


def fetched_with(*lifetimes: float, at: datetime = NOW) -> Fetched:
    tokens = tuple(
        IssuedGuestToken(
            volume="v", path=f"p{i}", token="t", expires_at=at + timedelta(seconds=seconds)
        )
        for i, seconds in enumerate(lifetimes)
    )
    return Fetched(
        credentials=GuestCredentials(service_account_tokens=tokens), raw=b"{}", fetched_at=at
    )


def test_refresh_is_a_fraction_of_the_earliest_lifetime() -> None:
    assert refresh_delay_seconds(fetched_with(100), 0.8) == pytest.approx(80)
    assert refresh_delay_seconds(fetched_with(300, 100, 200), 0.5) == pytest.approx(50)


def test_no_expiring_token_means_no_scheduled_refresh() -> None:
    assert refresh_delay_seconds(fetched_with(), 0.8) is None


def test_an_already_expired_token_is_an_error() -> None:
    with pytest.raises(FetchError, match="not after the guest clock"):
        refresh_delay_seconds(fetched_with(0), 0.8)


class Waits:
    """Replaces the loop's sleep: records each wait and decides when to stop."""

    def __init__(self, stop: asyncio.Event, stop_after: int) -> None:
        self.stop = stop
        self.stop_after = stop_after
        self.seconds: list[float] = []

    async def __call__(self, stop: asyncio.Event, seconds: float) -> bool:
        self.seconds.append(seconds)
        if len(self.seconds) >= self.stop_after:
            return True
        return False


async def test_loop_refreshes_at_the_fraction_then_again(
    config_dict: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    answers = iter([credentials_body(token="first"), credentials_body(token="second")])
    agent = make_agent(
        parse_config(config_dict), lambda request: httpx.Response(200, json=next(answers))
    )
    state = CredentialState(clock=agent.now)
    first = await agent.fetch_and_write()
    state.record_success(first)
    stop = asyncio.Event()
    waits = Waits(stop, stop_after=2)
    monkeypatch.setattr(agent_module, "_wait", waits)

    await run_refresh_loop(agent, state, first, stop)

    assert waits.seconds == [pytest.approx(80), pytest.approx(80)]
    assert (tmp_path / "tokens" / "identity" / "token").read_text() == "second"
    assert state.health() == (True, "ok")


async def test_loop_accounts_for_time_already_elapsed(
    config_dict: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [NOW]
    agent = make_agent(parse_config(config_dict), ok(credentials_body()), clock=lambda: now[0])
    state = CredentialState(clock=agent.now)
    first = await agent.fetch()
    now[0] = NOW + timedelta(seconds=30)
    waits = Waits(asyncio.Event(), stop_after=1)
    monkeypatch.setattr(agent_module, "_wait", waits)

    await run_refresh_loop(agent, state, first, asyncio.Event())

    assert waits.seconds == [pytest.approx(50)]


async def test_failed_refresh_logs_the_cause_retries_and_recovers(
    config_dict: dict,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    answers = iter(
        [
            httpx.Response(200, json=credentials_body(token="first")),
            httpx.Response(401, json={"error": "unauthorized"}),
            httpx.Response(401, json={"error": "unauthorized"}),
            httpx.Response(200, json=credentials_body(token="recovered")),
        ]
    )
    agent = make_agent(parse_config(config_dict), lambda request: next(answers))
    state = CredentialState(clock=agent.now)
    first = await agent.fetch_and_write()
    state.record_success(first)
    health_during_failure: list[tuple[bool, str]] = []

    class Recording(Waits):
        async def __call__(self, stop: asyncio.Event, seconds: float) -> bool:
            health_during_failure.append(state.health())
            return await super().__call__(stop, seconds)

    waits = Recording(asyncio.Event(), stop_after=4)
    monkeypatch.setattr(agent_module, "_wait", waits)

    with caplog.at_level(logging.INFO, logger="volundr.guest_agent"):
        await run_refresh_loop(agent, state, first, asyncio.Event())

    # the schedule, two retries at retry_seconds, then the schedule again after recovering
    assert waits.seconds == [pytest.approx(80), 10.0, 10.0, pytest.approx(80)]
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 2
    assert "HTTP 401: unauthorized" in errors[0].getMessage()
    assert "retrying in 10.0s" in errors[0].getMessage()
    assert "first" not in caplog.text and "recovered" not in caplog.text
    assert health_during_failure[1:3] == [(False, "the last refresh failed")] * 2
    assert health_during_failure[3] == (True, "ok")
    assert (tmp_path / "tokens" / "identity" / "token").read_text() == "recovered"
    assert state.health() == (True, "ok")


async def test_loop_with_nothing_expiring_waits_for_shutdown(make_config) -> None:
    config = parse_config(make_config(token_volumes=[]))
    body = {"service_account_tokens": [], "forge_session": {"token": "f", "grants": []}}
    agent = make_agent(config, ok(body))
    state = CredentialState(clock=agent.now)
    first = await agent.fetch_and_write()
    stop = asyncio.Event()

    task = asyncio.create_task(run_refresh_loop(agent, state, first, stop))
    await asyncio.sleep(0.05)
    assert not task.done()
    stop.set()
    await asyncio.wait_for(task, 1)


async def test_loop_really_sleeps_and_refreshes_with_short_values(make_config) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(
            200, json=credentials_body(now=datetime.now(UTC), lifetime=timedelta(seconds=0.4))
        )

    config = parse_config(make_config(refresh_fraction=0.25))
    agent = GuestAgent(config, StaticSigner(), transport=httpx.MockTransport(handler))
    state = CredentialState(clock=agent.now)
    first = await agent.fetch_and_write()
    stop = asyncio.Event()

    task = asyncio.create_task(run_refresh_loop(agent, state, first, stop))
    await asyncio.sleep(0.5)
    stop.set()
    await asyncio.wait_for(task, 1)
    await agent.close()

    assert len(calls) >= 3


# --- health ---------------------------------------------------------------------------------


def test_health_requires_a_fetch() -> None:
    assert CredentialState().health() == (False, "no credentials fetched yet")


def test_health_fails_once_a_token_has_expired() -> None:
    now = [NOW]
    state = CredentialState(clock=lambda: now[0])
    state.record_success(fetched_with(100))

    assert state.health() == (True, "ok")
    now[0] = NOW + timedelta(seconds=100)
    assert state.health() == (False, "a token has expired")


def test_health_fails_after_a_failed_refresh_until_success() -> None:
    state = CredentialState(clock=lambda: NOW)
    state.record_success(fetched_with(100))
    state.record_failure("boom")

    assert state.health() == (False, "the last refresh failed")
    state.record_success(fetched_with(100))
    assert state.health() == (True, "ok")
