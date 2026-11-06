"""Session definition contributor — merges definition defaults into Helm values."""

from copy import deepcopy
from typing import Any

from volundr.config import SessionDefinitionConfig
from volundr.domain.models import Session
from volundr.domain.ports import SessionContext, SessionContribution, SessionContributor

#: Kubernetes pod-storage values a definition may default for the session chart.
_POD_STORAGE_DEFAULTS = ("homeVolume", "persistence")


def _drop_empty_deployment_owned_defaults(values: dict[str, Any]) -> dict[str, Any]:
    """Remove empty values for fields that deployment defaults own."""
    volundr = values.get("volundr")
    if not isinstance(volundr, dict):
        return values

    if volundr.get("apiUrl") == "":
        volundr.pop("apiUrl")
    if not volundr:
        values.pop("volundr")
    return values


def _drop_pod_storage_defaults(key: str, values: dict[str, Any]) -> dict[str, Any]:
    """Remove pod-storage defaults a VM session has no use for.

    A VM runtime keeps workspace and home on guest-local disk and archives
    them at stop, so a definition's pod mount paths and home-volume toggle do
    not apply. A definition that names a claim asks for storage a VM cannot
    mount, so it is refused rather than dropped.
    """
    for field in _POD_STORAGE_DEFAULTS:
        storage = values.pop(field, None)
        if isinstance(storage, dict) and storage.get("existingClaim"):
            raise ValueError(
                f"Session definition {key!r} sets {field}.existingClaim, which a VM "
                f"runtime cannot mount; remove it from the definition's defaults"
            )
    return values


class SessionDefinitionContributor(SessionContributor):
    """Looks up the session definition key from context and deep-merges its defaults.

    Session definitions (e.g. skuldClaude, skuldCodex) carry broker
    configuration (cliType, transportAdapter) and other Helm value
    defaults. This contributor runs early in the pipeline so that
    definition defaults can be overridden by later contributors
    (templates, profiles, resources).
    """

    def __init__(
        self,
        *,
        definitions: dict[str, SessionDefinitionConfig] | None = None,
        default_definition: str = "",
        **_extra: object,
    ):
        self._definitions = definitions or {}
        self._default_definition = default_definition

    @property
    def name(self) -> str:
        return "session_definition"

    async def contribute(
        self,
        session: Session,
        context: SessionContext,
    ) -> SessionContribution:
        key = context.definition or self._default_definition
        if not key:
            return SessionContribution()

        defn = self._definitions.get(key)
        if not defn or not defn.enabled:
            return SessionContribution()

        values: dict[str, Any] = deepcopy(defn.defaults)
        values = _drop_empty_deployment_owned_defaults(values)
        # VM runtimes own their guest-local storage, as the storage contributor
        # assumes; the runtime still refuses storage that a later contributor sets.
        if context.storage_backend == "vm" or context.runtime_backend == "vm":
            values = _drop_pod_storage_defaults(key, values)
        if defn.default_model and "model" not in values:
            values["model"] = defn.default_model
        return SessionContribution(values=values)
