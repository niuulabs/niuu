/**
 * Multi-mount search result merge ranking.
 *
 * Each mount produces an ordered result list. This module merges results from
 * multiple mounts into a single ranked list, applying per-mount priority
 * weighting and deduplicating on page path.
 */

import type { SearchResult } from '../domain/page';

export interface MountSearchResults {
  mountName: string;
  /** Higher priority = more weight. Typically the Mount.priority field. */
  priority: number;
  results: SearchResult[];
}

/**
 * Merge search results from multiple mounts into a single ranked list.
 *
 * Scoring: each result receives a positional score in [0,1] based on its
 * position within the mount's result list, multiplied by the mount priority.
 * When the same page appears in multiple mounts, the highest score wins.
 * The merged list is sorted by score descending.
 */
export function mergeSearchResults(allResults: MountSearchResults[]): SearchResult[] {
  const best = new Map<string, { result: SearchResult; score: number }>();

  for (const { priority, results } of allResults) {
    const n = results.length;
    if (n === 0) continue;

    for (let i = 0; i < n; i++) {
      const result = results[i]!;
      const positionalScore = (n - i) / n;
      const score = positionalScore * priority;
      const existing = best.get(result.path);

      if (!existing || score > existing.score) {
        best.set(result.path, { result, score });
      }
    }
  }

  return [...best.values()].sort((a, b) => b.score - a.score).map(({ result }) => result);
}
