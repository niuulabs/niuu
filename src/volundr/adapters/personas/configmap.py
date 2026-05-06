"""KubernetesConfigMapPersonaRegistry — persona registry backed by a k8s ConfigMap.

Reads and writes persona YAML into a Kubernetes ConfigMap's ``data`` map,
keyed as ``<name>.yaml``.  The pod's ServiceAccount is used for k8s auth,
so no credentials need to be managed outside of RBAC.

On first save, if the ConfigMap does not exist, it is auto-created.  All
other k8s API errors propagate as ``RuntimeError`` — the REST layer surfaces
these as HTTP 500 with the message embedded.
"""

from __future__ import annotations

import logging
from typing import Any

from ravn.adapters.personas.loader import FilesystemPersonaAdapter, PersonaConfig
from ravn.ports.persona import PersonaRegistryPort

logger = logging.getLogger(__name__)

# Built-in persona names shipped with the ravn image (src/ravn/personas/*.yaml).
# Stored as a frozen constant so is_builtin() works without filesystem access.
_BUILTIN_NAMES: frozenset[str] = frozenset(
    {
        "architect",
        "autonomous-agent",
        "coder",
        "coding-agent",
        "coordinator",
        "decomposer",
        "draft-a-note",
        "health-auditor",
        "investigator",
        "mimir-curator",
        "office-hours",
        "planning-agent",
        "produce-recap",
        "qa-agent",
        "raid-executor",
        "reporter",
        "research-agent",
        "research-and-distill",
        "retro-analyst",
        "review-arbiter",
        "reviewer",
        "security",
        "security-auditor",
        "ship-agent",
        "verifier",
    }
)


