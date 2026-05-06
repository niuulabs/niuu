/**
 * Full semantic validation for Workflow DAGs.
 *
 * `validateWorkflowFull` goes beyond the structural checks in `validateWorkflow`
 * (which only enforces schema invariants) to catch semantic issues that would
 * cause a workflow to fail at runtime.
 *
 * Owner: plugin-tyr.
 */

import type { Workflow } from './workflow';
import { detectCycle } from './topologicalSort';

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
  | 'no_producer'
  | 'no_consumer';

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
 * 1. **cycle** — directed cycle exists; every participating node gets an issue.
 * 2. **orphan** — node has no edges at all (workflow has >1 node).
 * 3. **dangling_condition** — `cond` node has fewer than 2 outgoing edges.
 * 4. **confidence_underset** — when any stage is raid-mapped, other `stage`
 *    nodes without `raidId` are treated as unplanned work.
 * 5. **missing_persona** — `stage` node has an empty `personaIds` array.
 * 6. **no_producer** — `gate`/`cond` node has no incoming edges.
 * 7. **no_consumer** — `stage` node has no outgoing edges (non-singleton workflow).
 *
 * Returns an empty array when the workflow is valid.
 */
export function validateWorkflowFull(workflow: Workflow): WorkflowIssue[] {
  const issues: WorkflowIssue[] = [];
  const { nodes, edges } = workflow;
  const kindLabel = (kind: Workflow['nodes'][number]['kind']) => {
    switch (kind) {
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
    }
  };

  // ── 1. Cycle detection ────────────────────────────────────────────────────
  const cycleNodeIds = detectCycle(
    nodes.map((n) => n.id),
    edges,
  );
  for (const nodeId of cycleNodeIds) {
    issues.push({
      kind: 'cycle',
      nodeId,
      message: 'Node is part of a directed cycle',
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
  const hasMappedRaidStages = stages.some((node) => Boolean(node.raidId));
  for (const node of stages) {
    if (!hasMappedRaidStages || node.raidId) continue;
    issues.push({
      kind: 'confidence_underset',
      nodeId: node.id,
      message: 'Stage has no raid assigned — work is unplanned',
      severity: 'warning',
    });
  }

  // ── 5. Missing personas ───────────────────────────────────────────────────
  for (const node of nodes) {
    if (node.kind !== 'stage') continue;
    if ((node.personaIds ?? []).length === 0) {
      issues.push({
        kind: 'missing_persona',
        nodeId: node.id,
        message: 'Stage has no personas assigned',
        severity: 'warning',
      });
    }
  }

  // ── 6. No-producer ────────────────────────────────────────────────────────
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

  // ── 7. No-consumer ────────────────────────────────────────────────────────
  // A stage node with no outgoing edges in a multi-node workflow is a dead end.
  if (nodes.length > 1) {
    for (const node of nodes) {
      if (node.kind !== 'stage') continue;
      const hasOut = edges.some((e) => e.source === node.id);
      if (!hasOut) {
        issues.push({
          kind: 'no_consumer',
          nodeId: node.id,
          message: 'Stage has no outgoing connection',
          severity: 'warning',
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
