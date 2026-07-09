"""Workload identity contributor for session pods."""

from __future__ import annotations

from volundr.domain.models import PodSpecAdditions, Session
from volundr.domain.ports import SessionContext, SessionContribution, SessionContributor


class WorkloadIdentityContributor(SessionContributor):
    """Mount an audience-scoped service-account token for workload JWT exchange."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        audience: str = "volundr-api",
        expiration_seconds: int = 1200,
        volume_name: str = "niuu-workload-identity",
        mount_path: str = "/var/run/secrets/niuu-workload",
        token_file_env: str = "NIUU_WORKLOAD_IDENTITY_TOKEN_FILE",
        exchange_url_env: str = "NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL",
        exchange_url: str = "",
        **_extra: object,
    ) -> None:
        self._enabled = enabled
        self._audience = audience
        self._expiration_seconds = expiration_seconds
        self._volume_name = volume_name
        self._mount_path = mount_path.rstrip("/")
        self._token_file_env = token_file_env
        self._exchange_url_env = exchange_url_env
        self._exchange_url = exchange_url

    @property
    def name(self) -> str:
        return "workload_identity"

    async def contribute(
        self,
        session: Session,
        context: SessionContext,
    ) -> SessionContribution:
        if context.runtime_backend == "openshell":
            return SessionContribution()

        if not self._enabled:
            return SessionContribution()

        token_file = f"{self._mount_path}/token"
        env = [{"name": self._token_file_env, "value": token_file}]
        if self._exchange_url:
            env.append({"name": self._exchange_url_env, "value": self._exchange_url})

        return SessionContribution(
            pod_spec=PodSpecAdditions(
                volumes=(
                    {
                        "name": self._volume_name,
                        "projected": {
                            "sources": [
                                {
                                    "serviceAccountToken": {
                                        "path": "token",
                                        "audience": self._audience,
                                        "expirationSeconds": self._expiration_seconds,
                                    }
                                }
                            ]
                        },
                    },
                ),
                volume_mounts=(
                    {
                        "name": self._volume_name,
                        "mountPath": self._mount_path,
                        "readOnly": True,
                    },
                ),
                env=tuple(env),
            )
        )
