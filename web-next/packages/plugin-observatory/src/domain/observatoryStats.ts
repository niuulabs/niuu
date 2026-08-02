import type { Topology, TopologyNode } from './index';
import { deriveAgentMeshes } from './agentMesh';

/**
 * Counts for the topbar readout.
 *
 * `null` means "not discovered", which is rendered as a dash rather than a
 * zero — an estate with no pod data is not an estate with no pods, and the
 * readout is the first thing an operator trusts.
 */
export interface ObservatoryStats {
  realms: number | null;
  clusters: number | null;
  hosts: number | null;
  pods: number | null;
  residents: number | null;
  meshes: number | null;
  mimirs: number | null;
}

const RESIDENT_TYPES: ReadonlySet<string> = new Set(['ravn_long', 'valkyrie', 'resident']);

function countOf(nodes: readonly TopologyNode[], typeId: string): number {
  return nodes.filter((node) => node.typeId === typeId).length;
}

/** Sum a numeric field that adapters attach per host, when they attach it. */
function sumMetric(nodes: readonly TopologyNode[], field: string): number | null {
  const values = nodes
    .map((node) => (node as unknown as Record<string, unknown>)[field])
    .filter((value): value is number => typeof value === 'number');
  return values.length ? values.reduce((total, value) => total + value, 0) : null;
}

export function deriveObservatoryStats(topology: Topology | null): ObservatoryStats {
  // Before a snapshot arrives there is nothing to count. Reporting zeros then
  // states an empty estate, which is the one thing the readout must not do
  // while it is still waiting.
  if (!topology) {
    return {
      realms: null,
      clusters: null,
      hosts: null,
      pods: null,
      residents: null,
      meshes: null,
      mimirs: null,
    };
  }

  const nodes = topology.nodes;
  return {
    realms: countOf(nodes, 'realm'),
    clusters: countOf(nodes, 'cluster'),
    hosts: countOf(nodes, 'host'),
    pods: sumMetric(nodes, 'pods'),
    residents: nodes.filter((node) => RESIDENT_TYPES.has(node.typeId)).length,
    meshes: deriveAgentMeshes(topology).length,
    mimirs: countOf(nodes, 'mimir'),
  };
}

/** Nodes of one type, ordered for a stable rail listing. */
export function nodesOfType(topology: Topology | null, typeIds: readonly string[]): TopologyNode[] {
  const wanted = new Set(typeIds);
  return (topology?.nodes ?? [])
    .filter((node) => wanted.has(node.typeId))
    .sort((a, b) => a.label.localeCompare(b.label));
}

export const RESIDENT_TYPE_IDS = ['ravn_long', 'valkyrie', 'resident'] as const;
