import { describe, it, expect } from 'vitest';
import { ageBucket } from './ageBucket';

const NOW = new Date('2026-04-19T12:00:00Z');

describe('ageBucket', () => {
  it('buckets same-day updates as today', () => {
    expect(ageBucket('2026-04-19T08:00:00Z', NOW)).toBe('today');
  });
  it('buckets a 3-day-old update as this-week', () => {
    expect(ageBucket('2026-04-16T12:00:00Z', NOW)).toBe('this-week');
  });
  it('buckets a 20-day-old update as this-month', () => {
    expect(ageBucket('2026-03-30T12:00:00Z', NOW)).toBe('this-month');
  });
  it('buckets a 90-day-old update as older', () => {
    expect(ageBucket('2026-01-19T12:00:00Z', NOW)).toBe('older');
  });
});
