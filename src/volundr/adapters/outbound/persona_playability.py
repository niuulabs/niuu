"""Whether a persona can be played on the harness a session of a model would run.

A session's model picks its session definition (Bifröst's catalog, else the Forge's
default), and the definition's broker defaults pick the Skuld transport and the node's own
permission settings. Skuld projects the session's persona onto that transport at startup
and refuses to start when the harness cannot play it. The same projection runs here, before
anything starts, so a launch or a session create that could only fail later is refused now,
with the harness's own reasons.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from niuu.config_models import SessionDefinitionConfig
from personas.domain.persona_document import PortablePersonaDefinition
from skuld.config import SkuldSettings
from skuld.personas.projection import PersonaProjectionError, ProjectionContext
from skuld.personas.registry import projector_for
from volundr.domain.ports import PersonaPlayabilityPort, Playability

#: What a Skuld node runs when its definition sets nothing, read from Skuld's own settings
#: so the check and the node never disagree.
_NODE_DEFAULTS = SkuldSettings.model_fields


def _node_default(name: str) -> Any:
    return _NODE_DEFAULTS[name].default


class SkuldPersonaPlayability(PersonaPlayabilityPort):
    """Projects a persona onto the harness a model's session would run, as Skuld does."""

    def __init__(
        self,
        *,
        definitions: Mapping[str, SessionDefinitionConfig],
        default_definition: str,
        resolve_definition: Callable[[str], str | None],
    ) -> None:
        self._definitions = definitions
        self._default_definition = default_definition
        self._resolve_definition = resolve_definition

    def check(
        self, model: str, persona: Mapping[str, Any], *, definition: str | None = None
    ) -> Playability:
        key = definition or self._resolve_definition(model) or self._default_definition
        if not key:
            return Playability(
                model,
                "",
                "",
                (
                    f"model {model!r} names no session definition in the model catalog and "
                    "this Forge has no default_definition",
                ),
            )
        config = self._definitions.get(key)
        if config is None or not config.enabled:
            return Playability(
                model, key, "", (f"session definition {key!r} of model {model!r} is not enabled",)
            )
        broker = config.defaults.get("broker") if isinstance(config.defaults, Mapping) else None
        broker = broker if isinstance(broker, Mapping) else {}
        transport = str(broker.get("transportAdapter") or _node_default("transport_adapter"))
        try:
            document = PortablePersonaDefinition.from_dict(dict(persona))
            projector = projector_for(transport)
            projection = projector.project(document, self._context(transport, broker))
        except (PersonaProjectionError, ValueError) as exc:
            return Playability(model, key, transport, (str(exc),))
        return Playability(
            model, key, transport, tuple(gap.describe() for gap in projection.refusals)
        )

    @staticmethod
    def _context(transport: str, broker: Mapping[str, Any]) -> ProjectionContext:
        skip = broker.get("skipPermissions")
        return ProjectionContext(
            transport=transport,
            # The projection only names files under it; nothing is written by a check.
            runtime_dir=Path(tempfile.gettempdir()) / "persona-playability",
            node_skip_permissions=(
                bool(skip) if skip is not None else bool(_node_default("skip_permissions"))
            ),
            node_claude_permission_mode=str(
                broker.get("claudePermissionMode") or _node_default("claude_permission_mode")
            ),
            node_approval_policy=str(
                broker.get("approvalPolicy") or _node_default("approval_policy")
            ),
            node_sandbox=str(broker.get("sandbox") or _node_default("sandbox")),
        )
