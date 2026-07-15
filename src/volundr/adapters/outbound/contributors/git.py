"""Git contributor — resolves authenticated clone URL via UserIntegrationService."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from volundr.domain.models import Session
from volundr.domain.ports import (
    SessionContext,
    SessionContribution,
    SessionContributor,
)

if TYPE_CHECKING:
    from volundr.adapters.outbound.git_registry import GitProviderRegistry
    from volundr.domain.services.user_integration import UserIntegrationService

logger = logging.getLogger(__name__)


class GitContributor(SessionContributor):
    """Provides authenticated git clone URL for session pods.

    Delegates to ``UserIntegrationService`` when available, which
    combines shared/org-level providers with the user's own integration
    credentials (resolved on-the-fly, never cached).  Falls back to
    shared ``GitProviderRegistry`` when no user integration service
    is configured.
    """

    def __init__(
        self,
        *,
        git_registry: GitProviderRegistry | None = None,
        user_integration: UserIntegrationService | None = None,
        **_extra: object,
    ):
        self._git_registry = git_registry
        self._user_integration = user_integration

    @property
    def name(self) -> str:
        return "git"

    async def contribute(
        self,
        session: Session,
        context: SessionContext,
    ) -> SessionContribution:
        if not session.repo:
            return SessionContribution()

        if context.runtime_backend == "openshell":
            base_branch = getattr(session.source, "base_branch", "") if session.source else ""
            return SessionContribution(
                values={
                    "git": {
                        "repoUrl": session.repo,
                        "cloneUrl": session.repo,
                        "branch": session.branch,
                        "baseBranch": base_branch,
                    },
                }
            )

        clone_url = None

        # Prefer user-scoped resolution (shared + per-user credentials)
        if context.principal and self._user_integration:
            provider = await self._user_integration.find_git_provider_for(
                session.repo,
                context.principal.user_id,
            )
            if provider:
                clone_url = provider.get_clone_url(session.repo)

        # Fall back to shared registry
        if not clone_url and self._git_registry:
            clone_url = self._git_registry.get_clone_url(session.repo)

        if not clone_url:
            return SessionContribution()

        base_branch = getattr(session.source, "base_branch", "") if session.source else ""
        values: dict[str, Any] = {
            "git": {
                "repoUrl": session.repo,
                "cloneUrl": clone_url,
                "branch": session.branch,
                "baseBranch": base_branch,
            },
        }
        return SessionContribution(values=values)
