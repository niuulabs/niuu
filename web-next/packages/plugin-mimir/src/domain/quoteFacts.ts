/**
 * Ask-mode quoting — verbatim Key Facts from the pages that answered a
 * question. Extracted so the scene's Ask
 * panel and any other consumer share one quoting rule instead of
 * duplicating it; nothing here composes or rephrases a fact.
 */
import { getZoneByKind } from './page';
import type { Page } from './page';

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
}

/**
 * Quote every Key Fact from `pages`, in page order.
 */
export function quoteKeyFacts(pages: Page[]): QuotedFact[] {
  return pages.flatMap((page) =>
    factsOf(page).map((fact, index) => ({
      page,
      fact,
      position: index + 1,
    })),
  );
}
