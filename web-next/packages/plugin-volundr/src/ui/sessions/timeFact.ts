import { useSyncExternalStore } from 'react';
import type { Session } from '../../domain/session';

/** How often a row's time fact is recomputed. Facts are whole minutes, so a minute is exact. */
export const TIME_FACT_TICK_MS = 60_000;

const MS_PER_MINUTE = 60_000;
const SECONDS_PER_MINUTE = 60;
const MINUTES_PER_HOUR = 60;
const HOURS_PER_DAY = 24;

type FactSession = Pick<
  Session,
  'state' | 'activityStateSince' | 'turnStartedAt' | 'lastActivityAt' | 'terminatedAt'
>;

const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | undefined;
/** Undefined while nothing is mounted, so the first read after a pause is fresh. */
let current: number | undefined;

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  // One timer serves every mounted row; a large fleet must not start one timer per row.
  timer ??= setInterval(() => {
    current = Date.now();
    listeners.forEach((notify) => notify());
  }, TIME_FACT_TICK_MS);
  current = Date.now();
  return () => {
    listeners.delete(listener);
    if (listeners.size > 0) return;
    clearInterval(timer);
    timer = undefined;
    current = undefined;
  };
}

function readClock(): number {
  current ??= Date.now();
  return current;
}

/** Wall-clock time, advanced once a minute and shared by every subscriber. */
export function useMinuteClock(): number {
  return useSyncExternalStore(subscribe, readClock, readClock);
}

/** "<1m", "4m", "2h", "3d": one unit, whole numbers, rounded down. */
export function compactDuration(ms: number): string {
  const minutes = Math.floor(Math.max(0, ms) / MS_PER_MINUTE);
  if (minutes < 1) return '<1m';
  if (minutes < MINUTES_PER_HOUR) return `${minutes}m`;
  const hours = Math.floor(minutes / MINUTES_PER_HOUR);
  if (hours < HOURS_PER_DAY) return `${hours}h`;
  return `${Math.floor(hours / HOURS_PER_DAY)}d`;
}

function anchorOf(session: FactSession): { verb: string; at?: string | null } | null {
  switch (session.state) {
    case 'running':
      return { verb: 'running', at: session.turnStartedAt ?? session.activityStateSince };
    case 'awaiting_input':
      return { verb: 'waiting', at: session.activityStateSince };
    case 'idle':
    case 'ready':
      return { verb: 'idle', at: session.activityStateSince ?? session.lastActivityAt };
    case 'requested':
    case 'provisioning':
      return { verb: 'starting', at: session.activityStateSince ?? session.lastActivityAt };
    case 'terminating':
      return { verb: 'stopping', at: session.activityStateSince ?? session.lastActivityAt };
    case 'terminated':
      return { verb: 'stopped', at: session.terminatedAt ?? session.lastActivityAt };
    case 'archived':
      return { verb: 'archived', at: session.terminatedAt ?? session.lastActivityAt };
    case 'failed':
      return { verb: 'failed', at: session.lastActivityAt };
  }
}

/**
 * The right-hand fact of a row: how long the session has been in its state ("running 4m",
 * "waiting 3m", "idle 2h", "stopped 3h"). Null when Forge reported no time to count from.
 */
export function sessionTimeFact(session: FactSession, now: number): string | null {
  const anchor = anchorOf(session);
  const since = Date.parse(anchor?.at ?? '');
  if (!anchor || !Number.isFinite(since)) return null;
  return `${anchor.verb} ${compactDuration(now - since)}`;
}

/** "3 h 21 m", "21 m", "<1 m": the worked time shown under the list heading. */
export function workedDuration(seconds: number): string {
  const minutes = Math.floor(Math.max(0, seconds) / SECONDS_PER_MINUTE);
  if (minutes < 1) return '<1 m';
  const hours = Math.floor(minutes / MINUTES_PER_HOUR);
  const rest = minutes % MINUTES_PER_HOUR;
  if (hours === 0) return `${rest} m`;
  return `${hours} h ${rest} m`;
}
