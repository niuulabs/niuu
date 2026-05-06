import type { Session, SessionState } from '../../domain/session';

/** States shown as subnav tabs in the Sessions page, in display order. */
export const SESSION_STATES: SessionState[] = [
  'running',
  'idle',
  'provisioning',
  'failed',
  'terminated',
];

export type SessionsByState = Record<SessionState, Session[]>;

/**
 * Group a flat list of sessions by their state.
 * Every possible state gets an entry (empty array if no sessions).
 */
export function groupByState(sessions: Session[]): SessionsByState {
  const result: SessionsByState = {
    requested: [],
    provisioning: [],
    ready: [],
    running: [],
    idle: [],
    terminating: [],
    terminated: [],
    failed: [],
  };

  for (const session of sessions) {
    result[session.state].push(session);
  }

  return result;
}

/** Count sessions in each subnav state. */
export function countByState(groups: SessionsByState): Record<SessionState, number> {
  return (Object.keys(groups) as SessionState[]).reduce(
    (acc, state) => {
      acc[state] = groups[state].length;
      return acc;
    },
    {} as Record<SessionState, number>,
  );
}
