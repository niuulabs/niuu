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
