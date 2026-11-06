/**
 * The right dock's open panes, as Lexi's: tabs the person opens from "+", one shown at a time,
 * each closable. Changes and Files are two of its kinds; a previewed file and a side chat are
 * the others. Live preview is left out until a session port has a safe browser route.
 */
export type DockPane =
  | { kind: 'changes' }
  | { kind: 'files' }
  | { kind: 'file'; path: string }
  /** A side chat: empty until forked or started, then the child session it shows. */
  | { kind: 'side'; childId: string | null };

export type DockPaneKind = DockPane['kind'];

export interface DockState {
  panes: readonly DockPane[];
  /** The pane shown, by {@link dockPaneId}; null with no panes. */
  active: string | null;
  /** Whether the dock is shown. Hiding it keeps its panes for when it comes back. */
  open: boolean;
}

export const EMPTY_DOCK: DockState = { panes: [], active: null, open: false };

/** One tab per Changes, Files and side chat; one per previewed file. */
export function dockPaneId(pane: DockPane): string {
  return pane.kind === 'file' ? `file:${pane.path}` : pane.kind;
}

export type DockAction =
  | { type: 'open'; pane: DockPane }
  | { type: 'select'; id: string }
  | { type: 'close'; id: string }
  | { type: 'toggle' }
  | { type: 'hide' }
  | { type: 'side'; childId: string };

export function dockReducer(state: DockState, action: DockAction): DockState {
  switch (action.type) {
    case 'open': {
      const id = dockPaneId(action.pane);
      const present = state.panes.some((pane) => dockPaneId(pane) === id);
      // Opening an open side chat shows it as it is, rather than forgetting its session.
      return {
        panes: present ? state.panes : [...state.panes, action.pane],
        active: id,
        open: true,
      };
    }
    case 'select':
      return { ...state, active: action.id, open: true };
    case 'close': {
      const index = state.panes.findIndex((pane) => dockPaneId(pane) === action.id);
      if (index === -1) return state;
      const panes = state.panes.filter((_, at) => at !== index);
      const next = panes[Math.min(index, panes.length - 1)];
      return {
        panes,
        active: state.active === action.id ? (next ? dockPaneId(next) : null) : state.active,
        open: state.open && panes.length > 0,
      };
    }
    case 'toggle':
      if (state.open) return { ...state, open: false };
      // An empty dock opens on Changes, as Lexi's does.
      if (state.panes.length === 0)
        return dockReducer(state, { type: 'open', pane: { kind: 'changes' } });
      return { ...state, open: true };
    case 'hide':
      return { ...state, open: false };
    case 'side':
      return {
        ...state,
        panes: state.panes.map((pane) =>
          pane.kind === 'side' ? { kind: 'side', childId: action.childId } : pane,
        ),
      };
  }
}

/** The pane on show, if the dock is open and has one. */
export function activeDockPane(state: DockState): DockPane | null {
  if (!state.open) return null;
  return state.panes.find((pane) => dockPaneId(pane) === state.active) ?? null;
}
