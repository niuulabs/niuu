/**
 * Mímir source domain — raw ingest records.
 *
 * Sources are append-only records of the original content that was ingested
 * into a mount. Pages are compiled from one or more sources.
 */

export type OriginType = 'web' | 'rss' | 'arxiv' | 'file' | 'mail' | 'chat';

export interface Source {
  id: string;
  title: string;
  originType: OriginType;
  /** URL of the original document (for web / rss / arxiv origins). */
  originUrl?: string;
  /** File system path (for file origins). */
  originPath?: string;
  /** ISO-8601 timestamp when the source was ingested. */
  ingestedAt: string;
  /** ID of the ravn that ingested this source. */
  ingestAgent: string;
  /** Paths of pages this source was compiled into. */
  compiledInto: string[];
  /** Original raw content. */
  content: string;
}
