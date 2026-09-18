import { afterEach, describe, expect, it, vi } from 'vitest';
import { getCompactUxChatPrefs, getConversationView, setConversationView } from './compactUxPrefs';

const NS = 'niuu.compactUx.';
const FORGE_DEFAULTS = {
  showMessageActions: true,
  showAgentAvatar: true,
  timestamp: 'always',
  copyMode: 'inline',
};

describe('compactUxPrefs — chat prefs', () => {
  afterEach(() => {
    localStorage.clear();
  });

  it('returns the Forge review defaults when nothing is set', () => {
    expect(getCompactUxChatPrefs()).toEqual(FORGE_DEFAULTS);
  });

  it('reads the compact overrides for booleans and enums', () => {
    localStorage.setItem(`${NS}showMessageActions`, '0');
    localStorage.setItem(`${NS}showAgentAvatar`, 'false');
    localStorage.setItem(`${NS}timestamp`, 'hover');
    localStorage.setItem(`${NS}copyMode`, 'hover');
    expect(getCompactUxChatPrefs()).toEqual({
      showMessageActions: false,
      showAgentAvatar: false,
      timestamp: 'hover',
      copyMode: 'hover',
    });
  });

  it('falls back to the default for an out-of-range enum value', () => {
    localStorage.setItem(`${NS}timestamp`, 'bogus');
    expect(getCompactUxChatPrefs().timestamp).toBe('always');
  });
});

describe('compactUxPrefs — conversation view', () => {
  afterEach(() => {
    localStorage.clear();
  });

  it('defaults to expanded', () => {
    expect(getConversationView()).toBe('expanded');
  });

  it('persists and reads back a set view', () => {
    setConversationView('compact');
    expect(localStorage.getItem(`${NS}conversationView`)).toBe('compact');
    expect(getConversationView()).toBe('compact');

    setConversationView('expanded');
    expect(getConversationView()).toBe('expanded');
  });

  it('ignores an invalid persisted view', () => {
    localStorage.setItem(`${NS}conversationView`, 'sideways');
    expect(getConversationView()).toBe('expanded');
  });

  it('falls back safely when localStorage throws', () => {
    const getSpy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(getCompactUxChatPrefs()).toEqual(FORGE_DEFAULTS);
    expect(getConversationView()).toBe('expanded');
    getSpy.mockRestore();

    const setSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(() => setConversationView('expanded')).not.toThrow();
    setSpy.mockRestore();
  });

  it('returns defaults and no-ops under SSR (no window)', () => {
    vi.stubGlobal('window', undefined);
    expect(getCompactUxChatPrefs()).toEqual(FORGE_DEFAULTS);
    expect(getConversationView()).toBe('expanded');
    expect(() => setConversationView('expanded')).not.toThrow();
    vi.unstubAllGlobals();
  });
});
