"""Forge registered-host admin calls for `niuu remote`, through the Niuu host's facade.

Every call names its Forge with ``?instance_id=``, as the web console does: the Guild
facade (``niuu.adapters.inbound.rest_volundr``) passes it to that Forge and returns the
Forge's own answer, 4xx bodies included.
"""

from __future__ import annotations

from typing import Any

import httpx

from cli.api.client import APIClient

INSTANCES_PATH = "/api/v1/niuu/instances"
HOSTS_PATH = "/api/v1/forge/compute/hosts"


class ForgeAPIError(Exception):
    """The Niuu host or its Forge refused a call; the message carries their reason."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text.strip() or response.reason_phrase
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])
    return str(body)


def _checked(response: httpx.Response, what: str) -> Any:
    if response.status_code == 401:
        raise ForgeAPIError(
            f"{what}: not authenticated (401: {_detail(response)}). Run `niuu login`.",
            status_code=401,
        )
    if not response.is_success:
        raise ForgeAPIError(
            f"{what}: {response.status_code} {_detail(response)}",
            status_code=response.status_code,
        )
    if response.status_code == 204 or not response.content:
        return None
    return response.json()


class ComputeHostsAPI:
    """The compute-host routes of one Niuu host."""

    def __init__(self, client: APIClient) -> None:
        self._client = client

    @property
    def base_url(self) -> str:
        return self._client.base_url

    async def _call(
        self,
        method: str,
        path: str,
        what: str,
        *,
        json: Any | None = None,
        params: dict[str, str] | None = None,
    ) -> Any:
        try:
            response = await self._client.request(method, path, json=json, params=params)
        except httpx.RequestError as exc:
            raise ForgeAPIError(
                f"{what}: cannot reach {self._client.base_url} ({type(exc).__name__}: {exc}). "
                "Check the context's URL (`niuu context list`) or pass --url."
            ) from exc
        return _checked(response, what)

    async def forges(self) -> list[dict[str, Any]]:
        """The Forge instances this user can see on the Niuu host."""
        return await self._call(
            "GET",
            INSTANCES_PATH,
            "listing Forges",
            params={"kind": "volundr", "enabledOnly": "true"},
        )

    async def overview(self, instance_id: str) -> dict[str, Any]:
        """Hosts, pool summary, connectors, runtimes and pairing codes of one Forge."""
        try:
            return await self._call(
                "GET", HOSTS_PATH, "listing hosts", params={"instance_id": instance_id}
            )
        except ForgeAPIError as exc:
            if exc.status_code != 404:
                raise
            # The console reads it the same way: a Forge without compute.hosts has no route.
            raise ForgeAPIError(
                f"{exc}. This Forge serves no registered hosts; configure compute.hosts on it.",
                status_code=404,
            ) from exc

    async def mint(self, instance_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._call(
            "POST",
            f"{HOSTS_PATH}/pairing-codes",
            "minting a pairing code",
            json=body,
            params={"instance_id": instance_id},
        )

    async def revoke(self, instance_id: str, code_id: str) -> None:
        await self._call(
            "DELETE",
            f"{HOSTS_PATH}/pairing-codes/{code_id}",
            "revoking the pairing code",
            params={"instance_id": instance_id},
        )

    async def set_slots(self, instance_id: str, host_id: str, slots: int) -> dict[str, Any]:
        return await self._call(
            "PATCH",
            f"{HOSTS_PATH}/{host_id}",
            "setting the slot count",
            json={"slots": slots},
            params={"instance_id": instance_id},
        )

    async def drain(self, instance_id: str, host_id: str, *, remove: bool) -> dict[str, Any]:
        return await self._call(
            "POST",
            f"{HOSTS_PATH}/{host_id}/drain",
            "draining the host",
            json={"remove": remove},
            params={"instance_id": instance_id},
        )
