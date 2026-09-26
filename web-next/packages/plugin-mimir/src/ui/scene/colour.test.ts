import { describe, it, expect } from 'vitest';
import { proofBucket, ageBucket, nodeKindGroup, nodeColour } from './colour';
import { DEFAULT_MEMORY_PALETTE } from './palette';

const NOW = Date.parse('2026-04-19T12:00:00Z');
const DAY_MS = 24 * 60 * 60 * 1000;

describe('proofBucket', () => {
  it('maps confidence values to their bucket', () => {
    expect(proofBucket('high')).toBe('high');
    expect(proofBucket('medium')).toBe('medium');
    expect(proofBucket('low')).toBe('low');
    expect(proofBucket(null)).toBe('none');
    expect(proofBucket(undefined)).toBe('none');
  });
});

describe('ageBucket', () => {
  it('buckets today, this-week, this-month and older ages (delegating to the shared boundaries)', () => {
    expect(ageBucket(new Date(NOW - 1000).toISOString(), NOW)).toBe('today');
    expect(ageBucket(new Date(NOW - 3 * DAY_MS).toISOString(), NOW)).toBe('this-week');
    expect(ageBucket(new Date(NOW - 20 * DAY_MS).toISOString(), NOW)).toBe('this-month');
    expect(ageBucket(new Date(NOW - 90 * DAY_MS).toISOString(), NOW)).toBe('older');
  });

  it('treats missing or unparsable timestamps as older', () => {
    expect(ageBucket(undefined, NOW)).toBe('older');
    expect(ageBucket('not-a-date', NOW)).toBe('older');
  });

  it('treats a future timestamp as today rather than throwing', () => {
    expect(ageBucket(new Date(NOW + DAY_MS).toISOString(), NOW)).toBe('today');
  });
});

describe('nodeKindGroup', () => {
  it('groups by kind when present', () => {
    expect(nodeKindGroup({ kind: 'person' })).toBe('entity');
    expect(nodeKindGroup({ kind: 'decision' })).toBe('decision');
  });

  it('falls back to "page" when kind is absent or unrecognised', () => {
    expect(nodeKindGroup({ kind: undefined })).toBe('page');
    expect(nodeKindGroup({ kind: 'unknown-kind' })).toBe('page');
  });
});

describe('nodeColour', () => {
  const node = { kind: 'person', confidence: 'high', updatedAt: new Date(NOW).toISOString() };

  it('colours by kind group under "type"', () => {
    expect(nodeColour(node, 'type', DEFAULT_MEMORY_PALETTE, NOW)).toBe(
      DEFAULT_MEMORY_PALETTE.kind.entity,
    );
  });

  it('colours by confidence under "proof"', () => {
    expect(nodeColour(node, 'proof', DEFAULT_MEMORY_PALETTE, NOW)).toBe(
      DEFAULT_MEMORY_PALETTE.proof.high,
    );
  });

  it('colours by recency under "age"', () => {
    expect(nodeColour(node, 'age', DEFAULT_MEMORY_PALETTE, NOW)).toBe(
      DEFAULT_MEMORY_PALETTE.age.today,
    );
  });
});
