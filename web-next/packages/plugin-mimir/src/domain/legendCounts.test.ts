import { describe, it, expect } from 'vitest';
import { countsByKindGroup, countsByConfidence, countsByAgeBucket } from './legendCounts';
import { FAKE_GRAPH } from '../testing/fakeMimirService';

const NOW = new Date('2026-04-19T12:00:00Z');

describe('countsByKindGroup', () => {
  it('counts fixture nodes per kind group, omitting empty groups', () => {
    expect(countsByKindGroup(FAKE_GRAPH.nodes)).toEqual([
      { id: 'topic', count: 2 },
      { id: 'entity', count: 1 },
      { id: 'decision', count: 1 },
      { id: 'directive', count: 1 },
    ]);
  });
  it('returns [] for an empty graph', () => {
    expect(countsByKindGroup([])).toEqual([]);
  });
});

describe('countsByConfidence', () => {
  it('always returns all three tiers, high to low', () => {
    expect(countsByConfidence(FAKE_GRAPH.nodes)).toEqual([
      { id: 'high', count: 3 },
      { id: 'medium', count: 1 },
      { id: 'low', count: 1 },
    ]);
  });
});

describe('countsByAgeBucket', () => {
  it('buckets by updatedAt relative to now', () => {
    const counts = countsByAgeBucket(FAKE_GRAPH.nodes, NOW);
    expect(counts.map((c) => c.id)).toEqual(['today', 'this-week', 'this-month', 'older']);
    expect(counts.reduce((sum, c) => sum + c.count, 0)).toBe(FAKE_GRAPH.nodes.length);
  });
});
