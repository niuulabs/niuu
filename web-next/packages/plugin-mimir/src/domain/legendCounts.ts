/**
 * Legend counts for the Memory Explore "Colour by" panel — how many graph
 * nodes fall into each kind group / confidence tier / age bucket.
 */
import type { GraphNode } from './api-types';
import { KIND_GROUPS, kindGroup, type KindGroup } from './memoryKinds';
import { AGE_BUCKETS, ageBucket, type AgeBucketId } from './ageBucket';

/** Counts per kind group, in `KIND_GROUPS` order, only groups present in `nodes`. */
export function countsByKindGroup(nodes: GraphNode[]): Array<{ id: KindGroup; count: number }> {
  return KIND_GROUPS.map((group) => ({
    id: group.id,
    count: nodes.filter((n) => kindGroup(n.kind) === group.id).length,
  })).filter((entry) => entry.count > 0);
}

const CONFIDENCE_ORDER: Array<'high' | 'medium' | 'low'> = ['high', 'medium', 'low'];

/** Counts per confidence tier, high → low, always all three (even zero). Nodes with no declared confidence are not counted. */
export function countsByConfidence(
  nodes: GraphNode[],
): Array<{ id: 'high' | 'medium' | 'low'; count: number }> {
  return CONFIDENCE_ORDER.map((id) => ({
    id,
    count: nodes.filter((n) => n.confidence === id).length,
  }));
}

/** Counts per age bucket, in `AGE_BUCKETS` order, always all four (even zero). */
export function countsByAgeBucket(
  nodes: GraphNode[],
  now: Date = new Date(),
): Array<{ id: AgeBucketId; count: number }> {
  return AGE_BUCKETS.map(({ id }) => ({
    id,
    count: nodes.filter((n) => ageBucket(n.updatedAt, now) === id).length,
  }));
}
