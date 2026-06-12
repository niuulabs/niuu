/**
 * Mímir retrieval analytics domain — eval reports and query statistics.
 *
 * Eval reports come from offline retrieval-quality benchmark runs; query
 * stats describe live search traffic (volume, zero-result misses, recents).
 * Both are optional capabilities: a backend without the eval subsystem
 * returns null and the UI shows an empty state.
 */

/** Retrieval-quality metrics for one slice (overall or one category). */
export interface EvalMetrics {
  /** Precision within the top 5 results, in [0, 1]. */
  precisionAt5: number;
  /** Mean reciprocal rank, in [0, 1]. */
  mrr: number;
  /** Recall within the top 10 results, in [0, 1]. */
  recallAt10: number;
}

/** Latest retrieval-eval benchmark report. */
export interface EvalReport {
  /** ISO-8601 timestamp the report was generated. */
  generatedAt: string;
  overall: EvalMetrics;
  /** Metrics broken out per page category. */
  byCategory: Record<string, EvalMetrics>;
  /** Number of benchmark queries evaluated. */
  queryCount: number;
}

/** One entry in the recent-query log. */
export interface QueryLogEntry {
  /** ISO-8601 timestamp of the query. */
  ts: string;
  query: string;
  resultCount: number;
}

/** Live search-traffic statistics. */
export interface QueryStats {
  /** Total queries served. */
  total: number;
  /** Queries that returned zero results. */
  zeroResultCount: number;
  /** Recent query log, newest first. */
  recent: QueryLogEntry[];
}

/** Zero-result queries from the recent log — the retrieval misses to fix. */
export function zeroResultQueries(stats: QueryStats): QueryLogEntry[] {
  return stats.recent.filter((entry) => entry.resultCount === 0);
}
