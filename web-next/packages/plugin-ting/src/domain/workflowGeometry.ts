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

/** Every node kind but `end` is drawn as one card of this width. */
export const CARD_WIDTH = 236;
/** The card's head: its icon tile, title and who runs it. */
export const CARD_HEADER_HEIGHT = 58;
/** One row of the card's ports: an incoming event on the left, an outcome on the right. */
export const CARD_PORT_ROW = 22;
/** Room above and below the card's port rows. */
export const CARD_PORT_PAD = 6;
/** The end of a workflow is a small "Done" pill, not a card. */
export const END_CARD_WIDTH = 150;
export const END_CARD_HEIGHT = 40;

/**
 * The size of a node's card with `rows` port rows. Port rows sit under the head, so a
 * socket's centre is `portTop + CARD_PORT_ROW * (index + 0.5)` on its side.
 */
export function workflowCardSize(kind: WorkflowNode['kind'], rows: number): WorkflowSize {
  if (kind === 'end') return { width: END_CARD_WIDTH, height: END_CARD_HEIGHT };
  if (rows <= 0) return { width: CARD_WIDTH, height: CARD_HEADER_HEIGHT };
  return {
    width: CARD_WIDTH,
    height: CARD_HEADER_HEIGHT + CARD_PORT_PAD * 2 + rows * CARD_PORT_ROW,
    portTop: CARD_HEADER_HEIGHT + CARD_PORT_PAD,
    portBottom: CARD_PORT_PAD,
  };
}

/**
 * Estimate the rendered card size before the canvas has mounted, from the same card
 * contract the canvas draws, so a positionless graph lays out without overlaps.
 */
export function estimateWorkflowNodeSize(
  node: WorkflowNode,
  context: WorkflowPortCatalogContext = {},
): WorkflowSize {
  const catalog = nodePortCatalog(node, context);
  return workflowCardSize(node.kind, Math.max(catalog.inputs.length, catalog.outputs.length));
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
    measured && measured.width > 0 && measured.height > 0
      ? measured
      : workflowCardSize(node.kind, 0);
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
