/**
 * Full semantic validation for Workflow DAGs.
 *
 * `validateWorkflowFull` goes beyond the structural checks in `validateWorkflow`
 * (which only enforces schema invariants) to catch semantic issues that would
 * cause a workflow to fail at runtime.
 *
 * Owner: plugin-ting.
 */

import type { Workflow, WorkflowNode } from './workflow';
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
  | 'subworkflow_limits'
  | 'include';

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
 * 6a. **include contract** — `include` node pins a declared workflow
 *    dependency, maps at least one node, and its local ids don't collide
 *    with another node or another include; when the pinned workflow is in
 *    `workflowCatalog`, every mapped source id must also be one of its
 *    `stage`/`gate` nodes.
 * 7. **no_producer** — `gate`/`cond`/`include` node has no incoming edges.
 * 8. **no_consumer** — `stage`/`wait` node has no outgoing edges
 *    (non-singleton workflow).
 *
 * An `include` node's own id never carries edges directly — real edges
 * target the local ids it provides (see `providedNodeIds`) — so orphan and
 * no-producer connectivity checks treat those provided ids as though they
 * belonged to the include node itself.
 *
 * Returns an empty array when the workflow is valid.
 */
export function validateWorkflowFull(
  workflow: Workflow,
  _modelCatalog?: Record<string, WorkflowModelCatalogEntry>,
  workflowCatalog?: readonly Workflow[],
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
      case 'include':
        return 'Include';
    }
  };

  /**
   * Ids that count as "this node" for connectivity purposes — a node's own
   * id for every kind except `include`, whose provided local ids (the ids a
   * pinned workflow's stages/gates take in this graph) are the ones a real
   * edge actually names. Without this, every correctly authored include node
   * would read as orphaned: nothing in `edges` ever targets its own id.
   */
  function connectionIds(node: WorkflowNode): string[] {
    return node.kind === 'include' ? [node.id, ...Object.values(node.nodes ?? {})] : [node.id];
  }

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
      const ids = connectionIds(node);
      const hasIn = edges.some((e) => ids.includes(e.target));
      const hasOut = edges.some((e) => ids.includes(e.source));
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

  // An include inlines another workflow's stage/gate nodes verbatim at
  // freeze time, under locally chosen ids. It must pin a real workflow
  // dependency, map at least one node, and use local ids that don't collide
  // with anything else in this document — a collision would silently
  // rebind an edge meant for one node onto another once the include is
  // expanded.
  const includeLocalIdOwners = new Map<string, string[]>();
  for (const node of nodes) {
    if (node.kind !== 'include') continue;

    const alias = node.workflow?.trim() ?? '';
    if (!alias || !workflow.workflowDependencies?.[alias]) {
      issues.push({
        kind: 'include',
        nodeId: node.id,
        message: 'Include must reference a declared workflow dependency',
        severity: 'error',
      });
    }

    const mappings = Object.entries(node.nodes ?? {});
    if (mappings.length === 0) {
      issues.push({
        kind: 'include',
        nodeId: node.id,
        message: 'Include must map at least one node from the included workflow',
        severity: 'error',
      });
    }

    const seenLocalIds = new Set<string>();
    for (const [, localId] of mappings) {
      if (seenLocalIds.has(localId)) {
        issues.push({
          kind: 'include',
          nodeId: node.id,
          message: `Local id '${localId}' is mapped more than once`,
          severity: 'error',
        });
      }
      seenLocalIds.add(localId);
      includeLocalIdOwners.set(localId, [...(includeLocalIdOwners.get(localId) ?? []), node.id]);
    }

    // Cross-check against the pinned workflow's own graph only when it's
    // actually loaded in the editor's catalog — an unloaded dependency is a
    // fact about the environment, not a defect in this document, so it must
    // never raise this issue on its own.
    const dependency = alias ? workflow.workflowDependencies?.[alias] : undefined;
    const catalogChild = dependency
      ? workflowCatalog?.find((candidate) => candidate.id === dependency.id)
      : undefined;
    if (catalogChild) {
      const catalogNodeKinds = new Map(
        catalogChild.nodes.map((candidate) => [candidate.id, candidate.kind]),
      );
      for (const [sourceId] of mappings) {
        const sourceKind = catalogNodeKinds.get(sourceId);
        if (sourceKind !== 'stage' && sourceKind !== 'gate') {
          issues.push({
            kind: 'include',
            nodeId: node.id,
            message: `Included node '${sourceId}' is not a stage or gate in the pinned workflow`,
            severity: 'error',
          });
        }
      }
    }
  }

  const realNodeIds = new Set(nodes.map((node) => node.id));
  for (const [localId, owners] of includeLocalIdOwners) {
    const uniqueOwners = [...new Set(owners)];
    if (realNodeIds.has(localId)) {
      for (const ownerId of uniqueOwners) {
        issues.push({
          kind: 'include',
          nodeId: ownerId,
          message: `Local id '${localId}' collides with an existing node id`,
          severity: 'error',
        });
      }
    }
    if (uniqueOwners.length > 1) {
      for (const ownerId of uniqueOwners) {
        issues.push({
          kind: 'include',
          nodeId: ownerId,
          message: `Local id '${localId}' is used by more than one include`,
          severity: 'error',
        });
      }
    }
  }

  // ── 7. No-producer ────────────────────────────────────────────────────────
  // Gates, conditions, and terminal nodes should have at least one inbound connection.
  if (nodes.length > 1) {
    for (const node of nodes) {
      if (node.kind === 'trigger' || node.kind === 'stage' || node.kind === 'resource') continue;
      const hasIn = edges.some((e) => connectionIds(node).includes(e.target));
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
