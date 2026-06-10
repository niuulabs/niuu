"""Dynamic OpenBao secret injection via the official OpenBao agent injector.

This adapter creates session-scoped Kubernetes service accounts plus
session-scoped JWT roles in OpenBao on demand. The pod itself is annotated
for the OpenBao injector, and a per-session ConfigMap provides the agent HCL
used to render credentials into the pod at startup.

The resulting flow is:
1. Volundr stores credentials in OpenBao KV v2 under
   ``<mount>/data/users/{user_id}/{credential_name}``.
2. When a session starts, Volundr creates a dedicated ServiceAccount and
   session-specific JWT role bound to that account.
3. Volundr creates a ConfigMap with OpenBao Agent config that renders the
   requested credential fields to env/file destinations.
4. The OpenBao injector mutates the session pod and runs the init agent.
5. On cleanup, the session role, ConfigMap, and ServiceAccount are deleted.
"""

from __future__ import annotations

import logging
import re
import textwrap

from volundr.adapters.outbound.openbao import OpenBaoAdminClient, OpenBaoAdminConfig
from volundr.domain.models import CredentialMapping, PodSpecAdditions
from volundr.domain.ports import SecretInjectionPort

logger = logging.getLogger(__name__)

_ENV_FILE_PATH = "/run/secrets/env.sh"

_ANNOTATION_PREFIX = "vault.hashicorp.com"
_INJECT_ANNOTATION = f"{_ANNOTATION_PREFIX}/agent-inject"
_INIT_FIRST_ANNOTATION = f"{_ANNOTATION_PREFIX}/agent-init-first"
_PRE_POPULATE_ANNOTATION = f"{_ANNOTATION_PREFIX}/agent-pre-populate"
_PRE_POPULATE_ONLY_ANNOTATION = f"{_ANNOTATION_PREFIX}/agent-pre-populate-only"
_CONFIG_MAP_ANNOTATION = f"{_ANNOTATION_PREFIX}/agent-configmap"
_SECRET_VOLUME_PATH_ANNOTATION = f"{_ANNOTATION_PREFIX}/secret-volume-path"
_AUTH_TYPE_ANNOTATION = f"{_ANNOTATION_PREFIX}/auth-type"
_AUTH_PATH_ANNOTATION = f"{_ANNOTATION_PREFIX}/auth-path"
_ROLE_ANNOTATION = f"{_ANNOTATION_PREFIX}/role"
_COPY_VOLUME_MOUNTS_ANNOTATION = f"{_ANNOTATION_PREFIX}/agent-copy-volume-mounts"
_INJECT_CONTAINERS_ANNOTATION = f"{_ANNOTATION_PREFIX}/agent-inject-containers"
_OPENBAO_NAMESPACE_ANNOTATION = f"{_ANNOTATION_PREFIX}/namespace"
_AGENT_IMAGE_ANNOTATION = f"{_ANNOTATION_PREFIX}/agent-image"


