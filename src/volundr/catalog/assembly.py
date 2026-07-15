"""Single source of truth for wiring the Forge catalog.

The catalog (launch specs + session definitions) is built here and reused by
both the full Forge and the standalone catalog component, so the two serve
identical routes and behaviour. System-scope launch specs are config-driven and
need no database; user-scope launch specs require a repository (supplied by the
full Forge, omitted by the standalone component).
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter

from volundr.adapters.inbound.rest_launch_specs import create_launch_specs_router
from volundr.adapters.outbound.config_launch_specs import ConfigLaunchSpecProvider
from volundr.config import Settings
from volundr.domain.ports import LaunchSpecProvider, LaunchSpecRepository
from volundr.domain.services.launch_spec import LaunchSpecService

# Catalog routes live under Volundr. Forge is reserved for runtime/session APIs.
VOLUNDR_PREFIX = "/api/v1/volundr"


@dataclass(frozen=True)
class CatalogComponents:
    """Wired catalog provider, service, and router."""

    launch_spec_provider: LaunchSpecProvider
    launch_spec_service: LaunchSpecService
    router: APIRouter


def build_catalog(
    settings: Settings,
    *,
    launch_spec_repository: LaunchSpecRepository | None = None,
) -> CatalogComponents:
    """Build the catalog (launch specs + session definitions).

    Args:
        settings: Application settings (``launch_specs`` + ``session_definitions``).
        launch_spec_repository: Optional. When provided (full Forge), enables
            user-scope launch-spec CRUD. The standalone catalog passes ``None``
            and serves system-scope specs only.
    """
    provider = ConfigLaunchSpecProvider(settings.launch_specs)
    service = LaunchSpecService(provider, repository=launch_spec_repository)
    router = create_launch_specs_router(
        service,
        settings.session_definitions,
        prefix=VOLUNDR_PREFIX,
    )
    return CatalogComponents(
        launch_spec_provider=provider,
        launch_spec_service=service,
        router=router,
    )
