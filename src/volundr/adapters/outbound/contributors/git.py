"""Git contributor — resolves authenticated clone URL via UserIntegrationService."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

from volundr.domain.models import Session
from volundr.domain.ports import (
    SessionContext,
    SessionContribution,
    SessionContributor,
)
from volundr.domain.services.user_integration import git_token_path

if TYPE_CHECKING:
    from volundr.adapters.outbound.git_registry import GitProviderRegistry
    from volundr.domain.services.user_integration import UserIntegrationService

logger = logging.getLogger(__name__)


class GitContributor(SessionContributor):
    """Resolve the attached Settings integration for Kubernetes session Git.

    Kubernetes values carry only a clean URL and injected token file reference.
    Local processes retain provider URL authentication; OpenShell owns its auth.
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

        if context.runtime_backend == "openshell" and not context.principal:
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

        if context.runtime_backend in {"kubernetes", "openshell"} and context.principal:
            if self._user_integration is None:
                raise ValueError("Session Git requires the Settings integration service")
            resolved = await self._user_integration.find_session_git_provider(
                session.repo,
                context.principal.user_id,
                context.integration_connections,
            )
            if resolved is None:
                raise ValueError(
                    "Attach a source-control integration for this repository in Settings"
                )
            connection, provider = resolved
            clone_url = provider.get_clone_url(session.repo)
            if not clone_url:
                raise ValueError("The selected Git integration cannot clone this repository")
            parsed = urlsplit(clone_url)
            if parsed.scheme != "https" or not parsed.username or not parsed.password:
                raise ValueError("The selected Git integration requires HTTPS token authentication")
            clean_url = urlunsplit(parsed._replace(netloc=parsed.netloc.rsplit("@", 1)[-1]))
            return SessionContribution(
                values={
                    "git": {
                        "repoUrl": clean_url,
                        "cloneUrl": clean_url,
                        "branch": session.branch,
                        "baseBranch": getattr(session.source, "base_branch", ""),
                        "credentials": {
                            "secretName": "",
                            "tokenFile": (
                                git_token_path(connection.id)
                                if context.runtime_backend == "kubernetes"
                                else ""
                            ),
                            "username": parsed.username,
                        },
                        "userName": context.principal.email,
                        "userEmail": context.principal.email,
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
