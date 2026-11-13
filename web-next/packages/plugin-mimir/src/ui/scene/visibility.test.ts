import { describe, it, expect } from 'vitest';
import { computeVisibility } from './visibility';
import type { MimirGraph } from '../../domain/api-types';

function node(
  id: string,
  overrides: Partial<MimirGraph['nodes'][number]> = {},
): MimirGraph['nodes'][number] {
  return {
    id,
    title: id,
    category: 'topic',
    kind: 'topic',
    updatedAt: '2026-04-19T00:00:00Z',
    firstSeen: '2026-01-01T00:00:00Z',
    confidence: null,
    ...overrides,
  };
}

describe('computeVisibility — no mode active', () => {
  const graph: MimirGraph = {
    nodes: [node('a'), node('b')],
    edges: [{ source: 'a', target: 'b', relation: 'depends_on' }],
  };

  it('marks every node visible and normal when nothing is hidden/focused', () => {
    const result = computeVisibility(graph, {});
    expect(result.hasSpotlight).toBe(false);
    for (const v of result.nodes.values()) {
      expect(v.visible).toBe(true);
      expect(v.litLevel).toBe('normal');
    }
    expect(result.edges[0]?.litLevel).toBe('normal');
    expect(result.edges[0]?.label).toBeNull();
  });
});

describe('computeVisibility — hiddenGroups', () => {
  const graph: MimirGraph = {
    nodes: [node('topic-1', { kind: 'topic' }), node('person-1', { kind: 'person' })],
    edges: [{ source: 'topic-1', target: 'person-1', relation: 'wikilink' }],
  };

  it('hides nodes whose kind group is in hiddenGroups', () => {
    const result = computeVisibility(graph, { hiddenGroups: ['entity'] });
    expect(result.nodes.get('topic-1')?.visible).toBe(true);
    expect(result.nodes.get('person-1')?.visible).toBe(false);
  });

  it('hides an edge when either endpoint is hidden', () => {
    const result = computeVisibility(graph, { hiddenGroups: ['entity'] });
    expect(result.edges[0]?.visible).toBe(false);
  });

  it('groups a node with no kind as "page" rather than throwing', () => {
    const graphNoKind: MimirGraph = { nodes: [node('x', { kind: undefined })], edges: [] };
    const result = computeVisibility(graphNoKind, { hiddenGroups: ['page'] });
    expect(result.nodes.get('x')?.visible).toBe(false);
  });

  it('a kind-less node stays visible when "page" is not hidden', () => {
    const graphNoKind: MimirGraph = { nodes: [node('x', { kind: undefined })], edges: [] };
    const result = computeVisibility(graphNoKind, { hiddenGroups: ['entity'] });
    expect(result.nodes.get('x')?.visible).toBe(true);
  });
});

describe('computeVisibility — asOf', () => {
  const graph: MimirGraph = {
    nodes: [
      node('old', { firstSeen: '2026-01-01T00:00:00Z' }),
      node('born-recently', { firstSeen: '2026-04-18T00:00:00Z' }),
      node('future', { firstSeen: '2026-05-01T00:00:00Z' }),
      node('no-first-seen', { firstSeen: undefined }),
    ],
    edges: [],
  };
  const asOf = '2026-04-19T00:00:00Z';

  it('hides nodes first seen after asOf', () => {
    const result = computeVisibility(graph, { asOf });
    expect(result.nodes.get('future')?.visible).toBe(false);
    expect(result.nodes.get('old')?.visible).toBe(true);
  });

  it('never hides a node with no firstSeen', () => {
    const result = computeVisibility(graph, { asOf });
    expect(result.nodes.get('no-first-seen')?.visible).toBe(true);
  });

  it('gives a born ring to nodes seen shortly before asOf', () => {
    const result = computeVisibility(graph, { asOf });
    expect(result.nodes.get('born-recently')?.bornRing).toBe(true);
    expect(result.nodes.get('old')?.bornRing).toBe(false);
  });

  it('gives no born ring when asOf is unset', () => {
    const result = computeVisibility(graph, {});
    expect(result.nodes.get('born-recently')?.bornRing).toBe(false);
  });
});

