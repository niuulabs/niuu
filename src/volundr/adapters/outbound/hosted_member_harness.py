"""Stop or restart one member of a unit by asking the member's own Skuld.

A pod's containers, and a sandbox's peer workloads, are started and ended with the pod or
sandbox: nothing outside them can stop or restart one. A member is its own Forge session,
though, with its own harness, so Forge asks its Skuld (``/api/harness/stop`` and
``/api/harness/restart``, see ``skuld.harness_control``), which ends its harness and, for a
restart, replaces its own process so that it reads the credential Forge rewrote.

A restart is never skipped: a Skuld that does not come back ready, or whose credential file
has not been delivered, is an error naming the setting that bounds the wait.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from niuu.cap_telemetry import record_cap_hit
from niuu.ports.session_proxy import SessionProxyTarget
from volundr.domain.models import Session
from volundr.domain.ports import HostedUnitError
from volundr.domain.session_broker import broker_target_request

logger = logging.getLogger(__name__)

#: Defaults of the pod-manager kwargs that bound a restart (config, not logic). The wait is
#: the time a changed Secret takes to reach a running container (the kubelet's sync period
#: plus its cache), the real bound of a restart on Kubernetes; the poll is how often to ask
#: again; the request is the longest one call to a Skuld may take (it ends its harness first).
DEFAULT_RESTART_WAIT_SECONDS = 180.0
DEFAULT_POLL_SECONDS = 2.0
DEFAULT_REQUEST_SECONDS = 30.0

#: The pod-manager kwarg that bounds a restart's wait; named in the error a timeout raises.
RESTART_WAIT_KNOB = "pod_manager.kwargs.hosted_member_restart_wait_seconds"


class MemberHarnessControl:
    """Asks a member's Skuld to stop or restart its harness."""

    def __init__(
        self,
        *,
        restart_wait_seconds: float,
        poll_seconds: float,
        request_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        for name, value in (
            ("hosted_member_restart_wait_seconds", restart_wait_seconds),
            ("hosted_member_poll_seconds", poll_seconds),
            ("hosted_member_request_seconds", request_seconds),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive, got {value}")
        self._wait = restart_wait_seconds
        self._poll = poll_seconds
        self._request = request_seconds
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._request, transport=self._transport)

    @staticmethod
    def _url(member: Session, target: SessionProxyTarget | None, *segments: str):
        if target is None:
            raise HostedUnitError(
                f"Member session {member.id} has no broker target, so its Skuld cannot be "
                "asked to stop or restart its harness"
            )
        return broker_target_request(target, *segments)

    async def stop(self, member: Session, target: SessionProxyTarget | None) -> None:
        """Ask the member's Skuld to end its harness (idempotent); raises when it cannot."""
        url, headers = self._url(member, target, "api", "harness", "stop")
        async with self._client() as client:
            try:
                response = await client.post(url, headers=headers)
            except httpx.HTTPError as exc:
                raise HostedUnitError(
                    f"Member session {member.id}: its Skuld could not be asked to stop "
                    f"({type(exc).__name__}: {exc}); its process may still be running"
                ) from exc
        if response.status_code != httpx.codes.OK:
            raise HostedUnitError(
                f"Member session {member.id}: its Skuld refused to stop its harness "
                f"({response.status_code}: {_detail(response)})"
            )

    async def restart(
        self, member: Session, target: SessionProxyTarget | None, launch_id: str
    ) -> None:
        """Ask the member's Skuld to restart under ``launch_id`` and wait until it is ready.

        The credential file is delivered some time after Forge rewrites it, so the Skuld
        answers ``credential_stale`` until it carries the launch; this asks again until
        the restart wait is over.
        """
        restart_url, headers = self._url(member, target, "api", "harness", "restart")
        ready_url, _ = self._url(member, target, "ready")
        deadline = time.monotonic() + self._wait
        async with self._client() as client:
            await self._request_restart(client, member, restart_url, headers, launch_id, deadline)
            await self._await_ready(client, member, ready_url, headers, deadline)

    async def _request_restart(
        self,
        client: httpx.AsyncClient,
        member: Session,
        url: str,
        headers: dict[str, str],
        launch_id: str,
        deadline: float,
    ) -> None:
        last = "no answer yet"
        while time.monotonic() < deadline:
            try:
                response = await client.post(url, headers=headers, json={"launch_id": launch_id})
            except httpx.HTTPError as exc:
                raise HostedUnitError(
                    f"Member session {member.id}: its Skuld could not be asked to restart "
                    f"({type(exc).__name__}: {exc})"
                ) from exc
            if response.status_code == httpx.codes.OK:
                return
            if _error_code(response) != "credential_stale":
                raise HostedUnitError(
                    f"Member session {member.id}: its Skuld refused to restart its harness "
                    f"({response.status_code}: {_detail(response)})"
                )
            last = _detail(response)
            await asyncio.sleep(self._poll)
        self._expired(member, f"the new credential did not reach it ({last})")

    async def _await_ready(
        self,
        client: httpx.AsyncClient,
        member: Session,
        url: str,
        headers: dict[str, str],
        deadline: float,
    ) -> None:
        while time.monotonic() < deadline:
            await asyncio.sleep(self._poll)
            try:
                response = await client.get(url, headers=headers)
            except httpx.HTTPError:
                continue  # the process is being replaced; it has no listener for a moment
            if response.status_code == httpx.codes.OK:
                return
        self._expired(member, "it did not come back ready")

    def _expired(self, member: Session, why: str) -> None:
        message = (
            f"Member session {member.id} did not restart within {self._wait:g}s: {why}. Raise "
            f"{RESTART_WAIT_KNOB} if the cluster delivers a changed Secret more slowly, or "
            "stop the run and start it again"
        )
        record_cap_hit(RESTART_WAIT_KNOB, message)
        raise HostedUnitError(message)


def _error_code(response: httpx.Response) -> str:
    detail = _json(response).get("detail")
    return str(detail.get("error", "")) if isinstance(detail, dict) else ""


def _detail(response: httpx.Response) -> str:
    detail = _json(response).get("detail")
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("error") or detail)
    return str(detail or response.text[:200])


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
