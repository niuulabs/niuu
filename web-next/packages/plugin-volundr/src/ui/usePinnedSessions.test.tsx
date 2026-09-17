import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { usePinnedSessions } from './usePinnedSessions';

describe('pinned sessions', () => {
  beforeEach(() => localStorage.clear());

  it('remembers pin order and synchronizes separate controls and browser tabs', () => {
    const first = renderHook(() => usePinnedSessions());
    const second = renderHook(() => usePinnedSessions());
    act(() => first.result.current.toggle('one'));
    act(() => second.result.current.toggle('two'));
    expect(first.result.current.ids).toEqual(['one', 'two']);
    act(() => first.result.current.toggle('one'));
    expect(second.result.current.ids).toEqual(['two']);
    expect(JSON.parse(localStorage.getItem('niuu.forge.pinnedSessions')!)).toEqual(['two']);
    const remounted = renderHook(() => usePinnedSessions());
    expect(remounted.result.current.pinned.has('two')).toBe(true);
    act(() => {
      localStorage.setItem('niuu.forge.pinnedSessions', '["other-tab"]');
      window.dispatchEvent(new Event('storage'));
    });
    expect(first.result.current.ids).toEqual(['other-tab']);
    expect(second.result.current.ids).toEqual(['other-tab']);
  });

  it.each(['{', '{}', 'null', '[1,null,"",false]'])(
    'ignores invalid optional saved pins: %s',
    (stored) => {
      localStorage.setItem('niuu.forge.pinnedSessions', stored);
      const { result } = renderHook(() => usePinnedSessions());
      expect(result.current.ids).toEqual([]);
      act(() => result.current.toggle('valid'));
      expect(result.current.ids).toEqual(['valid']);
    },
  );

  it('deduplicates saved pins without losing their order', () => {
    localStorage.setItem('niuu.forge.pinnedSessions', '["two","one","two",null]');
    const { result } = renderHook(() => usePinnedSessions());
    expect(result.current.ids).toEqual(['two', 'one']);
  });
});