describe('computeVisibility — focus', () => {
  // a - b - c - d - e (chain)
  const graph: MimirGraph = {
    nodes: [node('a'), node('b'), node('c'), node('d'), node('e')],
    edges: [
      { source: 'a', target: 'b', relation: 'depends_on' },
      { source: 'b', target: 'c', relation: 'depends_on' },
      { source: 'c', target: 'd', relation: 'depends_on' },
      { source: 'd', target: 'e', relation: 'depends_on' },
    ],
  };

  it('lights the focused node and its neighbours within depth, keeps the next hop readable, recedes the rest', () => {
    const result = computeVisibility(graph, { focus: { nodeId: 'b', depth: 1 } });
    expect(result.hasSpotlight).toBe(true);
    expect(result.nodes.get('a')?.litLevel).toBe('lit');
    expect(result.nodes.get('b')?.litLevel).toBe('lit');
    expect(result.nodes.get('b')?.isFocus).toBe(true);
    expect(result.nodes.get('a')?.isFocus).toBe(false);
    expect(result.nodes.get('c')?.litLevel).toBe('lit');
    expect(result.nodes.get('d')?.litLevel).toBe('dim-soft');
    expect(result.nodes.get('e')?.litLevel).toBe('dim-strong');
  });

  it('depth 0 lights only the focused node', () => {
    const result = computeVisibility(graph, { focus: { nodeId: 'b', depth: 0 } });
    expect(result.nodes.get('b')?.litLevel).toBe('lit');
    expect(result.nodes.get('a')?.litLevel).toBe('dim-soft');
    expect(result.nodes.get('c')?.litLevel).toBe('dim-soft');
    expect(result.nodes.get('d')?.litLevel).toBe('dim-strong');
  });

  it('lights edges between two lit nodes and labels only the focus’s own links', () => {
    const result = computeVisibility(graph, { focus: { nodeId: 'b', depth: 2 } });
    const ab = result.edges.find((e) => e.source === 'a' && e.target === 'b')!;
    const cd = result.edges.find((e) => e.source === 'c' && e.target === 'd')!;
    const de = result.edges.find((e) => e.source === 'd' && e.target === 'e')!;
    expect(ab.litLevel).toBe('lit');
    expect(ab.label).toBe('depends on');
    // Lit (both ends within depth 2) but not the focus's own link: no label soup.
    expect(cd.litLevel).toBe('lit');
    expect(cd.label).toBeNull();
    expect(de.litLevel).toBe('dim-soft');
    expect(de.label).toBeNull();
  });

  it('does nothing when the focused node does not exist', () => {
    const result = computeVisibility(graph, { focus: { nodeId: 'zzz', depth: 2 } });
    expect(result.hasSpotlight).toBe(false);
  });
});

