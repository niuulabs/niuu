/**
 * Full semantic validation for Workflow DAGs.
 *
 * `validateWorkflowFull` goes beyond the structural checks in `validateWorkflow`
 * (which only enforces schema invariants) to catch semantic issues that would
 * cause a workflow to fail at runtime.
 *
 * Owner: plugin-ting.
 */

import type { Workflow } from './workflow';
import { detectCycle } from './topologicalSort';
import {
  parseWorkflowEdgeLabel,
  stagePersonaIds,
  structuralWorkflowEdges,
  workflowPersonaModelConflicts,
  type WorkflowModelCatalogEntry,
} from './workflowSemantics';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type WorkflowIssueKind =
  | 'cycle'
  | 'orphan'
  | 'dangling_condition'
  | 'resource_link'
  | 'confidence_underset'
  | 'missing_persona'
  | 'missing_model'
  | 'persona_model_conflict'
  | 'no_producer'
  | 'no_consumer'
  | 'subworkflow_dependency'
  | 'subworkflow_template'
  | 'subworkflow_coordinator'
  | 'subworkflow_blocked_event'
  | 'subworkflow_joined_event'
  | 'subworkflow_limits';

export interface WorkflowIssue {
  kind: WorkflowIssueKind;
  /** ID of the offending node, or null for workflow-level issues. */
  nodeId: string | null;
  message: string;
  severity: 'error' | 'warning';
}

// ---------------------------------------------------------------------------
// Validator
// ---------------------------------------------------------------------------

/**
 * Run all semantic validation rules on a workflow.
 *
 * Rules checked (in order):
 *
 * 1. **cycle** — unexpected directed cycle exists; expected retry/re-entry
 *    edges are excluded from this check.
 * 2. **orphan** — node has no edges at all (workflow has >1 node).
 * 3. **dangling_condition** — `cond` node has fewer than 2 outgoing edges.
 * 4. **confidence_underset** — when any stage is run-mapped, other `stage`
 *    nodes without `runId` are treated as unplanned work.
 * 5. **missing_persona** — `stage` node has no stage members or persona IDs.
 * 6. **subworkflow contract** — child dependency, coordinator, events, and
 *    runtime expansion limits are complete and internally consistent.
 * 7. **no_producer** — `gate`/`cond` node has no incoming edges.
 * 8. **no_consumer** — `stage`/`wait` node has no outgoing edges
 *    (non-singleton workflow).
 *
 * Returns an empty array when the workflow is valid.
 */
