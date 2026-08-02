import { useSyncExternalStore } from 'react';
import type { EdgeLayer } from '../domain/edgeLayer';
import type { ComputeClass } from '../domain/computeClass';

// ── Types ─────────────────────────────────────────────────────────────────────

export type ObservatoryFilter = 'all' | 'agents' | 'runs' | 'services' | 'devices';

interface ObservatoryStoreState {
  selectedId: string | null;
  /**
   * The node the camera should travel to, or null when it should stay put.
   *
   * Separate from `selectedId` because not every selection is a request to go
   * somewhere. Picking a resident out of the rail is — it may be off screen.
   * Picking a mesh is not: its members are scattered across clusters, which is
   * the entire point of a mesh, so flying to whichever one happens to be first
   * frames the least representative thing in it.
   */
  focusId: string | null;
  filter: ObservatoryFilter;
  /** Layers the operator has switched off. Empty means everything is shown. */
  hiddenLayers: ReadonlySet<EdgeLayer>;
  /** Compute classes switched off. Their nodes fade rather than disappear. */
  hiddenCompute: ReadonlySet<ComputeClass>;
  /** Present mode: rail, inspector and feed step aside, leaving the graph. */
  presenting: boolean;
}

interface ObservatoryStore {
  read(): ObservatoryStoreState;
  setSelected(id: string | null, options?: { focus?: boolean }): void;
  setFilter(filter: ObservatoryFilter): void;
  toggleLayer(layer: EdgeLayer): void;
  setHiddenLayers(layers: ReadonlySet<EdgeLayer>): void;
  toggleCompute(compute: ComputeClass): void;
  setHiddenCompute(compute: ReadonlySet<ComputeClass>): void;
  setPresenting(presenting: boolean): void;
  subscribe(fn: () => void): () => void;
}

/**
 * The layers the calm view puts down — and the state the Observatory opens in.
 * Exported so the filter strip's `calm` button and the initial state cannot
 * disagree about what calm means.
 */
export const CALM_HIDDEN_LAYERS: readonly EdgeLayer[] = ['platform', 'observability'];

// ── Module-level singleton ────────────────────────────────────────────────────
// All three plugin slots (content, subnav, topbar) share state through this
// store. The content slot owns the data; subnav/topbar subscribe and read.

let _store: ObservatoryStore | null = null;

export function getObservatoryStore(): ObservatoryStore {
  if (_store) return _store;

  const subscribers = new Set<() => void>();
  let state: ObservatoryStoreState = {
    selectedId: null,
    focusId: null,
    filter: 'all',
    // Opens calm. Platform wiring and telemetry are the two layers that
    // dominate by edge count and say the least about what the estate is doing,
    // so showing everything at once makes the first look a hairball. Both are
    // one click away in the filter strip.
    hiddenLayers: new Set<EdgeLayer>(CALM_HIDDEN_LAYERS),
    hiddenCompute: new Set<ComputeClass>(),
    presenting: false,
  };

  _store = {
    read(): ObservatoryStoreState {
      return state;
    },
    setSelected(id: string | null, options?: { focus?: boolean }): void {
      if (state.selectedId === id) return;
      const focus = options?.focus ?? true;
      state = { ...state, selectedId: id, focusId: focus ? id : null };
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
    setPresenting(presenting: boolean): void {
      if (state.presenting === presenting) return;
      state = { ...state, presenting };
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
