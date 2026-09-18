"""Model gateway contributor — routes self-hosted models through Bifrost.

A session whose model the Bifrost catalog marks ``provider: local`` gets
``SKULD__MODEL_GATEWAY__URL``, which points both Claude Code and Codex at the
gateway. Cloud models are left untouched. The catalog volundr already loads is
the only source of truth — nothing is registered per model.
"""

from __future__ import annotations

from typing import Any

from volundr.domain.models import ModelProvider, Session
from volundr.domain.ports import (
    PricingProvider,
    SessionContext,
    SessionContribution,
    SessionContributor,
)

MODEL_GATEWAY_URL_ENV = "SKULD__MODEL_GATEWAY__URL"


class ModelGatewayContributor(SessionContributor):
    """Emit the gateway env for sessions on a catalog-local model."""

    def __init__(
        self,
        *,
        gateway_url: str = "",
        pricing_provider: PricingProvider | None = None,
        **_extra: object,
    ):
        self._gateway_url = gateway_url.strip()
        self._catalog = pricing_provider

    @property
    def name(self) -> str:
        return "model_gateway"

    async def contribute(
        self,
        session: Session,
        context: SessionContext,
    ) -> SessionContribution:
        if not self._gateway_url or self._catalog is None or not session.model:
            return SessionContribution()
        local = any(
            model.id == session.model and model.provider == ModelProvider.LOCAL
            for model in self._catalog.list_models()
        )
        if not local:
            return SessionContribution()
        values: dict[str, Any] = {
            "envVars": [{"name": MODEL_GATEWAY_URL_ENV, "value": self._gateway_url}]
        }
        return SessionContribution(values=values)
