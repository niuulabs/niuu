/**
 * Pure utility functions for the WorkflowBuilder graph canvas.
 *
 * No React imports — these are plain functions used by both components and tests.
 *
 * Owner: plugin-ting (WorkflowBuilder).
 */

import type {
  Workflow,
  WorkflowNode,
  WorkflowEdge,
  WorkflowStageNode,
} from '../../domain/workflow';
import { serializePortableWorkflow } from '../../domain/workflowPortable';

// ---------------------------------------------------------------------------
// Node geometry constants
// ---------------------------------------------------------------------------

// 224, not 172: the redesigned stage card (GraphView's StageNode) needs room
// for a typed port footer with real event-type text. Every consumer of this
// constant (edgeAnchor, findStageAtPoint, drop-to-create hit-testing) already
// parametrizes on it, so this is the only geometry constant the new card
// treatment changes — stageNodeHeight/renderedStageHeight's formulas are
// unchanged.
export const STAGE_WIDTH = 224;
export const STAGE_HEIGHT = 92;
export const GATE_SIZE = 76; // diamond bounding box
export const COND_RADIUS = 34; // circle radius
export const TRIGGER_WIDTH = 168;
export const TRIGGER_HEIGHT = 58;
export const WAIT_WIDTH = 168;
export const WAIT_HEIGHT = 58;
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
    case 'subworkflow':
      return { x: node.position.x + TRIGGER_WIDTH / 2, y: node.position.y + TRIGGER_HEIGHT / 2 };
    case 'wait':
      return { x: node.position.x + WAIT_WIDTH / 2, y: node.position.y + WAIT_HEIGHT / 2 };
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
export function workflowToYaml(workflow: Workflow): string {
  return serializePortableWorkflow(workflow);
}
