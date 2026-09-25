"""Model gateway contributor — routes self-hosted models through Bifrost.

A session whose model the Bifrost catalog marks ``provider: local`` gets
``SKULD__MODEL_GATEWAY__URL``, which points both Claude Code and Codex at the
gateway. Cloud models are left untouched. The catalog volundr already loads is
the only source of truth — nothing is registered per model.
"""

from __future__ import annotations

from typing import Any

from volundr.domain.model_gateway import (  # noqa: F401 - re-exported
    MODEL_GATEWAY_TOKEN_ENV,
    MODEL_GATEWAY_URL_ENV,
    OPEN_GATEWAY_TOKEN,
)
from volundr.domain.models import ModelProvider, Session
from volundr.domain.ports import (
    PricingProvider,
    SessionContext,
    SessionContribution,
    SessionContributor,
)


class ModelGatewayContributor(SessionContributor):
    """Emit the gateway env for sessions on a catalog-local model."""

    def __init__(
        self,
        *,
        gateway_url: str = "",
        pricing_provider: PricingProvider | None = None,
        auth_mode: str = "envoy",
        **_extra: object,
    ):
        self._gateway_url = gateway_url.strip()
        self._catalog = pricing_provider
        self._auth_mode = auth_mode

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
        env_vars = [{"name": MODEL_GATEWAY_URL_ENV, "value": self._gateway_url}]
        if self._auth_mode != "oidc":
            # No real per-session credential exists yet (see OPEN_GATEWAY_TOKEN's
            # docstring) — 'oidc' hosts cannot reach this at all today since the
            # CLI refuses to start with the bifrost plugin enabled there. For
            # every other mode, send the named open-gateway sentinel rather than
            # leaving the token unset: the Claude/Codex transports (claude_env.py,
            # codex_ws.py) raise when the gateway URL is set and the token is
            # blank, so an unset token here would break every local-model session
            # under 'none'/'envoy' instead of silently degrading.
            env_vars.append({"name": MODEL_GATEWAY_TOKEN_ENV, "value": OPEN_GATEWAY_TOKEN})
        values: dict[str, Any] = {"envVars": env_vars}
        return SessionContribution(values=values)
