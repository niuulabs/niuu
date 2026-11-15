"""ForgeProposalNotifier — Forge's notifications for memory proposals.

- ``POST /api/v1/forge/sessions/{session_id}/memory/proposals`` with the proposing
  session's own ``forge_session`` credential (scope ``forge:memory``, the session's own):
  Forge tells the approvers and owners of the session's project.
- ``POST /api/v1/forge/projects/{project_id}/memory/proposals/{proposal_id}/decided``
  with the deciding person's own login: Forge settles what it told them.

Anything but a 2xx is :class:`ProposalNotifyError` naming Forge's answer.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from mimir.ports.proposal_notifier import ProposalNotifier, ProposalNotifyError, WaitingProposal

#: Ids only ever go into Forge's URL path; nothing else may ride along.
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class ForgeProposalNotifier(ProposalNotifier):
    """Args:
    base_url: Forge's base URL, e.g. ``http://volundr:8080``.
    timeout_seconds: per-request timeout.
    """

    def __init__(self, base_url: str, *, timeout_seconds: float) -> None:
        if not base_url.strip():
            raise ValueError("memory.forge_base_url is empty: set it to Forge's base URL")
        if timeout_seconds <= 0:
            raise ValueError("memory.forge_timeout_seconds must be positive")
        self._base = base_url.rstrip("/")
        self._timeout = float(timeout_seconds)
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    @staticmethod
    def _id(value: str, what: str) -> str:
        if not _ID.fullmatch(value):
            raise ProposalNotifyError(f"{what} {value!r} is not a valid id")
        return value

    async def _post(self, path: str, body: dict[str, Any], authorization: str, what: str) -> None:
        headers = {"Accept": "application/json"}
        if authorization:
            headers["Authorization"] = authorization
        try:
            response = await self._http().post(self._base + path, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise ProposalNotifyError(
                f"Forge could not be told {what} at {self._base}: {exc}"
            ) from exc
        if not 200 <= response.status_code < 300:
            raise ProposalNotifyError(
                f"Forge answered {response.status_code} when told {what}: {response.text}"
            )

    async def waiting(self, session_id: str, proposal: WaitingProposal, *, credential: str) -> None:
        if not credential.strip():
            raise ProposalNotifyError("telling Forge needs the proposing session's credential")
        path = f"/api/v1/forge/sessions/{self._id(session_id, 'session')}/memory/proposals"
        body = {
            "proposal_id": self._id(proposal.proposal_id, "proposal"),
            "project_id": proposal.project_id,
            "title": proposal.title,
            "why": proposal.why,
        }
        what = f"that proposal {proposal.proposal_id} waits"
        await self._post(path, body, f"Bearer {credential.strip()}", what)

    async def decided(
        self, project_id: str, proposal_id: str, *, authorization: str, outcome: str
    ) -> None:
        path = (
            f"/api/v1/forge/projects/{self._id(project_id, 'project')}/memory/proposals/"
            f"{self._id(proposal_id, 'proposal')}/decided"
        )
        await self._post(
            path, {"outcome": outcome}, authorization, f"that proposal {proposal_id} was decided"
        )