class OpenBaoAgentInjectionAdapter(SecretInjectionPort):
    """Session-scoped OpenBao injection adapter using the upstream injector."""

    def __init__(
        self,
        *,
        openbao_url: str = "https://openbao.example.com",
        namespace: str = "skuld",
        openbao_namespace: str = "",
        mount_path: str = "volundr",
        auth_path: str = "jwt",
        audience: str = "https://kubernetes.default.svc.cluster.local",
        auth_method: str = "token",
        token: str = "",
        approle_mount_path: str = "auth/approle",
        role_id: str = "",
        secret_id: str = "",
        agent_image: str = "",
        service_account_prefix: str = "openbao-session",
        configmap_prefix: str = "openbao-agent",
        copy_volume_mounts_from: str = "skuld",
        inject_containers: str = "skuld,devrunner",
        role_ttl: str = "1h",
        **_extra: object,
    ) -> None:
        self._openbao_url = openbao_url.rstrip("/")
        self._namespace = namespace
        self._openbao_namespace = openbao_namespace.strip("/")
        self._mount_path = mount_path.strip("/")
        self._auth_path = auth_path.strip("/")
        self._audience = audience
        self._agent_image = agent_image
        self._service_account_prefix = service_account_prefix
        self._configmap_prefix = configmap_prefix
        self._copy_volume_mounts_from = copy_volume_mounts_from
        self._inject_containers = inject_containers
        self._role_ttl = role_ttl
        self._admin = OpenBaoAdminClient(
            OpenBaoAdminConfig(
                url=self._openbao_url,
                token=token,
                namespace=self._openbao_namespace,
                auth_method=auth_method,
                approle_mount_path=approle_mount_path,
                role_id=role_id,
                secret_id=secret_id,
            )
        )

    async def pod_spec_additions(
        self,
        user_id: str,
        session_id: str,
    ) -> PodSpecAdditions:
        role_name = self._role_name(session_id)
        annotations = {
            _INJECT_ANNOTATION: "true",
            _INIT_FIRST_ANNOTATION: "true",
            _PRE_POPULATE_ANNOTATION: "true",
            _PRE_POPULATE_ONLY_ANNOTATION: "true",
            _CONFIG_MAP_ANNOTATION: self._configmap_name(session_id),
            _SECRET_VOLUME_PATH_ANNOTATION: "/run/secrets",
            _AUTH_TYPE_ANNOTATION: "jwt",
            _AUTH_PATH_ANNOTATION: self._auth_mount_path(),
            _ROLE_ANNOTATION: role_name,
        }
        if self._copy_volume_mounts_from:
            annotations[_COPY_VOLUME_MOUNTS_ANNOTATION] = self._copy_volume_mounts_from
        if self._inject_containers:
            annotations[_INJECT_CONTAINERS_ANNOTATION] = self._inject_containers
        if self._openbao_namespace:
            annotations[_OPENBAO_NAMESPACE_ANNOTATION] = self._openbao_namespace
        if self._agent_image:
            annotations[_AGENT_IMAGE_ANNOTATION] = self._agent_image

        return PodSpecAdditions(
            annotations=annotations,
            service_account=self._service_account_name(session_id),
        )

    async def ensure_secret_provider_class(
        self,
        user_id: str,
        credential_mappings: list[CredentialMapping],
        session_id: str | None = None,
        tenant_id: str | None = None,
    ) -> None:
        if not credential_mappings or not session_id:
            return

        service_account_name = self._service_account_name(session_id)
        role_name = self._role_name(session_id)
        policy_name = self._admin.user_policy_name(self._mount_path, user_id)

        await self._ensure_service_account(service_account_name, session_id, user_id)
        await self._admin.ensure_service_account_access(
            mount_path=self._mount_path,
            user_id=user_id,
            tenant_id=tenant_id or "",
            auth_path=self._auth_path,
            audience=self._audience,
            service_account_namespace=self._namespace,
            service_account_name=service_account_name,
            policy_name=policy_name,
            role_name=role_name,
            ttl=self._role_ttl,
        )
        await self._create_or_update_configmap(
            name=self._configmap_name(session_id),
            data=self._build_configmap_data(
                user_id=user_id,
                credential_mappings=credential_mappings,
                role_name=role_name,
            ),
            labels={
                "app.kubernetes.io/managed-by": "volundr",
                "volundr.niuu.io/session-id": session_id,
            },
            annotations={
                "volundr.niuu.io/openbao-role": role_name,
                "volundr.niuu.io/service-account": service_account_name,
                "volundr.niuu.io/user-id": user_id,
            },
        )

    async def provision_user(self, user_id: str) -> None:
        logger.debug("provision_user called for %s (no-op)", user_id)

    async def deprovision_user(self, user_id: str) -> None:
        logger.debug("deprovision_user called for %s (no-op)", user_id)

    async def cleanup_session(self, session_id: str) -> None:
        role_name = self._role_name(session_id)
        service_account_name = self._service_account_name(session_id)
        configmap_name = self._configmap_name(session_id)

        try:
            await self._admin.delete_jwt_role(role_name, auth_path=self._auth_path)
        except Exception:
            logger.warning("Failed to delete OpenBao role %s", role_name, exc_info=True)

        await self._delete_configmap(configmap_name)
        await self._delete_service_account(service_account_name)

    def _configmap_name(self, session_id: str) -> str:
        return self._k8s_name(self._configmap_prefix, session_id)

    def _service_account_name(self, session_id: str) -> str:
        return self._k8s_name(self._service_account_prefix, session_id)

    def _role_name(self, session_id: str) -> str:
        suffix = self._sanitize_name(session_id)[:32]
        return f"{self._mount_path}-session-{suffix}"

    def _auth_mount_path(self) -> str:
        if self._auth_path.startswith("auth/"):
            return self._auth_path
        return f"auth/{self._auth_path}"

    def _credential_path(self, user_id: str, credential_name: str) -> str:
        return f"{self._mount_path}/data/users/{user_id}/{credential_name}"

    def _build_configmap_data(
        self,
        *,
        user_id: str,
        credential_mappings: list[CredentialMapping],
        role_name: str,
    ) -> dict[str, str]:
        config_hcl = self._build_agent_config_hcl(
            user_id=user_id,
            credential_mappings=credential_mappings,
            role_name=role_name,
        )
        return {
            "config.hcl": config_hcl,
            "config-init.hcl": config_hcl,
        }

    def _build_agent_config_hcl(
        self,
        *,
        user_id: str,
        credential_mappings: list[CredentialMapping],
        role_name: str,
    ) -> str:
        lines = [
            "exit_after_auth = true",
            "",
            "vault {",
            f'  address = "{self._openbao_url}"',
            "}",
            "",
            "auto_auth {",
            '  method "jwt" {',
            f'    mount_path = "{self._auth_mount_path()}"',
            "    config = {",
            '      path = "/var/run/secrets/kubernetes.io/serviceaccount/token"',
            f'      role = "{role_name}"',
            "      remove_jwt_after_reading = false",
            "    }",
            "  }",
            "",
            '  sink "file" {',
            "    config = {",
            '      path = "/home/vault/.vault-token"',
            "    }",
            "  }",
            "}",
        ]
        if self._openbao_namespace:
            lines[4:4] = [
                f'  namespace = "{self._openbao_namespace}"',
            ]

        templates = self._build_template_blocks(user_id, credential_mappings)
        if templates:
            lines.append("")
            lines.extend(templates)

        return "\n".join(lines) + "\n"

    def _build_template_blocks(
        self,
        user_id: str,
        credential_mappings: list[CredentialMapping],
    ) -> list[str]:
        blocks: list[str] = []
        env_lines: list[str] = []

        for mapping in credential_mappings:
            secret_path = self._credential_path(user_id, mapping.credential_name)

            for env_var, field_name in mapping.env_mappings.items():
                env_lines.extend(
                    [
                        f'{{{{ with secret "{secret_path}" }}}}',
                        f"export {env_var}='{{{{ index .Data.data \"{field_name}\" }}}}'",
                        "{{ end }}",
                    ]
                )

            for target_path, field_name in mapping.file_mappings.items():
                blocks.append(
                    self._template_block(
                        destination=target_path,
                        content="\n".join(
                            [
                                f'{{{{- with secret "{secret_path}" -}}}}',
                                f'{{{{ index .Data.data "{field_name}" }}}}',
                                "{{- end -}}",
                            ]
                        ),
                    )
                )

        if env_lines:
            blocks.insert(
                0,
                self._template_block(
                    destination=_ENV_FILE_PATH,
                    content="\n".join(env_lines) + "\n",
                ),
            )

        return blocks

    @staticmethod
    def _template_block(destination: str, content: str) -> str:
        return textwrap.dedent(
            f"""\
            template {{
              destination = "{destination}"
              perms = "0640"
              contents = <<EOT
            {content}
            EOT
            }}
            """
        ).rstrip()

    async def _ensure_service_account(
        self,
        name: str,
        session_id: str,
        user_id: str,
    ) -> None:
        from kubernetes_asyncio import client

        api_client, core_api = await self._core_api()
        body = client.V1ServiceAccount(
            metadata=client.V1ObjectMeta(
                name=name,
                namespace=self._namespace,
                labels={
                    "app.kubernetes.io/managed-by": "volundr",
                    "volundr.niuu.io/session-id": session_id,
                },
                annotations={
                    "volundr.niuu.io/user-id": user_id,
                },
            )
        )
        try:
            await core_api.create_namespaced_service_account(self._namespace, body)
        except Exception as exc:
            if "409" in str(exc) or "AlreadyExists" in str(exc):
                await core_api.replace_namespaced_service_account(name, self._namespace, body)
            else:
                raise
        finally:
            await api_client.close()

    async def _create_or_update_configmap(
        self,
        *,
        name: str,
        data: dict[str, str],
        labels: dict[str, str],
        annotations: dict[str, str] | None = None,
    ) -> None:
        from kubernetes_asyncio import client

        api_client, core_api = await self._core_api()
        body = client.V1ConfigMap(
            metadata=client.V1ObjectMeta(
                name=name,
                namespace=self._namespace,
                labels=labels,
                annotations=annotations,
            ),
            data=data,
        )
        try:
            await core_api.create_namespaced_config_map(self._namespace, body)
        except Exception as exc:
            if "409" in str(exc) or "AlreadyExists" in str(exc):
                await core_api.replace_namespaced_config_map(name, self._namespace, body)
            else:
                raise
        finally:
            await api_client.close()

    async def _delete_configmap(self, name: str) -> None:
        api_client, core_api = await self._core_api()
        try:
            await core_api.delete_namespaced_config_map(name=name, namespace=self._namespace)
        except Exception as exc:
            if "404" not in str(exc) and "NotFound" not in str(exc):
                logger.warning("Failed to delete ConfigMap %s", name, exc_info=True)
        finally:
            await api_client.close()

    async def _delete_service_account(self, name: str) -> None:
        api_client, core_api = await self._core_api()
        try:
            await core_api.delete_namespaced_service_account(name=name, namespace=self._namespace)
        except Exception as exc:
            if "404" not in str(exc) and "NotFound" not in str(exc):
                logger.warning("Failed to delete ServiceAccount %s", name, exc_info=True)
        finally:
            await api_client.close()

    async def _core_api(self):
        from kubernetes_asyncio import client, config

        try:
            config.load_incluster_config()
        except config.ConfigException:
            await config.load_kube_config()

        api_client = client.ApiClient()
        return api_client, client.CoreV1Api(api_client)

    @staticmethod
    def _sanitize_name(value: str) -> str:
        value = value.lower()
        value = re.sub(r"[^a-z0-9-]+", "-", value)
        value = re.sub(r"-{2,}", "-", value)
        return value.strip("-") or "session"

    def _k8s_name(self, prefix: str, raw_suffix: str) -> str:
        prefix_part = self._sanitize_name(prefix)[:20].rstrip("-")
        suffix_part = self._sanitize_name(raw_suffix)
        max_suffix = max(1, 63 - len(prefix_part) - 1)
        return f"{prefix_part}-{suffix_part[:max_suffix]}".strip("-")
