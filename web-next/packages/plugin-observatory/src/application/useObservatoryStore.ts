import { useSyncExternalStore } from 'react';
import type { EdgeLayer } from '../domain/edgeLayer';
import type { ComputeClass } from '../domain/computeClass';

// ── Types ─────────────────────────────────────────────────────────────────────

export type ObservatoryFilter = 'all' | 'agents' | 'runs' | 'services' | 'devices';

/**
 * Which stage the topology is drawn on.
 *
 * Not two products: one estate, one layout, one palette, seen either as a plan
 * or as a model. The choice lives in the store rather than in the page because
 * the topbar owns the control and the content slot owns the stage, and the two
 * are rendered into different parts of the shell.
 */
export type ObservatoryView = '2d' | '3d';

/** Persisted so the operator's choices survive a reload. */
const VIEW_STORAGE_KEY = 'niuu.observatory.view';
const MOTION_STORAGE_KEY = 'niuu.observatory.motion';

/**
 * Read a stored preference, falling back when storage will not answer.
 *
 * Private browsing, a quota that has been hit, an embedder that blocks it —
 * a preference that cannot be read is not worth failing a page load over.
 */
function readStored(key: string, fallback: string): string {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

function writeStored(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // A preference that cannot be saved is not worth failing a click over.
  }
}

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
  /** Plan or model. */
  view: ObservatoryView;
  /**
   * Whether the stage is animating.
   *
   * Covers both views, because it is one preference about one estate: the
   * pulses, the travelling marks, the well's ripples and the idle camera
   * drift all stop together. Held still, the Observatory is a diagram — which
   * is what you want for a screenshot, a long look at a dense cluster, or a
   * machine you would rather not have painting sixty times a second.
   */
  motion: boolean;
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
  setView(view: ObservatoryView): void;
  setMotion(motion: boolean): void;
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
    view: readStored(VIEW_STORAGE_KEY, '2d') === '3d' ? '3d' : '2d',
    // Animating unless the operator has said otherwise.
    motion: readStored(MOTION_STORAGE_KEY, 'on') !== 'off',
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
    setView(view: ObservatoryView): void {
      if (state.view === view) return;
      state = { ...state, view };
      writeStored(VIEW_STORAGE_KEY, view);
      subscribers.forEach((fn) => fn());
    },
    setMotion(motion: boolean): void {
      if (state.motion === motion) return;
      state = { ...state, motion };
      writeStored(MOTION_STORAGE_KEY, motion ? 'on' : 'off');
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
