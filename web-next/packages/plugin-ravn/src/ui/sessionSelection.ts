/**
 * Opening a ravn session in the Sessions view.
 *
 * The Sessions view keeps its selection in the URL and in local storage, and
 * listens for `ravn:session-selected`. Every surface that can open a session —
 * the fleet detail panel, the Residents board — goes through here so the three
 * stay in step.
 */

import type { Session } from '../domain/session';
import { saveStorage } from './storage';

export const SESSION_STORAGE_KEY = 'ravn.session';
export const SESSION_SELECTED_EVENT = 'ravn:session-selected';

/** Stable session key — session ids are only unique within an instance. */
export function sessionKey(session: Pick<Session, 'id' | 'ravnId' | 'instanceId'>): string {
  return session.instanceId
    ? `${encodeURIComponent(session.instanceId)}:${encodeURIComponent(session.ravnId)}:${session.id}`
    : session.id;
}

export function dispatchSessionSelection(
  session: Pick<Session, 'id' | 'ravnId' | 'instanceId'>,
): void {
  saveStorage(SESSION_STORAGE_KEY, sessionKey(session));
  window.dispatchEvent(
    new CustomEvent(SESSION_SELECTED_EVENT, {
      detail: { sessionId: session.id, ravnId: session.ravnId, instanceId: session.instanceId },
    }),
  );
  const params = new URLSearchParams(window.location.search);
  params.set('session', session.id);
  params.set('ravn_id', session.ravnId);
  if (session.instanceId) params.set('instance_id', session.instanceId);
  else params.delete('instance_id');
  window.history.pushState(null, '', `/ravn/sessions?${params.toString()}`);
  window.dispatchEvent(new Event('popstate'));
}
