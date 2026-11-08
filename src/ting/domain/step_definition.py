"""What the step engine runs for a stored workflow: its graph with includes resolved.

A stored workflow may contain ``include`` nodes, which stand in for stages and gates
copied from another pinned workflow. The engine runs the graph those copies make, so
this resolves them from the workflow's own pinned dependency closure and pairs the
result with the persona documents and pins its stage members run as.
"""

from __future__ import annotations

from ting.domain.models import WorkflowDefinition
from ting.domain.workflow_includes import (
    graph_has_include_nodes,
    include_resolver_from_workflow_definitions,
    resolve_workflow_includes,
)
from ting.ports.workflow_steps import PersonaRef, StepRunDefinition


def step_run_definition(workflow: WorkflowDefinition) -> StepRunDefinition:
    """The definition a run of ``workflow`` executes; includes are resolved, never skipped."""
    graph = workflow.graph
    if graph_has_include_nodes(graph):
        graph = resolve_workflow_includes(
            graph,
            persona_dependencies=workflow.persona_dependencies,
            workflow_dependencies=workflow.workflow_dependencies,
            resolve_alias=include_resolver_from_workflow_definitions(workflow.workflow_definitions),
        )
    return StepRunDefinition(
        graph=graph,
        persona_definitions=workflow.persona_definitions,
        persona_refs={
            alias: PersonaRef(alias, pin.id, pin.revision, pin.digest)
            for alias, pin in workflow.persona_dependencies.items()
        },
    )
