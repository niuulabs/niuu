import { describe, it, expect } from 'vitest';
import { layoutWorkflow } from './workflowLayout';
import type { WorkflowEdge } from './workflow';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function edge(id: string, source: string, target: string, label?: string): WorkflowEdge {
  return { id, source, target, label, cp1: { x: 0, y: 0 }, cp2: { x: 0, y: 0 } };
}

describe('layoutWorkflow', () => {
  it('returns an empty layout for no nodes', () => {
    const result = layoutWorkflow([], []);
    expect(result.positions.size).toBe(0);
    expect(result.columns).toBe(0);
    expect(result.cycleNodeIds).toEqual([]);
  });

  it('places a linear chain in increasing columns, same row', () => {
    const nodeIds = ['a', 'b', 'c'];
    const edges = [edge('e1', 'a', 'b', 'x.a -> x.b'), edge('e2', 'b', 'c', 'x.b -> x.c')];
    const { positions, columns } = layoutWorkflow(nodeIds, edges);
    expect(columns).toBe(3);
    const [a, b, c] = ['a', 'b', 'c'].map((id) => positions.get(id)!);
    expect(a.x).toBeLessThan(b.x);
    expect(b.x).toBeLessThan(c.x);
    expect(a.y).toBe(b.y);
    expect(b.y).toBe(c.y);
  });

  it('fans a parallel split into the same column, distinct rows', () => {
    const nodeIds = ['a', 'b', 'c'];
    const edges = [edge('e1', 'a', 'b', 'x.a -> x.b'), edge('e2', 'a', 'c', 'x.a -> x.c')];
    const { positions } = layoutWorkflow(nodeIds, edges);
    const [b, c] = ['b', 'c'].map((id) => positions.get(id)!);
    expect(b.x).toBe(c.x);
    expect(b.y).not.toBe(c.y);
  });

  it('joins a fan-in after the deepest branch', () => {
    // a -> b -> d, a -> c -> e -> d (c/e branch is one column longer)
    const nodeIds = ['a', 'b', 'c', 'd', 'e'];
    const edges = [
      edge('e1', 'a', 'b', 'x.a -> x.b'),
      edge('e2', 'a', 'c', 'x.a -> x.c'),
      edge('e3', 'c', 'e', 'x.c -> x.e'),
      edge('e4', 'b', 'd', 'x.b -> x.d'),
      edge('e5', 'e', 'd', 'x.e -> x.d'),
    ];
    const { positions } = layoutWorkflow(nodeIds, edges);
    const d = positions.get('d')!;
    const e = positions.get('e')!;
    const b = positions.get('b')!;
    expect(d.x).toBeGreaterThan(e.x);
    expect(d.x).toBeGreaterThan(b.x);
  });

  it('does not let a re-entry edge pull its source backwards or register as a cycle', () => {
    // trigger -> stage -> gate is the forward flow; gate -> stage is a
    // changes_requested re-entry loop. Every real Ting workflow starts with
    // a trigger node, which is what makes `stage` a genuine non-root (it
    // has a real structural predecessor) rather than a re-entry-only node —
    // see the next test for that case.
    const nodeIds = ['trigger', 'stage', 'gate'];
    const edges = [
      edge('e1', 'trigger', 'stage', 'x.requested -> x.requested'),
      edge('e2', 'stage', 'gate', 'stage.completed -> gate.review'),
      edge('e3', 'gate', 'stage', 'gate.changes_requested -> stage.requested'),
    ];
    const { positions, cycleNodeIds } = layoutWorkflow(nodeIds, edges);
    expect(cycleNodeIds).toEqual([]);
    expect(positions.get('trigger')!.x).toBeLessThan(positions.get('stage')!.x);
    expect(positions.get('stage')!.x).toBeLessThan(positions.get('gate')!.x);
  });

  it('pulls a node fed only by re-entry edges forward of column 0, next to what it feeds', () => {
    // request -> plan -> coordinate -> workstreams  (structural spine)
    // repair -> workstreams is structural (its own label is not re-entry)
    // but repair's only inbound edges are re-entry (repair_requested loops),
    // so repair has zero structural predecessors and would otherwise land
    // in column 0 beside `request`.
    const nodeIds = ['request', 'plan', 'coordinate', 'workstreams', 'repair'];
    const edges = [
      edge('e1', 'request', 'plan', 'delivery.requested -> delivery.requested'),
      edge('e2', 'plan', 'coordinate', 'plan.approved -> plan.approved'),
      edge('e3', 'coordinate', 'workstreams', 'children.waiting -> children.waiting'),
      edge(
        'e4',
        'workstreams',
        'repair',
        'workstream.repair_requested -> workstream.repair_requested',
      ),
      edge('e5', 'repair', 'workstreams', 'children.waiting -> children.waiting'),
    ];
    const { positions, columns } = layoutWorkflow(nodeIds, edges);
    const request = positions.get('request')!;
    const repair = positions.get('repair')!;
    const workstreams = positions.get('workstreams')!;
    expect(repair.x).toBeGreaterThan(request.x);
    expect(repair.x).toBeLessThan(workstreams.x);
    expect(columns).toBe(4); // request, plan, coordinate, {repair, workstreams} — repair shares workstreams' predecessor column
  });

  it('still positions nodes in a genuine structural cycle instead of dropping them, and reports them', () => {
    const nodeIds = ['a', 'b', 'c'];
    // a -> b -> c -> b is a real cycle (label carries no re-entry marker)
    const edges = [
      edge('e1', 'a', 'b', 'x.a -> x.b'),
      edge('e2', 'b', 'c', 'x.b -> x.c'),
      edge('e3', 'c', 'b', 'x.c -> x.b'),
    ];
    const { positions, cycleNodeIds } = layoutWorkflow(nodeIds, edges);
    expect(cycleNodeIds).toEqual(['b', 'c']);
    expect(positions.size).toBe(3);
    for (const id of nodeIds) {
      expect(positions.get(id)).toBeDefined();
    }
  });

  it('honours custom spacing and origin options', () => {
    const nodeIds = ['a', 'b'];
    const edges = [edge('e1', 'a', 'b', 'x.a -> x.b')];
    const { positions } = layoutWorkflow(nodeIds, edges, {
      columnSpacing: 100,
      rowSpacing: 50,
      originX: 10,
      originY: 20,
    });
    expect(positions.get('a')).toEqual({ x: 10, y: 20 });
    expect(positions.get('b')).toEqual({ x: 110, y: 20 });
  });

  it('produces the same id → position mapping regardless of input node order', () => {
    // Map insertion order follows the nodeIds argument, so compare sorted
    // entries rather than the maps' iteration order directly.
    const sorted = (m: Map<string, unknown>) =>
      [...m.entries()].sort(([a], [b]) => a.localeCompare(b));
    const nodeIds = ['c', 'a', 'b'];
    const edges = [edge('e1', 'a', 'b', 'x.a -> x.b'), edge('e2', 'a', 'c', 'x.a -> x.c')];
    const first = layoutWorkflow(nodeIds, edges);
    const second = layoutWorkflow([...nodeIds].reverse(), edges);
    expect(sorted(first.positions)).toEqual(sorted(second.positions));
  });

  it('places isolated nodes with no edges in a single column, distinct rows', () => {
    const { positions, columns } = layoutWorkflow(['x', 'y', 'z'], []);
    expect(columns).toBe(1);
    const rows = new Set(['x', 'y', 'z'].map((id) => positions.get(id)!.y));
    expect(rows.size).toBe(3);
  });

  it('uses measured card sizes to keep columns and rows from overlapping', () => {
    const nodeIds = ['a', 'b', 'c'];
    const edges = [
      edge('e1', 'a', 'b', 'a.done -> b.start'),
      edge('e2', 'a', 'c', 'a.done -> c.start'),
    ];
    const { positions } = layoutWorkflow(nodeIds, edges, {
      nodeSizes: new Map([
        ['a', { width: 250, height: 90 }],
        ['b', { width: 180, height: 140 }],
        ['c', { width: 180, height: 70 }],
      ]),
      columnGap: 50,
      rowGap: 30,
      originX: 10,
      originY: 20,
    });
    expect(positions.get('a')).toEqual({ x: 10, y: 20 });
    expect(positions.get('b')?.x).toBe(310);
    expect(positions.get('c')?.x).toBe(310);
    expect(Math.abs(positions.get('b')!.y - positions.get('c')!.y)).toBeGreaterThanOrEqual(100);
  });

  it('reserves space above nodes for feedback lanes', () => {
    const edges = [
      edge('start', 'trigger', 'a', 'work.requested -> work.requested'),
      edge('forward', 'a', 'b', 'work.ready -> work.ready'),
      edge('repair', 'b', 'a', 'work.repair_requested -> work.requested'),
    ];
    const { positions } = layoutWorkflow(['trigger', 'a', 'b'], edges, {
      originY: 40,
      feedbackLaneSpacing: 32,
    });
    expect(positions.get('a')?.y).toBe(72);
    expect(positions.get('b')?.y).toBe(72);
  });
});
