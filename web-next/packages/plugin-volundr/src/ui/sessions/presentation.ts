import type { Session, SessionState } from '../../domain/session';

export const SESSION_FILTERS = [
  'live',
  'active',
  'idle',
  'attention',
  'stopped',
  'failed',
  'all',
  'archived',
] as const;
export type SessionFilter = (typeof SESSION_FILTERS)[number];
export const FILTER_LABELS: Record<SessionFilter, string> = {
  live: 'Live',
  all: 'All',
  active: 'Working',
  idle: 'Idle',
  attention: 'Needs you',
  stopped: 'Stopped',
  failed: 'Errors',
  archived: 'Archived',
};
export const SESSION_STATE_LABELS: Record<SessionState, string> = {
  requested: 'Queued',
  provisioning: 'Starting',
  ready: 'Connected',
  running: 'Working',
  idle: 'Idle',
  awaiting_input: 'Needs you',
  terminating: 'Stopping',
  terminated: 'Stopped',
  archived: 'Archived',
  failed: 'Error',
};
export const STOPPABLE_STATES = new Set<SessionState>([
  'requested',
  'provisioning',
  'ready',
  'running',
  'idle',
  'awaiting_input',
]);

export function matchesSessionFilter(session: Session, filter: SessionFilter): boolean {
  switch (filter) {
    case 'all':
      return true;
    case 'live':
      return STOPPABLE_STATES.has(session.state);
    case 'active':
      return session.state === 'running';
    case 'idle':
      return session.state === 'idle' || session.state === 'ready';
    case 'attention':
      return session.state === 'awaiting_input';
    case 'stopped':
      return session.state === 'terminated' || session.state === 'terminating';
    case 'failed':
      return session.state === 'failed';
    case 'archived':
      return session.state === 'archived';
  }
}

export const SIDEBAR_WIDTH = { default: 272, min: 232, max: 640, keyboardStep: 20 } as const;
