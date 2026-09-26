/**
 * Pure visibility/lighting rules for the memory scene.
 *
 * Turns a graph plus the scene's mode props (hidden groups, focus, answers,
 * path, the `asOf` replay cursor, disputed ids) into a per-node and
 * per-edge description of what to draw and how lit it is. Nothing here
 * touches three.js or the DOM — `MemoryScene` reads this to build meshes
 * and overlays, and every rule is exercised directly in tests.
 */

import type { GraphEdge, GraphNode, MimirGraph } from '../../domain/api-types';
import { kindGroup, type KindGroup } from '../../domain/memoryKinds';
import { BORN_WINDOW_MS } from './scene3dConfig';
import { relationLabel } from './relationLabel';
import type { SceneAnswer, SceneFocus } from './types';

export type LitLevel = 'lit' | 'dim-strong' | 'dim-soft' | 'normal';

export interface NodeVisibility {
  id: string;
  visible: boolean;
  litLevel: LitLevel;
  bornRing: boolean;
  answerNumber: number | null;
  disputed: boolean;
}

export interface EdgeVisibility {
  key: string;
  source: string;
  target: string;
  type: string | undefined;
  visible: boolean;
  litLevel: LitLevel;
  contradiction: boolean;
  label: string | null;
  /** 1-based position in `path` when this edge is a step of it, else null. */
  pathOrder: number | null;
}

export interface SceneVisibility {
  nodes: Map<string, NodeVisibility>;
  edges: EdgeVisibility[];
  /** True when focus/answers/path put the scene in "spotlight" mode. */
  hasSpotlight: boolean;
}

export interface VisibilityInput {
  hiddenGroups?: ReadonlySet<KindGroup> | readonly KindGroup[];
  focus?: SceneFocus | null;
  answers?: readonly SceneAnswer[];
  path?: readonly string[] | null;
  asOf?: string | null;
  disputedIds?: ReadonlySet<string> | readonly string[];
}

function isContradictionType(type: string | undefined): boolean {
  return type === 'contradicts' || type === 'disagrees_with' || type === 'conflicts_with';
}

function buildAdjacency(
  nodeIds: ReadonlySet<string>,
  edges: readonly GraphEdge[],
): Map<string, string[]> {
  const adjacency = new Map<string, string[]>();
  for (const edge of edges) {
    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) continue;
    (adjacency.get(edge.source) ?? adjacency.set(edge.source, []).get(edge.source)!).push(
      edge.target,
    );
    (adjacency.get(edge.target) ?? adjacency.set(edge.target, []).get(edge.target)!).push(
      edge.source,
    );
  }
  return adjacency;
}

/** Node ids within `depth` hops of `startId`, including `startId` itself. */
function bfsWithinDepth(
  startId: string,
  depth: number,
  adjacency: Map<string, string[]>,
): Set<string> {
  const visited = new Set<string>([startId]);
  let frontier = [startId];
  for (let hop = 0; hop < depth && frontier.length > 0; hop += 1) {
    const next: string[] = [];
    for (const id of frontier) {
      for (const neighbour of adjacency.get(id) ?? []) {
        if (!visited.has(neighbour)) {
          visited.add(neighbour);
          next.push(neighbour);
        }
      }
    }
    frontier = next;
  }
  return visited;
}

function isNodeHidden(
  node: GraphNode,
  hiddenGroups: ReadonlySet<KindGroup>,
  asOfMs: number | null,
): boolean {
  if (hiddenGroups.has(kindGroup(node.kind))) return true;
  if (asOfMs === null) return false;
  if (!node.firstSeen) return false;
  const firstSeenMs = Date.parse(node.firstSeen);
  if (Number.isNaN(firstSeenMs)) return false;
  return firstSeenMs > asOfMs;
}

function isBornRing(node: GraphNode, asOfMs: number | null): boolean {
  if (asOfMs === null || !node.firstSeen) return false;
  const firstSeenMs = Date.parse(node.firstSeen);
  if (Number.isNaN(firstSeenMs)) return false;
  const age = asOfMs - firstSeenMs;
  return age >= 0 && age <= BORN_WINDOW_MS;
}

