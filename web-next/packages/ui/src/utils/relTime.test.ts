import { describe, it, expect, vi } from 'vitest';
import { relTime } from './relTime';

describe('relTime', () => {
  it('returns — for zero timestamp', () => {
    expect(relTime(0)).toBe('—');
  });

  it('formats seconds ago from a number timestamp', () => {
    vi.useFakeTimers();
    vi.setSystemTime(10_000);
    expect(relTime(5_000)).toBe('5s ago');
    vi.useRealTimers();
  });

  it('formats minutes ago from a number timestamp', () => {
    vi.useFakeTimers();
    vi.setSystemTime(300_000);
    expect(relTime(1)).toBe('4m ago');
    vi.useRealTimers();
  });

  it('formats hours ago from a number timestamp', () => {
    vi.useFakeTimers();
    vi.setSystemTime(7_200_000);
    expect(relTime(1)).toBe('1h ago');
    vi.useRealTimers();
  });

  it('formats days ago from a number timestamp', () => {
    vi.useFakeTimers();
    vi.setSystemTime(172_800_000);
    expect(relTime(1)).toBe('1d ago');
    vi.useRealTimers();
  });

  it('accepts an ISO date string', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T01:00:00Z').getTime());
    expect(relTime('2026-01-01T00:00:00Z')).toBe('1h ago');
    vi.useRealTimers();
  });
});
