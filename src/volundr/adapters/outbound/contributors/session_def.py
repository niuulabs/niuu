"""Session definition contributor — merges definition defaults into Helm values."""

from copy import deepcopy
from typing import Any

from volundr.config import SessionDefinitionConfig
from volundr.domain.models import Session
from volundr.domain.ports import SessionContext, SessionContribution, SessionContributor


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
        if defn.default_model and "model" not in values:
            values["model"] = defn.default_model
        return SessionContribution(values=values)
