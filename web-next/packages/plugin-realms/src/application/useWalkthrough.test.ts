import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { FIRST_REALM_WALKTHROUGH, useWalkthrough } from './useWalkthrough';

describe('useWalkthrough', () => {
  afterEach(() => localStorage.clear());

  it('starts at the first step and advances as steps are marked done', () => {
    const { result } = renderHook(() => useWalkthrough(FIRST_REALM_WALKTHROUGH));
    expect(result.current.currentStepId).toBe('template');
    expect(result.current.complete).toBe(false);
    act(() => result.current.markDone('template'));
    expect(result.current.currentStepId).toBe('connect');
    act(() => result.current.markDone('template'));
    expect(result.current.done).toEqual(['template']);
  });

  it('completes, hides and resets', () => {
    const { result } = renderHook(() => useWalkthrough(FIRST_REALM_WALKTHROUGH));
    act(() => {
      for (const step of FIRST_REALM_WALKTHROUGH.steps) result.current.markDone(step.id);
    });
    expect(result.current.complete).toBe(true);
    act(() => result.current.setHidden(true));
    expect(result.current.hidden).toBe(true);
    act(() => result.current.reset());
    expect(result.current.done).toEqual([]);
    expect(result.current.hidden).toBe(false);
  });

  it('ignores corrupt stored progress', () => {
    localStorage.setItem('niuu.compactUx.walkthrough.first-realm', '{not json');
    const { result } = renderHook(() => useWalkthrough(FIRST_REALM_WALKTHROUGH));
    expect(result.current.done).toEqual([]);
  });
});
