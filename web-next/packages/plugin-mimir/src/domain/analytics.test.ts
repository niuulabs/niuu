import { describe, it, expect } from 'vitest';
import { zeroResultQueries, type QueryStats } from './analytics';

describe('zeroResultQueries', () => {
  const stats: QueryStats = {
    total: 5,
    zeroResultCount: 2,
    recent: [
      { ts: '2026-04-19T10:00:00Z', query: 'hit', resultCount: 3 },
      { ts: '2026-04-19T09:00:00Z', query: 'miss one', resultCount: 0 },
      { ts: '2026-04-19T08:00:00Z', query: 'miss two', resultCount: 0 },
    ],
  };

  it('returns only entries with zero results', () => {
    const misses = zeroResultQueries(stats);
    expect(misses).toHaveLength(2);
    expect(misses.map((m) => m.query)).toEqual(['miss one', 'miss two']);
  });

  it('returns an empty list when every query had hits', () => {
    expect(
      zeroResultQueries({
        total: 1,
        zeroResultCount: 0,
        recent: [{ ts: '2026-04-19T10:00:00Z', query: 'hit', resultCount: 1 }],
      }),
    ).toEqual([]);
  });
});
