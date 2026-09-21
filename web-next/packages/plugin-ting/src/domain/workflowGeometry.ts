import type { WorkflowNode } from './workflow';
import type { WorkflowEdge } from './workflow';
import {
  nodePortCatalog,
  type WorkflowNodePort,
  type WorkflowPortCatalogContext,
} from './workflowPorts';
import { isReentryEdge } from './workflowSemantics';

export interface WorkflowPoint {
  x: number;
  y: number;
}
export interface WorkflowSize {
  width: number;
  height: number;
  /** Optional offset from the top edge where typed port rows begin. */
  portTop?: number;
  /** Optional offset from the bottom edge excluded from typed port rows. */
  portBottom?: number;
}
export interface WorkflowBounds extends WorkflowPoint, WorkflowSize {}

export interface WorkflowGeometryContext {
  measurements?: ReadonlyMap<string, WorkflowSize> | Readonly<Record<string, WorkflowSize>>;
}

const DEFAULT_SIZES: Record<WorkflowNode['kind'], WorkflowSize> = {
  stage: { width: 224, height: 92 },
  gate: { width: 76, height: 76 },
  cond: { width: 68, height: 68 },
  trigger: { width: 168, height: 58 },
  end: { width: 52, height: 52 },
  resource: { width: 168, height: 58 },
  subworkflow: { width: 168, height: 58 },
  wait: { width: 168, height: 58 },
  include: { width: 200, height: 66 },
};

/**
 * Estimate the rendered card size before the canvas has mounted. The formula
 * mirrors the card contract, so imported positionless graphs get collision-free
 * initial layout while measured DOM sizes remain authoritative afterwards.
 */
export function estimateWorkflowNodeSize(
  node: WorkflowNode,
  context: WorkflowPortCatalogContext = {},
): WorkflowSize {
  const base = DEFAULT_SIZES[node.kind];
  const catalog = nodePortCatalog(node, context);
  const rows = Math.max(catalog.inputs.length, catalog.outputs.length);
  if (node.kind === 'stage') {
    const memberCount =
      node.stageMembers && node.stageMembers.length > 0
        ? node.stageMembers.length
        : (node.personaIds?.length ?? 0);
    const members = Math.max(memberCount, 1);
    const headerHeight = base.height + Math.max(0, members - 1) * 22;
    return {
      width: base.width,
      height: headerHeight + (rows > 0 ? 22 + rows * 14 : 0),
      portTop: rows > 0 ? headerHeight + 22 : undefined,
    };
  }
  if (node.kind === 'wait' || node.kind === 'include') {
    return {
      width: base.width,
      height: base.height + (rows > 0 ? 16 + rows * 14 : 0),
      portTop: rows > 0 ? base.height + 16 : undefined,
    };
  }
  return base;
}

export function workflowSizeFor(
  measurements: WorkflowGeometryContext['measurements'],
  nodeId: string,
): WorkflowSize | undefined {
  if (!measurements) return undefined;
  const get = (measurements as ReadonlyMap<string, WorkflowSize>).get;
  if (typeof get === 'function') return get.call(measurements, nodeId);
  return (measurements as Readonly<Record<string, WorkflowSize>>)[nodeId];
}

export function nodeBounds(
  node: WorkflowNode,
  context: WorkflowGeometryContext = {},
): WorkflowBounds {
  const measured = workflowSizeFor(context.measurements, node.id);
  const size =
    measured && measured.width > 0 && measured.height > 0 ? measured : DEFAULT_SIZES[node.kind];
  return { x: node.position.x, y: node.position.y, width: size.width, height: size.height };
}

/** Return the boundary socket centre for a resolved or explicitly unresolved port. */
export function portAnchor(
  node: WorkflowNode,
  port: WorkflowNodePort,
  context: WorkflowGeometryContext = {},
): WorkflowPoint {
  const bounds = nodeBounds(node, context);
  const measured = workflowSizeFor(context.measurements, node.id);
  const portTop = Math.max(0, Math.min(bounds.height, measured?.portTop ?? 0));
  const portBottom = Math.max(0, Math.min(bounds.height - portTop, measured?.portBottom ?? 0));
  const portHeight = bounds.height - portTop - portBottom;
  const count = Math.max(1, port.count);
  const hasMeasuredPortRegion =
    measured?.portTop !== undefined || measured?.portBottom !== undefined;
  const portFraction = hasMeasuredPortRegion
    ? (port.index + 0.5) / count
    : (port.index + 1) / (count + 1);
  return {
    x: port.side === 'left' ? bounds.x : bounds.x + bounds.width,
    y: bounds.y + portTop + portHeight * portFraction,
  };
}

/** Stable outer-lane assignments for feedback edges, independent of input order. */
export function feedbackLaneAssignments(
  edges: readonly WorkflowEdge[],
): ReadonlyMap<string, number> {
  return new Map(
    edges
      .filter(isReentryEdge)
      .map((edge) => edge.id)
      .sort((left, right) => left.localeCompare(right))
      .map((edgeId, index) => [edgeId, index]),
  );
}
