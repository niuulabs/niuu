/**
 * Mímir page domain — the core knowledge-base entity.
 *
 * A page is a compiled-truth document assembled from raw sources. It has a
 * type (entity / topic / directive / preference / decision), a confidence
 * level, and structured zones that represent different aspects of knowledge.
 */

// ---------------------------------------------------------------------------
// Page type and confidence
// ---------------------------------------------------------------------------

export type PageType = 'entity' | 'topic' | 'directive' | 'preference' | 'decision';

export type Confidence = 'high' | 'medium' | 'low';

// ---------------------------------------------------------------------------
// Zones — structured content blocks within a page
// ---------------------------------------------------------------------------

export type ZoneKind = 'key-facts' | 'relationships' | 'assessment' | 'timeline';

export interface ZoneKeyFacts {
  kind: 'key-facts';
  items: string[];
}

export interface ZoneRelationships {
  kind: 'relationships';
  items: Array<{ slug: string; note: string }>;
}

export interface ZoneAssessment {
  kind: 'assessment';
  text: string;
}

export interface ZoneTimeline {
  kind: 'timeline';
  items: Array<{ date: string; note: string; source: string }>;
}

export type Zone = ZoneKeyFacts | ZoneRelationships | ZoneAssessment | ZoneTimeline;

// ---------------------------------------------------------------------------
// Page types
// ---------------------------------------------------------------------------

/** Lightweight page reference shown in lists and search results. */
export interface PageMeta {
  path: string;
  title: string;
  summary: string;
  category: string;
  type: PageType;
  confidence: Confidence;
  entityType?: string;
  /** Mounts that carry this page. */
  mounts: string[];
  updatedAt: string;
  updatedBy: string;
  sourceIds: string[];
  size: number;
  /** Whether a human has flagged this page for review. */
  flagged?: boolean;
  /** Related page slugs — carried through from Page so backlinks can be computed. */
  related?: string[];
}

/** Full page with structured zone content. */
export interface Page extends PageMeta {
  /** Related page slugs. */
  related: string[];
  /** Structured zones — absent when the page hasn't been fully compiled. */
  zones?: Zone[];
}

/** Minimal search result. */
export interface SearchResult {
  path: string;
  title: string;
  summary: string;
  category: string;
  type: PageType;
  confidence: Confidence;
  /** Relevance score in [0, 1] — present for semantic and hybrid modes. */
  score?: number;
  /** Mounts that carry this page. */
  mounts?: string[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Returns true if the page has high confidence. */
export function isHighConfidence(page: Pick<Page, 'confidence'>): boolean {
  return page.confidence === 'high';
}

/**
 * Find the first zone of a given kind in a page's zone list.
 * Returns undefined if zones are absent or the kind isn't present.
 */
export function getZoneByKind<K extends ZoneKind>(
  zones: Zone[],
  kind: K,
): Extract<Zone, { kind: K }> | undefined {
  return zones.find((z): z is Extract<Zone, { kind: K }> => z.kind === kind);
}

/** Extract a PageMeta from a full Page. */
export function toPageMeta(page: Page): PageMeta {
  return {
    path: page.path,
    title: page.title,
    summary: page.summary,
    category: page.category,
    type: page.type,
    confidence: page.confidence,
    entityType: page.entityType,
    flagged: page.flagged,
    mounts: page.mounts,
    updatedAt: page.updatedAt,
    updatedBy: page.updatedBy,
    sourceIds: page.sourceIds,
    size: page.size,
    related: page.related,
  };
}
