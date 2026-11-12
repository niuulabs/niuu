"""Forge session credentials on Bifröst's model routes (``forge:model``).

A session's broker, or the memory gateway acting for a session, calls the model
routes with the session's own Forge credential so the answer is charged to the
session's owner. This wrapper sits in front of the configured auth adapter for those
three routes only (``/v1/messages``, ``/v1/chat/completions``, ``/v1/responses``),
which is what makes the acceptance work in every ``auth_mode`` and nowhere else:

* A request that does not present a ``forge_session`` credential goes to the
  configured adapter untouched, so every mode behaves as it always did.
* A ``forge_session`` credential without ``forge:model`` is refused 403.
* Otherwise Forge confirms it (``niuu.forge_mcp.session_proof``). A refusal is a 401
  naming why; Forge being unreachable is a 503. Nothing is admitted unconfirmed and
  nothing is cached.
* The identity is what Forge answered: agent = the session's owner, tenant = its
  tenant, session = its id. No attribution header is read, so a session cannot
  charge another owner, tenant or session.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request

from bifrost.auth import AgentIdentity
from bifrost.ports.auth import AuthPort
from niuu.domain.services.token_scope import FORGE_MODEL_SCOPE, token_has_scope
from niuu.forge_mcp.credentials import is_forge_session_token
from niuu.forge_mcp.session_proof import (
    ForgeSessionVerifier,
    ForgeUnavailableError,
    SessionNotConfirmedError,
)

logger = logging.getLogger(__name__)

_BEARER_PREFIX = "bearer "


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith(_BEARER_PREFIX):
        return ""
    return header[len(_BEARER_PREFIX) :].strip()


class ForgeSessionModelAuth(AuthPort):
    """Admit a confirmed Forge session credential, else defer to ``inner``.

    Args:
        inner: the configured auth adapter (open / pat / mesh / oidc).
        verifier: confirms credentials with Forge; ``None`` when ``forge_session.base_url``
            is not configured, in which case a session credential is refused naming it.
    """

    def __init__(self, inner: AuthPort, verifier: ForgeSessionVerifier | None) -> None:
        self._inner = inner
        self._verifier = verifier

    async def extract(self, request: Request) -> AgentIdentity:
        token = _bearer(request)
        if not token or not is_forge_session_token(token):
            return await self._inner.extract(request)

        if not token_has_scope(token, FORGE_MODEL_SCOPE):
            raise HTTPException(
                status_code=403,
                detail=f"Session credential does not carry {FORGE_MODEL_SCOPE}",
            )
        if self._verifier is None:
            raise HTTPException(
                status_code=401,
                detail=(
                    "Bifröst cannot confirm a Forge session credential: "
                    "forge_session.base_url is not configured"
                ),
            )
        try:
            confirmed = await self._verifier.confirm(token)
        except ForgeUnavailableError as exc:
            logger.error("Forge session credential could not be confirmed: %s", exc)
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except SessionNotConfirmedError as exc:
            logger.warning("Forge session credential refused: %s", exc)
            raise HTTPException(status_code=401, detail=str(exc)) from exc

        return AgentIdentity(
            agent_id=confirmed.owner_id,
            tenant_id=confirmed.tenant_id,
            session_id=confirmed.session_id,
        )
