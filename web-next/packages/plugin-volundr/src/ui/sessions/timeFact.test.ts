import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import {
  TIME_FACT_TICK_MS,
  compactDuration,
  sessionTimeFact,
  useMinuteClock,
  workedDuration,
} from './timeFact';

const NOW = Date.parse('2026-11-05T12:00:00Z');
const ago = (ms: number) => new Date(NOW - ms).toISOString();
const MIN = 60_000;
const HOUR = 60 * MIN;

describe('compactDuration', () => {
  it.each([
    [0, '<1m'],
    [59_999, '<1m'],
    [-5_000, '<1m'],
    [4 * MIN, '4m'],
    [59 * MIN, '59m'],
    [2 * HOUR + 30 * MIN, '2h'],
    [3 * 24 * HOUR + 5 * HOUR, '3d'],
  ])('writes %d ms as %s', (ms, expected) => {
    expect(compactDuration(ms)).toBe(expected);
  });
});

describe('sessionTimeFact', () => {
  it('counts a running session from its turn, falling back to its state change', () => {
    expect(
      sessionTimeFact(
        { state: 'running', turnStartedAt: ago(4 * MIN), activityStateSince: ago(9 * MIN) },
        NOW,
      ),
    ).toBe('running 4m');
    expect(sessionTimeFact({ state: 'running', activityStateSince: ago(9 * MIN) }, NOW)).toBe(
      'running 9m',
    );
  });

  it.each([
    [{ state: 'awaiting_input', activityStateSince: ago(3 * MIN) }, 'waiting 3m'],
    [{ state: 'idle', activityStateSince: ago(2 * HOUR) }, 'idle 2h'],
    [{ state: 'idle', lastActivityAt: ago(25 * MIN) }, 'idle 25m'],
    [{ state: 'ready', activityStateSince: ago(MIN) }, 'idle 1m'],
    [{ state: 'provisioning', lastActivityAt: ago(2 * MIN) }, 'starting 2m'],
    [{ state: 'requested', lastActivityAt: ago(2 * MIN) }, 'starting 2m'],
    [{ state: 'terminating', lastActivityAt: ago(MIN) }, 'stopping 1m'],
    [{ state: 'terminated', lastActivityAt: ago(3 * HOUR) }, 'stopped 3h'],
    [{ state: 'archived', terminatedAt: ago(2 * 24 * HOUR) }, 'archived 2d'],
    [{ state: 'failed', lastActivityAt: ago(10 * MIN) }, 'failed 10m'],
  ] as const)('writes %j as %s', (session, expected) => {
    expect(sessionTimeFact(session, NOW)).toBe(expected);
  });

  it('says nothing when Forge reported no time to count from', () => {
    expect(sessionTimeFact({ state: 'idle' }, NOW)).toBeNull();
    expect(sessionTimeFact({ state: 'idle', activityStateSince: 'not a date' }, NOW)).toBeNull();
  });
});

describe('workedDuration', () => {
  it.each([
    [0, '<1 m'],
    [59, '<1 m'],
    [21 * 60, '21 m'],
    [3 * 3600 + 21 * 60, '3 h 21 m'],
    [3600, '1 h 0 m'],
  ])('writes %d seconds as %s', (seconds, expected) => {
    expect(workedDuration(seconds)).toBe(expected);
  });
});

describe('useMinuteClock', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('advances once a minute for every subscriber and stops its timer when none are left', () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    const first = renderHook(() => useMinuteClock());
    const second = renderHook(() => useMinuteClock());
    expect(first.result.current).toBe(NOW);

    act(() => {
      vi.advanceTimersByTime(TIME_FACT_TICK_MS - 1);
    });
    expect(first.result.current).toBe(NOW);

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(first.result.current).toBe(NOW + TIME_FACT_TICK_MS);
    expect(second.result.current).toBe(NOW + TIME_FACT_TICK_MS);
    expect(vi.getTimerCount()).toBe(1);

    first.unmount();
    expect(vi.getTimerCount()).toBe(1);
    second.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