describe('computeVisibility — kind filter', () => {
  const graph: MimirGraph = {
    nodes: [
      node('decision', { kind: 'decision' }),
      node('ines', { kind: 'person' }),
      node('ticket', { kind: 'ticket' }),
      node('ola', { kind: 'person' }),
    ],
    edges: [
      { source: 'ines', target: 'decision', relation: 'owns' },
      { source: 'decision', target: 'ticket', relation: 'planned in' },
    ],
  };

  it('keeps only the chosen kinds; everything else recedes but stays drawn', () => {
    const result = computeVisibility(graph, { kinds: new Set(['person']) });
    expect(result.nodes.get('ines')?.litLevel).toBe('normal');
    expect(result.nodes.get('ola')?.litLevel).toBe('normal');
    expect(result.nodes.get('decision')?.visible).toBe(true);
    expect(result.nodes.get('decision')?.litLevel).toBe('dim-strong');
    expect(result.edges[0]?.litLevel).toBe('dim-strong');
  });

  it('never recedes the focused node, and lights only its neighbours of the kept kind', () => {
    const result = computeVisibility(graph, {
      kinds: new Set(['person']),
      focus: { nodeId: 'decision', depth: 1 },
    });
    expect(result.nodes.get('decision')?.litLevel).toBe('lit');
    expect(result.nodes.get('ines')?.litLevel).toBe('lit');
    expect(result.nodes.get('ticket')?.litLevel).toBe('dim-strong');
    const owns = result.edges.find((e) => e.relation === 'owns')!;
    const planned = result.edges.find((e) => e.relation === 'planned in')!;
    expect(owns.label).toBe('owns');
    expect(planned.litLevel).toBe('dim-strong');
    expect(planned.label).toBeNull();
  });

  it('null keeps every kind', () => {
    const result = computeVisibility(graph, { kinds: null });
    for (const v of result.nodes.values()) expect(v.litLevel).toBe('normal');
  });
});

describe('computeVisibility — recent change', () => {
  const NOW = Date.parse('2026-11-12T12:00:00Z');
  const graph: MimirGraph = {
    nodes: [
      node('changed', { changedAt: '2026-11-10', firstSeen: '2026-01-01T00:00:00Z' }),
      node('new', { changedAt: null, firstSeen: '2026-11-12T08:00:00Z' }),
      node('old', { changedAt: '2026-09-01', firstSeen: '2026-01-01T00:00:00Z' }),
    ],
    edges: [
      { source: 'changed', target: 'new', relation: 'moved', since: '2026-11-12' },
      { source: 'changed', target: 'old', relation: 'replaces', since: '2026-08-01' },
      { source: 'new', target: 'old', relation: 'mentions', since: null },
    ],
  };

  it('marks pages changed or first seen this week, and links introduced this week', () => {
    const result = computeVisibility(graph, { now: NOW });
    expect(result.nodes.get('changed')?.recent).toBe(true);
    expect(result.nodes.get('changed')?.fresh).toBe(false);
    expect(result.nodes.get('new')?.recent).toBe(true);
    expect(result.nodes.get('new')?.fresh).toBe(true);
    expect(result.nodes.get('old')?.recent).toBe(false);
    expect(result.edges.map((e) => e.recent)).toEqual([true, false, false]);
  });

  it('keeps a recent page above the dim cloud while something else is focused', () => {
    const lone: MimirGraph = {
      nodes: [...graph.nodes, node('far'), node('farther', { changedAt: '2026-11-11' })],
      edges: [{ source: 'far', target: 'old', relation: 'mentions' }],
    };
    const result = computeVisibility(lone, { now: NOW, focus: { nodeId: 'far', depth: 1 } });
    expect(result.nodes.get('farther')?.litLevel).toBe('dim-soft');
    expect(result.nodes.get('changed')?.litLevel).toBe('dim-soft');
  });

  it('judges "recent" against the replayed day, and hides links introduced after it', () => {
    const result = computeVisibility(graph, { now: NOW, asOf: '2026-08-03T23:59:59.999Z' });
    expect(result.nodes.get('changed')?.recent).toBe(false);
    expect(result.nodes.get('new')?.visible).toBe(false);
    const replaces = result.edges.find((e) => e.relation === 'replaces')!;
    const moved = result.edges.find((e) => e.relation === 'moved')!;
    expect(replaces.visible).toBe(true);
    expect(replaces.recent).toBe(true);
    expect(moved.visible).toBe(false);
  });
});

