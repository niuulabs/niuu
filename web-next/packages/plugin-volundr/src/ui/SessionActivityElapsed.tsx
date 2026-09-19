import { useSyncExternalStore } from 'react';
import type { Session } from '../domain/session';
import { SESSION_STATE_LABELS } from './sessions/presentation';

// One clock for all mounted rows; opening a large fleet must not create hundreds
// of independent timers. No network requests are needed to advance elapsed time.
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | undefined;
const clock = () => Math.floor(Date.now() / 1000) * 1000;
function subscribe(listener: () => void) {
  listeners.add(listener);
  timer ??= setInterval(() => listeners.forEach((notify) => notify()), 1000);
  return () => {
    listeners.delete(listener);
    if (!listeners.size) {
      clearInterval(timer);
      timer = undefined;
    }
  };
}

type Activity = Pick<Session, 'state' | 'activityStateSince' | 'turnStartedAt' | 'lastActivityAt'>;

export function sessionActivityElapsed(session: Activity, now: number): string {
  const timedState = ['running', 'idle', 'awaiting_input', 'provisioning'].includes(session.state);
  const anchor =
    session.state === 'running'
      ? (session.turnStartedAt ?? session.activityStateSince)
      : timedState
        ? session.activityStateSince
        : session.lastActivityAt;
  const timestamp = Date.parse(anchor ?? '');
  const label = SESSION_STATE_LABELS[session.state];
  if (!Number.isFinite(timestamp) || session.state === 'ready') return label;
  const seconds = Math.max(0, Math.floor((now - timestamp) / 1000));
  const duration =
    seconds < 60
      ? `${seconds}s`
      : seconds < 3600
        ? `${Math.floor(seconds / 60)}m ${seconds % 60}s`
        : seconds < 86400
          ? `${Math.floor(seconds / 3600)}h ${Math.floor(seconds / 60) % 60}m`
          : `${Math.floor(seconds / 86400)}d ${Math.floor(seconds / 3600) % 24}h`;
  return timedState ? `${label} ${duration}` : `${duration} ago`;
}

export function SessionActivityElapsed({ session }: { session: Activity }) {
  const now = useSyncExternalStore(subscribe, clock, clock);
  const label = sessionActivityElapsed(session, now);
  return (
    <span title={label} data-testid="session-activity-elapsed">
      {label}
    </span>
  );
}
