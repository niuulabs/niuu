import type { PageMeta, Page, SearchResult } from '../domain/page';
import type { Source, OriginType } from '../domain/source';
import type { MimirStats, MimirGraph } from '../domain/api-types';
import type { EntityKind, EntityMeta } from '../domain/entity';

export type SearchMode = 'fts' | 'semantic' | 'hybrid';

/**
 * Port: IPageStore
 *
 * CRUD and search over compiled pages and their raw source records. A page
 * store is scoped to one or more mounts depending on the adapter.
 */
export interface IPageStore {
  /** Fleet-wide statistics (page count, categories, health). */
  getStats(): Promise<MimirStats>;

  /**
   * List page metadata, optionally filtered by mount and/or category.
   */
  listPages(options?: { mountName?: string; category?: string }): Promise<PageMeta[]>;

  /**
   * Fetch a single page by path.
   * Returns null when no page exists at that path.
   */
  getPage(path: string, mountName?: string): Promise<Page | null>;

  /**
   * Create or update a page at the given path.
   * The write is routed according to the mount's write-routing rules.
   */
  upsertPage(path: string, content: string, mountName?: string): Promise<void>;

  /**
   * Full-text, semantic, or hybrid search across pages.
   * Defaults to hybrid mode.
   */
  search(query: string, mode?: SearchMode): Promise<SearchResult[]>;

  /**
   * List raw source records, optionally filtered by origin type and/or mount.
   * Sources are the append-only ingest records that pages are compiled from.
   */
  listSources(options?: { originType?: OriginType; mountName?: string }): Promise<Source[]>;

  /**
   * Get the raw source records attributed to a specific page.
   */
  getPageSources(path: string): Promise<Source[]>;

  /**
   * Ingest a source from a remote URL (web, RSS, or arxiv).
   * Returns the newly created source record.
   */
  ingestUrl(url: string): Promise<Source>;

  /**
   * Ingest a source from an uploaded file.
   * Returns the newly created source record.
   */
  ingestFile(file: File): Promise<Source>;

  /**
   * Fetch the knowledge graph (page nodes + relationship edges).
   * Optionally scoped to a single mount.
   */
  getGraph(options?: { mountName?: string }): Promise<MimirGraph>;

  /**
   * List entity pages, optionally filtered by entity kind.
   * Returns lightweight EntityMeta summaries suitable for list views.
   */
  listEntities(options?: { kind?: EntityKind }): Promise<EntityMeta[]>;
}
