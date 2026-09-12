"""OAuth 2.0 device authorization grant (RFC 8628) as a credential enrollment runner.

GitHub Apps and GitLab applications both let a user sign in from a device
that only knows a public client id: the platform asks the provider for a
device code, shows the user the verification URL and user code, and polls
the token endpoint until the user approves. No client secret, no callback
URL, so it works on a single-host install exactly like the Codex device
flow, without a helper container.

Which providers support it is data on the integration definition
(``oauth.device_authorization_url`` + ``credential_enrollment.method =
"oauth_device"``); the client id comes from ``oauth.clients.<slug>``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx

from volundr.domain.models import (
    CredentialEnrollment,
    CredentialEnrollmentPoll,
    CredentialEnrollmentState,
)
from volundr.domain.ports import CredentialEnrollmentRunnerPort
from volundr.domain.services.integration_registry import IntegrationRegistry

logger = logging.getLogger(__name__)

OAUTH_DEVICE_METHOD = "oauth_device"
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
DEFAULT_INTERVAL_SECONDS = 5
DEFAULT_EXPIRES_IN_SECONDS = 900
SLOW_DOWN_PENALTY_SECONDS = 5
DEFAULT_TIMEOUT_SECONDS = 15.0


@dataclass
class _DeviceSession:
    token_url: str
    client_id: str
    device_code: str
    verification_uri: str
    user_code: str
    interval: float
    next_poll_at: datetime
    expires_at: datetime


def _as_dict(response: httpx.Response) -> dict[str, Any]:
    """Provider token responses are JSON when asked; GitHub falls back to a query string."""
    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        data = response.json()
        return data if isinstance(data, dict) else {}
    parsed: dict[str, Any] = {}
    for pair in response.text.split("&"):
        if "=" in pair:
            key, value = pair.split("=", 1)
            parsed[key] = httpx.URL(f"http://x/?v={value}").params.get("v", value)
    return parsed


class OAuthDeviceFlowRunner(CredentialEnrollmentRunnerPort):
    """Run the device grant in-process for definitions that declare one.

    Args:
        registry: Integration catalog (device URL, token URL, scopes per slug).
        client_ids: ``{slug: client_id}`` from ``oauth.clients``.
        request_timeout: Seconds per HTTP call to the provider.
    """

    def __init__(
        self,
        *,
        registry: IntegrationRegistry,
        client_ids: dict[str, str],
        request_timeout: float = DEFAULT_TIMEOUT_SECONDS,
        **_extra: object,
    ) -> None:
        self._registry = registry
        self._client_ids = {slug: cid for slug, cid in client_ids.items() if cid}
        self._timeout = float(request_timeout)
        self._sessions: dict[UUID, _DeviceSession] = {}

    # ------------------------------------------------------------------
    # Capability
    # ------------------------------------------------------------------

    def supports_enrollment(self, method: str) -> bool:
        return method == OAUTH_DEVICE_METHOD

    def available_for(self, slug: str, method: str) -> bool:
        if not self.supports_enrollment(method):
            return False
        definition = self._registry.get_definition(slug)
        if definition is None or definition.oauth is None:
            return False
        return bool(definition.oauth.device_authorization_url) and slug in self._client_ids

    def _config(self, slug: str) -> tuple[str, str, str, tuple[str, ...]]:
        definition = self._registry.get_definition(slug)
        if definition is None or definition.oauth is None:
            raise ValueError(f"Integration {slug!r} has no OAuth specification")
        if not definition.oauth.device_authorization_url:
            raise ValueError(f"Integration {slug!r} does not declare a device authorization URL")
        client_id = self._client_ids.get(slug, "")
        if not client_id:
            raise ValueError(
                f"No OAuth client id configured for {slug!r}; set oauth.clients.{slug}.client_id "
                "(a public client id with the device flow enabled) or use an API key instead"
            )
        return (
            definition.oauth.device_authorization_url,
            definition.oauth.token_url,
            client_id,
            definition.oauth.scopes,
        )

    # ------------------------------------------------------------------
    # CredentialEnrollmentRunnerPort
    # ------------------------------------------------------------------

    async def start_enrollment(self, enrollment: CredentialEnrollment) -> CredentialEnrollment:
        if not self.supports_enrollment(enrollment.method):
            raise ValueError("Unsupported login method")
        device_url, token_url, client_id, scopes = self._config(enrollment.provider_slug)
        form: dict[str, str] = {"client_id": client_id}
        if scopes:
            form["scope"] = " ".join(scopes)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                device_url, data=form, headers={"Accept": "application/json"}
            )
        if response.status_code != 200:
            raise ValueError(
                f"{enrollment.provider_slug} refused the device authorization request "
                f"(HTTP {response.status_code}): {response.text[:200]}"
            )
        data = _as_dict(response)
        if "error" in data:
            raise ValueError(
                f"{enrollment.provider_slug} refused the device authorization request: "
                f"{data.get('error')} {data.get('error_description', '')}".strip()
            )
        now = datetime.now(UTC)
        raw_interval = data.get("interval")
        interval = (
            float(raw_interval) if raw_interval is not None else float(DEFAULT_INTERVAL_SECONDS)
        )
        expires_in = float(data.get("expires_in") or DEFAULT_EXPIRES_IN_SECONDS)
        session = _DeviceSession(
            token_url=token_url,
            client_id=client_id,
            device_code=str(data["device_code"]),
            verification_uri=str(
                data.get("verification_uri_complete") or data.get("verification_uri") or ""
            ),
            user_code=str(data.get("user_code") or ""),
            interval=interval,
            next_poll_at=now + timedelta(seconds=interval),
            expires_at=min(enrollment.expires_at, now + timedelta(seconds=expires_in)),
        )
        self._sessions[enrollment.id] = session
        # The challenge is known immediately; the record carries it so the
        # first status read already shows the URL and code.
        return replace(
            enrollment,
            state=CredentialEnrollmentState.AWAITING_USER,
            verification_uri=session.verification_uri,
            user_code=session.user_code,
            runner_ref={"runner": OAUTH_DEVICE_METHOD},
        )

    async def poll_enrollment(self, enrollment: CredentialEnrollment) -> CredentialEnrollmentPoll:
        session = self._sessions.get(enrollment.id)
        if session is None:
            # A platform restart loses the device code; the user starts again.
            return CredentialEnrollmentPoll(
                state=CredentialEnrollmentState.FAILED, error_code="login_worker_missing"
            )
        now = datetime.now(UTC)
        if now >= session.expires_at:
            self._sessions.pop(enrollment.id, None)
            return CredentialEnrollmentPoll(state=CredentialEnrollmentState.EXPIRED)
        waiting = CredentialEnrollmentPoll(
            state=CredentialEnrollmentState.AWAITING_USER,
            verification_uri=session.verification_uri,
            user_code=session.user_code,
        )
        if now < session.next_poll_at:
            return waiting

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                session.token_url,
                data={
                    "client_id": session.client_id,
                    "device_code": session.device_code,
                    "grant_type": DEVICE_GRANT_TYPE,
                },
                headers={"Accept": "application/json"},
            )
        data = _as_dict(response)
        error = str(data.get("error") or "")
        if error == "authorization_pending" or (not error and "access_token" not in data):
            session.next_poll_at = now + timedelta(seconds=session.interval)
            return waiting
        if error == "slow_down":
            session.interval += SLOW_DOWN_PENALTY_SECONDS
            session.next_poll_at = now + timedelta(seconds=session.interval)
            return waiting
        if error == "expired_token":
            self._sessions.pop(enrollment.id, None)
            return CredentialEnrollmentPoll(state=CredentialEnrollmentState.EXPIRED)
        if error == "access_denied":
            self._sessions.pop(enrollment.id, None)
            return CredentialEnrollmentPoll(
                state=CredentialEnrollmentState.FAILED, error_code="provider_login_rejected"
            )
        if error:
            self._sessions.pop(enrollment.id, None)
            logger.error(
                "Device flow for %s failed: %s %s",
                enrollment.provider_slug,
                error,
                data.get("error_description", ""),
            )
            return CredentialEnrollmentPoll(
                state=CredentialEnrollmentState.FAILED, error_code=f"oauth_{error}"
            )

        self._sessions.pop(enrollment.id, None)
        credential: dict[str, str] = {"token": str(data["access_token"])}
        if data.get("refresh_token"):
            credential["refresh_token"] = str(data["refresh_token"])
        if data.get("expires_in"):
            expires_at = now + timedelta(seconds=float(data["expires_in"]))
            credential["expires_at"] = expires_at.isoformat()
        return CredentialEnrollmentPoll(
            state=CredentialEnrollmentState.COMPLETE, credential_data=credential
        )

    async def cancel_enrollment(self, enrollment: CredentialEnrollment) -> None:
        self._sessions.pop(enrollment.id, None)


class CompositeCredentialEnrollmentRunner(CredentialEnrollmentRunnerPort):
    """Dispatch each enrollment to the first runner that implements its method."""

    def __init__(self, runners: list[CredentialEnrollmentRunnerPort]) -> None:
        if not runners:
            raise ValueError("At least one enrollment runner is required")
        self._runners = list(runners)

    def _for(self, method: str) -> CredentialEnrollmentRunnerPort:
        for runner in self._runners:
            if runner.supports_enrollment(method):
                return runner
        raise ValueError(f"No enrollment runner implements {method!r}")

    def supports_enrollment(self, method: str) -> bool:
        return any(runner.supports_enrollment(method) for runner in self._runners)

    def available_for(self, slug: str, method: str) -> bool:
        return any(runner.available_for(slug, method) for runner in self._runners)

    async def start_enrollment(self, enrollment: CredentialEnrollment) -> CredentialEnrollment:
        return await self._for(enrollment.method).start_enrollment(enrollment)

    async def poll_enrollment(self, enrollment: CredentialEnrollment) -> CredentialEnrollmentPoll:
        return await self._for(enrollment.method).poll_enrollment(enrollment)

    async def cancel_enrollment(self, enrollment: CredentialEnrollment) -> None:
        await self._for(enrollment.method).cancel_enrollment(enrollment)

    async def submit_code(self, enrollment: CredentialEnrollment, code: str) -> None:
        await self._for(enrollment.method).submit_code(enrollment, code)
