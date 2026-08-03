/**
 * Edge layers — the readable groupings of the relationship taxonomy.
 *
 * `EdgeRelationType` has ten values, which is too many to expose as ten
 * toggles. They collapse into six layers that answer distinct questions:
 * who collaborates, what remembers, what runs inference, what holds the
 * platform together, what watches it, and what the physical world reports.
 *
 * The mapping is exhaustive over EdgeRelationType, so a relation added to the
 * domain must be placed in a layer here or the compiler rejects it.
 */

import type { EdgeRelationType, TopologyEdge } from './index';

export type EdgeLayer = 'mesh' | 'memory' | 'inference' | 'platform' | 'observability' | 'signals';

export const EDGE_LAYERS: readonly EdgeLayer[] = [
  'mesh',
  'memory',
  'inference',
  'platform',
  'observability',
  'signals',
] as const;

/**
 * Chip labels. Short on purpose: seven of these plus the compute chips share
 * one strip, and "Mímir memory" next to "Model calls" reads as two phrases
 * rather than two switches.
 */
export const EDGE_LAYER_LABELS: Readonly<Record<EdgeLayer, string>> = {
  mesh: 'Agent mesh',
  memory: 'Mímir',
  inference: 'Models',
  platform: 'Platform',
  observability: 'Telemetry',
  signals: 'Signals',
};

const RELATION_LAYER: Readonly<Record<EdgeRelationType, EdgeLayer>> = {
  member_of: 'mesh',
  reads: 'memory',
  writes: 'memory',
  routes_to: 'inference',
  // The two halves of one journey. `uses` is an agent reaching its model
  // gateway and `routes_to` is that gateway reaching the model, so splitting
  // them across layers meant the calm view drew the model being called and
  // hid who called it — which is the gap the caller edge exists to close.
  uses: 'inference',
  observes: 'observability',
  signals_to: 'signals',
  contains: 'platform',
  manages: 'platform',
  exposes: 'platform',
};

/**
 * The layer an edge belongs to.
 *
 * An edge with no declared relation is platform wiring: that is what the
 * majority of undeclared structural links turn out to be, and hiding them
 * behind a layer nobody enables would make them silently invisible.
 */
export function edgeLayer(edge: Pick<TopologyEdge, 'relationType'>): EdgeLayer {
  if (!edge.relationType) return 'platform';
  return RELATION_LAYER[edge.relationType];
}

/** Edges surviving the current layer filter. */
export function visibleEdges<T extends Pick<TopologyEdge, 'relationType'>>(
  edges: readonly T[],
  hiddenLayers: ReadonlySet<EdgeLayer>,
): T[] {
  if (hiddenLayers.size === 0) return [...edges];
  return edges.filter((edge) => !hiddenLayers.has(edgeLayer(edge)));
}

/** How many edges sit in each layer, for the filter counts. */
export function countEdgesByLayer(
  edges: readonly Pick<TopologyEdge, 'relationType'>[],
): Record<EdgeLayer, number> {
  const counts = Object.fromEntries(EDGE_LAYERS.map((layer) => [layer, 0])) as Record<
    EdgeLayer,
    number
  >;
  for (const edge of edges) counts[edgeLayer(edge)] += 1;
  return counts;
}
