"""Infisical Agent Injector SecretInjection adapter.

Uses the Infisical Agent Injector (mutating admission webhook) to inject
secrets into session pods. The webhook adds an init container that fetches
secrets from Infisical and renders them as files in a shared emptyDir volume.

Volundr never sees secret values — the agent handles authentication and
secret retrieval at pod startup.

**Security model — ephemeral, folder-scoped Machine Identities:**

Each session gets its own temporary Machine Identity with Kubernetes Auth.
The identity has a folder-scoped privilege restricted to
``/users/{user_id}/`` so it can only read that user's credentials.

Volundr authenticates to Infisical via Universal Auth (long-lived, for
management operations) and uses that to create/delete per-session identities.
Session pods authenticate via their ServiceAccount token (Kubernetes Auth) —
no credentials are stored in the agent ConfigMap.

On cleanup the identity is deleted, immediately revoking all access.
The agent also revokes its own token on shutdown (before cleanup runs)
via the ``agent-revoke-on-shutdown`` annotation.

Credentials are stored per-field in the Infisical credential store project
at ``/users/{user_id}/{credential_name}/{field_name}``. The Go templates
reference these paths directly to render env vars and config files.

Uses the dynamic adapter pattern (plain **kwargs constructor).
"""

from __future__ import annotations

import logging

import httpx

from volundr.domain.models import CredentialMapping, PodSpecAdditions
from volundr.domain.ports import SecretInjectionPort

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 30.0

# Annotation keys for the Infisical Agent Injector webhook
_INJECT_ANNOTATION = "org.infisical.com/inject"
_INJECT_MODE_ANNOTATION = "org.infisical.com/inject-mode"
_CONFIG_MAP_ANNOTATION = "org.infisical.com/agent-config-map"
_REVOKE_ON_SHUTDOWN_ANNOTATION = "org.infisical.com/agent-revoke-on-shutdown"
_SECURITY_CONTEXT_ANNOTATION = "org.infisical.com/agent-set-security-context"

# Path where the agent renders the env file inside the pod
_ENV_FILE_PATH = "/run/secrets/env.sh"

# Default TTL for per-session identity access tokens (1 minute)
_DEFAULT_TOKEN_TTL_SECONDS = 60


