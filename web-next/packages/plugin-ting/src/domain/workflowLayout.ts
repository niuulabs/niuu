/**
 * Auto-layout for workflow DAGs.
 *
 * Turns a node/edge set into `{x, y}` positions, for graphs the author never
 * hand-positioned — which, in practice, is every workflow generated or
 * migrated outside the editor (Ting workflow YAML carries no `position:`
 * field at all).
 *
 * Layering reuses `topologicalSort()` / `structuralWorkflowEdges()` — the
 * same primitives `PipelineView` already uses for its read-only column
 * layout — so a re-entry edge (`isReentryEdge`, e.g. a gate's
 * `changes_requested` loop back into the stage it gates) never pulls its
 * source backwards or registers as a structural cycle.
 *
 * Owner: plugin-ting.
 */

import type { WorkflowEdge } from './workflow';
import { isReentryEdge, structuralWorkflowEdges } from './workflowSemantics';
import { workflowSizeFor, type WorkflowSize } from './workflowGeometry';
import { topologicalSort } from './topologicalSort';

export interface LayoutPosition {
  x: number;
  y: number;
}

export interface WorkflowLayoutOptions {
  /** Horizontal distance between columns, in pixels. */
  columnSpacing?: number;
  /** Vertical distance between rows within a column, in pixels. */
  rowSpacing?: number;
  originX?: number;
  originY?: number;
  /** Measured rendered sizes. When supplied, columns and rows cannot overlap. */
  nodeSizes?: ReadonlyMap<string, WorkflowSize> | Readonly<Record<string, WorkflowSize>>;
  /** Gap between measured columns. */
  columnGap?: number;
  /** Gap between measured rows. */
  rowGap?: number;
  /** Vertical room reserved above nodes for each feedback edge lane. */
  feedbackLaneSpacing?: number;
}

export interface WorkflowLayoutResult {
  positions: Map<string, LayoutPosition>;
  /** Number of columns actually used (including any overflow column). */
  columns: number;
  /**
   * Nodes placed in the overflow column because they sit in a genuine
   * structural cycle — one that survives re-entry-edge exclusion. Layout
   * still positions them (nothing silently disappears from the canvas),
   * but this list is exactly `detectCycle()`'s "cycle" validation issue and
   * the caller should surface it as an error, not treat placement as
   * resolving it.
   */
  cycleNodeIds: string[];
}

const DEFAULTS = {
  columnSpacing: 320,
  rowSpacing: 170,
  originX: 40,
  originY: 40,
  columnGap: 96,
  rowGap: 36,
  feedbackLaneSpacing: 28,
};

/**
 * Compute deterministic `{x, y}` positions for every node in `nodeIds`.
 *
 * `edges` should be the workflow's full edge list (re-entry edges included);
 * this function filters to `structuralWorkflowEdges()` itself for layering,
 * the same way `PipelineView` does.
 */
