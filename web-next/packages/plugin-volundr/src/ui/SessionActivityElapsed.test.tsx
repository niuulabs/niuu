import { act, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { SessionActivityElapsed, sessionActivityElapsed } from './SessionActivityElapsed';

const now = Date.parse('2026-09-19T12:05:00Z');
afterEach(() => vi.useRealTimers());

it('measures the turn across tool transitions and uses the idle transition after completion', () => {
  expect(
    sessionActivityElapsed(
      {
        state: 'running',
        turnStartedAt: '2026-09-19T12:00:00Z',
        activityStateSince: '2026-09-19T12:04:00Z',
      },
      now,
    ),
  ).toBe('Working 5m 0s');
  expect(
    sessionActivityElapsed(
      {
        state: 'idle',
        turnStartedAt: '2026-09-19T12:00:00Z',
        activityStateSince: '2026-09-19T12:04:30Z',
        lastActivityAt: '2026-09-19T12:05:00Z',
      },
      now,
    ),
  ).toBe('Idle 30s');
  expect(
    sessionActivityElapsed(
      { state: 'awaiting_input', activityStateSince: '2026-09-19T12:04:59Z' },
      now,
    ),
  ).toBe('Needs you 1s');
});

it('does not invent timing from heartbeat metadata when no state anchor is known', () => {
  expect(
    sessionActivityElapsed({ state: 'running', lastActivityAt: '2026-09-19T12:04:00Z' }, now),
  ).toBe('Working');
  expect(sessionActivityElapsed({ state: 'idle', activityStateSince: 'invalid' }, now)).toBe(
    'Idle',
  );
  expect(
    sessionActivityElapsed({ state: 'ready', lastActivityAt: '2026-09-19T12:04:00Z' }, now),
  ).toBe('Connected');
  expect(
    sessionActivityElapsed({ state: 'idle', activityStateSince: '2026-09-19T12:06:00Z' }, now),
  ).toBe('Idle 0s');
  expect(
    sessionActivityElapsed({ state: 'archived', lastActivityAt: '2026-09-17T10:00:00Z' }, now),
  ).toBe('2d 2h ago');
});

it('ticks live from the same anchor and stops its shared clock after unmount', () => {
  vi.useFakeTimers();
  vi.setSystemTime(now);
  const { rerender, unmount } = render(
    <SessionActivityElapsed
      session={{ state: 'running', turnStartedAt: '2026-09-19T12:04:30Z' }}
    />,
  );
  expect(screen.getByText('Working 30s')).toBeInTheDocument();
  act(() => vi.advanceTimersByTime(2000));
  expect(screen.getByText('Working 32s')).toBeInTheDocument();
  rerender(
    <SessionActivityElapsed
      session={{ state: 'idle', activityStateSince: new Date(now + 2000).toISOString() }}
    />,
  );
  expect(screen.getByText('Idle 0s')).toBeInTheDocument();
  act(() => vi.advanceTimersByTime(1000));
  expect(screen.getByText('Idle 1s')).toBeInTheDocument();
  unmount();
  expect(vi.getTimerCount()).toBe(0);
});
