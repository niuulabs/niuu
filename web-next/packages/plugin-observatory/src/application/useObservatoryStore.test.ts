import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  __resetObservatoryStore,
  getObservatoryStore,
  useObservatoryStore,
} from './useObservatoryStore';

describe('useObservatoryStore', () => {
  afterEach(() => {
    __resetObservatoryStore();
  });

  it('reuses the singleton store instance', () => {
    expect(getObservatoryStore()).toBe(getObservatoryStore());
  });

  it('publishes changes and ignores no-op updates', () => {
    const notify = vi.fn();
    const store = getObservatoryStore();
    const unsubscribe = store.subscribe(notify);

    const { result } = renderHook(() => useObservatoryStore());

    expect(result.current[0]).toEqual({ selectedId: null, filter: 'all' });

    act(() => {
      store.setSelected('agent-1');
      store.setSelected('agent-1');
      store.setFilter('agents');
      store.setFilter('agents');
    });

    expect(result.current[0]).toEqual({ selectedId: 'agent-1', filter: 'agents' });
    expect(notify).toHaveBeenCalledTimes(2);

    unsubscribe();
  });
});
