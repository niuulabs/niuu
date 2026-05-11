/**
 * Pure utility functions for the WorkflowBuilder graph canvas.
 *
 * No React imports — these are plain functions used by both components and tests.
 *
 * Owner: plugin-tyr (WorkflowBuilder).
 */

import type {
  Workflow,
  WorkflowNode,
  WorkflowEdge,
  WorkflowStageNode,
} from '../../domain/workflow';
import { stagePersonaIds } from '../../domain/workflowSemantics';

// ---------------------------------------------------------------------------
// Node geometry constants
// ---------------------------------------------------------------------------

export const STAGE_WIDTH = 172;
export const STAGE_HEIGHT = 92;
export const GATE_SIZE = 76; // diamond bounding box
export const COND_RADIUS = 34; // circle radius
export const TRIGGER_WIDTH = 168;
export const TRIGGER_HEIGHT = 58;
export const END_RADIUS = 26;
export const RESOURCE_WIDTH = 168;
export const RESOURCE_HEIGHT = 58;

/** Default bezier control-point offset (pixels). */
const CP_OFFSET = 92;

export function normalizedStageMembers(node: WorkflowStageNode) {
  if (node.stageMembers && node.stageMembers.length > 0) {
    return node.stageMembers.map((member) => ({
      personaId: member.personaId,
      model: member.model ?? '',
      budget: member.budget ?? 40,
      consumesEventTypes: member.consumesEventTypes ?? [],
      eventFilters: member.eventFilters ?? {},
    }));
  }
  return (node.personaIds ?? []).map((personaId) => ({
    personaId,
    model: '',
    budget: 40,
    consumesEventTypes: [],
    eventFilters: {},
  }));
}

// ---------------------------------------------------------------------------
// ID generation
// ---------------------------------------------------------------------------

/** Generate a short collision-resistant node ID. */
export function makeNodeId(): string {
  return `node-${Math.random().toString(36).slice(2, 9)}`;
}

/** Generate a short collision-resistant edge ID. */
export function makeEdgeId(): string {
  return `edge-${Math.random().toString(36).slice(2, 9)}`;
}

// ---------------------------------------------------------------------------
// Bezier helpers
// ---------------------------------------------------------------------------

/**
 * Compute sensible default bezier control points given source and target positions.
 *
 * Control points are stored *relative to the anchor node* as required by the schema:
 *   cp1 relative to source centre
 *   cp2 relative to target centre
 *
 * Produces a smooth S-curve for horizontal-ish connections and a vertical
 * S-curve for vertical-ish connections.
 */
export function defaultBezierCPs(
  source: { x: number; y: number },
  target: { x: number; y: number },
): { cp1: { x: number; y: number }; cp2: { x: number; y: number } } {
  const dx = target.x - source.x;
  const dy = target.y - source.y;
  const isMoreHorizontal = Math.abs(dx) >= Math.abs(dy);

  if (isMoreHorizontal) {
    return {
      cp1: { x: CP_OFFSET, y: 0 },
      cp2: { x: -CP_OFFSET, y: 0 },
    };
  }
  return {
    cp1: { x: 0, y: CP_OFFSET },
    cp2: { x: 0, y: -CP_OFFSET },
  };
}

// ---------------------------------------------------------------------------
// Node centre helpers
// ---------------------------------------------------------------------------

export function stageNodeHeight(node: WorkflowStageNode): number {
  const memberCount = Math.max(normalizedStageMembers(node).length, 1);
  return STAGE_HEIGHT + Math.max(0, memberCount - 1) * 22;
}

