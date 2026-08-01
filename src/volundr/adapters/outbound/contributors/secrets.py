"""Secret injection contributors — wraps SecretInjectionPort and SecretRepository."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from volundr.domain.models import CredentialMapping, MountType, Session, StoredCredential
from volundr.domain.ports import (
    CredentialStorePort,
    SecretInjectionPort,
    SecretRepository,
    SessionContext,
    SessionContribution,
    SessionContributor,
)

if TYPE_CHECKING:
    from volundr.domain.services.integration_registry import IntegrationRegistry
    from volundr.domain.services.mount_strategies import SecretMountStrategyRegistry

logger = logging.getLogger(__name__)

_MIMIR_SECRET_VOLUME_PATH = "/run/secrets/mimir"
_OPENSHELL_VALUES_KEY = "openshell"
_OPENSHELL_CREDENTIAL_MAPPINGS_KEY = "credentialMappings"


def _secret_file_name(value: str) -> str:
    normalized = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in value)
    normalized = normalized.strip(".-_")
    return normalized or "credential"


def _integration_auth_ref(slug: str) -> str:
    return f"integration:{slug}"


def _mapping_payload(mapping: CredentialMapping) -> dict[str, object]:
    return {
        "credentialName": mapping.credential_name,
        "envMappings": dict(mapping.env_mappings),
        "fileMappings": dict(mapping.file_mappings),
    }


def _openshell_credential_values(mappings: list[CredentialMapping]) -> dict[str, object]:
    return {
        _OPENSHELL_VALUES_KEY: {
            _OPENSHELL_CREDENTIAL_MAPPINGS_KEY: [_mapping_payload(mapping) for mapping in mappings],
        },
    }


def _codex_auth_values(
    context: SessionContext,
    registry: IntegrationRegistry | None,
) -> dict[str, object]:
    if registry is None:
        return {}
    for connection in context.integration_connections:
        definition = registry.get_definition(connection.slug)
        enrollment = definition.credential_enrollment if definition is not None else None
        if enrollment is None or enrollment.method != "codex_device":
            continue
        return {
            "broker": {
                "codexAuth": {
                    "kwargs": {
                        "credential_name": connection.credential_name,
                        "credential_field": enrollment.credential_field,
                    }
                }
            }
        }
    return {}


class SecretInjectionContributor(SessionContributor):
    """Returns PodSpecAdditions for secret injection (agent injector, hostPath, etc.).

    Orchestrates:
    1. Building credential mappings from integration definitions and mount strategies
    2. Ensuring injection config exists for the user's credentials
    3. Returning pod spec additions (annotations, volumes, mounts) from the adapter
    """

    def __init__(
        self,
        *,
        secret_injection: SecretInjectionPort | None = None,
        integration_registry: IntegrationRegistry | None = None,
        credential_store: CredentialStorePort | None = None,
        mount_strategies: SecretMountStrategyRegistry | None = None,
        **_extra: object,
    ):
        self._secret_injection = secret_injection
        self._registry = integration_registry
        self._credential_store = credential_store
        self._mount_strategies = mount_strategies

    @property
    def name(self) -> str:
        return "secret_injection"

    async def _build_mappings(
        self,
        context: SessionContext,
        owner_id: str,
    ) -> list[CredentialMapping]:
        """Build credential mappings from integration connections + direct credentials."""
        mappings: list[CredentialMapping] = []

        # Integration connections — mapping comes from IntegrationDefinition
        for conn in context.integration_connections:
            env_mappings: dict[str, str] = {}
            file_mappings: dict[str, str] = {}

            if self._registry:
                defn = self._registry.get_definition(conn.slug)
                if defn is not None:
                    env_mappings.update(defn.env_from_credentials)
                    if defn.mcp_server:
                        env_mappings.update(defn.mcp_server.env_from_credentials)
                    file_mappings.update(defn.file_mounts)
                    auth_ref = _integration_auth_ref(conn.slug)
                    if auth_ref in self._mimir_auth_refs(context):
                        file_mappings[
                            f"{_MIMIR_SECRET_VOLUME_PATH}/{_secret_file_name(auth_ref)}/token"
                        ] = "token"

            mappings.append(
                CredentialMapping(
                    credential_name=conn.credential_name,
                    env_mappings=env_mappings,
                    file_mappings=file_mappings,
                )
            )

        # Direct credential names — mapping comes from SecretMountStrategy
        for cred_name in context.credential_names:
            mapping = await self._resolve_credential_mapping(owner_id, cred_name)
            if cred_name in self._mimir_auth_refs(context):
                mapping.file_mappings[
                    f"{_MIMIR_SECRET_VOLUME_PATH}/{_secret_file_name(cred_name)}/token"
                ] = "token"
            mappings.append(mapping)

        return mappings

    def _mimir_auth_refs(self, context: SessionContext) -> set[str]:
        mimir = context.workload_config.get("mimir")
        if not isinstance(mimir, dict):
            return set()

        refs: set[str] = set()
        for raw_ref in list(mimir.get("registry_refs") or []):
            if not isinstance(raw_ref, dict):
                continue
            auth_ref = str(raw_ref.get("auth_ref") or raw_ref.get("authRef") or "").strip()
            if auth_ref:
                refs.add(auth_ref)
        return refs

    async def _resolve_credential_mapping(
        self,
        owner_id: str,
        cred_name: str,
    ) -> CredentialMapping:
        """Resolve a direct credential name into a CredentialMapping using mount strategies.

        Fetches credential metadata (type + keys) from the store, then uses the
        mount strategy for that type to determine env_mappings vs file_mappings.
        Falls back to an empty mapping if the store or strategies are unavailable.
        """
        if not self._credential_store or not self._mount_strategies:
            return CredentialMapping(credential_name=cred_name)

        stored = await self._credential_store.get("user", owner_id, cred_name)
        if stored is None:
            logger.warning(
                "Credential %r not found for user %s — skipping mapping",
                cred_name,
                owner_id,
            )
            return CredentialMapping(credential_name=cred_name)

        return self._mapping_from_stored(stored)

    def _mapping_from_stored(self, stored: StoredCredential) -> CredentialMapping:
        """Build a CredentialMapping from StoredCredential metadata + mount strategy."""
        strategy = self._mount_strategies.get(stored.secret_type)
        mount_spec = strategy.default_mount_spec(
            secret_path=f"/users/{stored.owner_id}/{stored.name}",
            secret_data={k: "" for k in stored.keys},
        )

        env_mappings: dict[str, str] = {}
        file_mappings: dict[str, str] = {}

        if mount_spec.mount_type == MountType.ENV_FILE:
            # Credential name becomes the env var; key is the field to extract.
            # Single key: FOO=<value of api_key>
            # Multiple keys: FOO_API_KEY=..., FOO_ORG_ID=...
            cred_upper = stored.name.upper().replace("-", "_")
            if len(stored.keys) == 1:
                env_mappings[cred_upper] = stored.keys[0]
            else:
                for key in stored.keys:
                    env_mappings[f"{cred_upper}_{key.upper()}"] = key
        elif mount_spec.mount_type in (MountType.FILE, MountType.TEMPLATE):
            # Each key becomes a file at the strategy's destination
            dest = mount_spec.destination.rstrip("/")
            if len(stored.keys) == 1:
                # Single key → mount directly at destination
                file_mappings[dest] = stored.keys[0]
            else:
                # Multiple keys → mount each as a file under destination dir
                for key in stored.keys:
                    file_mappings[f"{dest}/{key}"] = key

        return CredentialMapping(
            credential_name=stored.name,
            env_mappings=env_mappings,
            file_mappings=file_mappings,
        )

    async def contribute(
        self,
        session: Session,
        context: SessionContext,
    ) -> SessionContribution:
        if not session.owner_id:
            return SessionContribution()

        mappings = await self._build_mappings(context, session.owner_id)
        if not mappings:
            return SessionContribution()

        values = _openshell_credential_values(mappings)
        codex_values = _codex_auth_values(context, self._registry)
        if codex_values:
            values.update(codex_values)
        if context.runtime_backend == "openshell":
            return SessionContribution(values=values)

        if not self._secret_injection:
            return SessionContribution(values=values)

        # Ensure injection config exists (ConfigMap, SPC, etc.)
        try:
            await self._secret_injection.ensure_secret_provider_class(
                session.owner_id,
                mappings,
                session_id=str(session.id),
                tenant_id=session.tenant_id,
            )
        except Exception:
            logger.warning(
                "Failed to ensure injection config for user %s — skipping secret volume injection",
                session.owner_id,
                exc_info=True,
            )
            return SessionContribution(values=values)

        # Get pod spec additions (annotations, volumes, mounts)
        pod_spec = await self._secret_injection.pod_spec_additions(
            session.owner_id,
            str(session.id),
        )

        return SessionContribution(values=values, pod_spec=pod_spec)

    async def cleanup(
        self,
        session: Session,
        context: SessionContext,
    ) -> None:
        if self._secret_injection is None:
            return
        await self._secret_injection.cleanup_session(str(session.id))


class SecretsContributor(SessionContributor):
    """Creates ephemeral session secrets via SecretRepository."""

    def __init__(
        self,
        *,
        secret_repo: SecretRepository | None = None,
        **_extra: object,
    ):
        self._secret_repo = secret_repo

    @property
    def name(self) -> str:
        return "secrets"

    async def contribute(
        self,
        session: Session,
        context: SessionContext,
    ) -> SessionContribution:
        return SessionContribution()

    async def cleanup(
        self,
        session: Session,
        context: SessionContext,
    ) -> None:
        if self._secret_repo is None:
            return
        await self._secret_repo.delete_session_secrets(str(session.id))