export function validateWorkflowFull(
  workflow: Workflow,
  _modelCatalog?: Record<string, WorkflowModelCatalogEntry>,
): WorkflowIssue[] {
  const issues: WorkflowIssue[] = [];
  const { nodes, edges } = workflow;
  const kindLabel = (kind: Workflow['nodes'][number]['kind']) => {
    switch (kind) {
      case 'subworkflow':
        return 'Child workflows';
      case 'stage':
        return 'Stage';
      case 'gate':
        return 'Gate';
      case 'cond':
        return 'Condition';
      case 'trigger':
        return 'Trigger';
      case 'end':
        return 'End';
      case 'resource':
        return 'Resource';
      case 'wait':
        return 'Wait';
    }
  };

  // ── 1. Cycle detection ────────────────────────────────────────────────────
  const cycleNodeIds = detectCycle(
    nodes.map((n) => n.id),
    structuralWorkflowEdges(edges),
  );
  for (const nodeId of cycleNodeIds) {
    issues.push({
      kind: 'cycle',
      nodeId,
      message: 'Node is part of an unexpected directed cycle',
      severity: 'error',
    });
  }

  // ── 2. Orphan detection ───────────────────────────────────────────────────
  if (nodes.length > 1) {
    for (const node of nodes) {
      if (node.kind === 'resource') continue;
      const hasIn = edges.some((e) => e.target === node.id);
      const hasOut = edges.some((e) => e.source === node.id);
      if (!hasIn && !hasOut) {
        issues.push({
          kind: 'orphan',
          nodeId: node.id,
          message: 'Node is not connected to anything',
          severity: 'warning',
        });
      }
    }
  }

  // ── 3. Dangling conditions ────────────────────────────────────────────────
  for (const node of nodes) {
    if (node.kind !== 'cond') continue;
    const outCount = edges.filter((e) => e.source === node.id).length;
    if (outCount < 2) {
      issues.push({
        kind: 'dangling_condition',
        nodeId: node.id,
        message: `Condition node needs ≥2 outgoing edges (has ${outCount})`,
        severity: 'error',
      });
    }
  }

  // ── 4. Confidence underset ────────────────────────────────────────────────
  const stages = nodes.filter((node) => node.kind === 'stage');
  const hasMappedRunStages = stages.some((node) => Boolean(node.runId));
  for (const node of stages) {
    if (!hasMappedRunStages || node.runId) continue;
    issues.push({
      kind: 'confidence_underset',
      nodeId: node.id,
      message: 'Stage has no run assigned — work is unplanned',
      severity: 'warning',
    });
  }

  // ── 5. Missing personas ───────────────────────────────────────────────────
  for (const node of nodes) {
    if (node.kind !== 'stage') continue;
    if (stagePersonaIds(node).length === 0) {
      issues.push({
        kind: 'missing_persona',
        nodeId: node.id,
        message: 'Stage has no personas assigned',
        severity: 'warning',
      });
    }
    const missingModelMember = (node.stageMembers ?? []).find(
      (member) => !member.model || !member.model.trim(),
    );
    if (missingModelMember) {
      issues.push({
        kind: 'missing_model',
        nodeId: node.id,
        message: `Stage persona '${missingModelMember.personaId}' has no model assigned`,
        severity: 'error',
      });
    }
  }

  const personaModelConflicts = workflowPersonaModelConflicts(workflow);
  for (const [personaId, models] of Object.entries(personaModelConflicts)) {
    issues.push({
      kind: 'persona_model_conflict',
      nodeId: null,
      message: `Persona '${personaId}' is assigned multiple models in one workflow: ${models.join(', ')}`,
      severity: 'error',
    });
  }

  // A child workflow is executable only when every runtime-owned part of its
  // expansion contract can be resolved from the saved parent document. The
  // joined event deliberately lives on its outgoing edge; it is not a second
  // node field that could drift from the graph.
  for (const node of nodes) {
    if (node.kind !== 'subworkflow') continue;

    const templateEntries = Object.entries(node.templates ?? {});
    if (templateEntries.length === 0) {
      issues.push({
        kind: 'subworkflow_dependency',
        nodeId: node.id,
        message: 'Child workflow must declare at least one template',
        severity: 'error',
      });
    }
    for (const [templateName, alias] of templateEntries) {
      if (!templateName.trim()) {
        issues.push({
          kind: 'subworkflow_template',
          nodeId: node.id,
          message: 'Child workflow template names must not be blank',
          severity: 'error',
        });
        continue;
      }
      if (!alias || !workflow.workflowDependencies?.[alias]) {
        issues.push({
          kind: 'subworkflow_dependency',
          nodeId: node.id,
          message: `Template '${templateName}' must select a declared workflow dependency`,
          severity: 'error',
        });
      }
    }

    const templateNames = new Set(templateEntries.map(([templateName]) => templateName));
    for (const declaredChild of node.children ?? []) {
      const childTemplate = declaredChild.template?.trim();
      if (!childTemplate) {
        if (templateNames.size > 1) {
          issues.push({
            kind: 'subworkflow_template',
            nodeId: node.id,
            message: `Child '${declaredChild.key}' must name a template; this node offers: ${[...templateNames].sort().join(', ')}`,
            severity: 'error',
          });
        }
        continue;
      }
      if (!templateNames.has(childTemplate)) {
        issues.push({
          kind: 'subworkflow_template',
          nodeId: node.id,
          message: `Child '${declaredChild.key}' names unknown template '${childTemplate}'`,
          severity: 'error',
        });
      }
    }

    if (!node.allowedCoordinator || !workflow.personaDependencies?.[node.allowedCoordinator]) {
      issues.push({
        kind: 'subworkflow_coordinator',
        nodeId: node.id,
        message: 'Child workflow coordinator must be a declared persona dependency',
        severity: 'error',
      });
    }

    const blockedEvent = node.blockedEvent?.trim() ?? '';
    if (!blockedEvent) {
      issues.push({
        kind: 'subworkflow_blocked_event',
        nodeId: node.id,
        message: 'Child workflow must declare the event published when its children block',
        severity: 'error',
      });
    }

    const outgoingEdges = edges.filter((edge) => edge.source === node.id);
    const parsedOutgoing = outgoingEdges.map((edge) => parseWorkflowEdgeLabel(edge.label));
    const joinedEvents = new Set(
      parsedOutgoing.map((contract) => contract?.sourceEventType.trim() ?? '').filter(Boolean),
    );
    if (
      outgoingEdges.length === 0 ||
      parsedOutgoing.some((contract) => contract === null) ||
      joinedEvents.size !== 1
    ) {
      issues.push({
        kind: 'subworkflow_joined_event',
        nodeId: node.id,
        message: 'Child workflow outgoing connections must name one joined event',
        severity: 'error',
      });
    } else if (blockedEvent && joinedEvents.has(blockedEvent)) {
      issues.push({
        kind: 'subworkflow_joined_event',
        nodeId: node.id,
        message: 'Child workflow joined event must differ from its blocked event',
        severity: 'error',
      });
    }

    const maxActiveChildren = node.maxActiveChildren ?? node.maxChildren;
    if (
      node.maxChildren <= 0 ||
      node.maxChildren > 100 ||
      node.maxAttempts <= 0 ||
      node.maxAttempts > 10 ||
      maxActiveChildren <= 0 ||
      maxActiveChildren > node.maxChildren
    ) {
      issues.push({
        kind: 'subworkflow_limits',
        nodeId: node.id,
        message:
          'Child workflow limits require at most 100 children, 10 attempts, and active children no greater than total children',
        severity: 'error',
      });
    }
  }

  // ── 7. No-producer ────────────────────────────────────────────────────────
  // Gates, conditions, and terminal nodes should have at least one inbound connection.
  if (nodes.length > 1) {
    for (const node of nodes) {
      if (node.kind === 'trigger' || node.kind === 'stage' || node.kind === 'resource') continue;
      const hasIn = edges.some((e) => e.target === node.id);
      if (!hasIn) {
        issues.push({
          kind: 'no_producer',
          nodeId: node.id,
          message: `${kindLabel(node.kind)} node has no incoming connection`,
          severity: 'error',
        });
      }
    }
  }

  // ── 8. No-consumer ────────────────────────────────────────────────────────
  // Stage and passive wait nodes with no outgoing edges are dead ends.
  if (nodes.length > 1) {
    for (const node of nodes) {
      if (node.kind !== 'stage' && node.kind !== 'wait') continue;
      const hasOut = edges.some((e) => e.source === node.id);
      if (!hasOut) {
        issues.push({
          kind: 'no_consumer',
          nodeId: node.id,
          message: `${kindLabel(node.kind)} has no outgoing connection`,
          severity: node.kind === 'wait' ? 'error' : 'warning',
        });
      }
    }
  }

  const resourceNodeIds = new Set(
    nodes.filter((node) => node.kind === 'resource').map((node) => node.id),
  );

  for (const binding of workflow.resourceBindings ?? []) {
    if (resourceNodeIds.has(binding.resourceNodeId)) {
      continue;
    }

    issues.push({
      kind: 'resource_link',
      nodeId: binding.resourceNodeId,
      message: 'Resource binding references a missing resource node',
      severity: 'error',
    });
  }

  return issues;
}
