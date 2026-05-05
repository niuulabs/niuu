/**
 * Port: IEmbeddingStore
 *
 * Vector index per mount. Each mount may use a different embedding model;
 * multi-mount semantic queries fan out to each mount and results are merged
 * by the caller.
 */
export interface EmbeddingSearchResult {
  path: string;
  title: string;
  summary: string;
  /** Cosine similarity score in [0, 1]. */
  score: number;
  /** Name of the mount this result came from. */
  mountName: string;
}

export interface IEmbeddingStore {
  /**
   * Run a semantic (vector) search against the embedding index.
   *
   * @param query     Natural-language query string.
   * @param topK      Maximum results to return (default: 10).
   * @param mountName Scope to a specific mount; omit for fleet-wide.
   */
  semanticSearch(
    query: string,
    topK?: number,
    mountName?: string,
  ): Promise<EmbeddingSearchResult[]>;
}
