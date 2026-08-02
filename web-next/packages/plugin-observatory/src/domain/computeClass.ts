/**
 * Whose silicon a thing runs on.
 *
 * The three classes answer the question an operator actually asks of this
 * graph — "where is this running, and is it costing me money?" — and they are
 * the axis the canvas colours by, so a glance separates the Kubernetes estate
 * from the hardware in the room from the metered vendors outside.
 *
 * Every class is read off fields the discovery adapters attach, never guessed
 * from a name: `location` comes from Bifröst's provider endpoint (see
 * `_model_location` in `src/observatory/entity_discovery.py`), and `cluster`
 * is set only when the entity was found inside a Kubernetes cluster.
 */

import type { TopologyNode } from './index';

export type ComputeClass = 'k8s' | 'own' | 'outside';

export const COMPUTE_CLASSES: readonly ComputeClass[] = ['k8s', 'own', 'outside'] as const;

export const COMPUTE_CLASS_LABELS: Readonly<Record<ComputeClass, string>> = {
  k8s: 'Niuu realms · k8s',
  own: 'Your own silicon',
  outside: 'Outside · metered',
};

/** Short forms, for the filter chips where the full label will not fit. */
export const COMPUTE_CLASS_SHORT: Readonly<Record<ComputeClass, string>> = {
  k8s: 'k8s realms',
  own: 'own silicon',
  outside: 'outside',
};

function textField(node: TopologyNode, key: string): string {
  const value = (node as unknown as Record<string, unknown>)[key];
  return typeof value === 'string' ? value.toLowerCase() : '';
}

/**
 * Classify a node.
 *
 * A model is decided by where it is served from, because a model node inside a
 * cluster may still be a call out to a vendor. Everything else is decided by
 * placement: no cluster means it was found on a host or reported itself from
 * outside Kubernetes, which is exactly the bare-metal and workstation case the
 * push inbox exists to carry.
 */
export function computeClassOf(
  node: TopologyNode,
  inCluster = Boolean(node.cluster),
): ComputeClass {
  if (node.typeId === 'model') {
    const location = textField(node, 'location');
    if (location === 'external') return 'outside';
    if (location === 'internal') return 'own';
    // Unknown provenance: fall through to placement rather than assert a cost.
  }
  return inCluster ? 'k8s' : 'own';
}

/**
 * Resolve the class of every node at once.
 *
 * Placement cannot be read off a single node: not every adapter stamps
 * `cluster`, but containment always says the same thing — anything nested
 * under a cluster is in that cluster. Walking the parent chain here means a
 * source that omits the field is still placed correctly rather than being
 * reported as bare metal.
 */
export function computeClassMap(nodes: readonly TopologyNode[]): Map<string, ComputeClass> {
  const byId = new Map(nodes.map((node) => [node.id, node]));

  const nested = (node: TopologyNode): boolean => {
    // A cluster does not sit inside a cluster — it is one. Same for the
    // namespaces it holds. Without this the estate's own containers classify
    // as bare metal and the whole rail turns green.
    if (node.typeId === 'cluster' || node.typeId === 'namespace') return true;
    if (node.cluster) return true;
    const seen = new Set<string>([node.id]);
    let current: TopologyNode | undefined = node;
    while (current?.parentId && !seen.has(current.parentId)) {
      seen.add(current.parentId);
      current = byId.get(current.parentId);
      if (!current) return false;
      if (current.typeId === 'cluster' || current.cluster) return true;
    }
    return false;
  };

  return new Map(nodes.map((node) => [node.id, computeClassOf(node, nested(node))]));
}

/** How many nodes sit in each class, for the filter counts. */
export function countNodesByComputeClass(
  nodes: readonly TopologyNode[],
): Record<ComputeClass, number> {
  const counts = Object.fromEntries(COMPUTE_CLASSES.map((c) => [c, 0])) as Record<
    ComputeClass,
    number
  >;
  for (const cls of computeClassMap(nodes).values()) counts[cls] += 1;
  return counts;
}
