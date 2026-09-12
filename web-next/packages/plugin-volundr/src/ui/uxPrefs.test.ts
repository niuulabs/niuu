import { afterEach, describe, expect, it, vi } from 'vitest';
import { getShowDebugMeta, setShowDebugMeta } from './uxPrefs';

const KEY = 'niuu.compactUx.showDebugMeta';

describe('uxPrefs — showDebugMeta', () => {
  afterEach(() => {
    localStorage.clear();
  });

  it('defaults to false when unset', () => {
    expect(getShowDebugMeta()).toBe(false);
  });

  it('treats "1" and "true" as enabled', () => {
    localStorage.setItem(KEY, '1');
    expect(getShowDebugMeta()).toBe(true);
    localStorage.setItem(KEY, 'true');
    expect(getShowDebugMeta()).toBe(true);
  });

  it('treats any other value as disabled', () => {
    localStorage.setItem(KEY, '0');
    expect(getShowDebugMeta()).toBe(false);
    localStorage.setItem(KEY, 'nope');
    expect(getShowDebugMeta()).toBe(false);
  });

  it('persists and round-trips through setShowDebugMeta', () => {
    setShowDebugMeta(true);
    expect(localStorage.getItem(KEY)).toBe('1');
    expect(getShowDebugMeta()).toBe(true);

    setShowDebugMeta(false);
    expect(localStorage.getItem(KEY)).toBe('0');
    expect(getShowDebugMeta()).toBe(false);
  });

  it('falls back safely when localStorage throws', () => {
    const getSpy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(getShowDebugMeta()).toBe(false);
    getSpy.mockRestore();

    const setSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(() => setShowDebugMeta(true)).not.toThrow();
    setSpy.mockRestore();
  });

  it('returns the default and no-ops under SSR (no window)', () => {
    vi.stubGlobal('window', undefined);
    expect(getShowDebugMeta()).toBe(false);
    expect(() => setShowDebugMeta(true)).not.toThrow();
    vi.unstubAllGlobals();
  });
});
