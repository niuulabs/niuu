"""The memory layers a person can read, asked of Forge with their own login.

A person reads the org layer of their org and the layer of every project they are
a member of. Forge decides membership: ``GET /api/v1/forge/projects`` called with
the person's own ``Authorization`` lists only the projects that person is a member
of, so a project they are not on is absent here and reads as not found everywhere
else (design section 8).

There is no person layer yet: ``LayerKind`` has none, so there is nothing to list.
When one is added it is added here, once, for every caller of
:func:`readable_layers`.

Failures raise: Forge refusing the login is :class:`PersonRefusedError`, Forge
unreachable or answering something unusable is :class:`ForgeUnavailableError`.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from mimir.memory.models import Layer, LayerKind, MemoryValidationError
from mimir.ports.people_directory import ForgeUnavailableError, Person, PersonRefusedError

#: What the org layer is called where a name is shown.
ORG_NAME = "Org"
_PROJECTS_PATH = "/api/v1/forge/projects"


@dataclass(frozen=True)
class ReadableLayer:
    """A layer a person can read, with the name to show for it."""

    layer: Layer
    name: str


async def readable_layers(
    person: Person, *, forge_base_url: str, client: httpx.AsyncClient
) -> list[ReadableLayer]:
    """The org layer, then each project the person is a member of, in Forge's order.

    Args:
        person: the caller, with the ``Authorization`` header Forge is asked with.
        forge_base_url: Forge's base URL, e.g. ``http://volundr:8080``.
        client: the HTTP client to ask with; its timeout bounds the call.
    """
    layers = [ReadableLayer(Layer(LayerKind.ORG), ORG_NAME)]
    seen: set[str] = set()
    for project in await _projects(person, forge_base_url.rstrip("/"), client):
        layer = _project_layer(project)
        if layer.id in seen:
            continue
        seen.add(layer.id)
        layers.append(ReadableLayer(layer, _project_name(project, layer)))
    return layers


async def _projects(
    person: Person, base_url: str, client: httpx.AsyncClient
) -> list[dict[str, object]]:
    headers = {"Accept": "application/json"}
    if person.authorization:
        headers["Authorization"] = person.authorization
    try:
        response = await client.get(base_url + _PROJECTS_PATH, headers=headers)
    except httpx.HTTPError as exc:
        raise ForgeUnavailableError(
            f"Forge could not be asked for the person's projects at {base_url}: {exc}"
        ) from exc
    if response.status_code in (401, 403):
        raise PersonRefusedError(
            response.status_code,
            f"Forge refused this login for the person's projects ({response.status_code})",
        )
    if response.status_code != 200:
        raise ForgeUnavailableError(
            f"Forge answered {response.status_code} for the person's projects"
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise ForgeUnavailableError(
            "Forge's answer about the person's projects is not JSON"
        ) from exc
    if not isinstance(body, list) or not all(isinstance(p, dict) for p in body):
        raise ForgeUnavailableError(
            "Forge's answer about the person's projects is not a list of projects"
        )
    return body


def _project_layer(project: dict[str, object]) -> Layer:
    project_id = project.get("id")
    if not isinstance(project_id, str) or not project_id.strip():
        raise ForgeUnavailableError("Forge listed a project without an id")
    try:
        return Layer(LayerKind.PROJECT, project_id.strip().lower())
    except MemoryValidationError as exc:
        raise ForgeUnavailableError(
            f"Forge listed project {project_id!r}, whose id cannot name a memory layer: {exc}"
        ) from exc


def _project_name(project: dict[str, object], layer: Layer) -> str:
    name = project.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ForgeUnavailableError(f"Forge listed {layer.id} without a name")
    return name.strip()
