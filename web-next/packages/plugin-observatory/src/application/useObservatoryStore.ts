import { useSyncExternalStore } from 'react';
import type { EdgeLayer } from '../domain/edgeLayer';
import type { ComputeClass } from '../domain/computeClass';

// ── Types ─────────────────────────────────────────────────────────────────────

export type ObservatoryFilter = 'all' | 'agents' | 'runs' | 'services' | 'devices';

interface ObservatoryStoreState {
  selectedId: string | null;
  filter: ObservatoryFilter;
  /** Layers the operator has switched off. Empty means everything is shown. */
  hiddenLayers: ReadonlySet<EdgeLayer>;
  /** Compute classes switched off. Their nodes fade rather than disappear. */
  hiddenCompute: ReadonlySet<ComputeClass>;
}

interface ObservatoryStore {
  read(): ObservatoryStoreState;
  setSelected(id: string | null): void;
  setFilter(filter: ObservatoryFilter): void;
  toggleLayer(layer: EdgeLayer): void;
  setHiddenLayers(layers: ReadonlySet<EdgeLayer>): void;
  toggleCompute(compute: ComputeClass): void;
  setHiddenCompute(compute: ReadonlySet<ComputeClass>): void;
  subscribe(fn: () => void): () => void;
}

// ── Module-level singleton ────────────────────────────────────────────────────
// All three plugin slots (content, subnav, topbar) share state through this
// store. The content slot owns the data; subnav/topbar subscribe and read.

let _store: ObservatoryStore | null = null;

export function getObservatoryStore(): ObservatoryStore {
  if (_store) return _store;

  const subscribers = new Set<() => void>();
  let state: ObservatoryStoreState = {
    selectedId: null,
    filter: 'all',
    hiddenLayers: new Set<EdgeLayer>(),
    hiddenCompute: new Set<ComputeClass>(),
  };

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
    toggleLayer(layer: EdgeLayer): void {
      const next = new Set(state.hiddenLayers);
      if (next.has(layer)) next.delete(layer);
      else next.add(layer);
      state = { ...state, hiddenLayers: next };
      subscribers.forEach((fn) => fn());
    },
    setHiddenLayers(layers: ReadonlySet<EdgeLayer>): void {
      state = { ...state, hiddenLayers: new Set(layers) };
      subscribers.forEach((fn) => fn());
    },
    toggleCompute(compute: ComputeClass): void {
      const next = new Set(state.hiddenCompute);
      if (next.has(compute)) next.delete(compute);
      else next.add(compute);
      state = { ...state, hiddenCompute: next };
      subscribers.forEach((fn) => fn());
    },
    setHiddenCompute(compute: ReadonlySet<ComputeClass>): void {
      state = { ...state, hiddenCompute: new Set(compute) };
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
