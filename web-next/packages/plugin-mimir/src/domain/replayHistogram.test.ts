import { describe, it, expect } from 'vitest';
import {
  earliestFirstSeen,
  perDayHistogram,
  pagesKnownByDate,
  nodesFirstSeenOn,
  daysPerTick,
  REPLAY_SPEEDS,
} from './replayHistogram';

const NODES = [
  { firstSeen: '2026-04-01T09:00:00Z' },
  { firstSeen: '2026-04-01T14:00:00Z' },
  { firstSeen: '2026-04-03T10:00:00Z' },
];

describe('earliestFirstSeen', () => {
  it('returns the earliest date across nodes', () => {
    expect(earliestFirstSeen(NODES)).toBe('2026-04-01');
  });
  it('returns null for an empty list', () => {
    expect(earliestFirstSeen([])).toBeNull();
  });
});

describe('perDayHistogram', () => {
  it('counts pages per day, including zero-count days', () => {
    expect(perDayHistogram(NODES, '2026-04-01', '2026-04-03')).toEqual([
      { date: '2026-04-01', count: 2 },
      { date: '2026-04-02', count: 0 },
      { date: '2026-04-03', count: 1 },
    ]);
  });
});

describe('pagesKnownByDate', () => {
  it('counts nodes first seen on or before the date', () => {
    expect(pagesKnownByDate(NODES, '2026-04-01')).toBe(2);
    expect(pagesKnownByDate(NODES, '2026-04-03')).toBe(3);
    expect(pagesKnownByDate(NODES, '2026-03-31')).toBe(0);
  });
});

describe('nodesFirstSeenOn', () => {
  it('filters nodes matching an exact day', () => {
    expect(nodesFirstSeenOn(NODES, '2026-04-01')).toHaveLength(2);
    expect(nodesFirstSeenOn(NODES, '2026-04-02')).toHaveLength(0);
  });
});

describe('daysPerTick', () => {
  it('scales linearly with the speed multiplier', () => {
    for (const speed of REPLAY_SPEEDS) expect(daysPerTick(speed)).toBe(speed);
  });
});
