import type { IMountAdapter } from './IMountAdapter';
import type { IPageStore } from './IPageStore';
import type { IEmbeddingStore } from './IEmbeddingStore';
import type { ILintEngine } from './ILintEngine';

export type { IMountAdapter, RecentWrite } from './IMountAdapter';
export type { IPageStore, SearchMode } from './IPageStore';
export type { IEmbeddingStore, EmbeddingSearchResult } from './IEmbeddingStore';
export type { ILintEngine } from './ILintEngine';

/**
 * Composite Mímir service.
 *
 * Aggregates all four ports behind a single DI key (`'mimir'`).
 * Adapters implement this interface; components call it via
 * `useService<IMimirService>('mimir')`.
 */
export interface IMimirService {
  mounts: IMountAdapter;
  pages: IPageStore;
  embeddings: IEmbeddingStore;
  lint: ILintEngine;
}
