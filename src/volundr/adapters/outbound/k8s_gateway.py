"""Gateway adapters for session routing configuration.

Contains:
- InMemoryGatewayAdapter: dev/test adapter (returns config dict)
- K8sGatewayAdapter: production adapter (returns config dict)

Both adapters are config providers only. They supply the gateway
name, namespace, domain, and JWT settings that PodManager adapters
pass through to Skuld at session creation time.

Resource lifecycle (HTTPRoute, SecurityPolicy) is managed entirely
by the Skuld Helm chart — created on install, deleted on uninstall.
"""

from __future__ import annotations

from volundr.domain.ports import GatewayPort


class InMemoryGatewayAdapter(GatewayPort):
    """In-memory gateway adapter for development.

    Returns static gateway config without touching Kubernetes.
    """

    def __init__(
        self,
        *,
        gateway_name: str = "volundr-gateway",
        gateway_namespace: str = "volundr-system",
        **_extra: object,
    ) -> None:
        self._gateway_name = gateway_name
        self._gateway_namespace = gateway_namespace

    def get_gateway_config(self) -> dict[str, str]:
        """Return gateway configuration."""
        return {
            "gateway_name": self._gateway_name,
            "gateway_namespace": self._gateway_namespace,
        }


class K8sGatewayAdapter(GatewayPort):
    """Kubernetes Gateway API configuration provider.

    Provides gateway configuration for Skuld Helm chart HTTPRoute
    templates. The Gateway resource itself lives in the Volundr
    Helm chart. Per-session HTTPRoute and SecurityPolicy resources
    are managed by the Skuld Helm chart (created/deleted with the
    Helm release).

    Constructor accepts plain kwargs (dynamic adapter pattern).
    """

    def __init__(
        self,
        *,
        namespace: str = "volundr-sessions",
        gateway_name: str = "volundr-gateway",
        gateway_namespace: str = "volundr-system",
        gateway_domain: str = "",
        issuer_url: str = "",
        audience: str = "volundr",
        jwks_uri: str = "",
        workload_issuer_url: str = "",
        workload_audience: str = "volundr-api",
        workload_jwks_uri: str = "",
        cors_origins: list[str] | None = None,
        **_extra: object,
    ):
        self._namespace = namespace
        self._gateway_name = gateway_name
        self._gateway_namespace = gateway_namespace
        self._gateway_domain = gateway_domain
        self._issuer_url = issuer_url
        self._audience = audience
        self._jwks_uri = jwks_uri
        self._workload_issuer_url = workload_issuer_url
        self._workload_audience = workload_audience
        self._workload_jwks_uri = workload_jwks_uri
        self._cors_origins = cors_origins or ["*"]

    def get_gateway_config(self) -> dict[str, str]:
        """Return gateway configuration for Skuld Helm chart.

        The returned dict is merged into the Skuld Helm values
        under the ``gateway`` key by the PodManager adapter.
        """
        config: dict[str, str] = {
            "gateway_name": self._gateway_name,
            "gateway_namespace": self._gateway_namespace,
        }
        if self._gateway_domain:
            config["gateway_domain"] = self._gateway_domain
        if self._issuer_url:
            config["issuer_url"] = self._issuer_url
        if self._audience:
            config["audience"] = self._audience
        if self._jwks_uri:
            config["jwks_uri"] = self._jwks_uri
        if self._workload_issuer_url:
            config["workload_issuer_url"] = self._workload_issuer_url
        if self._workload_audience:
            config["workload_audience"] = self._workload_audience
        if self._workload_jwks_uri:
            config["workload_jwks_uri"] = self._workload_jwks_uri
        config["cors_origins"] = ",".join(self._cors_origins)
        return config
