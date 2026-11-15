"""What the session contributors that put a session on a shared mesh realm have in common.

A workflow run (``workflow_step_host``) and a Forge team (``team_room``) both write the
session spec's ``values.mesh`` and ``values.room``, which the skuld chart renders as Skuld's
``mesh`` and ``room`` settings and the runtimes that start Skuld from the environment turn into
``SKULD__MESH__...`` and ``SKULD__ROOM__...`` (``workflow_session_environment``).
"""

from typing import Any

from personas.domain.persona_document import (
    PortablePersonaDefinition,
    consumed_event_types,
    produced_event_types,
)

#: Top-level key of the session spec values the skuld chart renders as Skuld's ``mesh``.
MESH_VALUES_KEY = "mesh"
#: Top-level key of the session spec values the skuld chart renders as Skuld's ``room``.
ROOM_VALUES_KEY = "room"
#: What every Skuld session publishes after each turn, besides its persona's own events.
SESSION_OUTCOME_EVENT = "code.changed"


def announced_persona(document: object) -> dict[str, Any]:
    """What the member announces to its realm's other members: its persona and its topics.

    The same facts Skuld derives from the pinned persona for its own mesh identity, written on
    the pod so pod discovery (and the realm's room) can show them. A session pinned to no
    persona announces none.
    """
    if not isinstance(document, dict):
        return {}
    persona = PortablePersonaDefinition.from_dict(document)
    emits = {SESSION_OUTCOME_EVENT, *produced_event_types(persona.definition)}
    return {
        "announce": {
            "persona": persona.id,
            "consumes": sorted(consumed_event_types(persona.definition)),
            "emits": sorted(emits),
        }
    }
