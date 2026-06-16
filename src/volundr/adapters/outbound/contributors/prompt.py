"""Prompt contributor — injects ad-hoc system and initial prompts into session values."""

from typing import Any

from volundr.domain.models import Session
from volundr.domain.ports import (
    SessionContext,
    SessionContribution,
    SessionContributor,
)


class PromptContributor(SessionContributor):
    """Injects system_prompt and initial_prompt from the launch request.

    Runs after LaunchSpecContributor so ad-hoc prompts override launch-spec
    defaults via deep merge (last writer wins for the same key).
    """

    def __init__(self, **_extra: object) -> None:
        pass

    @property
    def name(self) -> str:
        return "prompt"

    async def contribute(
        self,
        session: Session,
        context: SessionContext,
    ) -> SessionContribution:
        values: dict[str, Any] = {}

        if context.system_prompt:
            values.setdefault("session", {})["systemPrompt"] = context.system_prompt
        if context.initial_prompt:
            values.setdefault("session", {})["initialPrompt"] = context.initial_prompt

        return SessionContribution(values=values)
