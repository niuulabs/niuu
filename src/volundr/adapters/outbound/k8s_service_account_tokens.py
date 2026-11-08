"""Issue session ServiceAccount tokens through the Kubernetes TokenRequest API.

This is the call a kubelet makes for a projected ``serviceAccountToken`` volume.
The controller makes it for VM sessions, which run outside the cluster, so the
tokens OpenBao and workload-identity exchange accept are the same ones a session
pod would carry. Forge needs ``create`` on ``serviceaccounts/token`` in the
configured namespace.
"""

from __future__ import annotations

from volundr.domain.service_account_tokens import (
    ServiceAccountToken,
    ServiceAccountTokenError,
    ServiceAccountTokenPort,
)


class KubernetesServiceAccountTokenIssuer(ServiceAccountTokenPort):
    """Namespace-scoped TokenRequest client, selected through dynamic adapter config."""

    def __init__(self, *, namespace: str, **_extra: object) -> None:
        if not namespace:
            raise ValueError(
                "KubernetesServiceAccountTokenIssuer requires the namespace that holds "
                "session ServiceAccounts"
            )
        self._namespace = namespace

    @property
    def namespace(self) -> str:
        return self._namespace

    async def issue(
        self,
        service_account: str,
        *,
        audience: str,
        expiration_seconds: int,
    ) -> ServiceAccountToken:
        from kubernetes_asyncio import client, config
        from kubernetes_asyncio.client.exceptions import ApiException

        if not service_account or not audience or expiration_seconds <= 0:
            raise ValueError("A token request needs a ServiceAccount, audience and lifetime")
        try:
            config.load_incluster_config()
        except config.ConfigException:
            await config.load_kube_config()
        api_client = client.ApiClient()
        try:
            response = await client.CoreV1Api(api_client).create_namespaced_service_account_token(
                service_account,
                self._namespace,
                client.AuthenticationV1TokenRequest(
                    spec=client.V1TokenRequestSpec(
                        audiences=[audience],
                        expiration_seconds=expiration_seconds,
                    )
                ),
            )
        except ApiException as exc:
            raise ServiceAccountTokenError(
                f"Kubernetes refused a token for ServiceAccount {self._namespace}/"
                f"{service_account} (HTTP {exc.status}); grant Forge create on "
                "serviceaccounts/token in that namespace"
            ) from None
        finally:
            await api_client.close()
        status = response.status
        if status is None or not status.token or status.expiration_timestamp is None:
            raise ServiceAccountTokenError(
                f"Kubernetes returned no token for ServiceAccount {self._namespace}/"
                f"{service_account}"
            )
        return ServiceAccountToken(token=status.token, expires_at=status.expiration_timestamp)
