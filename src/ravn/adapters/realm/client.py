"""Realm governance client — resolve a Valkyrie's tool-build trust grant.

Phase 0.5 landed a realm governance API in niuu, mounted on the Volundr host
(the same ``base_url`` Ravn already uses for Forge sessions). This client reads
the realm's trust grants over the workload-authenticated HTTP boundary — Ravn
never imports niuu/volundr, it only calls them.

The "build" action-class grant with the highest ``level`` governs this
Valkyrie's tool-building: ``grant.limits`` may hold a workflow selector
(``{"workflow": "tool-builder"}``) and ``grant.level`` is its autonomy rung.

The trust-level -> autonomy-mode table is config-driven (P5):
``ResidentEvolutionConfig.trust_level_autonomy_table`` carries the thresholds
and the composition root passes them to ``autonomy_mode_for_trust_level``. The
defaults live here as documented constants so ``ravn.config`` and this mapping
stay in lockstep.
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
# Trust-level -> autonomy-mode table (defaults)
#
# The three autonomy modes mirror ``ResidentEvolutionConfig.autonomy_mode``:
#   guarded    — records proposals only
#   autonomous — applies low-risk private/Environment changes
#   yolo       — evolves within delegated boundaries
#
# Default rung boundaries (thresholds are inclusive lower bounds; override via
# ``ResidentEvolutionConfig.trust_level_autonomy_table``):
#   level <  2  -> guarded     (minimal trust: propose only)
#   level >= 2  -> autonomous  (trusted for low-risk self-evolution)
#   level >= 4  -> yolo        (fully delegated within boundaries)
# ---------------------------------------------------------------------------
_AUTONOMY_MODE_GUARDED = "guarded"
_AUTONOMY_MODE_AUTONOMOUS = "autonomous"
_AUTONOMY_MODE_YOLO = "yolo"

#: Default lowest level that maps to ``autonomous``.
DEFAULT_AUTONOMOUS_TRUST_THRESHOLD = 2
#: Default lowest level that maps to ``yolo``.
DEFAULT_YOLO_TRUST_THRESHOLD = 4

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


def autonomy_mode_for_trust_level(
    level: int,
    *,
    autonomous_threshold: int = DEFAULT_AUTONOMOUS_TRUST_THRESHOLD,
    yolo_threshold: int = DEFAULT_YOLO_TRUST_THRESHOLD,
) -> str:
    """Map a realm trust level to a resident autonomy mode.

    Thresholds are inclusive lower bounds: ``level >= yolo_threshold`` is
    ``yolo``, ``level >= autonomous_threshold`` is ``autonomous``, anything
    below is ``guarded``. The composition root passes
    ``ResidentEvolutionConfig.trust_level_autonomy_table`` here; the defaults
    reproduce that table's defaults.
    """
    if yolo_threshold < autonomous_threshold:
        msg = (
            f"yolo_threshold ({yolo_threshold}) must be >= "
            f"autonomous_threshold ({autonomous_threshold})"
        )
        raise ValueError(msg)
    if level >= yolo_threshold:
        return _AUTONOMY_MODE_YOLO
    if level >= autonomous_threshold:
        return _AUTONOMY_MODE_AUTONOMOUS
    return _AUTONOMY_MODE_GUARDED


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
