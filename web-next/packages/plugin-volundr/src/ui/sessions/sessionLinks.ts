import type { SessionLink, SessionLinkTone, SessionLinksValue } from '@niuulabs/ui';
import type { Session } from '../../domain/session';
import { stateTone, type StateTone } from '../detail/sessionHeaderModel';
import { SESSION_STATE_LABELS } from './presentation';

const LINK_TONE: Record<StateTone, SessionLinkTone> = {
  working: 'ok',
  attention: 'warn',
  critical: 'critical',
  muted: 'muted',
};

/** How a session's state reads in a transcript card or a pill: working, waiting, failed, quiet. */
export function sessionLinkTone(session: Pick<Session, 'state'>): SessionLinkTone {
  return LINK_TONE[stateTone(session.state)];
}

/** Where a session opens: the session page for its id. */
export function sessionHref(id: string): string {
  return `/volundr/sessions/${encodeURIComponent(id)}`;
}

/**
 * What the transcript's cards and relay headers can open: a session of the list, found by
 * id or by name. The session being read is not one of them: a message cannot link to the
 * conversation it is already in.
 */
export function buildSessionLinks(
  sessions: readonly Session[],
  selfId: string,
  open: (id: string) => void,
): SessionLinksValue {
  const others = sessions.filter((session) => session.id !== selfId);
  return {
    resolve: (idOrName) => {
      const named = others.filter((session) => session.name === idOrName);
      // A name two sessions share does not say which one is meant.
      const session =
        others.find((candidate) => candidate.id === idOrName) ??
        (named.length === 1 ? named[0] : undefined);
      if (!session) return null;
      return {
        id: session.id,
        name: session.name || session.personaName || session.id,
        stateLabel: SESSION_STATE_LABELS[session.state],
        tone: sessionLinkTone(session),
        href: sessionHref(session.id),
      } satisfies SessionLink;
    },
    open,
  };
}
