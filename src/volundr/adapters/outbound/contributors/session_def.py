"""Session definition contributor — merges definition defaults into Helm values."""

from typing import Any

from volundr.config import SessionDefinitionConfig
from volundr.domain.models import Session
from volundr.domain.ports import SessionContext, SessionContribution, SessionContributor


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

        values: dict[str, Any] = dict(defn.defaults)
        return SessionContribution(values=values)