class InfisicalAgentInjectionAdapter(SecretInjectionPort):
    """Infisical Agent Injector secret injection adapter.

    Uses pod annotations to trigger the Infisical mutating webhook, which
    injects an init container that renders credential fields to env var
    files and config files.

    **Identity-based isolation:** Each session gets a temporary Machine
    Identity with Kubernetes Auth, scoped via additional privilege to
    ``/users/{user_id}/``.  The agent authenticates with the pod's
    ServiceAccount token.  On cleanup, the identity is deleted —
    immediately revoking all access.

    Args:
        infisical_url: Infisical server URL.
        client_id: Universal Auth client ID for management API calls.
        client_secret: Universal Auth client secret.
        namespace: Kubernetes namespace where session pods run.
        org_id: Infisical organization ID (needed to create identities).
        credential_project_id: Infisical project ID where credentials
            are stored (same project used by the credential store).
        environment: Infisical environment slug (e.g. "dev", "prod").
        token_ttl_seconds: TTL for per-session access tokens.
        kubernetes_host: K8s API server URL for TokenReview validation.
        allowed_service_accounts: SA names allowed to authenticate.
        token_reviewer_jwt: Long-lived SA token with system:auth-delegator
            permissions, used by Infisical to call the TokenReview API.
    """

    def __init__(
        self,
        *,
        infisical_url: str = "https://infisical.example.com",
        client_id: str = "",
        client_secret: str = "",
        namespace: str = "skuld",
        org_id: str = "",
        credential_project_id: str = "",
        environment: str = "dev",
        token_ttl_seconds: int = _DEFAULT_TOKEN_TTL_SECONDS,
        kubernetes_host: str = "https://kubernetes.default.svc",
        allowed_service_accounts: str = "default",
        token_reviewer_jwt: str = "",
        **_extra: object,
    ) -> None:
        self._infisical_url = infisical_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._namespace = namespace
        self._org_id = org_id
        self._credential_project_id = credential_project_id
        self._environment = environment
        self._token_ttl_seconds = token_ttl_seconds
        self._kubernetes_host = kubernetes_host
        self._allowed_service_accounts = allowed_service_accounts
        self._token_reviewer_jwt = token_reviewer_jwt
        self._access_token: str | None = None
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # HTTP client / auth helpers (management identity — Universal Auth)
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        self._client = httpx.AsyncClient(
            base_url=self._infisical_url,
            timeout=_HTTP_TIMEOUT,
        )
        return self._client

    async def _ensure_authenticated(self) -> str:
        if self._access_token:
            return self._access_token
        client = await self._get_client()
        response = await client.post(
            "/api/v1/auth/universal-auth/login",
            json={
                "clientId": self._client_id,
                "clientSecret": self._client_secret,
            },
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Infisical auth failed ({response.status_code})")
        self._access_token = response.json()["accessToken"]
        return self._access_token

    async def _headers(self) -> dict[str, str]:
        token = await self._ensure_authenticated()
        return {"Authorization": f"Bearer {token}"}

    # ------------------------------------------------------------------
    # Per-session Machine Identity management
    # ------------------------------------------------------------------

    async def _create_session_identity(
        self,
        user_id: str,
        session_id: str,
    ) -> str:
        """Create a temporary Machine Identity with Kubernetes Auth.

        1. Creates a Machine Identity named ``session-{session_id}``.
        2. Attaches Kubernetes Auth (pod SA token validates via TokenReview).
        3. Adds the identity to the credential project.
        4. Adds a folder-scoped privilege for ``/users/{user_id}/``.

        Returns:
            identity_id
        """
        client = await self._get_client()
        headers = await self._headers()

        # 1. Create Machine Identity
        resp = await client.post(
            "/api/v1/identities",
            headers=headers,
            json={
                "name": f"session-{session_id}",
                "organizationId": self._org_id,
                "role": "no-access",
            },
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Failed to create identity for session {session_id} ({resp.status_code})"
            )
        identity_id = resp.json()["identity"]["id"]

        # 2. Attach Kubernetes Auth
        k8s_auth_payload: dict = {
            "kubernetesHost": self._kubernetes_host,
            "allowedNamespaces": self._namespace,
            "allowedNames": self._allowed_service_accounts,
            "allowedAudience": "",
            "accessTokenTTL": self._token_ttl_seconds,
            "accessTokenMaxTTL": self._token_ttl_seconds,
            "accessTokenNumUsesLimit": 0,
        }
        if self._token_reviewer_jwt:
            k8s_auth_payload["tokenReviewerJwt"] = self._token_reviewer_jwt
        resp = await client.post(
            f"/api/v1/auth/kubernetes-auth/identities/{identity_id}",
            headers=headers,
            json=k8s_auth_payload,
        )
        if resp.status_code >= 400:
            await self._delete_identity(identity_id)
            raise RuntimeError(
                f"Failed to attach Kubernetes Auth to identity {identity_id} ({resp.status_code})"
            )

        # 3. Add identity as project member (required before adding privileges)
        resp = await client.post(
            f"/api/v1/projects/{self._credential_project_id}/memberships/identities/{identity_id}",
            headers=headers,
            json={"role": "no-access"},
        )
        if resp.status_code >= 400:
            await self._delete_identity(identity_id)
            raise RuntimeError(
                f"Failed to add identity {identity_id} to project ({resp.status_code})"
            )

        # 4. Add folder-scoped read privilege
        scoped_path = f"/users/{user_id}"
        resp = await client.post(
            "/api/v2/identity-project-additional-privilege",
            headers=headers,
            json={
                "identityId": identity_id,
                "projectId": self._credential_project_id,
                "slug": f"session-{session_id}-read",
                "type": {"isTemporary": False},
                "permissions": [
                    {
                        "subject": "secrets",
                        "action": ["readValue", "describeSecret"],
                        "conditions": {
                            "environment": self._environment,
                            "secretPath": {"$glob": f"{scoped_path}/**"},
                        },
                    },
                    {
                        "subject": "secret-folders",
                        "action": ["read"],
                        "conditions": {
                            "environment": self._environment,
                            "secretPath": {"$glob": f"{scoped_path}/**"},
                        },
                    },
                ],
            },
        )
        if resp.status_code >= 400:
            await self._delete_identity(identity_id)
            raise RuntimeError(
                f"Failed to add folder privilege for identity {identity_id} ({resp.status_code})"
            )

        logger.info(
            "Created session identity %s for session %s (user %s, path %s)",
            identity_id,
            session_id,
            user_id,
            scoped_path,
        )

        return identity_id

    async def _delete_identity(self, identity_id: str) -> None:
        """Delete a Machine Identity, revoking all access immediately."""
        client = await self._get_client()
        headers = await self._headers()

        resp = await client.delete(
            f"/api/v1/identities/{identity_id}",
            headers=headers,
        )
        if resp.status_code >= 400 and resp.status_code != 404:
            logger.warning(
                "Failed to delete identity %s: %s",
                identity_id,
                resp.status_code,
            )
        else:
            logger.info("Deleted identity %s", identity_id)

    # ------------------------------------------------------------------
    # Naming conventions
    # ------------------------------------------------------------------

    def _configmap_name(self, session_id: str) -> str:
        return f"infisical-agent-{session_id}"

    def _credential_folder(self, user_id: str, credential_name: str) -> str:
        """Infisical folder path matching the credential store layout."""
        return f"/users/{user_id}/{credential_name}"

    # ------------------------------------------------------------------
    # SecretInjectionPort implementation
    # ------------------------------------------------------------------

    async def pod_spec_additions(
        self,
        user_id: str,
        session_id: str,
    ) -> PodSpecAdditions:
        """Return pod annotations that trigger the Infisical Agent Injector."""
        return PodSpecAdditions(
            annotations={
                _INJECT_ANNOTATION: "true",
                _INJECT_MODE_ANNOTATION: "init",
                _CONFIG_MAP_ANNOTATION: self._configmap_name(session_id),
                _REVOKE_ON_SHUTDOWN_ANNOTATION: "true",
                _SECURITY_CONTEXT_ANNOTATION: "true",
            },
        )

    async def ensure_secret_provider_class(
        self,
        user_id: str,
        credential_mappings: list[CredentialMapping],
        session_id: str | None = None,
        tenant_id: str | None = None,
    ) -> None:
        """Create a scoped identity and ConfigMap for this session.

        1. Creates a temporary Machine Identity with folder-scoped access.
        2. Builds Go templates from credential mappings.
        3. Creates a ConfigMap with agent config using Kubernetes Auth.
        """
        if not credential_mappings or not session_id:
            return

        identity_id = await self._create_session_identity(user_id, session_id)

        configmap_name = self._configmap_name(session_id)
        configmap_data = self._build_configmap_data(
            user_id=user_id,
            credential_mappings=credential_mappings,
            identity_id=identity_id,
        )

        await self._create_or_update_configmap(
            name=configmap_name,
            data=configmap_data,
            labels={
                "app.kubernetes.io/managed-by": "volundr",
                "volundr.niuu.io/session-id": session_id,
            },
            annotations={
                "volundr.niuu.io/identity-id": identity_id,
            },
        )

        logger.info(
            "Created agent config ConfigMap %s for user %s with %d credentials",
            configmap_name,
            user_id,
            len(credential_mappings),
        )

    async def cleanup_session(self, session_id: str) -> None:
        """Delete the session's identity and ConfigMap."""
        configmap_name = self._configmap_name(session_id)

        from kubernetes_asyncio import client, config

        try:
            config.load_incluster_config()
        except config.ConfigException:
            await config.load_kube_config()

        api_client = client.ApiClient()
        core_api = client.CoreV1Api(api_client)

        try:
            # Read the ConfigMap to get the identity ID before deleting
            cm = await core_api.read_namespaced_config_map(
                name=configmap_name,
                namespace=self._namespace,
            )
            identity_id = (cm.metadata.annotations or {}).get("volundr.niuu.io/identity-id", "")

            # Delete the identity first (revokes all access immediately)
            if identity_id:
                await self._delete_identity(identity_id)

            # Delete the ConfigMap
            await core_api.delete_namespaced_config_map(
                name=configmap_name,
                namespace=self._namespace,
            )
            logger.info("Deleted agent config ConfigMap %s", configmap_name)

        except Exception as exc:
            if "404" in str(exc) or "NotFound" in str(exc):
                logger.debug("ConfigMap %s already cleaned up", configmap_name)
            else:
                logger.warning(
                    "Failed to cleanup session %s: %s",
                    session_id,
                    exc,
                    exc_info=True,
                )
        finally:
            await api_client.close()

    # ------------------------------------------------------------------
    # Agent config builder
    # ------------------------------------------------------------------

    def _build_configmap_data(
        self,
        *,
        user_id: str,
        credential_mappings: list[CredentialMapping],
        identity_id: str,
    ) -> dict[str, str]:
        """Build the ConfigMap ``config.yaml`` for the Infisical agent.

        Uses Kubernetes Auth — the injected init container authenticates
        via the pod's ServiceAccount token. The identity can only read
        secrets under ``/users/{user_id}/`` via a folder-scoped privilege.

        Produces two types of templates:
        1. **Env template** — all env var mappings rendered to a single
           ``/run/secrets/env.sh`` that the entrypoint sources.
        2. **File templates** — each file mapping rendered directly to the
           target path (SSH keys, kubeconfig, OAuth creds, etc.).

        Unmapped credentials (no env or file mappings) are rendered as
        raw files at ``/run/secrets/user/<credential-name>``.
        """
        import yaml

        templates = []
        env_lines: list[str] = []

        for mapping in credential_mappings:
            folder = self._credential_folder(user_id, mapping.credential_name)
            # Env var mappings → accumulate into env.sh
            for env_var, field_name in mapping.env_mappings.items():
                env_lines.append(
                    f'{{{{- with getSecretByName "{self._credential_project_id}" '
                    f'"{self._environment}" "{folder}" "{field_name}" }}}}'
                    f"\nexport {env_var}='{{{{ .Value }}}}'"
                    "\n{{- end }}"
                )

            # File mappings → each gets its own template
            for target_path, field_name in mapping.file_mappings.items():
                template_content = (
                    f'{{{{- with getSecretByName "{self._credential_project_id}" '
                    f'"{self._environment}" "{folder}" "{field_name}" }}}}'
                    "{{ .Value }}"
                    "{{- end }}"
                )
                templates.append(
                    {
                        "destination-path": target_path,
                        "template-content": template_content,
                    }
                )

            # Unmapped credentials — skip template generation.
            # Without explicit env/file mappings we don't know what to render.
            # The credential is still accessible via the identity's folder
            # privilege if needed in the future.

        # Combine all env lines into a single env.sh template
        if env_lines:
            templates.append(
                {
                    "destination-path": _ENV_FILE_PATH,
                    "template-content": "\n".join(env_lines) + "\n",
                }
            )

        config_dict = {
            "infisical": {
                "address": self._infisical_url,
                "auth": {
                    "type": "kubernetes",
                    "config": {
                        "identity-id": identity_id,
                    },
                },
            },
            "templates": templates,
        }

        return {
            "config.yaml": yaml.safe_dump(config_dict, default_flow_style=False),
        }

    # ------------------------------------------------------------------
    # ConfigMap management
    # ------------------------------------------------------------------

    async def _create_or_update_configmap(
        self,
        name: str,
        data: dict[str, str],
        labels: dict[str, str],
        annotations: dict[str, str] | None = None,
    ) -> None:
        from kubernetes_asyncio import client, config

        try:
            config.load_incluster_config()
        except config.ConfigException:
            await config.load_kube_config()

        api_client = client.ApiClient()
        core_api = client.CoreV1Api(api_client)

        configmap = client.V1ConfigMap(
            metadata=client.V1ObjectMeta(
                name=name,
                namespace=self._namespace,
                labels=labels,
                annotations=annotations,
            ),
            data=data,
        )

        try:
            await core_api.create_namespaced_config_map(
                namespace=self._namespace,
                body=configmap,
            )
        except Exception as exc:
            if "409" in str(exc) or "AlreadyExists" in str(exc):
                await core_api.replace_namespaced_config_map(
                    name=name,
                    namespace=self._namespace,
                    body=configmap,
                )
            else:
                raise
        finally:
            await api_client.close()

    # ------------------------------------------------------------------
    # User provisioning (no-op — identities are per-session)
    # ------------------------------------------------------------------

    async def provision_user(self, user_id: str) -> None:
        """No-op — identities are created per-session, not per-user."""
        logger.debug("provision_user called for %s (no-op)", user_id)

    async def deprovision_user(self, user_id: str) -> None:
        """No-op — identities are created per-session, not per-user."""
        logger.debug("deprovision_user called for %s (no-op)", user_id)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
