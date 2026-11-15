"""A workflow run's members: each persona, the document pinned for it and its model.

A run has one session per member, so a member's stages must agree on what that session
runs. The model a member's session runs comes from its stages, else the launch, else
Ting's default; the persona document is the one pinned in the run's workflow snapshot.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


class RunMemberError(ValueError):
    """A member's session cannot be described: no model, conflicting models, no document."""


@dataclass(frozen=True)
class RunMember:
    member: str
    model: str
    persona_definition: Mapping[str, Any]


def member_values(snapshot: Mapping[str, Any], member: str, key: str) -> set[str]:
    """The distinct non-empty values of ``key`` the member's stages give it."""
    values: set[str] = set()
    for raw in _stage_members(snapshot):
        if str(raw.get("personaId") or "") != member:
            continue
        value = str(raw.get(key) or "").strip()
        if value:
            values.add(value)
    return values


#: Who decides what a run offers next: Ting's engine, or a coordinator agent in the run's root.
ORCHESTRATION_ENGINE = "engine"
ORCHESTRATION_SESSION = "session"
ORCHESTRATION_MODES = frozenset({ORCHESTRATION_ENGINE, ORCHESTRATION_SESSION})


def run_orchestration(snapshot: Mapping[str, Any]) -> str:
    """The run's orchestration mode: the workflow's ``orchestration.mode``, else ``engine``.

    ``engine`` is the workflow document's own default for a workflow that names none; a mode
    the document names that is not one of ours is refused, never read as ``engine``.
    """
    raw = snapshot.get("orchestration")
    if raw is None:
        return ORCHESTRATION_ENGINE
    mode = raw.get("mode") if isinstance(raw, Mapping) else None
    if mode not in ORCHESTRATION_MODES:
        raise RunMemberError(
            f"the workflow's orchestration {raw!r} must be a mapping whose mode is one of "
            f"{', '.join(sorted(ORCHESTRATION_MODES))}"
        )
    return str(mode)


def member_topics(snapshot: Mapping[str, Any], member: str) -> tuple[str, ...]:
    """Every topic the workflow routes to the member: its stages' ``consumesEventTypes``.

    A member takes an offer only on a topic it subscribes to, and the engine offers a stage to
    the members whose stage names the topic, so the member must subscribe to all of them, not
    only to its persona's own ``consumes`` (the merge stage gives the coder ``merge.approved``).
    """
    topics: set[str] = set()
    for raw in _stage_members(snapshot):
        if str(raw.get("personaId") or "") != member:
            continue
        declared = raw.get("consumesEventTypes")
        if not isinstance(declared, list):
            declared = raw.get("consumes_event_types")
        for topic in declared if isinstance(declared, list) else []:
            if str(topic).strip():
                topics.add(str(topic).strip())
    return tuple(sorted(topics))


def member_event_filters(snapshot: Mapping[str, Any], member: str) -> dict[str, str]:
    """The member's ``eventFilters``: the one set all of its stages give it, else empty.

    A run has one session per member, and the session checks one set of filters, so stages
    that give the same member different filters cannot be served and are refused.
    """
    found: dict[frozenset[tuple[str, str]], dict[str, str]] = {}
    for raw in _stage_members(snapshot):
        if str(raw.get("personaId") or "") != member:
            continue
        filters = raw.get("eventFilters")
        named = (
            {str(key): str(value) for key, value in filters.items()}
            if isinstance(filters, Mapping)
            else {}
        )
        found[frozenset(named.items())] = named
    if len(found) > 1:
        raise RunMemberError(
            f"member {member!r} is given different eventFilters by different stages but a "
            "run has one session per member; give the member one set of eventFilters"
        )
    return next(iter(found.values()), {})


def member_model(
    snapshot: Mapping[str, Any], member: str, *, launch_model: str, default_model: str
) -> str:
    """The model the member's session runs: its stages', else the launch's, else the default."""
    models = member_values(snapshot, member, "model")
    if len(models) > 1:
        raise RunMemberError(
            f"member {member!r} is given different models by different stages "
            f"({', '.join(sorted(models))}) but a run has one session per member; "
            "give the member one model"
        )
    model = next(iter(models), "") or launch_model or default_model
    if not model:
        raise RunMemberError(
            f"no model for member {member!r}: set its model in the workflow, launch "
            "with a model, or set dispatch.default_model"
        )
    return model


def run_members(
    snapshot: Mapping[str, Any], *, launch_model: str, default_model: str
) -> list[RunMember]:
    """Every member the run's stages name that has a pinned document, in stage order.

    A member with no pinned document has nothing to describe: its step session is refused
    when the step creates it (``ForgeStepSessions``), loudly, as before.
    """
    documents = snapshot.get("persona_definitions")
    documents = documents if isinstance(documents, Mapping) else {}
    members: list[RunMember] = []
    seen: set[str] = set()
    for raw in _stage_members(snapshot):
        member = str(raw.get("personaId") or "")
        if not member or member in seen:
            continue
        seen.add(member)
        document = documents.get(member)
        if not isinstance(document, Mapping):
            continue
        members.append(
            RunMember(
                member=member,
                model=member_model(
                    snapshot, member, launch_model=launch_model, default_model=default_model
                ),
                persona_definition=document,
            )
        )
    return members


def _stage_members(snapshot: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    graph = snapshot.get("graph")
    nodes = graph.get("nodes") if isinstance(graph, Mapping) else None
    return [
        raw
        for node in nodes or []
        if isinstance(node, Mapping) and node.get("kind") == "stage"
        for raw in node.get("stageMembers") or []
        if isinstance(raw, Mapping)
    ]
