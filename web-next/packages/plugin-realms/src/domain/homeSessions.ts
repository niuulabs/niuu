import { sessionActivityTs, type Session, type SessionState } from '@niuulabs/plugin-volundr';

/** Sessions on the home page: the ones blocked on you, and the ones worth going back to. */

/** States that mean the session is still on its feet. */
const LIVE_STATES: ReadonlySet<SessionState> = new Set<SessionState>([
  'running',
  'provisioning',
  'requested',
  'ready',
  'idle',
  'terminating',
]);

export function isLive(session: Session): boolean {
  return LIVE_STATES.has(session.state);
}

/** Blocked on an answer from you, most recent first. */
export function needsYouSessions(sessions: Session[] | undefined): Session[] {
  return (sessions ?? [])
    .filter((session) => session.state === 'awaiting_input')
    .sort((a, b) => sessionActivityTs(b) - sessionActivityTs(a));
}

/** What to go back to: live sessions first, then by last activity. */
export function continueSessions(sessions: Session[] | undefined, limit: number): Session[] {
  return (sessions ?? [])
    .filter((session) => session.state !== 'awaiting_input')
    .sort(
      (a, b) =>
        Number(isLive(b)) - Number(isLive(a)) || sessionActivityTs(b) - sessionActivityTs(a),
    )
    .slice(0, limit);
}

/** What to call a session in a row: its title, its name, else its id. */
export function sessionLabel(session: Session): string {
  return session.title || session.name || session.id;
}
