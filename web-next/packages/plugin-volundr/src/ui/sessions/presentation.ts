import { modelLabel as readableModel } from '@niuulabs/domain';
import { sessionHarness, type Session, type SessionState } from '../../domain/session';

export const SESSION_FILTERS = [
  'unread',
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
  unread: 'Unread',
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
    case 'unread':
      return session.readState?.isUnread === true;
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

export const SIDEBAR_WIDTH = { default: 340, min: 288, max: 640, keyboardStep: 20 } as const;
/** The right dock (Changes, Files): its width when the person has not dragged it. */
export const DOCK_WIDTH = { default: 560, min: 340, max: 960, keyboardStep: 20 } as const;
/**
 * The conversation's narrowest usable width beside the dock, as Lexi's transcript minimum:
 * the dock narrows to keep it, and steps aside when even its own minimum does not fit.
 */
export const TRANSCRIPT_MIN_WIDTH = 480;

/** The model a row names: its own, else the harness that runs it. */
export function sessionModelLabel(
  session: Pick<Session, 'model' | 'sessionDefinition' | 'origin'>,
): string {
  if (session.model) return readableModel(session.model);
  return sessionHarness(session) === 'claude' ? 'Claude' : 'Codex';
}
