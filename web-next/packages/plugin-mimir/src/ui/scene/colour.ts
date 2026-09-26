/**
 * Pure colour classification for graph nodes.
 *
 * Takes a `MemoryPalette` (already resolved from CSS tokens by
 * `palette.ts`) so this module never touches the DOM — every bucketing rule
 * here is testable on plain data.
 */

import type { GraphNode } from '../../domain/api-types';
import { kindGroup, type KindGroup } from '../../domain/memoryKinds';
import { ageBucket as sharedAgeBucket } from '../../domain/ageBucket';
import type { AgeBucket, MemoryPalette, ProofBucket } from './palette';
import type { ColourBy } from './types';

/** Confidence -> the bucket the legend/scene groups it into. */
export function proofBucket(confidence: GraphNode['confidence']): ProofBucket {
  const normalised = confidence?.toLowerCase();
  if (normalised === 'high') return 'high';
  if (normalised === 'medium') return 'medium';
  if (normalised === 'low') return 'low';
  return 'none';
}

/**
 * How recently a node was updated, relative to `now`. Delegates to the
 * shared `domain/ageBucket.ts` bucketing (the single source of truth for
 * the boundaries, also used by the Memory Explore legend/filters) — this
 * wrapper only adapts its `(string, Date)` signature to the epoch-ms `now`
 * the rest of this module already threads through, and covers the missing
 * or unparsable timestamp case the shared function doesn't need to (a
 * `GraphNode.updatedAt` in the wild can be an empty "unknown" sentinel; see
 * the HTTP adapter).
 */
export function ageBucket(updatedAt: string | undefined, now: number): AgeBucket {
  if (!updatedAt || Number.isNaN(Date.parse(updatedAt))) return 'older';
  return sharedAgeBucket(updatedAt, new Date(now));
}

/** The kind group a node colours by under `colourBy: 'type'`. */
export function nodeKindGroup(node: Pick<GraphNode, 'kind'>): KindGroup {
  return kindGroup(node.kind);
}

/** Resolve the hex/CSS colour a node should be drawn in. */
export function nodeColour(
  node: Pick<GraphNode, 'kind' | 'confidence' | 'updatedAt'>,
  colourBy: ColourBy,
  palette: MemoryPalette,
  now: number,
): string {
  switch (colourBy) {
    case 'proof':
      return palette.proof[proofBucket(node.confidence)];
    case 'age':
      return palette.age[ageBucket(node.updatedAt, now)];
    case 'type':
    default:
      return palette.kind[nodeKindGroup(node)];
  }
}
