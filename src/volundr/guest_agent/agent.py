"""Fetch credentials from Forge, write them, and keep them fresh."""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx

from volundr.domain.guest_credentials import (
    METADATA_CREDENTIALS_PATH,
    GuestCredentials,
    IssuedGuestForgeSession,
    IssuedGuestToken,
)
from volundr.guest_agent.config import AgentConfig
from volundr.guest_agent.errors import ConfigError, FetchError, GuestAgentError
from volundr.guest_agent.signers import RequestSigner
from volundr.guest_agent.writer import PlannedFile, write_all

logger = logging.getLogger("volundr.guest_agent")


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Fetched:
    """One successful answer from Forge, with when it arrived."""

    credentials: GuestCredentials
    #: The response body exactly as Forge sent it; ``/niuu/v1/credentials`` serves it back.
    raw: bytes
    fetched_at: datetime

    @property
    def earliest_expiry(self) -> datetime | None:
        """When the first ServiceAccount token expires; ``None`` when there are none.

        The Forge session credential carries no expiry: it lives until the
        session stops or restarts, so it never schedules a refresh by itself.
        """
        expiries = [token.expires_at for token in self.credentials.service_account_tokens]
        return min(expiries) if expiries else None


def refresh_delay_seconds(fetched: Fetched, fraction: float) -> float | None:
    """Seconds after ``fetched_at`` to refresh, or ``None`` when nothing expires.

    ``fraction`` of the earliest token's lifetime, measured from when it was
    fetched. A token already expired on arrival is an error, not a zero delay.
    """
    expiry = fetched.earliest_expiry
    if expiry is None:
        return None
    lifetime = (expiry - fetched.fetched_at).total_seconds()
    if lifetime <= 0:
        raise FetchError(
            f"Forge issued a token that expires at {expiry.isoformat()}, which is not after "
            f"the guest clock ({fetched.fetched_at.isoformat()}); fix the guest's clock"
        )
    return lifetime * fraction


class CredentialState:
    """What the guest's own processes can read, and whether it is healthy."""

    def __init__(self, clock: Callable[[], datetime] = utc_now) -> None:
        self._clock = clock
        self._fetched: Fetched | None = None
        self._last_error: str | None = None

    def record_success(self, fetched: Fetched) -> None:
        self._fetched = fetched
        self._last_error = None

    def record_failure(self, message: str) -> None:
        self._last_error = message

    @property
    def raw(self) -> bytes | None:
        return self._fetched.raw if self._fetched else None

    def health(self) -> tuple[bool, str]:
        """``(healthy, reason)``: the last fetch succeeded and no token has expired."""
        if self._fetched is None:
            return False, "no credentials fetched yet"
        if self._last_error is not None:
            return False, "the last refresh failed"
        expiry = self._fetched.earliest_expiry
        if expiry is not None and self._clock() >= expiry:
            return False, "a token has expired"
        return True, "ok"


