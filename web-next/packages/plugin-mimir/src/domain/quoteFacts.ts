/**
 * Ask-mode quoting — verbatim Key Facts from the pages that answered a
 * question, each with its proof evidence. Extracted so the scene's Ask
 * panel and any other consumer share one quoting rule instead of
 * duplicating it; nothing here composes or rephrases a fact.
 */
import { getZoneByKind } from './page';
import type { Page } from './page';
import { evidenceForFact } from './evidence';
import type { FactEvidence } from './evidence';

/** Verbatim Key Facts on a page, in written order. */
export function factsOf(page: Page): string[] {
  return getZoneByKind(page.zones ?? [], 'key-facts')?.items ?? [];
}

export interface QuotedFact {
  page: Page;
  /** The fact, verbatim as written on the page. */
  fact: string;
  /** 1-based position within the page's Key Facts. */
  position: number;
  evidence: FactEvidence | null;
}

/**
 * Quote every Key Fact from `pages`, in page order, each matched to its
 * evidence row (by exact fact text) via `evidenceByPath`.
 */
export function quoteKeyFacts(
  pages: Page[],
  evidenceByPath: Map<string, FactEvidence[]>,
): QuotedFact[] {
  return pages.flatMap((page) => {
    const rows = evidenceByPath.get(page.path) ?? [];
    return factsOf(page).map((fact, index) => ({
      page,
      fact,
      position: index + 1,
      evidence: evidenceForFact(rows, fact),
    }));
  });
}
