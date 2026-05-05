/**
 * ActiveCursor — pulsing "in-progress" indicator for live sessions.
 *
 * State machine:
 *   idle     — session is not running (renders nothing)
 *   active   — session.status === 'running' (pulsing cursor shown)
 *   done     — session finished (renders nothing)
 */

import type { SessionStatus } from '../domain/session';

export type CursorState = 'idle' | 'active' | 'done';

/** Derive cursor state from a session status. */
export function cursorStateFromStatus(status: SessionStatus): CursorState {
  if (status === 'running') return 'active';
  if (status === 'stopped' || status === 'failed') return 'done';
  return 'idle';
}

interface ActiveCursorProps {
  status: SessionStatus;
  className?: string;
}

export function ActiveCursor({ status, className }: ActiveCursorProps) {
  const state = cursorStateFromStatus(status);
  if (state !== 'active') return null;

  return (
    <div
      className={className}
      role="status"
      aria-label="session in progress"
      aria-live="polite"
      data-cursor-state={state}
    >
      <span className="rv-active-cursor">
        <span className="rv-active-cursor__dot" aria-hidden="true" />
        <span className="rv-active-cursor__dot rv-active-cursor__dot--2" aria-hidden="true" />
        <span className="rv-active-cursor__dot rv-active-cursor__dot--3" aria-hidden="true" />
      </span>
    </div>
  );
}
