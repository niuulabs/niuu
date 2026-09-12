import { afterEach, describe, expect, it, vi } from 'vitest';
import { getCompactUxChatPrefs, getConversationView, setConversationView } from './compactUxPrefs';

const NS = 'niuu.compactUx.';

describe('compactUxPrefs — chat prefs', () => {
  afterEach(() => {
    localStorage.clear();
  });

  it('returns the compact defaults when nothing is set', () => {
    expect(getCompactUxChatPrefs()).toEqual({
      showMessageActions: false,
      showAgentAvatar: false,
      timestamp: 'hover',
      copyMode: 'hover',
    });
  });

  it('reads overridden boolean + enum prefs', () => {
    localStorage.setItem(`${NS}showMessageActions`, '1');
    localStorage.setItem(`${NS}showAgentAvatar`, 'true');
    localStorage.setItem(`${NS}timestamp`, 'always');
    localStorage.setItem(`${NS}copyMode`, 'inline');
    expect(getCompactUxChatPrefs()).toEqual({
      showMessageActions: true,
      showAgentAvatar: true,
      timestamp: 'always',
      copyMode: 'inline',
    });
  });

  it('falls back to the default for an out-of-range enum value', () => {
    localStorage.setItem(`${NS}timestamp`, 'bogus');
    expect(getCompactUxChatPrefs().timestamp).toBe('hover');
  });
});

describe('compactUxPrefs — conversation view', () => {
  afterEach(() => {
    localStorage.clear();
  });

  it('defaults to compact', () => {
    expect(getConversationView()).toBe('compact');
  });

  it('persists and reads back a set view', () => {
    setConversationView('expanded');
    expect(localStorage.getItem(`${NS}conversationView`)).toBe('expanded');
    expect(getConversationView()).toBe('expanded');

    setConversationView('compact');
    expect(getConversationView()).toBe('compact');
  });

  it('ignores an invalid persisted view', () => {
    localStorage.setItem(`${NS}conversationView`, 'sideways');
    expect(getConversationView()).toBe('compact');
  });

  it('falls back safely when localStorage throws', () => {
    const getSpy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(getCompactUxChatPrefs()).toEqual({
      showMessageActions: false,
      showAgentAvatar: false,
      timestamp: 'hover',
      copyMode: 'hover',
    });
    expect(getConversationView()).toBe('compact');
    getSpy.mockRestore();

    const setSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(() => setConversationView('expanded')).not.toThrow();
    setSpy.mockRestore();
  });

  it('returns defaults and no-ops under SSR (no window)', () => {
    vi.stubGlobal('window', undefined);
    expect(getCompactUxChatPrefs()).toEqual({
      showMessageActions: false,
      showAgentAvatar: false,
      timestamp: 'hover',
      copyMode: 'hover',
    });
    expect(getConversationView()).toBe('compact');
    expect(() => setConversationView('expanded')).not.toThrow();
    vi.unstubAllGlobals();
  });
});