/** Return the centre (x, y) of a node for edge anchoring. */
export function nodeCentre(node: WorkflowNode): { x: number; y: number } {
  switch (node.kind) {
    case 'stage':
      return {
        x: node.position.x + STAGE_WIDTH / 2,
        y: node.position.y + stageNodeHeight(node) / 2,
      };
    case 'gate':
      return { x: node.position.x + GATE_SIZE / 2, y: node.position.y + GATE_SIZE / 2 };
    case 'cond':
      return { x: node.position.x + COND_RADIUS, y: node.position.y + COND_RADIUS };
    case 'trigger':
      return { x: node.position.x + TRIGGER_WIDTH / 2, y: node.position.y + TRIGGER_HEIGHT / 2 };
    case 'end':
      return { x: node.position.x + END_RADIUS, y: node.position.y + END_RADIUS };
    case 'resource':
      return {
        x: node.position.x + RESOURCE_WIDTH / 2,
        y: node.position.y + RESOURCE_HEIGHT / 2,
      };
  }
}

// ---------------------------------------------------------------------------
// SVG path builder
// ---------------------------------------------------------------------------

/**
 * Build an SVG cubic bezier path string for an edge.
 *
 * @param edge  The edge with cp1/cp2 relative offsets.
 * @param nodes Map of node-id → WorkflowNode for position lookup.
 */
export function edgeToPath(edge: WorkflowEdge, nodes: Map<string, WorkflowNode>): string | null {
  const src = nodes.get(edge.source);
  const tgt = nodes.get(edge.target);
  if (!src || !tgt) return null;

  const s = nodeCentre(src);
  const t = nodeCentre(tgt);

  const c1x = s.x + edge.cp1.x;
  const c1y = s.y + edge.cp1.y;
  const c2x = t.x + edge.cp2.x;
  const c2y = t.y + edge.cp2.y;

  return `M ${s.x} ${s.y} C ${c1x} ${c1y}, ${c2x} ${c2y}, ${t.x} ${t.y}`;
}

// ---------------------------------------------------------------------------
// YAML serialiser
// ---------------------------------------------------------------------------

/**
 * Serialise a Workflow to a human-readable YAML string.
 *
 * The output is deterministic — keys are in a fixed meaningful order.
 */
