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
    localStorage.clear();
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
      focusId: null,
      filter: 'all',
      hiddenLayers: new Set(CALM_HIDDEN_LAYERS),
      hiddenCompute: new Set(),
      presenting: false,
      view: '2d',
    });

    act(() => {
      store.setSelected('agent-1');
      store.setSelected('agent-1');
      store.setFilter('agents');
      store.setFilter('agents');
    });

    expect(result.current[0]).toEqual({
      selectedId: 'agent-1',
      focusId: 'agent-1',
      filter: 'agents',
      hiddenLayers: new Set(CALM_HIDDEN_LAYERS),
      hiddenCompute: new Set(),
      presenting: false,
      view: '2d',
    });
    expect(notify).toHaveBeenCalledTimes(2);

    unsubscribe();
  });

  it('holds a selection the camera should not travel to', () => {
    // Picking a mesh marks its members without flying to one of them.
    const store = getObservatoryStore();
    const { result } = renderHook(() => useObservatoryStore());

    act(() => store.setSelected('mesh-member-1', { focus: false }));

    expect(result.current[0].selectedId).toBe('mesh-member-1');
    expect(result.current[0].focusId).toBeNull();
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

  describe('view', () => {
    it('opens on the plan', () => {
      expect(getObservatoryStore().read().view).toBe('2d');
    });

    it('switches stage and publishes the change once', () => {
      const store = getObservatoryStore();
      let calls = 0;
      const unsubscribe = store.subscribe(() => (calls += 1));

      store.setView('3d');
      store.setView('3d');
      unsubscribe();

      expect(store.read().view).toBe('3d');
      expect(calls).toBe(1);
    });

    it('remembers the choice for the next visit', () => {
      getObservatoryStore().setView('3d');
      expect(localStorage.getItem('niuu.observatory.view')).toBe('3d');

      __resetObservatoryStore();
      expect(getObservatoryStore().read().view).toBe('3d');
    });

    it('opens on the plan when storage is unreadable, rather than failing', () => {
      // Private browsing, or a storage quota that has been hit. A preference
      // that cannot be read is not worth failing a page load over.
      const getItem = vi.spyOn(globalThis.localStorage, 'getItem').mockImplementation(() => {
        throw new Error('denied');
      });
      const setItem = vi.spyOn(globalThis.localStorage, 'setItem').mockImplementation(() => {
        throw new Error('denied');
      });

      __resetObservatoryStore();
      const store = getObservatoryStore();
      expect(store.read().view).toBe('2d');
      expect(() => store.setView('3d')).not.toThrow();
      expect(store.read().view).toBe('3d');

      getItem.mockRestore();
      setItem.mockRestore();
    });
  });
});
