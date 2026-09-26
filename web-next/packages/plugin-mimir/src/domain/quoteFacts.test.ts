import { describe, it, expect } from 'vitest';
import { factsOf, quoteKeyFacts } from './quoteFacts';
import type { FactEvidence } from './evidence';
import type { Page } from './page';

function page(path: string, facts: string[]): Page {
  return {
    path,
    title: path,
    summary: '',
    category: 'infra',
    type: 'topic',
    confidence: 'high',
    mounts: ['platform'],
    updatedAt: '2026-01-01T00:00:00Z',
    updatedBy: 'ravn',
    sourceIds: ['s1'],
    related: [],
    size: 10,
    zones: [{ kind: 'key-facts', items: facts }],
  };
}

describe('factsOf', () => {
  it('returns verbatim Key Facts', () => {
    expect(factsOf(page('/a', ['fact 1', 'fact 2']))).toEqual(['fact 1', 'fact 2']);
  });

  it('returns [] when the page has no key-facts zone', () => {
    const noZones: Page = { ...page('/a', []), zones: [] };
    expect(factsOf(noZones)).toEqual([]);
  });
});

describe('quoteKeyFacts', () => {
  it('quotes every fact from every page, in page order, 1-based position', () => {
    const pages = [page('/a', ['a1', 'a2']), page('/b', ['b1'])];
    const quoted = quoteKeyFacts(pages, new Map());
    expect(quoted).toEqual([
      { page: pages[0], fact: 'a1', position: 1, evidence: null },
      { page: pages[0], fact: 'a2', position: 2, evidence: null },
      { page: pages[1], fact: 'b1', position: 1, evidence: null },
    ]);
  });

  it('matches each fact to its evidence row by exact text', () => {
    const evidence: FactEvidence = {
      fact: 'a1',
      proofCount: 3,
      trend: 'stable',
      latestSupport: '2026-01-01',
      supportingDates: ['2026-01-01'],
      sourceProofCount: 1,
    };
    const pages = [page('/a', ['a1', 'a2'])];
    const byPath = new Map([['/a', [evidence]]]);
    const quoted = quoteKeyFacts(pages, byPath);
    expect(quoted[0]!.evidence).toBe(evidence);
    expect(quoted[1]!.evidence).toBeNull();
  });

  it('returns [] for an empty page list', () => {
    expect(quoteKeyFacts([], new Map())).toEqual([]);
  });
});