export function workflowToYaml(
  workflow: Pick<
    Workflow,
    'id' | 'name' | 'nodes' | 'edges' | 'resourceBindings'
  >,
): string {
  const lines: string[] = [];

  lines.push(`id: ${JSON.stringify(workflow.id)}`);
  lines.push(`name: ${JSON.stringify(workflow.name)}`);

  if (workflow.nodes.length === 0) {
    lines.push('nodes: []');
  } else {
    lines.push('nodes:');
    for (const node of workflow.nodes) {
      lines.push(`  - id: ${JSON.stringify(node.id)}`);
      lines.push(`    kind: ${node.kind}`);
      lines.push(`    label: ${JSON.stringify(node.label)}`);
      if (node.kind === 'stage') {
        const stageMembers = normalizedStageMembers(node);
        const personaIds = stagePersonaIds(node);
        lines.push(`    raidId: ${node.raidId === null ? 'null' : JSON.stringify(node.raidId)}`);
        lines.push(
          `    personaIds: ${personaIds.length === 0 ? '[]' : `[${personaIds.map((p) => JSON.stringify(p)).join(', ')}]`}`,
        );
        if (stageMembers.length > 0) {
          lines.push('    stageMembers:');
          for (const member of stageMembers) {
            const parts = [
              `personaId: ${JSON.stringify(member.personaId)}`,
              `model: ${JSON.stringify(member.model ?? '')}`,
              `budget: ${member.budget}`,
            ];
            if ((member.consumesEventTypes ?? []).length > 0) {
              parts.push(
                `consumesEventTypes: [${member.consumesEventTypes.map((event) => JSON.stringify(event)).join(', ')}]`,
              );
            }
            if (member.eventFilters && Object.keys(member.eventFilters).length > 0) {
              parts.push(
                `eventFilters: {${Object.entries(member.eventFilters)
                  .map(([key, value]) => `${key}: ${JSON.stringify(value)}`)
                  .join(', ')}}`,
              );
            }
            lines.push(`      - {${parts.join(', ')}}`);
          }
        }
        lines.push(`    executionMode: ${node.executionMode ?? 'parallel'}`);
        lines.push(`    maxConcurrent: ${node.maxConcurrent ?? 3}`);
        lines.push(`    joinMode: ${node.joinMode ?? 'all'}`);
      }
      if (node.kind === 'gate') {
        lines.push(`    condition: ${JSON.stringify(node.condition)}`);
        lines.push(
          `    approvers: ${(node.approvers ?? []).length === 0 ? '[]' : `[${(node.approvers ?? []).map((a) => JSON.stringify(a)).join(', ')}]`}`,
        );
        lines.push(`    autoForwardAfter: ${JSON.stringify(node.autoForwardAfter ?? '30m')}`);
      }
      if (node.kind === 'cond') {
        lines.push(`    predicate: ${JSON.stringify(node.predicate)}`);
      }
      if (node.kind === 'trigger') {
        lines.push(`    source: ${JSON.stringify(node.source ?? 'manual dispatch')}`);
        lines.push(`    dispatchEvent: ${JSON.stringify(node.dispatchEvent ?? 'code.requested')}`);
      }
      if (node.kind === 'resource') {
        lines.push(`    resourceType: ${JSON.stringify(node.resourceType ?? 'mimir')}`);
        lines.push(`    bindingMode: ${JSON.stringify(node.bindingMode ?? 'registry')}`);
        lines.push(
          `    registryEntryId: ${node.registryEntryId === null ? 'null' : JSON.stringify(node.registryEntryId)}`,
        );
        lines.push(
          `    seedFromRegistryId: ${node.seedFromRegistryId === null ? 'null' : JSON.stringify(node.seedFromRegistryId)}`,
        );
        lines.push(
          `    categories: ${(node.categories ?? []).length === 0 ? '[]' : `[${(node.categories ?? []).map((cat) => JSON.stringify(cat)).join(', ')}]`}`,
        );
        if (node.path) {
          lines.push(`    path: ${JSON.stringify(node.path)}`);
        }
        if (node.url) {
          lines.push(`    url: ${JSON.stringify(node.url)}`);
        }
        if (node.role) {
          lines.push(`    role: ${JSON.stringify(node.role)}`);
        }
        if (node.authRef) {
          lines.push(`    authRef: ${JSON.stringify(node.authRef)}`);
        }
        if (typeof node.defaultReadPriority === 'number') {
          lines.push(`    defaultReadPriority: ${node.defaultReadPriority}`);
        }
      }
      lines.push(`    position: {x: ${node.position.x}, y: ${node.position.y}}`);
    }
  }

  if (workflow.edges.length === 0) {
    lines.push('edges: []');
  } else {
    lines.push('edges:');
    for (const edge of workflow.edges) {
      lines.push(`  - id: ${JSON.stringify(edge.id)}`);
      lines.push(`    source: ${JSON.stringify(edge.source)}`);
      lines.push(`    target: ${JSON.stringify(edge.target)}`);
      if (edge.label !== undefined) {
        lines.push(`    label: ${JSON.stringify(edge.label)}`);
      }
      lines.push(`    cp1: {x: ${edge.cp1.x}, y: ${edge.cp1.y}}`);
      lines.push(`    cp2: {x: ${edge.cp2.x}, y: ${edge.cp2.y}}`);
    }
  }

  const resourceBindings = workflow.resourceBindings ?? [];
  if (resourceBindings.length === 0) {
    lines.push('resourceBindings: []');
  } else {
    lines.push('resourceBindings:');
    for (const binding of resourceBindings) {
      lines.push(`  - id: ${JSON.stringify(binding.id)}`);
      lines.push(`    resourceNodeId: ${JSON.stringify(binding.resourceNodeId)}`);
      lines.push(`    targetType: ${binding.targetType}`);
      lines.push(`    targetId: ${JSON.stringify(binding.targetId)}`);
      lines.push(`    access: ${binding.access}`);
      lines.push(
        `    writePrefixes: ${(binding.writePrefixes ?? []).length === 0 ? '[]' : `[${(binding.writePrefixes ?? []).map((prefix: string) => JSON.stringify(prefix)).join(', ')}]`}`,
      );
      lines.push(`    readPriority: ${binding.readPriority}`);
    }
  }

  return lines.join('\n');
}
