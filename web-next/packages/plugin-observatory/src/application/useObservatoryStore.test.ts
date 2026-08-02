import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  CALM_HIDDEN_LAYERS,
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

    expect(result.current[0]).toEqual({
      selectedId: null,
      filter: 'all',
      hiddenLayers: new Set(CALM_HIDDEN_LAYERS),
      hiddenCompute: new Set(),
      presenting: false,
    });

    act(() => {
      store.setSelected('agent-1');
      store.setSelected('agent-1');
      store.setFilter('agents');
      store.setFilter('agents');
    });

    expect(result.current[0]).toEqual({
      selectedId: 'agent-1',
      filter: 'agents',
      hiddenLayers: new Set(CALM_HIDDEN_LAYERS),
      hiddenCompute: new Set(),
      presenting: false,
    });
    expect(notify).toHaveBeenCalledTimes(2);

    unsubscribe();
  });
});

describe('layer visibility', () => {
  beforeEach(() => {
    __resetObservatoryStore();
  });

  it('opens calm rather than showing every layer at once', () => {
    // Platform and telemetry dominate by edge count; leading with them makes
    // the first look a hairball.
    const { hiddenLayers } = getObservatoryStore().read();
    expect([...hiddenLayers].sort()).toEqual([...CALM_HIDDEN_LAYERS].sort());
  });

  it('toggles a layer off and back on', () => {
    const store = getObservatoryStore();
    store.toggleLayer('memory');
    expect(store.read().hiddenLayers.has('memory')).toBe(true);
    store.toggleLayer('memory');
    expect(store.read().hiddenLayers.has('memory')).toBe(false);
  });

  it('notifies subscribers on every layer change', () => {
    const store = getObservatoryStore();
    let calls = 0;
    const unsubscribe = store.subscribe(() => (calls += 1));
    store.toggleLayer('mesh');
    store.toggleLayer('inference');
    unsubscribe();
    expect(calls).toBe(2);
  });

  it('replaces the whole hidden set', () => {
    const store = getObservatoryStore();
    store.setHiddenLayers(new Set(['mesh', 'memory']));
    expect(store.read().hiddenLayers.size).toBe(2);
    store.setHiddenLayers(new Set());
    expect(store.read().hiddenLayers.size).toBe(0);
  });

  it('copies the incoming set so later mutation cannot leak in', () => {
    const store = getObservatoryStore();
    const incoming = new Set<'mesh'>(['mesh']);
    store.setHiddenLayers(incoming);
    incoming.clear();
    expect(store.read().hiddenLayers.has('mesh')).toBe(true);
  });
});
