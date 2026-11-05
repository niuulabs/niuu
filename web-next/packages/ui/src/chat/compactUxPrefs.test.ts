import { afterEach, describe, expect, it, vi } from 'vitest';
import { getCompactUxChatPrefs, getConversationView, setConversationView } from './compactUxPrefs';

const NS = 'niuu.compactUx.';
const FORGE_DEFAULTS = {
  showMessageActions: true,
  showAgentAvatar: false,
  timestamp: 'always',
  copyMode: 'inline',
  toolRuns: 'rows',
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
    localStorage.setItem(`${NS}showAgentAvatar`, 'true');
    localStorage.setItem(`${NS}timestamp`, 'hover');
    localStorage.setItem(`${NS}copyMode`, 'hover');
    localStorage.setItem(`${NS}toolRuns`, 'cards');
    expect(getCompactUxChatPrefs()).toEqual({
      showMessageActions: false,
      showAgentAvatar: true,
      timestamp: 'hover',
      copyMode: 'hover',
      toolRuns: 'cards',
    });
  });

  it('falls back to the default for an out-of-range enum value', () => {
    localStorage.setItem(`${NS}timestamp`, 'bogus');
    expect(getCompactUxChatPrefs().timestamp).toBe('always');
    localStorage.setItem(`${NS}toolRuns`, 'bogus');
    expect(getCompactUxChatPrefs().toolRuns).toBe('rows');
  });
});

describe('compactUxPrefs — conversation view', () => {
  afterEach(() => {
    localStorage.clear();
  });

  it("defaults to compact, folding each turn's work as Lexi does", () => {
    expect(getConversationView()).toBe('compact');
  });

  it('persists and reads back a set view', () => {
    setConversationView('expanded');
    expect(localStorage.getItem(`${NS}conversationFold`)).toBe('expanded');
    expect(getConversationView()).toBe('expanded');

    setConversationView('compact');
    expect(getConversationView()).toBe('compact');
  });

  it('ignores an invalid persisted view', () => {
    localStorage.setItem(`${NS}conversationFold`, 'sideways');
    expect(getConversationView()).toBe('compact');
  });

  it('falls back safely when localStorage throws', () => {
    const getSpy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(getCompactUxChatPrefs()).toEqual(FORGE_DEFAULTS);
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
    expect(getCompactUxChatPrefs()).toEqual(FORGE_DEFAULTS);
    expect(getConversationView()).toBe('compact');
    expect(() => setConversationView('expanded')).not.toThrow();
    vi.unstubAllGlobals();
  });
});