/** Compute per-node/edge visibility and lighting for one frame of props. */
export function computeVisibility(graph: MimirGraph, input: VisibilityInput): SceneVisibility {
  const hiddenGroups = new Set(input.hiddenGroups ?? []);
  const asOfMs = input.asOf ? Date.parse(input.asOf) : null;
  const disputedIds = new Set(input.disputedIds ?? []);
  const answerByNode = new Map((input.answers ?? []).map((a) => [a.nodeId, a.n]));
  const path = input.path ?? [];
  const pathSet = new Set(path);

  const visibleIds = new Set<string>();
  const nodes = new Map<string, NodeVisibility>();

  for (const node of graph.nodes) {
    const hidden = isNodeHidden(node, hiddenGroups, asOfMs);
    if (!hidden) visibleIds.add(node.id);
    nodes.set(node.id, {
      id: node.id,
      visible: !hidden,
      litLevel: 'normal',
      bornRing: !hidden && isBornRing(node, asOfMs),
      answerNumber: answerByNode.get(node.id) ?? null,
      disputed: disputedIds.has(node.id),
    });
  }

  const adjacency = buildAdjacency(visibleIds, graph.edges);

  const focusSet =
    input.focus && visibleIds.has(input.focus.nodeId)
      ? bfsWithinDepth(input.focus.nodeId, Math.max(0, input.focus.depth), adjacency)
      : null;
  const answerSet =
    input.answers && input.answers.length > 0
      ? new Set(input.answers.map((a) => a.nodeId).filter((id) => visibleIds.has(id)))
      : null;
  const pathVisibleSet = path.length > 0 ? new Set(path.filter((id) => visibleIds.has(id))) : null;

  const hasSpotlight = Boolean(focusSet || answerSet || pathVisibleSet);
  const strongDim = Boolean(focusSet);

  const litSet = new Set<string>([
    ...(focusSet ?? []),
    ...(answerSet ?? []),
    ...(pathVisibleSet ?? []),
  ]);

  if (hasSpotlight) {
    for (const [id, visibility] of nodes) {
      if (!visibility.visible) continue;
      visibility.litLevel = litSet.has(id) ? 'lit' : strongDim ? 'dim-strong' : 'dim-soft';
    }
  }

  const edges: EdgeVisibility[] = graph.edges.map((edge, index) => {
    const bothVisible = visibleIds.has(edge.source) && visibleIds.has(edge.target);
    const contradiction = isContradictionType(edge.type);

    let pathOrder: number | null = null;
    if (pathSet.has(edge.source) && pathSet.has(edge.target)) {
      const sourceIndex = path.indexOf(edge.source);
      const targetIndex = path.indexOf(edge.target);
      if (sourceIndex >= 0 && targetIndex === sourceIndex + 1) pathOrder = sourceIndex + 1;
      else if (targetIndex >= 0 && sourceIndex === targetIndex + 1) pathOrder = targetIndex + 1;
    }

    const litByFocus = Boolean(focusSet && focusSet.has(edge.source) && focusSet.has(edge.target));
    const litByAnswers = Boolean(
      answerSet && answerSet.has(edge.source) && answerSet.has(edge.target),
    );
    const litByPath = pathOrder !== null;
    const lit = bothVisible && (litByFocus || litByAnswers || litByPath);

    let litLevel: LitLevel = 'normal';
    if (hasSpotlight && bothVisible) {
      litLevel = lit ? 'lit' : strongDim ? 'dim-strong' : 'dim-soft';
    }

    return {
      key: `${edge.source}->${edge.target}#${index}`,
      source: edge.source,
      target: edge.target,
      type: edge.type,
      visible: bothVisible,
      litLevel,
      contradiction,
      label: lit ? relationLabel(edge.type) : null,
      pathOrder,
    };
  });

  return { nodes, edges, hasSpotlight };
}
