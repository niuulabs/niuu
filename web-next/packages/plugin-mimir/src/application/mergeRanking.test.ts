import { describe, it, expect } from 'vitest';
import { mergeSearchResults } from './mergeRanking';
import type { SearchResult } from '../domain/page';

const makeResult = (path: string, title = path): SearchResult => ({
  path,
  title,
  summary: `Summary of ${path}`,
  category: 'test',
  type: 'topic',
  confidence: 'high',
});

describe('mergeSearchResults', () => {
  it('returns empty array when no mounts provided', () => {
    expect(mergeSearchResults([])).toEqual([]);
  });

  it('returns empty array when all mounts have empty results', () => {
    expect(
      mergeSearchResults([
        { mountName: 'a', priority: 1, results: [] },
        { mountName: 'b', priority: 2, results: [] },
      ]),
    ).toEqual([]);
  });

  it('returns results from a single mount in order', () => {
    const r1 = makeResult('/a');
    const r2 = makeResult('/b');
    const r3 = makeResult('/c');
    const merged = mergeSearchResults([{ mountName: 'local', priority: 1, results: [r1, r2, r3] }]);
    expect(merged.map((r) => r.path)).toEqual(['/a', '/b', '/c']);
  });

  it('deduplicates results that appear in multiple mounts', () => {
    const r1 = makeResult('/shared');
    const r2 = makeResult('/only-a');
    const r3 = makeResult('/only-b');
    const merged = mergeSearchResults([
      { mountName: 'a', priority: 1, results: [r1, r2] },
      { mountName: 'b', priority: 1, results: [r1, r3] },
    ]);
    const paths = merged.map((r) => r.path);
    expect(paths.filter((p) => p === '/shared')).toHaveLength(1);
  });

  it('favours results from higher-priority mounts', () => {
    const high = makeResult('/high-priority-page');
    const low = makeResult('/low-priority-page');
    const merged = mergeSearchResults([
      { mountName: 'low', priority: 1, results: [low] },
      { mountName: 'high', priority: 10, results: [high] },
    ]);
    expect(merged[0]!.path).toBe('/high-priority-page');
  });

  it('top position in mount scores higher than lower positions', () => {
    const top = makeResult('/top');
    const mid = makeResult('/mid');
    const bot = makeResult('/bot');
    const merged = mergeSearchResults([
      { mountName: 'local', priority: 1, results: [top, mid, bot] },
    ]);
    const paths = merged.map((r) => r.path);
    expect(paths.indexOf('/top')).toBeLessThan(paths.indexOf('/mid'));
    expect(paths.indexOf('/mid')).toBeLessThan(paths.indexOf('/bot'));
  });

  it('when same page in two mounts, keeps the higher-scored entry', () => {
    const result = makeResult('/overlap');
    // Mount b has priority 5 (higher) and the result is at position 0
    const merged = mergeSearchResults([
      { mountName: 'a', priority: 1, results: [result] },
      { mountName: 'b', priority: 5, results: [result] },
    ]);
    // The result should appear exactly once
    expect(merged.filter((r) => r.path === '/overlap')).toHaveLength(1);
    // And it should be first (highest score)
    expect(merged[0]!.path).toBe('/overlap');
  });

  it('merges results from three mounts without duplication', () => {
    const shared = makeResult('/shared');
    const onlyA = makeResult('/only-a');
    const onlyB = makeResult('/only-b');
    const onlyC = makeResult('/only-c');
    const merged = mergeSearchResults([
      { mountName: 'a', priority: 3, results: [shared, onlyA] },
      { mountName: 'b', priority: 2, results: [shared, onlyB] },
      { mountName: 'c', priority: 1, results: [onlyC] },
    ]);
    expect(merged.length).toBe(4);
    const paths = new Set(merged.map((r) => r.path));
    expect(paths).toContain('/shared');
    expect(paths).toContain('/only-a');
    expect(paths).toContain('/only-b');
    expect(paths).toContain('/only-c');
  });

  it('handles a single result in a single mount', () => {
    const r = makeResult('/single');
    const merged = mergeSearchResults([{ mountName: 'local', priority: 1, results: [r] }]);
    expect(merged).toHaveLength(1);
    expect(merged[0]!.path).toBe('/single');
  });
});
