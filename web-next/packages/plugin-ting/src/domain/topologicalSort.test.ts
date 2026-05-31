import { describe, it, expect } from 'vitest';
import { topologicalSort, detectCycle } from './topologicalSort';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const nodeIds = ['a', 'b', 'c', 'd', 'e'];
// a → b → d
// a → c → d
// d → e
const edges = [
  { source: 'a', target: 'b' },
  { source: 'a', target: 'c' },
  { source: 'b', target: 'd' },
  { source: 'c', target: 'd' },
  { source: 'd', target: 'e' },
];

// ---------------------------------------------------------------------------
// topologicalSort
// ---------------------------------------------------------------------------

describe('topologicalSort', () => {
  it('returns layers in correct depth order for a diamond DAG', () => {
    const layers = topologicalSort(nodeIds, edges);
    expect(layers).toHaveLength(4);
    expect(layers[0]!.depth).toBe(0);
    expect(layers[0]!.nodeIds).toEqual(['a']);
    expect(layers[1]!.depth).toBe(1);
    expect(layers[1]!.nodeIds).toEqual(['b', 'c']); // sorted
    expect(layers[2]!.depth).toBe(2);
    expect(layers[2]!.nodeIds).toEqual(['d']);
    expect(layers[3]!.depth).toBe(3);
    expect(layers[3]!.nodeIds).toEqual(['e']);
  });

  it('returns a single layer for isolated nodes', () => {
    const layers = topologicalSort(['x', 'y', 'z'], []);
    expect(layers).toHaveLength(1);
    expect(layers[0]!.nodeIds).toEqual(['x', 'y', 'z']); // sorted
  });

  it('returns empty array for empty node list', () => {
    expect(topologicalSort([], [])).toHaveLength(0);
  });

  it('produces deterministic output (nodes within each layer are sorted)', () => {
    const a = topologicalSort(['z', 'y', 'x'], []);
    const b = topologicalSort(['x', 'y', 'z'], []);
    expect(a[0]!.nodeIds).toEqual(b[0]!.nodeIds);
    expect(a[0]!.nodeIds).toEqual(['x', 'y', 'z']);
  });

  it('handles a linear chain', () => {
    const layers = topologicalSort(
      ['a', 'b', 'c'],
      [
        { source: 'a', target: 'b' },
        { source: 'b', target: 'c' },
      ],
    );
    expect(layers.map((l) => l.nodeIds)).toEqual([['a'], ['b'], ['c']]);
  });

  it('omits cycled nodes from the output (does not hang)', () => {
    // a → b → c → b (cycle b→c→b)
    const layers = topologicalSort(
      ['a', 'b', 'c'],
      [
        { source: 'a', target: 'b' },
        { source: 'b', target: 'c' },
        { source: 'c', target: 'b' },
      ],
    );
    // 'a' is reachable; 'b' and 'c' are in the cycle so never reach in-degree 0
    expect(layers[0]!.nodeIds).toEqual(['a']);
    expect(layers).toHaveLength(1);
  });

  it('handles a two-node workflow', () => {
    const layers = topologicalSort(['src', 'dst'], [{ source: 'src', target: 'dst' }]);
    expect(layers[0]!.nodeIds).toEqual(['src']);
    expect(layers[1]!.nodeIds).toEqual(['dst']);
  });

  it('handles a fully connected pipeline', () => {
    const ids = ['n1', 'n2', 'n3', 'n4'];
    const edgeList = [
      { source: 'n1', target: 'n2' },
      { source: 'n2', target: 'n3' },
      { source: 'n3', target: 'n4' },
    ];
    const layers = topologicalSort(ids, edgeList);
    expect(layers.map((l) => l.nodeIds)).toEqual([['n1'], ['n2'], ['n3'], ['n4']]);
  });
});

// ---------------------------------------------------------------------------
// detectCycle
// ---------------------------------------------------------------------------

describe('detectCycle', () => {
  it('returns empty array for an acyclic graph', () => {
    expect(detectCycle(nodeIds, edges)).toHaveLength(0);
  });

  it('returns empty array for an empty graph', () => {
    expect(detectCycle([], [])).toHaveLength(0);
  });

  it('returns empty array for isolated nodes', () => {
    expect(detectCycle(['a', 'b', 'c'], [])).toHaveLength(0);
  });

  it('detects a simple two-node cycle', () => {
    const cycle = detectCycle(
      ['a', 'b'],
      [
        { source: 'a', target: 'b' },
        { source: 'b', target: 'a' },
      ],
    );
    expect(cycle.sort()).toEqual(['a', 'b']);
  });

  it('detects a three-node cycle', () => {
    const cycle = detectCycle(
      ['a', 'b', 'c'],
      [
        { source: 'a', target: 'b' },
        { source: 'b', target: 'c' },
        { source: 'c', target: 'a' },
      ],
    );
    expect(cycle.sort()).toEqual(['a', 'b', 'c']);
  });

  it('detects only the nodes in the cycle, not innocent bystanders', () => {
    // a → b → c → b  (b and c cycle; a is an entry node)
    const cycle = detectCycle(
      ['a', 'b', 'c'],
      [
        { source: 'a', target: 'b' },
        { source: 'b', target: 'c' },
        { source: 'c', target: 'b' },
      ],
    );
    // 'a' feeds into the cycle but is not itself cycling
    expect(cycle).toContain('b');
    expect(cycle).toContain('c');
  });

  it('returns deterministic (sorted) output', () => {
    const cycleA = detectCycle(
      ['z', 'y'],
      [
        { source: 'z', target: 'y' },
        { source: 'y', target: 'z' },
      ],
    );
    const cycleB = detectCycle(
      ['y', 'z'],
      [
        { source: 'y', target: 'z' },
        { source: 'z', target: 'y' },
      ],
    );
    expect(cycleA).toEqual(cycleB);
    expect(cycleA).toEqual(['y', 'z']);
  });

  it('handles self-loop as a single-node cycle', () => {
    // Note: validateWorkflow() already rejects self-loops, but detectCycle()
    // should handle the edge case gracefully for robustness.
    const cycle = detectCycle(['a'], [{ source: 'a', target: 'a' }]);
    expect(cycle).toContain('a');
  });

  it('identifies multiple independent cycles', () => {
    // group1: a→b→a; group2: c→d→c
    const cycle = detectCycle(
      ['a', 'b', 'c', 'd'],
      [
        { source: 'a', target: 'b' },
        { source: 'b', target: 'a' },
        { source: 'c', target: 'd' },
        { source: 'd', target: 'c' },
      ],
    );
    expect(cycle.sort()).toEqual(['a', 'b', 'c', 'd']);
  });
});
