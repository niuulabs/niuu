from __future__ import annotations

from ravn.domain.capability_catalog import (
    CapabilityKind,
    WorkflowCapability,
    WorkflowSelector,
    capability_from_skill,
    capability_from_tool,
    capability_from_workflow,
    filter_capabilities,
    select_workflow,
)


def test_workflow_selector_matches_names_and_ids() -> None:
    workflow = WorkflowCapability("wf-build", "Tool Builder", tags=["tool-builder"])

    assert WorkflowSelector(names=["Tool Builder"]).matches(workflow)
    assert WorkflowSelector(names=["wf-build"]).matches(workflow)
    assert not WorkflowSelector(names=["Research"]).matches(workflow)


def test_workflow_selector_matches_any_or_all_tags() -> None:
    workflow = WorkflowCapability(
        "wf-build",
        "Tool Builder",
        tags=["tool-builder", "capability-builder"],
    )

    assert WorkflowSelector(tags=["tool-builder"]).matches(workflow)
    assert WorkflowSelector(tags=["missing", "tool-builder"]).matches(workflow)
    assert not WorkflowSelector(
        tags=["missing", "tool-builder"],
        require_all_tags=True,
    ).matches(workflow)


def test_select_workflow_returns_first_matching_catalog_entry() -> None:
    workflows = [
        WorkflowCapability("wf-research", "Research", tags=["research"]),
        WorkflowCapability("wf-build", "Tool Builder", tags=["tool-builder"]),
    ]

    assert select_workflow(WorkflowSelector(tags=["tool-builder"]), workflows) == workflows[1]
    assert select_workflow(WorkflowSelector(tags=["missing"]), workflows) is None
    assert select_workflow(WorkflowSelector(), workflows) is None


def test_tool_capability_projects_to_claude_and_codex_shapes() -> None:
    capability = capability_from_tool(
        name="mimir_search",
        description="Search Mimir.",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        required_permission="mimir:read",
        tags=["mimir", "search"],
    )

    assert capability.kind is CapabilityKind.TOOL
    assert capability.to_catalog_dict()["id"] == "tool:mimir_search"
    assert capability.to_claude_tool() == {
        "name": "mimir_search",
        "description": "Search Mimir.",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
    }
    assert capability.to_codex_action()["required_permission"] == "mimir:read"


def test_skill_capability_keeps_existing_skill_registry_shape() -> None:
    capability = capability_from_skill(
        skill_id="skill-1",
        name="Investigate pod crash",
        description="Gather read-only pod failure context.",
        requires_tools=["kubernetes_inspect", "mimir_write"],
    )

    assert capability.kind is CapabilityKind.SKILL
    assert capability.required_permission == "skill:run"
    assert capability.to_claude_tool() is None
    assert capability.to_catalog_dict()["metadata"]["requires_tools"] == [
        "kubernetes_inspect",
        "mimir_write",
    ]


def test_workflow_capability_is_a_catalog_entry_not_a_signal_route() -> None:
    capability = capability_from_workflow(
        WorkflowCapability(
            workflow_id="wf-build",
            name="Tool Builder",
            description="Build missing tools.",
            version="1",
            tags=["tool-builder"],
        ),
        source="ting",
        source_index=2,
    )

    assert capability.kind is CapabilityKind.WORKFLOW
    assert capability.required_permission == "workflow:launch"
    assert capability.to_catalog_dict()["metadata"]["source_index"] == 2
    assert capability.to_codex_action()["kind"] == "workflow"


def test_filter_capabilities_by_kind_and_tag() -> None:
    capabilities = [
        capability_from_tool(
            name="mimir_search",
            description="Search memory.",
            input_schema={},
            required_permission="mimir:read",
            tags=["mimir"],
        ),
        capability_from_workflow(
            WorkflowCapability("wf-build", "Tool Builder", tags=["tool-builder"])
        ),
    ]

    assert [
        item.name
        for item in filter_capabilities(
            capabilities,
            kind=CapabilityKind.WORKFLOW,
            tags=["tool-builder"],
        )
    ] == ["Tool Builder"]
