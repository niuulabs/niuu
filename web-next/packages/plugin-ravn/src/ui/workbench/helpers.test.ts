import { describe, expect, it, vi } from 'vitest';
import { errorText } from './errorText';
import { legacySessionSearch } from './legacyRoutes';
import {
  SESSION_SELECTED_EVENT,
  conversationHref,
  dispatchSessionSelection,
  sessionKey,
} from '../sessionSelection';

describe('errorText', () => {
  it('prefers the server detail and unwraps a proxied detail', () => {
    const error = Object.assign(new Error('API request failed: 502'), {
      detail: '{"detail":"Failed to read resident logs: connection aborted"}',
    });
    expect(errorText(error, 'fallback')).toBe('Failed to read resident logs: connection aborted');
  });

  it('keeps a plain detail and a detail that is not JSON', () => {
    expect(errorText({ detail: 'budget persistence is unavailable' }, 'x')).toBe(
      'budget persistence is unavailable',
    );
    expect(errorText({ detail: '{not json' }, 'x')).toBe('{not json');
    expect(errorText({ detail: '{"other":1}' }, 'x')).toBe('{"other":1}');
  });

  it('falls to the message, then the fallback', () => {
    expect(errorText(new Error('boom'), 'x')).toBe('boom');
    expect(errorText(null, 'fallback')).toBe('fallback');
    expect(errorText({}, 'fallback')).toBe('fallback');
  });
});

describe('legacySessionSearch', () => {
  it('maps an old conversation link onto the workbench', () => {
    expect(legacySessionSearch({ session: 's', ravn_id: 'r', instance_id: 'i' })).toEqual({
      ravn: 'r',
      instance_id: 'i',
      tab: 'chat',
      session: 's',
    });
    expect(legacySessionSearch({})).toEqual({ tab: 'chat' });
  });
});

describe('session selection', () => {
  const session = { id: 's-1', ravnId: 'r-1', instanceId: 'i 1' };

  it('keys sessions by target and ravn', () => {
    expect(sessionKey(session)).toBe('i%201:r-1:s-1');
    expect(sessionKey({ id: 's-1', ravnId: 'r-1' })).toBe('s-1');
  });

  it('links a conversation to its ravn chat tab', () => {
    expect(conversationHref(session)).toBe('/ravn?ravn=r-1&instance_id=i+1&tab=chat&session=s-1');
    expect(conversationHref({ id: 's', ravnId: 'r' })).toBe('/ravn?ravn=r&tab=chat&session=s');
  });

  it('announces the selection and moves the browser there', () => {
    const listener = vi.fn();
    window.addEventListener(SESSION_SELECTED_EVENT, listener);
    dispatchSessionSelection(session);
    window.removeEventListener(SESSION_SELECTED_EVENT, listener);
    expect(listener).toHaveBeenCalled();
    expect(`${window.location.pathname}${window.location.search}`).toBe(conversationHref(session));
  });
});
