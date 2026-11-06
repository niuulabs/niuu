import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useMediaQuery } from './useMediaQuery';

function stubMatchMedia(initial: boolean) {
  let matches = initial;
  const listeners = new Set<() => void>();
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({
      get matches() {
        return matches;
      },
      addEventListener: (_: string, listener: () => void) => listeners.add(listener),
      removeEventListener: (_: string, listener: () => void) => listeners.delete(listener),
    })),
  );
  return {
    change(next: boolean) {
      matches = next;
      listeners.forEach((listener) => listener());
    },
    listeners,
  };
}

describe('useMediaQuery', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('reports the match and follows changes until unmounted', () => {
    const media = stubMatchMedia(false);
    const { result, unmount } = renderHook(() => useMediaQuery('(max-width: 1100px)'));
    expect(result.current).toBe(false);
    act(() => media.change(true));
    expect(result.current).toBe(true);
    unmount();
    expect(media.listeners.size).toBe(0);
  });
});