describe('computeVisibility — answers', () => {
  const graph: MimirGraph = {
    nodes: [node('a'), node('b'), node('c')],
    edges: [
      { source: 'a', target: 'b', relation: 'explains' },
      { source: 'b', target: 'c', relation: 'explains' },
    ],
  };

  it('lights answer nodes and gives them their number, dims the rest softly', () => {
    const result = computeVisibility(graph, {
      answers: [
        { nodeId: 'a', n: 1 },
        { nodeId: 'b', n: 2 },
      ],
    });
    expect(result.nodes.get('a')?.litLevel).toBe('lit');
    expect(result.nodes.get('a')?.answerNumber).toBe(1);
    expect(result.nodes.get('b')?.answerNumber).toBe(2);
    expect(result.nodes.get('c')?.litLevel).toBe('dim-soft');
    expect(result.nodes.get('c')?.answerNumber).toBeNull();
  });

  it('lights the edge directly between two answer nodes', () => {
    const result = computeVisibility(graph, {
      answers: [
        { nodeId: 'a', n: 1 },
        { nodeId: 'b', n: 2 },
      ],
    });
    const ab = result.edges.find((e) => e.source === 'a' && e.target === 'b')!;
    expect(ab.litLevel).toBe('lit');
    // Answers light their links but carry no relation labels: those are read near a focus.
    expect(ab.label).toBeNull();
  });

  it('dims answers less strongly than focus (dim-soft, not dim-strong)', () => {
    const result = computeVisibility(graph, { answers: [{ nodeId: 'a', n: 1 }] });
    expect(result.nodes.get('c')?.litLevel).toBe('dim-soft');
  });
});

describe('computeVisibility — path', () => {
  const graph: MimirGraph = {
    nodes: [node('a'), node('b'), node('c'), node('d')],
    edges: [
      { source: 'a', target: 'b', relation: 'depends_on' },
      { source: 'b', target: 'c', relation: 'depends_on' },
      { source: 'd', target: 'a', relation: 'depends_on' },
    ],
  };

  it('lights nodes along the path and labels edges with their order', () => {
    const result = computeVisibility(graph, { path: ['a', 'b', 'c'] });
    expect(result.nodes.get('a')?.litLevel).toBe('lit');
    expect(result.nodes.get('b')?.litLevel).toBe('lit');
    expect(result.nodes.get('c')?.litLevel).toBe('lit');
    expect(result.nodes.get('d')?.litLevel).toBe('dim-soft');

    const ab = result.edges.find((e) => e.source === 'a' && e.target === 'b')!;
    const bc = result.edges.find((e) => e.source === 'b' && e.target === 'c')!;
    expect(ab.pathOrder).toBe(1);
    expect(bc.pathOrder).toBe(2);
    expect(ab.litLevel).toBe('lit');
    // A traced path reads its relations step by step.
    expect(ab.label).toBe('depends on');
    expect(bc.label).toBe('depends on');
  });

  it('handles an edge whose direction is reversed relative to the path order', () => {
    const result = computeVisibility(graph, { path: ['a', 'd'] });
    const da = result.edges.find((e) => e.source === 'd' && e.target === 'a')!;
    expect(da.pathOrder).toBe(1);
  });

  it('does not mark an edge as path-lit when its endpoints are non-consecutive in the path', () => {
    const result = computeVisibility(graph, { path: ['a', 'c', 'b'] });
    // a->b exists in the graph but a and b are not consecutive in this path.
    const ab = result.edges.find((e) => e.source === 'a' && e.target === 'b')!;
    expect(ab.pathOrder).toBeNull();
  });
});

describe('computeVisibility — contradiction edges', () => {
  it('flags contradicts/disagrees_with/conflicts_with as contradiction edges', () => {
    const graph: MimirGraph = {
      nodes: [node('a'), node('b'), node('c'), node('d')],
      edges: [
        { source: 'a', target: 'b', relation: 'contradicts' },
        { source: 'b', target: 'c', relation: 'disagrees_with' },
        { source: 'c', target: 'd', relation: 'depends_on' },
      ],
    };
    const result = computeVisibility(graph, {});
    expect(result.edges[0]?.contradiction).toBe(true);
    expect(result.edges[1]?.contradiction).toBe(true);
    expect(result.edges[2]?.contradiction).toBe(false);
  });
});
