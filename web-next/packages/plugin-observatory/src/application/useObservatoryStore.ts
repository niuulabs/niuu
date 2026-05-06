import { useSyncExternalStore } from 'react';

// ── Types ─────────────────────────────────────────────────────────────────────

export type ObservatoryFilter = 'all' | 'agents' | 'raids' | 'services' | 'devices';

interface ObservatoryStoreState {
  selectedId: string | null;
  filter: ObservatoryFilter;
}

interface ObservatoryStore {
  read(): ObservatoryStoreState;
  setSelected(id: string | null): void;
  setFilter(filter: ObservatoryFilter): void;
  subscribe(fn: () => void): () => void;
}

// ── Module-level singleton ────────────────────────────────────────────────────
// All three plugin slots (content, subnav, topbar) share state through this
// store. The content slot owns the data; subnav/topbar subscribe and read.

let _store: ObservatoryStore | null = null;

export function getObservatoryStore(): ObservatoryStore {
  if (_store) return _store;

  const subscribers = new Set<() => void>();
  let state: ObservatoryStoreState = { selectedId: null, filter: 'all' };

  _store = {
    read(): ObservatoryStoreState {
      return state;
    },
    setSelected(id: string | null): void {
      if (state.selectedId === id) return;
      state = { ...state, selectedId: id };
      subscribers.forEach((fn) => fn());
    },
    setFilter(filter: ObservatoryFilter): void {
      if (state.filter === filter) return;
      state = { ...state, filter };
      subscribers.forEach((fn) => fn());
    },
    subscribe(fn: () => void): () => void {
      subscribers.add(fn);
      return () => subscribers.delete(fn);
    },
  };

  return _store;
}

/** Reset the singleton for testing — clears state and subscribers. */
export function __resetObservatoryStore(): void {
  _store = null;
}

/**
 * React hook that subscribes to the Observatory store using useSyncExternalStore,
 * which is tear-safe in React 19's concurrent renderer.
 */
export function useObservatoryStore(): [ObservatoryStoreState, ObservatoryStore] {
  const store = getObservatoryStore();
  const state = useSyncExternalStore(store.subscribe, store.read);
  return [state, store];
}
