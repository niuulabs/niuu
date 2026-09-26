import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useMemoryUrl } from './useMemoryUrl';
import type { MemoryViewSearch } from './memoryViewSearch';

let currentSearch: MemoryViewSearch = {};
const navigateMock = vi.fn();

vi.mock('@tanstack/react-router', () => ({
  useSearch: () => currentSearch,
  useNavigate: () => navigateMock,
}));

function applyNavigate(): MemoryViewSearch {
  const call = navigateMock.mock.calls.at(-1)![0];
  const searcher = call.search as (prev: Record<string, unknown>) => Record<string, unknown>;
  return searcher(currentSearch as Record<string, unknown>) as MemoryViewSearch;
}

describe('useMemoryUrl', () => {
  beforeEach(() => {
    currentSearch = {};
    navigateMock.mockClear();
  });

  it('patches merge into the current search and drop nullish fields', () => {
    currentSearch = { mount: 'platform' };
    const { result } = renderHook(() => useMemoryUrl());
    result.current.focusNode('/a');
    expect(applyNavigate()).toEqual({ mount: 'platform', focus: '/a' });
  });

  it('setDepth / setMount / setView / setColour / setAsOf patch their field', () => {
    const { result } = renderHook(() => useMemoryUrl());
    result.current.setDepth(2);
    expect(applyNavigate()).toEqual({ depth: 2 });
    result.current.setMount('shared');
    expect(applyNavigate()).toEqual({ mount: 'shared' });
    result.current.setView('2d');
    expect(applyNavigate()).toEqual({ view: '2d' });
    result.current.setColour('age');
    expect(applyNavigate()).toEqual({ colour: 'age' });
    result.current.setAsOf('2026-04-01');
    expect(applyNavigate()).toEqual({ asOf: '2026-04-01' });
  });

  describe('handleEscape', () => {
    it('clears a traced path first, without touching the URL', () => {
      currentSearch = { focus: '/a' };
      const { result } = renderHook(() => useMemoryUrl());
      const clearTracedPath = vi.fn();
      result.current.handleEscape(['/a', '/b'], clearTracedPath);
      expect(clearTracedPath).toHaveBeenCalled();
      expect(navigateMock).not.toHaveBeenCalled();
    });

    it('clears focus next when no path is traced', () => {
      currentSearch = { focus: '/a', asOf: '2026-04-01' };
      const { result } = renderHook(() => useMemoryUrl());
      result.current.handleEscape([], vi.fn());
      expect(applyNavigate()).toEqual({ asOf: '2026-04-01' });
    });

    it('exits replay last', () => {
      currentSearch = { asOf: '2026-04-01' };
      const { result } = renderHook(() => useMemoryUrl());
      result.current.handleEscape([], vi.fn());
      expect(applyNavigate()).toEqual({});
    });

    it('does nothing when there is nothing to step back out of', () => {
      currentSearch = {};
      const { result } = renderHook(() => useMemoryUrl());
      result.current.handleEscape([], vi.fn());
      expect(navigateMock).not.toHaveBeenCalled();
    });
  });
});
