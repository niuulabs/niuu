"""Factory for resolving per-owner TrackerPort adapter instances."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from niuu.domain.models import (
    IntegrationConnection,
    IntegrationType,
    credential_owner,
    project_owner_id,
)
from niuu.ports.credentials import CredentialStorePort
from niuu.ports.integrations import IntegrationRepository, IntegrationUsageLog
from niuu.utils import import_class
from ting.adapters.native import NativeTrackerAdapter
from ting.adapters.team_tracker import TeamTrackerRecorder
from ting.ports.tracker import (
    TrackerPort,
    TrackerResolution,
    TrackerResolutionFailure,
)

logger = logging.getLogger(__name__)


def _resolve_tracker_adapter(adapter: str) -> str | None:
    """Map shared tracker adapters onto Ting-owned implementations."""
    if adapter == "volundr.adapters.outbound.linear.LinearAdapter":
        return "ting.adapters.linear.LinearTrackerAdapter"
    if adapter == "volundr.adapters.outbound.jira.JiraAdapter":
        return "ting.adapters.jira.JiraTrackerAdapter"
    if adapter.startswith("volundr."):
        return None
    return adapter


def _normalize_tracker_credentials(adapter: str, credentials: dict[str, Any]) -> dict[str, Any]:
    """Bridge generic credential payloads to adapter-specific constructor kwargs."""
    normalized = dict(credentials)
    if (
        adapter.endswith("LinearTrackerAdapter")
        and "api_key" not in normalized
        and "access_token" not in normalized
    ):
        # A bare ``token`` is a personal API key; an ``access_token`` is an OAuth
        # token (a team's Linear app) and stays one, it is sent as Bearer.
        token = normalized.get("token")
        if token:
            normalized["api_key"] = token
    return normalized


class TrackerAdapterFactory:
    """Resolves tracker adapters for a specific owner from stored credentials.

    The pool is injected into every adapter so that adapters that need local
    postgres storage (e.g. LinearAdapter's run_progress table) receive it
    without exposing it as a user-config kwarg.
    """

    def __init__(
        self,
        integration_repo: IntegrationRepository,
        credential_store: CredentialStorePort,
        pool: Any | None = None,
        *,
        member_projects: Callable[[str], Awaitable[list[str]]] | None = None,
        usage_log: IntegrationUsageLog | None = None,
    ) -> None:
        self._integration_repo = integration_repo
        self._credential_store = credential_store
        self._pool = pool
        self._member_projects = member_projects
        self._usage_log = usage_log

    async def for_owner(self, owner_id: str) -> list[TrackerPort]:
        """Return all enabled TrackerPort adapters for the owner.

        Uses the dynamic adapter pattern: import_class(conn.adapter)(**kwargs).
        The pool is injected alongside the credential/config kwargs so adapters
        that require local postgres storage (LinearAdapter) receive it.
        """
        resolution = await self.for_owner_with_resolution(owner_id)
        return list(resolution.adapters)

    async def for_owner_with_resolution(self, owner_id: str) -> TrackerResolution:
        """Resolve adapters without hiding failures for enabled connections.

        Existing command paths consume :meth:`for_owner`.  Read surfaces that
        promise source coverage use this richer result so a broken configured
        connection cannot look like a complete, empty tracker catalog.

        The owner's own connections come first, then those of every project the
        owner is a member of (team trackers), when ``member_projects`` is set.
        """
        adapters: list[TrackerPort] = []
        failures: list[TrackerResolutionFailure] = []
        seen: set[str] = set()
        own = await self._integration_repo.list_connections(
            owner_id,
            integration_type=IntegrationType.ISSUE_TRACKER,
        )
        await self._resolve_connections(own, owner_id, seen, adapters, failures)
        for connections in await self._team_connections(owner_id, failures):
            await self._resolve_connections(connections, owner_id, seen, adapters, failures)
        if self._pool is not None:
            if not adapters:
                logger.info(
                    "No external tracker integrations configured for owner %s; "
                    "using the native tracker",
                    owner_id,
                )
            native = NativeTrackerAdapter(pool=self._pool)
            native.bind_connection(connection_id="native", provider="native", name="Niuu")
            adapters.append(native)
        return TrackerResolution(adapters=tuple(adapters), failures=tuple(failures))

    async def _team_connections(
        self, owner_id: str, failures: list[TrackerResolutionFailure]
    ) -> list[list[IntegrationConnection]]:
        """The tracker connections of each project the owner is a member of."""
        if self._member_projects is None:
            return []
        try:
            project_ids = await self._member_projects(owner_id)
        except Exception:
            failures.append(
                TrackerResolutionFailure(
                    connection_id="",
                    code="teamTrackersUnavailable",
                    message="The owner's project memberships could not be read, so the "
                    "team trackers are unavailable.",
                )
            )
            logger.error(
                "Could not list the projects of owner %s; their team trackers are unavailable",
                owner_id,
                exc_info=True,
            )
            return []
        return [
            await self._integration_repo.list_connections(
                project_owner_id(project_id),
                integration_type=IntegrationType.ISSUE_TRACKER,
            )
            for project_id in project_ids
        ]

    async def _resolve_connections(
        self,
        connections: list[IntegrationConnection],
        owner_id: str,
        seen: set[str],
        adapters: list[TrackerPort],
        failures: list[TrackerResolutionFailure],
    ) -> None:
        """Build an adapter for each enabled connection; failures are reported, not hidden."""
        for conn in connections:
            if not conn.enabled or conn.id in seen:
                continue
            seen.add(conn.id)
            if conn.project_id and self._usage_log is None:
                failures.append(
                    TrackerResolutionFailure(
                        connection_id=str(conn.id),
                        code="teamUsageUnrecorded",
                        message="A team tracker cannot be used without a usage log.",
                    )
                )
                logger.error(
                    "Team tracker connection %s of project %s was refused: no integration "
                    "usage log is wired, and an unrecorded team use is not allowed",
                    conn.id,
                    conn.project_id,
                )
                continue
            resolved_adapter = _resolve_tracker_adapter(conn.adapter)
            if resolved_adapter is None:
                failures.append(
                    TrackerResolutionFailure(
                        connection_id=str(conn.id),
                        code="unsupportedAdapter",
                        message="The configured tracker adapter is not supported by Ting.",
                    )
                )
                continue
            try:
                cred = await self._credential_store.get_value(
                    *credential_owner(conn),
                    conn.credential_name,
                )
                if cred is None:
                    failures.append(
                        TrackerResolutionFailure(
                            connection_id=str(conn.id),
                            code="credentialUnavailable",
                            message="The configured tracker credential is unavailable.",
                        )
                    )
                    continue

                cls = import_class(resolved_adapter)
                kwargs = {**_normalize_tracker_credentials(resolved_adapter, cred), **conn.config}
                if self._pool is not None:
                    kwargs["pool"] = self._pool
                tracker = cls(**kwargs)
                tracker.bind_connection(
                    connection_id=str(conn.id),
                    provider=str(conn.slug),
                    name=str(conn.config.get("name") or conn.config.get("site_url") or conn.slug),
                )
                if conn.project_id and self._usage_log is not None:
                    tracker = TeamTrackerRecorder(
                        tracker,
                        usage_log=self._usage_log,
                        connection_id=str(conn.id),
                        project_id=str(conn.project_id),
                        acting_for=owner_id,
                    )
                adapters.append(tracker)
            except (ImportError, TypeError, ValueError, AttributeError) as exc:
                failures.append(
                    TrackerResolutionFailure(
                        connection_id=str(conn.id),
                        code="adapterUnavailable",
                        message="The configured tracker adapter could not be initialized.",
                    )
                )
                logger.error(
                    "Failed to create tracker adapter for connection %s: %s",
                    conn.id,
                    exc,
                )
            except Exception:
                failures.append(
                    TrackerResolutionFailure(
                        connection_id=str(conn.id),
                        code="adapterUnavailable",
                        message="The configured tracker adapter could not be initialized.",
                    )
                )
                logger.error(
                    "Unexpected error creating tracker adapter for connection %s",
                    conn.id,
                    exc_info=True,
                )
