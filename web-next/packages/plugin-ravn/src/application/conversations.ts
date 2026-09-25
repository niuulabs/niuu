/**
 * Conversations with one ravn.
 *
 * Runtimes that hold several conversations list them through the resident
 * control API; every other ravn's conversations are the fleet sessions that
 * name it. Either way the page shows one list, live ones first.
 */

import type { Ravn } from '../domain/ravn';
import type { Session } from '../domain/session';

export function belongsToRavn(
  session: Pick<Session, 'ravnId' | 'instanceId'>,
  ravn: Pick<Ravn, 'id' | 'instanceId'>,
): boolean {
  if (session.ravnId !== ravn.id) return false;
  return !ravn.instanceId || !session.instanceId || session.instanceId === ravn.instanceId;
}

export function sortConversations(sessions: Session[]): Session[] {
  return [...sessions].sort((left, right) => {
    const live = Number(right.status === 'running') - Number(left.status === 'running');
    if (live !== 0) return live;
    return right.createdAt.localeCompare(left.createdAt);
  });
}

/** The requested conversation when it exists, else the live one, else the newest. */
export function pickConversation(sessions: Session[], requestedId: string | null): Session | null {
  if (requestedId) {
    const requested = sessions.find((session) => session.id === requestedId);
    if (requested) return requested;
  }
  return sortConversations(sessions)[0] ?? null;
}

export function conversationTitle(session: Pick<Session, 'id' | 'title'>): string {
  return session.title?.trim() || `Conversation ${session.id.slice(0, 8)}`;
}