class GuestAgent:
    def __init__(
        self,
        config: AgentConfig,
        signer: RequestSigner,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._config = config
        self._signer = signer
        self._clock = clock
        url = config.metadata_url.rstrip("/") + METADATA_CREDENTIALS_PATH
        self._url = url
        self._path = urlsplit(url).path
        self._client = httpx.AsyncClient(
            transport=transport,
            verify=_tls_context(config.ca_file),
            timeout=config.request_timeout_seconds,
        )

    @property
    def config(self) -> AgentConfig:
        return self._config

    def now(self) -> datetime:
        return self._clock()

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch(self) -> Fetched:
        body = json.dumps(
            {"allocation_id": self._config.allocation_id, "slot": self._config.slot}
        ).encode()
        headers = await self._signer.headers(method="POST", path=self._path, body=body)
        headers["Content-Type"] = "application/json"
        try:
            response = await self._client.post(self._url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            raise FetchError(f"request to {self._url} failed: {type(exc).__name__}: {exc}") from exc
        fetched_at = self._clock()
        if response.status_code != 200:
            raise FetchError(
                f"Forge rejected the credentials request with HTTP {response.status_code}: "
                f"{_error_reason(response)}"
            )
        credentials = _parse_credentials(response.content)
        return Fetched(credentials=credentials, raw=response.content, fetched_at=fetched_at)

    async def fetch_and_write(self) -> Fetched:
        fetched = await self.fetch()
        # Validate the schedule before touching any file, so a bad answer writes nothing.
        refresh_delay_seconds(fetched, self._config.refresh_fraction)
        files = self._plan_files(fetched.credentials)
        await asyncio.to_thread(write_all, files)
        return fetched

    def _plan_files(self, credentials: GuestCredentials) -> list[PlannedFile]:
        issued = {(t.volume, t.path): t for t in credentials.service_account_tokens}
        files: list[PlannedFile] = []
        for volume in self._config.token_volumes:
            for entry in volume.tokens:
                token = issued.get((volume.volume, entry.path))
                if token is None:
                    raise FetchError(
                        f"Forge's answer has no token for volume {volume.volume!r} path "
                        f"{entry.path!r}, which token_volumes plans; the launch and the "
                        "guest config disagree"
                    )
                files.append(
                    PlannedFile(
                        directory=volume.directory, name=entry.path, content=token.token.encode()
                    )
                )
        target = self._config.forge_session
        if target is None:
            return files
        if credentials.forge_session is None:
            raise FetchError(
                "Forge's answer has no forge_session, which forge_session plans; the session "
                "has no Forge credential to deliver"
            )
        files.append(
            PlannedFile(
                directory=target.directory,
                name=target.file,
                content=credentials.forge_session.token.encode(),
            )
        )
        return files


async def run_once(agent: GuestAgent) -> None:
    """Fetch and write every planned file once."""
    fetched = await agent.fetch_and_write()
    logger.info("wrote %s", _describe(fetched))


async def run_refresh_loop(
    agent: GuestAgent, state: CredentialState, first: Fetched, stop: asyncio.Event
) -> None:
    """Refresh at ``refresh_fraction`` of the earliest token's lifetime until ``stop``.

    A failed refresh is logged at ERROR and retried every ``retry_seconds``;
    the previous files stay in place meanwhile and ``/niuu/v1/health`` reports 503.
    """
    config = agent.config
    fetched = first
    while True:
        delay = refresh_delay_seconds(fetched, config.refresh_fraction)
        if delay is None:
            logger.info("no ServiceAccount token expires; waiting for shutdown")
            await stop.wait()
            return
        elapsed = (agent.now() - fetched.fetched_at).total_seconds()
        if await _wait(stop, max(delay - elapsed, 0.0)):
            return
        while True:
            try:
                fetched = await agent.fetch_and_write()
            except GuestAgentError as exc:
                state.record_failure(str(exc))
                logger.error(
                    "credential refresh failed, retrying in %ss: %s", config.retry_seconds, exc
                )
                if await _wait(stop, config.retry_seconds):
                    return
                continue
            state.record_success(fetched)
            logger.info("refreshed %s", _describe(fetched))
            break


async def _wait(stop: asyncio.Event, seconds: float) -> bool:
    """Sleep up to ``seconds``; return True when asked to stop first."""
    try:
        await asyncio.wait_for(stop.wait(), seconds)
    except TimeoutError:
        return False
    return True


def _describe(fetched: Fetched) -> str:
    credentials = fetched.credentials
    forge = "with" if credentials.forge_session else "without"
    count = len(credentials.service_account_tokens)
    return f"{count} ServiceAccount token(s) {forge} a Forge session credential"


def _tls_context(ca_file: str) -> ssl.SSLContext | bool:
    if not ca_file:
        return True
    try:
        return ssl.create_default_context(cafile=ca_file)
    except (OSError, ssl.SSLError) as exc:
        raise ConfigError(f"ca_file {ca_file!r} cannot be loaded as a CA bundle: {exc}") from exc


def _error_reason(response: httpx.Response) -> str:
    try:
        document = response.json()
    except ValueError:
        return "no JSON error body"
    reason = document.get("error") if isinstance(document, dict) else None
    return reason if isinstance(reason, str) else "no error reason in the body"


def _parse_credentials(body: bytes) -> GuestCredentials:
    try:
        document = json.loads(body)
    except ValueError as exc:
        raise FetchError("Forge's answer is not JSON") from exc
    if not isinstance(document, dict):
        raise FetchError("Forge's answer is not a JSON object")
    raw_tokens = document.get("service_account_tokens")
    if not isinstance(raw_tokens, list):
        raise FetchError("Forge's answer has no service_account_tokens list")
    tokens = tuple(_parse_token(item, index) for index, item in enumerate(raw_tokens))
    return GuestCredentials(
        service_account_tokens=tokens,
        forge_session=_parse_forge_session(document.get("forge_session")),
    )


def _parse_token(item: object, index: int) -> IssuedGuestToken:
    where = f"service_account_tokens[{index}]"
    if not isinstance(item, dict):
        raise FetchError(f"Forge's answer: {where} is not an object")
    values: dict[str, str] = {}
    for name in ("volume", "path", "token", "expires_at"):
        value = item.get(name)
        if not isinstance(value, str) or not value:
            raise FetchError(f"Forge's answer: {where}.{name} is missing or empty")
        values[name] = value
    try:
        expires_at = datetime.fromisoformat(values["expires_at"])
    except ValueError as exc:
        raise FetchError(f"Forge's answer: {where}.expires_at is not ISO-8601") from exc
    if expires_at.tzinfo is None:
        raise FetchError(f"Forge's answer: {where}.expires_at has no UTC offset")
    return IssuedGuestToken(
        volume=values["volume"],
        path=values["path"],
        token=values["token"],
        expires_at=expires_at,
    )


def _parse_forge_session(item: object) -> IssuedGuestForgeSession | None:
    if item is None:
        return None
    if not isinstance(item, dict):
        raise FetchError("Forge's answer: forge_session is not an object or null")
    token = item.get("token")
    if not isinstance(token, str) or not token:
        raise FetchError("Forge's answer: forge_session.token is missing or empty")
    grants = item.get("grants")
    if not isinstance(grants, list) or not all(isinstance(g, str) for g in grants):
        raise FetchError("Forge's answer: forge_session.grants is not a list of strings")
    return IssuedGuestForgeSession(token=token, grants=tuple(grants))
