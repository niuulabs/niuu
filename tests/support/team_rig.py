"""A Forge team over in-memory state: real session, forge, participant and team services.

Only the edges are fakes: the pod manager, the repositories, the user directory and the persona
catalog. A team started through it takes the same unit paths a production Forge takes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from unittest.mock import AsyncMock

from identity.adapters.cedar import CedarAuthorizationAdapter
from identity.models import TenantMembership, User
from niuu.domain.models import Principal
from tests.conftest import (
    InMemorySessionParticipantRepository,
    InMemorySessionRepository,
    MockPodManager,
)
from volundr.domain.models import GitSource
from volundr.domain.ports import (
    PersonaPlayabilityPort,
    Playability,
    SessionContributor,
    SessionPersona,
    SessionPersonaProvider,
)
from volundr.domain.services.forge import ForgeService
from volundr.domain.services.session import SessionService
from volundr.domain.services.session_participants import SessionParticipantService
from volundr.domain.services.team import TeamLaunch, TeamMemberSpec, TeamService

TENANT = "acme"
OWNER = "ada"
DIRECTORY = {
    "ada": User(id="ada", email="ada@example.test", display_name="Ada"),
    "bo": User(id="bo", email="bo@example.test", display_name="Bo"),
    "cy": User(id="cy", email="cy@example.test", display_name="Cy"),
}
OWNER_PRINCIPAL = Principal(OWNER, "ada@example.test", TENANT, ["volundr:developer"])

PERSONAS = {
    "architect": SessionPersona(
        name="Architect",
        system_prompt="",
        document={
            "schema_version": 1,
            "id": "architect",
            "revision": "r1",
            "definition": {"name": "architect"},
        },
    ),
    "coder": SessionPersona(
        name="Coder",
        system_prompt="",
        document={
            "schema_version": 1,
            "id": "coder",
            "revision": "r1",
            "definition": {"name": "coder"},
        },
    ),
    "bare": SessionPersona(name="Bare", system_prompt="", document={}),
}


class Personas(SessionPersonaProvider):
    async def get(self, owner_id: str, name: str) -> SessionPersona | None:
        return PERSONAS.get(name)


class StubPlayability(PersonaPlayabilityPort):
    """Plays every persona except on the models named in ``refused``."""

    def __init__(self, refused: Mapping[str, str] | None = None) -> None:
        self._refused = dict(refused or {})

    def check(
        self, model: str, persona: Mapping[str, Any], *, definition: str | None = None
    ) -> Playability:
        reasons = (self._refused[model],) if model in self._refused else ()
        return Playability(model, definition or "", "ws", reasons)


class TeamRig:
    """The services a team needs, with the in-memory state they share."""

    def __init__(
        self,
        *,
        runtime_backend: str = "process",
        invitations_refused: str | None = None,
        persona_provider: SessionPersonaProvider | None = Personas(),
        refused_models: Mapping[str, str] | None = None,
        contributors: list[SessionContributor] | None = None,
    ) -> None:
        self.repository = InMemorySessionRepository()
        self.pod_manager = MockPodManager()
        self.session_service = SessionService(
            self.repository,
            self.pod_manager,
            authorization=CedarAuthorizationAdapter(),
            provisioning_initial_delay=0,
            contributors=contributors,
        )
        users = AsyncMock()
        users.get.side_effect = DIRECTORY.get
        users.get_memberships.return_value = [TenantMembership(user_id="x", tenant_id=TENANT)]
        self.grants = InMemorySessionParticipantRepository()
        self.participants = SessionParticipantService(self.grants, self.session_service, users)
        self.forge = ForgeService(
            self.session_service,
            session_participant_service=self.participants,
            persona_playability=StubPlayability(refused_models),
        )
        self.service = TeamService(
            self.forge,
            self.participants,
            runtime_backend=runtime_backend,
            invitations_refused=invitations_refused,
            persona_provider=persona_provider,
        )

    async def settle(self) -> None:
        """Let the start of every unit that began finish, so no task outlives its test."""
        for task in list(self.session_service._provisioning_tasks.values()):
            await task


def launch(**fields: Any) -> TeamLaunch:
    """A valid two-agent team; ``fields`` replace what it names."""
    values: dict[str, Any] = {
        "name": "ship-it",
        "source": GitSource(repo="https://github.com/acme/repo", branch="main"),
        "members": [
            TeamMemberSpec(
                model="claude-sonnet-4-6",
                definition="skuldClaude",
                persona_name="architect",
                lead=True,
            ),
            TeamMemberSpec(model="gpt-5", definition="skuldCodex", persona_name="coder"),
        ],
    }
    values.update(fields)
    return TeamLaunch(**values)
