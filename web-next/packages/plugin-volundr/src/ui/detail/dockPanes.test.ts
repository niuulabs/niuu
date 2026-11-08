import { describe, expect, it } from 'vitest';
import { EMPTY_DOCK, activeDockPane, dockPaneId, dockReducer, type DockState } from './dockPanes';

const run = (...actions: Parameters<typeof dockReducer>[1][]): DockState =>
  actions.reduce(dockReducer, EMPTY_DOCK);

describe('dock panes', () => {
  it('names one tab per kind and one per previewed file', () => {
    expect(dockPaneId({ kind: 'changes' })).toBe('changes');
    expect(dockPaneId({ kind: 'side', childId: 'c1' })).toBe('side');
    expect(dockPaneId({ kind: 'file', path: '/workspace/a.md' })).toBe('file:/workspace/a.md');
  });

  it('opens a pane as a new tab, or shows the one already open', () => {
    const state = run(
      { type: 'open', pane: { kind: 'changes' } },
      { type: 'open', pane: { kind: 'file', path: '/a' } },
      { type: 'open', pane: { kind: 'changes' } },
    );
    expect(state.panes.map(dockPaneId)).toEqual(['changes', 'file:/a']);
    expect(activeDockPane(state)).toEqual({ kind: 'changes' });
  });

  it('points an open live preview at the port and path asked for', () => {
    const state = run(
      { type: 'open', pane: { kind: 'preview', port: 5173, path: '/' } },
      { type: 'open', pane: { kind: 'changes' } },
      { type: 'open', pane: { kind: 'preview', port: 8000, path: '/docs' } },
    );
    expect(state.panes.map(dockPaneId)).toEqual(['preview', 'changes']);
    expect(activeDockPane(state)).toEqual({ kind: 'preview', port: 8000, path: '/docs' });
  });

  it("keeps a side chat's session when it is opened again", () => {
    const state = run(
      { type: 'open', pane: { kind: 'side', childId: null } },
      { type: 'side', childId: 'child-1' },
      { type: 'open', pane: { kind: 'side', childId: null } },
    );
    expect(activeDockPane(state)).toEqual({ kind: 'side', childId: 'child-1' });
  });

  it('closes a tab onto its neighbour and hides the dock with the last one', () => {
    let state = run(
      { type: 'open', pane: { kind: 'changes' } },
      { type: 'open', pane: { kind: 'files' } },
      { type: 'open', pane: { kind: 'file', path: '/a' } },
      { type: 'select', id: 'files' },
    );
    state = dockReducer(state, { type: 'close', id: 'files' });
    expect(state.active).toBe('file:/a');
    state = dockReducer(state, { type: 'close', id: 'changes' });
    expect(state.active).toBe('file:/a');
    expect(dockReducer(state, { type: 'close', id: 'nothing' })).toBe(state);
    state = dockReducer(state, { type: 'close', id: 'file:/a' });
    expect(state).toEqual({ panes: [], active: null, open: false });
  });

  it('toggles: an empty dock opens on Changes; hiding keeps the panes', () => {
    let state = dockReducer(EMPTY_DOCK, { type: 'toggle' });
    expect(activeDockPane(state)).toEqual({ kind: 'changes' });
    state = dockReducer(state, { type: 'open', pane: { kind: 'files' } });
    state = dockReducer(state, { type: 'toggle' });
    expect(activeDockPane(state)).toBeNull();
    expect(state.panes).toHaveLength(2);
    state = dockReducer(state, { type: 'toggle' });
    expect(activeDockPane(state)).toEqual({ kind: 'files' });
    state = dockReducer(state, { type: 'hide' });
    expect(state.open).toBe(false);
  });
});
