"""Every member of a shipped workflow can be played by the harness its model runs on.

A member's model picks its session definition from Bifröst's catalog, the definition picks
the Skuld transport (the Forge chart's ``sessionDefinitions``), and Skuld refuses to start a
persona its harness cannot enforce. A shipped workflow whose member is refused cannot finish:
its member session never comes up, and the run fails only when Forge gives up on the install.
So the pairing is checked here, with the same projector Skuld runs at startup.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest
import yaml

from bifrost.config import _default_models
from personas.domain.persona_document import PortablePersonaDefinition
from skuld.personas.projection import ProjectionContext
from skuld.personas.registry import projector_for
from ting.system_workflows import load_system_workflows

ROOT = Path(__file__).resolve().parents[2]
CHART_VALUES = ROOT / "charts" / "volundr" / "values.yaml"


def _session_definitions() -> dict[str, dict[str, Any]]:
    values = yaml.safe_load(CHART_VALUES.read_text())
    return values["sessionDefinitions"]


def _broker_of(model: str) -> dict[str, Any]:
    """The Skuld broker settings a session of ``model`` starts with."""
    catalog = {entry.id: entry for entry in _default_models()}
    entry = catalog.get(model)
    assert entry is not None, f"model {model!r} is not in Bifröst's default catalog"
    definition = entry.session_definition
    assert definition, f"model {model!r} names no session definition in Bifröst's catalog"
    definitions = _session_definitions()
    assert definition in definitions, f"no session definition {definition!r} in the Forge chart"
    return definitions[definition]["defaults"]["broker"]


def _transport_class(adapter: str) -> type:
    module, name = adapter.rsplit(".", 1)
    return getattr(importlib.import_module(module), name)


def _members() -> list[tuple[str, str, str, dict[str, Any]]]:
    members = []
    for workflow in load_system_workflows():
        for node in workflow.graph.get("nodes") or []:
            for member in node.get("stageMembers") or []:
                persona = str(member.get("personaId") or "")
                members.append(
                    (
                        workflow.name,
                        persona,
                        str(member.get("model") or ""),
                        workflow.persona_definitions[persona],
                    )
                )
    return members


MEMBERS = _members()


def test_every_shipped_member_names_its_model() -> None:
    assert MEMBERS
    assert all(model for _, _, model, _ in MEMBERS)


@pytest.mark.parametrize(
    ("workflow", "persona", "model", "document"),
    MEMBERS,
    ids=[f"{w}:{p}:{m}" for w, p, m, _ in MEMBERS],
)
def test_the_harness_of_each_members_model_can_play_its_persona(
    workflow: str, persona: str, model: str, document: dict[str, Any], tmp_path: Path
) -> None:
    broker = _broker_of(model)
    adapter = broker["transportAdapter"]
    context = ProjectionContext(
        transport=adapter,
        runtime_dir=tmp_path,
        node_skip_permissions=bool(broker.get("skipPermissions")),
        node_claude_permission_mode=str(broker.get("claudePermissionMode") or ""),
        node_approval_policy=str(broker.get("approvalPolicy") or ""),
        node_sandbox=str(broker.get("sandbox") or ""),
        supports_read_only_mcp_boundary=bool(
            getattr(_transport_class(adapter), "supports_read_only_mcp_boundary", False)
        ),
    )

    projection = projector_for(adapter).project(
        PortablePersonaDefinition.from_dict(document), context
    )

    assert not projection.refusals, (
        f"{workflow}: {persona} on {model} ({adapter}) is refused: "
        + "; ".join(gap.describe() for gap in projection.refusals)
    )
