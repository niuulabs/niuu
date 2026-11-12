import type { IMountAdapter } from './IMountAdapter';
import type { IPageStore } from './IPageStore';
import type { IEmbeddingStore } from './IEmbeddingStore';

export type { IMountAdapter, RecentWrite } from './IMountAdapter';
export type { IPageStore, SearchMode } from './IPageStore';
export type { LiveActivity, LiveActivityKind } from '../domain/api-types';
export type { IEmbeddingStore, EmbeddingSearchResult } from './IEmbeddingStore';

/**
 * Composite Mímir service.
 *
 * Aggregates all three ports behind a single DI key (`'mimir'`).
 * Adapters implement this interface; components call it via
 * `useService<IMimirService>('mimir')`.
 */
export interface IMimirService {
  mounts: IMountAdapter;
  pages: IPageStore;
  embeddings: IEmbeddingStore;
}