export function layoutWorkflow(
  nodeIds: string[],
  edges: ReadonlyArray<WorkflowEdge>,
  options: WorkflowLayoutOptions = {},
): WorkflowLayoutResult {
  const {
    columnSpacing,
    rowSpacing,
    originX,
    originY,
    nodeSizes,
    columnGap,
    rowGap,
    feedbackLaneSpacing,
  } = { ...DEFAULTS, ...options };

  if (nodeIds.length === 0) {
    return { positions: new Map(), columns: 0, cycleNodeIds: [] };
  }

  const structural = structuralWorkflowEdges(edges);
  const layers = topologicalSort(nodeIds, structural);
  const layeredIds = new Set(layers.flatMap((layer) => layer.nodeIds));
  const cycleNodeIds = nodeIds.filter((id) => !layeredIds.has(id)).sort();

  const column = new Map<string, number>();
  for (const layer of layers) {
    for (const id of layer.nodeIds) column.set(id, layer.depth);
  }
  // Cycle nodes get one overflow column past the last real layer, rather
  // than vanishing from the canvas — `cycleNodeIds` is how the caller finds
  // out something is structurally wrong.
  const overflowColumn = layers.length;
  for (const id of cycleNodeIds) column.set(id, overflowColumn);

  const preds = new Map<string, string[]>(nodeIds.map((id) => [id, []]));
  const succs = new Map<string, string[]>(nodeIds.map((id) => [id, []]));
  for (const edge of structural) {
    preds.get(edge.target)?.push(edge.source);
    succs.get(edge.source)?.push(edge.target);
  }
  const totalInDegree = new Map<string, number>(nodeIds.map((id) => [id, 0]));
  for (const edge of edges) {
    totalInDegree.set(edge.target, (totalInDegree.get(edge.target) ?? 0) + 1);
  }

  // A node with zero structural predecessors lands in column 0 by
  // construction (topologicalSort treats it as a root). That's correct for
  // the workflow's actual entry point(s), but wrong for a node reachable
  // only via re-entry edges — e.g. a repair stage fed solely by
  // `changes_requested`/`repair_requested` loops. Left at column 0 it reads
  // as a second entry point next to the trigger. Pull it forward to sit
  // just before whatever it structurally feeds.
  //
  // Iterated to a fixed point (bounded by node count) rather than a single
  // pass, so a chain of such nodes settles correctly; real graphs are small
  // enough that this is cheap.
  for (let pass = 0; pass < nodeIds.length; pass += 1) {
    let changed = false;
    for (const id of nodeIds) {
      const hasStructuralPred = (preds.get(id) ?? []).length > 0;
      const isReentryOnly = !hasStructuralPred && (totalInDegree.get(id) ?? 0) > 0;
      const succColumns = (succs.get(id) ?? []).map((s) => column.get(s) ?? overflowColumn);
      if (!isReentryOnly || succColumns.length === 0) continue;
      const target = Math.max(1, Math.min(...succColumns) - 1);
      if (column.get(id) !== target) {
        column.set(id, target);
        changed = true;
      }
    }
    if (!changed) break;
  }

  // Row assignment: within a column, order nodes by the average row of
  // their already-placed structural predecessors (a simple barycenter
  // heuristic — not full crossing minimization), falling back to
  // lexicographic order for roots and pulled-forward nodes that have none.
  const row = new Map<string, number>();
  const maxColumn = Math.max(...[...column.values()]);
  for (let c = 0; c <= maxColumn; c += 1) {
    const idsInColumn = nodeIds.filter((id) => column.get(id) === c);
    const withKey = idsInColumn.map((id) => {
      const predRows = (preds.get(id) ?? []).filter((p) => row.has(p)).map((p) => row.get(p)!);
      const desired =
        predRows.length > 0 ? predRows.reduce((a, b) => a + b, 0) / predRows.length : Infinity;
      return { id, desired };
    });
    withKey.sort((a, b) =>
      a.desired !== b.desired ? a.desired - b.desired : a.id.localeCompare(b.id),
    );
    withKey.forEach(({ id }, index) => row.set(id, index));
  }

  const feedbackLaneCount = edges.filter(isReentryEdge).length;
  const layoutOriginY = originY + feedbackLaneCount * feedbackLaneSpacing;
  const positions = new Map<string, LayoutPosition>();
  if (!nodeSizes) {
    for (const id of nodeIds) {
      positions.set(id, {
        x: originX + (column.get(id) ?? 0) * columnSpacing,
        y: layoutOriginY + (row.get(id) ?? 0) * rowSpacing,
      });
    }
    return { positions, columns: maxColumn + 1, cycleNodeIds };
  }

  const columnWidths = Array.from({ length: maxColumn + 1 }, () => 0);
  for (const id of nodeIds) {
    const c = column.get(id) ?? 0;
    columnWidths[c] = Math.max(columnWidths[c] ?? 0, workflowSizeFor(nodeSizes, id)?.width ?? 0);
  }
  const columnX: number[] = [originX];
  for (let c = 1; c <= maxColumn; c += 1) {
    columnX[c] = (columnX[c - 1] ?? originX) + (columnWidths[c - 1] ?? 0) + columnGap;
  }
  for (let c = 0; c <= maxColumn; c += 1) {
    const ids = nodeIds
      .filter((id) => (column.get(id) ?? 0) === c)
      .sort((a, b) => (row.get(a) ?? 0) - (row.get(b) ?? 0));
    let y = layoutOriginY;
    for (const id of ids) {
      positions.set(id, { x: columnX[c] ?? originX, y });
      y += (workflowSizeFor(nodeSizes, id)?.height ?? 0) + rowGap;
    }
  }

  return { positions, columns: maxColumn + 1, cycleNodeIds };
}
