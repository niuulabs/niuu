/**
 * Which deck a node stands on.
 *
 * The 2D canvas has to say "this runs on that" with a ring drawn around a
 * cluster of dots, and once three levels of containment overlap the ring stops
 * carrying the claim. In 3D the claim is a direction: every level of the chain
 * gets its own height, so a host is visibly *under* the agents on it and a
 * cluster is visibly under both.
 *
 * Height comes from the node's type rather than from its measured depth in the
 * parent chain. Depth would make the same kind of thing sit at different
 * heights in different realms — a warden directly under a cluster and a warden
 * under a namespace would end up on separate decks, and the decks would stop
 * meaning anything.
 */

import type { TopologyNode } from '../../domain';
import { TIER } from './scene3dConfig';

/** Types whose deck is not the leaf deck. Everything else runs on something. */
const TIER_BY_TYPE: Readonly<Record<string, number>> = {
  realm: TIER.FLOOR,
  cloud: TIER.FLOOR,
  cluster: TIER.CLUSTER,
  namespace: TIER.NAMESPACE,
  host: TIER.HOST,
  run: TIER.HOST,
};

/**
 * The containers — the types that hold other things and are therefore drawn as
 * a deck rather than as an object standing on one.
 */
export const CONTAINER_TYPES: ReadonlySet<string> = new Set([
  'realm',
  'cloud',
  'cluster',
  'namespace',
  'host',
  'run',
]);

export function isContainerType(typeId: string): boolean {
  return CONTAINER_TYPES.has(typeId);
}

/** Height of the deck a node stands on, in world units. */
export function elevationFor(node: TopologyNode): number {
  return TIER_BY_TYPE[node.typeId] ?? TIER.LEAF;
}
