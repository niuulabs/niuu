"""Realm governance client — resolve a Valkyrie's tool-build trust grant.

Phase 0.5 landed a realm governance API in niuu, mounted on the Volundr host
(the same ``base_url`` Ravn already uses for Forge sessions). This client reads
the realm's trust grants over the workload-authenticated HTTP boundary — Ravn
never imports niuu/volundr, it only calls them.

The "build" action-class grant with the highest ``level`` governs this
Valkyrie's tool-building: ``grant.limits`` may hold a workflow selector
(``{"workflow": "tool-builder"}``) and ``grant.level`` is its autonomy rung.

The trust-level -> autonomy-mode table lives here as documented constants.
Phase 5 (P5) may move this table into config; until then it is the single
authoritative mapping so ``ravn.config`` never carries the rungs as bare
literals.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ravn.adapters.tool_build.http import (
    AsyncJsonHttpClient,
    client_from_workload_identity,
)

logger = logging.getLogger(__name__)

#: Action class of the grant that governs tool-building.
BUILD_ACTION_CLASS = "build"

#: HTTP status that means the request succeeded.
_HTTP_OK = 200

# ---------------------------------------------------------------------------
# Trust-level -> autonomy-mode table
#
# Explicit, documented rungs (no scattered literals). The three autonomy modes
# mirror ``ResidentEvolutionConfig.autonomy_mode``:
#   guarded    — records proposals only
#   autonomous — applies low-risk private/Environment changes
#   yolo       — evolves within delegated boundaries
#
# Rung boundaries (inclusive):
#   level <= 1        -> guarded     (minimal trust: propose only)
#   2 <= level <= 3   -> autonomous  (trusted for low-risk self-evolution)
#   level >= 4        -> yolo        (fully delegated within boundaries)
# ---------------------------------------------------------------------------
_AUTONOMY_MODE_GUARDED = "guarded"
_AUTONOMY_MODE_AUTONOMOUS = "autonomous"
_AUTONOMY_MODE_YOLO = "yolo"

#: Highest level that still maps to ``guarded``.
_GUARDED_MAX_LEVEL = 1
#: Highest level that maps to ``autonomous``; anything above is ``yolo``.
_AUTONOMOUS_MAX_LEVEL = 3

#: Auth kwargs forwarded verbatim to ``client_from_workload_identity``.
_AUTH_KWARG_NAMES = (
    "external_token_env",
    "workload_token_file",
    "workload_exchange_url",
    "workload_audiences",
)


@dataclass(frozen=True)
class BuildGrant:
    """The realm's 'build' trust grant governing this Valkyrie's tool-building."""

    level: int
    limits: dict[str, Any]
    target: str


def autonomy_mode_for_trust_level(level: int) -> str:
    """Map a realm trust level to a resident autonomy mode.

    See the module-level table for the rung boundaries. P5 may move this table
    into config; for now it is the single authoritative mapping.
    """
    if level <= _GUARDED_MAX_LEVEL:
        return _AUTONOMY_MODE_GUARDED
    if level <= _AUTONOMOUS_MAX_LEVEL:
        return _AUTONOMY_MODE_AUTONOMOUS
    return _AUTONOMY_MODE_YOLO


def workflow_selector_from_grant(grant: BuildGrant) -> dict[str, Any] | None:
    """Derive a workflow selector dict from a build grant's ``limits``.

    A grant may pin the Ting workflow it commissions via
    ``limits = {"workflow": "tool-builder"}``. That string is treated as a
    workflow name/id selector. Returns ``None`` when no workflow is pinned.
    """
    workflow = grant.limits.get("workflow")
    if not isinstance(workflow, str) or not workflow.strip():
        return None
    return {"names": [workflow.strip()]}


def build_realm_client_kwargs(
    *,
    realm_api_kwargs: dict[str, Any],
    tool_build_kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Pick the auth kwargs for the realm client.

    Prefers ``realm_api_kwargs``; falls back to the tool-build adapter's auth
    settings so a Valkyrie that already authenticates to Forge does not need to
    repeat its workload-identity config for realm calls.
    """
    source = realm_api_kwargs or tool_build_kwargs
    return {name: source[name] for name in _AUTH_KWARG_NAMES if name in source}


class RealmClient:
    """Read a realm's trust grants over the workload-authenticated HTTP boundary."""

    def __init__(
        self,
        *,
        base_url: str,
        client: AsyncJsonHttpClient | None = None,
        external_token_env: str = "",
        workload_token_file: str = "",
        workload_exchange_url: str = "",
        workload_audiences: list[str] | None = None,
    ) -> None:
        if not base_url:
            msg = "RealmClient requires a base_url"
            raise ValueError(msg)
        self._base_url = base_url.rstrip("/")
        self._client = (
            client
            if client is not None
            else client_from_workload_identity(
                base_url=base_url,
                external_token_env=external_token_env,
                workload_token_file=workload_token_file,
                workload_exchange_url=workload_exchange_url,
                workload_audiences=workload_audiences,
            )
        )

    @property
    def base_url(self) -> str:
        """Normalized base URL the client talks to (read-only, for diagnostics)."""
        return self._base_url

    async def resolve_build_grant(self, realm_slug: str) -> BuildGrant | None:
        """Return the highest-level 'build' grant for ``realm_slug``, or ``None``.

        Returns ``None`` for the legitimate no-grant cases: an unknown realm or
        HTTP error (non-200), or a realm with no 'build' action-class grant.
        Raises (fails loudly) when a 200 response body is not a JSON list or a
        matching 'build' grant is missing its required ``level`` — a malformed
        governance response must not be silently treated as "no grant".
        """
        if not realm_slug:
            return None
        url = f"{self._base_url}/api/v1/realms/{realm_slug}/trust-grants"
        resp = await self._client.get(url)
        if resp.status_code != _HTTP_OK:
            logger.info(
                "realm %s trust-grants returned HTTP %s; no build grant resolved",
                realm_slug,
                resp.status_code,
            )
            return None
        if not isinstance(resp.body, list):
            msg = (
                f"realm {realm_slug} trust-grants returned a non-list body: "
                f"{type(resp.body).__name__}"
            )
            raise ValueError(msg)

        build_grants = [
            grant
            for grant in resp.body
            if isinstance(grant, dict) and grant.get("action_class") == BUILD_ACTION_CLASS
        ]
        if not build_grants:
            return None
        highest = max(build_grants, key=_grant_level)
        return _grant_from_body(highest, realm_slug=realm_slug)


def _grant_level(grant: dict[str, Any]) -> int:
    """Sort key: the grant's numeric ``level`` (defaults to lowest when absent)."""
    level = grant.get("level")
    if isinstance(level, bool) or not isinstance(level, int):
        return -1
    return level


def _grant_from_body(grant: dict[str, Any], *, realm_slug: str) -> BuildGrant:
    """Build a :class:`BuildGrant`, failing loudly on a malformed 'build' grant."""
    level = grant.get("level")
    if isinstance(level, bool) or not isinstance(level, int):
        msg = f"realm {realm_slug} build grant is missing a numeric 'level': {grant!r}"
        raise ValueError(msg)
    limits = grant.get("limits") or {}
    if not isinstance(limits, dict):
        msg = f"realm {realm_slug} build grant has non-object 'limits': {limits!r}"
        raise ValueError(msg)
    return BuildGrant(
        level=level,
        limits=dict(limits),
        target=str(grant.get("target") or ""),
    )
