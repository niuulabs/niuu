/**
 * Opening a ravn conversation.
 *
 * A conversation lives on its ravn's Chat tab in the workbench. Every surface
 * that can open one — the Residents board, other plugins — goes through here
 * so the link shape stays in one place.
 */

import type { Session } from '../domain/session';

export const SESSION_SELECTED_EVENT = 'ravn:session-selected';

/** Stable session key — session ids are only unique within an instance. */
export function sessionKey(session: Pick<Session, 'id' | 'ravnId' | 'instanceId'>): string {
  return session.instanceId
    ? `${encodeURIComponent(session.instanceId)}:${encodeURIComponent(session.ravnId)}:${session.id}`
    : session.id;
}

/** Where a conversation opens: its ravn, on the Chat tab, with it selected. */
export function conversationHref(session: Pick<Session, 'id' | 'ravnId' | 'instanceId'>): string {
  const params = new URLSearchParams();
  params.set('ravn', session.ravnId);
  if (session.instanceId) params.set('instance_id', session.instanceId);
  params.set('tab', 'chat');
  params.set('session', session.id);
  return `/ravn?${params.toString()}`;
}

export function dispatchSessionSelection(
  session: Pick<Session, 'id' | 'ravnId' | 'instanceId'>,
): void {
  window.dispatchEvent(
    new CustomEvent(SESSION_SELECTED_EVENT, {
      detail: { sessionId: session.id, ravnId: session.ravnId, instanceId: session.instanceId },
    }),
  );
  window.history.pushState(null, '', conversationHref(session));
  window.dispatchEvent(new Event('popstate'));
}
