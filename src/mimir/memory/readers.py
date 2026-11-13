"""The layers a person reads outside a session (design section 8).

A person reads the org layer (they are in the org) and the layers of the projects they
are on; Forge says which, asked with their own login, so a project they are not on reads
as not found. Run and session layers are not read here: they belong to one session's or
run's owner, and a person reads them from that session.

- **One project** (``project_id`` given): that project, the memories it attaches, and
  the org. This is what a session of the project reads, less its own session and run.
- **No project**: the org and every active project the person is on. Attached memories
  belong to one project's view (their names are only unique within a project), so they
  are read only when a project is named.
"""

from __future__ import annotations

from mimir.memory.gateway import MemoryGateway, Session
from mimir.ports.people_directory import PeopleDirectory, Person


class PersonReader:
    """Args:
    gateway: the memory gateway.
    people: Forge's answers about a person's projects.
    """

    def __init__(self, gateway: MemoryGateway, people: PeopleDirectory) -> None:
        self._gateway = gateway
        self._people = people

    async def open(self, person: Person, project_id: str | None) -> Session:
        """The person's reading view. Raises NotVisibleError for a project they are not on."""
        if project_id:
            project = project_id.strip().lower()
            # Forge answers 404 to a person who is not on the project.
            await self._people.project_role(project, person)
            attachments = await self._people.project_attachments(project, person)
            return await self._gateway.open_reader(
                person.tenant_id, project_ids=[project], attachments=attachments
            )
        projects = await self._people.projects(person)
        return await self._gateway.open_reader(
            person.tenant_id, project_ids=[p.project_id for p in projects]
        )
