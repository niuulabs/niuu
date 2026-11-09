import type { WorkflowEdge, WorkflowNode } from './workflow';
import { estimateWorkflowNodeSize } from './workflowGeometry';
import { layoutWorkflow } from './workflowLayout';

/**
 * A stored workflow graph placed for the canvas: nodes without a position are laid out, and
 * edges without control points get the curve the builder draws. The same placement whether
 * the graph is a workflow being edited or the snapshot a run was launched with.
 */
export function placeWorkflowGraph(
  rawNodes: readonly WorkflowNode[],
  rawEdges: readonly WorkflowEdge[],
): { nodes: WorkflowNode[]; edges: WorkflowEdge[] } {
  const nodeSizes = new Map(
    rawNodes.map((node) => [node.id, estimateWorkflowNodeSize(node, { edges: [...rawEdges] })]),
  );
  const initialLayout = layoutWorkflow(
    rawNodes.map((node) => node.id),
    [...rawEdges],
    // The canvas routes feedback below cards; reserve no empty lanes above them.
    { originX: 96, originY: 64, nodeSizes, columnGap: 72, rowGap: 36, feedbackLaneSpacing: 0 },
  );
  const nodes = rawNodes.map((node, index) => ({
    ...node,
    position: node.position ??
      initialLayout.positions.get(node.id) ?? { x: 96, y: 144 + index * 160 },
  }));

  const positions = new Map(nodes.map((node) => [node.id, node.position]));
  const edges = rawEdges.map((edge) => {
    if (edge.cp1 && edge.cp2) {
      return edge;
    }

    const source = positions.get(edge.source);
    const target = positions.get(edge.target);
    const isMostlyHorizontal =
      source && target ? Math.abs(target.x - source.x) >= Math.abs(target.y - source.y) : true;

    return {
      ...edge,
      cp1: edge.cp1 ?? (isMostlyHorizontal ? { x: 92, y: 0 } : { x: 0, y: 92 }),
      cp2: edge.cp2 ?? (isMostlyHorizontal ? { x: -92, y: 0 } : { x: 0, y: -92 }),
    };
  });
  return { nodes, edges };
}