class KubernetesConfigMapPersonaRegistry(PersonaRegistryPort):
    """Persona registry backed by a Kubernetes ConfigMap.

    Implements :class:`~ravn.ports.persona.PersonaRegistryPort` by reading and
    writing the ``ravn-personas`` ConfigMap via the k8s API.

    Designed to run inside a pod — loads in-cluster credentials automatically
    via ``kubernetes.config.load_incluster_config()``.  Falls back to
    ``load_kube_config()`` for local development.

    Args:
        namespace: Kubernetes namespace where the ConfigMap lives.
        configmap_name: Name of the ConfigMap to use.
        _api: Optional ``CoreV1Api`` instance injected in tests to avoid
            requiring a live cluster.  When ``None``, the API is lazily
            created on first use.
    """

    def __init__(
        self,
        namespace: str = "default",
        configmap_name: str = "ravn-personas",
        _api: Any = None,
    ) -> None:
        self._namespace = namespace
        self._configmap_name = configmap_name
        self._api = _api

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_api(self) -> Any:
        """Return the CoreV1Api, loading cluster config on first call."""
        if self._api is not None:
            return self._api

        try:
            import kubernetes.client
            import kubernetes.config
        except ImportError as exc:
            raise RuntimeError(
                "The 'kubernetes' package is required for KubernetesConfigMapPersonaRegistry. "
                "Install it with: pip install kubernetes"
            ) from exc

        try:
            kubernetes.config.load_incluster_config()
        except kubernetes.config.ConfigException:
            kubernetes.config.load_kube_config()

        self._api = kubernetes.client.CoreV1Api()
        return self._api

    def _read_data(self) -> dict[str, str]:
        """Return the ConfigMap's ``data`` dict, or ``{}`` when it doesn't exist."""
        api = self._get_api()
        try:
            cm = api.read_namespaced_config_map(
                name=self._configmap_name,
                namespace=self._namespace,
            )
            return cm.data or {}
        except Exception as exc:
            if getattr(exc, "status", None) == 404:
                return {}
            raise RuntimeError(
                f"Failed to read ConfigMap {self._configmap_name!r} "
                f"in namespace {self._namespace!r}: {exc}"
            ) from exc

    def _patch_data(self, data: dict[str, str]) -> None:
        """Patch the ConfigMap's data map in-place."""
        api = self._get_api()
        try:
            api.patch_namespaced_config_map(
                name=self._configmap_name,
                namespace=self._namespace,
                body={"data": data},
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to patch ConfigMap {self._configmap_name!r}: {exc}"
            ) from exc

    def _create_configmap(self, data: dict[str, str]) -> None:
        """Create the ConfigMap from scratch with the given data."""
        api = self._get_api()
        try:
            import kubernetes.client as _k8s

            body = _k8s.V1ConfigMap(
                metadata=_k8s.V1ObjectMeta(
                    name=self._configmap_name,
                    namespace=self._namespace,
                ),
                data=data,
            )
        except ImportError:
            body = {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {
                    "name": self._configmap_name,
                    "namespace": self._namespace,
                },
                "data": data,
            }

        try:
            api.create_namespaced_config_map(
                namespace=self._namespace,
                body=body,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to create ConfigMap {self._configmap_name!r}: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # PersonaPort — read operations
    # ------------------------------------------------------------------

    def load(self, name: str) -> PersonaConfig | None:
        """Return the named persona from the ConfigMap, or ``None`` if absent."""
        data = self._read_data()
        yaml_text = data.get(f"{name}.yaml")
        if yaml_text is None:
            return None
        return FilesystemPersonaAdapter.parse(yaml_text)

    def list_names(self) -> list[str]:
        """Return a sorted list of persona names present in the ConfigMap."""
        data = self._read_data()
        return sorted(k.removesuffix(".yaml") for k in data if k.endswith(".yaml"))

    # ------------------------------------------------------------------
    # PersonaRegistryPort — write operations
    # ------------------------------------------------------------------

    def save(self, config: PersonaConfig) -> None:
        """Persist *config* to the ConfigMap, auto-creating it if absent.

        Attempts a strategic merge patch first (adds/updates a single key).
        Falls back to creating the ConfigMap when it doesn't exist yet.
        """
        key = f"{config.name}.yaml"
        yaml_text = FilesystemPersonaAdapter.to_yaml(config)

        api = self._get_api()
        try:
            api.patch_namespaced_config_map(
                name=self._configmap_name,
                namespace=self._namespace,
                body={"data": {key: yaml_text}},
            )
        except Exception as exc:
            if getattr(exc, "status", None) == 404:
                self._create_configmap({key: yaml_text})
                return
            raise RuntimeError(
                f"Failed to save persona to ConfigMap {self._configmap_name!r}: {exc}"
            ) from exc
        logger.debug("Saved persona %r to ConfigMap %r", config.name, self._configmap_name)

    def delete(self, name: str) -> bool:
        """Remove the persona key from the ConfigMap.

        Returns ``True`` when the key was present and removed.
        Returns ``False`` when the key was not found (including missing ConfigMap).

        Uses a null-value patch so that strategic merge patch actually removes
        the key (absent keys are preserved, not deleted).
        """
        key = f"{name}.yaml"

        # Check the key exists before issuing the delete patch.
        data = self._read_data()
        if key not in data:
            return False

        # Null-value in a strategic merge patch deletes the key.
        api = self._get_api()
        try:
            api.patch_namespaced_config_map(
                name=self._configmap_name,
                namespace=self._namespace,
                body={"data": {key: None}},
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to patch ConfigMap {self._configmap_name!r}: {exc}"
            ) from exc
        logger.debug("Deleted persona %r from ConfigMap %r", name, self._configmap_name)
        return True

    # ------------------------------------------------------------------
    # PersonaRegistryPort — provenance queries
    # ------------------------------------------------------------------

    def is_builtin(self, name: str) -> bool:
        """Return ``True`` when *name* is a built-in persona shipped with ravn."""
        return name in _BUILTIN_NAMES

    def load_all(self) -> list[PersonaConfig]:
        """Return all personas stored in the ConfigMap."""
        data = self._read_data()
        result: list[PersonaConfig] = []
        for key, yaml_text in sorted(data.items()):
            if not key.endswith(".yaml"):
                continue
            persona = FilesystemPersonaAdapter.parse(yaml_text)
            if persona is not None:
                result.append(persona)
        return result

    def source(self, name: str) -> str:
        """Return the ConfigMap source string, or ``''`` when *name* is absent."""
        data = self._read_data()
        if f"{name}.yaml" not in data:
            return ""
        return f"[configmap:{self._namespace}/{self._configmap_name}]"
