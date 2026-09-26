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
    edges: [{ source: 'a', target: 'b', type: 'depends_on' }],
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
    edges: [{ source: 'topic-1', target: 'person-1', type: 'wikilink' }],
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
  // a - b - c - d (chain)
  const graph: MimirGraph = {
    nodes: [node('a'), node('b'), node('c'), node('d')],
    edges: [
      { source: 'a', target: 'b', type: 'depends_on' },
      { source: 'b', target: 'c', type: 'depends_on' },
      { source: 'c', target: 'd', type: 'depends_on' },
    ],
  };

  it('lights the focused node and its neighbours within depth', () => {
    const result = computeVisibility(graph, { focus: { nodeId: 'b', depth: 1 } });
    expect(result.hasSpotlight).toBe(true);
    expect(result.nodes.get('a')?.litLevel).toBe('lit');
    expect(result.nodes.get('b')?.litLevel).toBe('lit');
    expect(result.nodes.get('c')?.litLevel).toBe('lit');
    expect(result.nodes.get('d')?.litLevel).toBe('dim-strong');
  });

  it('depth 0 lights only the focused node', () => {
    const result = computeVisibility(graph, { focus: { nodeId: 'b', depth: 0 } });
    expect(result.nodes.get('b')?.litLevel).toBe('lit');
    expect(result.nodes.get('a')?.litLevel).toBe('dim-strong');
    expect(result.nodes.get('c')?.litLevel).toBe('dim-strong');
  });

  it('lights edges between two lit nodes and labels them', () => {
    const result = computeVisibility(graph, { focus: { nodeId: 'b', depth: 1 } });
    const ab = result.edges.find((e) => e.source === 'a' && e.target === 'b')!;
    const cd = result.edges.find((e) => e.source === 'c' && e.target === 'd')!;
    expect(ab.litLevel).toBe('lit');
    expect(ab.label).toBe('depends on');
    expect(cd.litLevel).toBe('dim-strong');
    expect(cd.label).toBeNull();
  });

  it('does nothing when the focused node does not exist', () => {
    const result = computeVisibility(graph, { focus: { nodeId: 'zzz', depth: 2 } });
    expect(result.hasSpotlight).toBe(false);
  });
});

describe('computeVisibility — answers', () => {
  const graph: MimirGraph = {
    nodes: [node('a'), node('b'), node('c')],
    edges: [
      { source: 'a', target: 'b', type: 'explains' },
      { source: 'b', target: 'c', type: 'explains' },
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
      { source: 'a', target: 'b', type: 'depends_on' },
      { source: 'b', target: 'c', type: 'depends_on' },
      { source: 'd', target: 'a', type: 'depends_on' },
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

describe('computeVisibility — disputedIds', () => {
  it('flags disputed nodes regardless of other modes', () => {
    const graph: MimirGraph = { nodes: [node('a'), node('b')], edges: [] };
    const result = computeVisibility(graph, { disputedIds: ['a'] });
    expect(result.nodes.get('a')?.disputed).toBe(true);
    expect(result.nodes.get('b')?.disputed).toBe(false);
  });
});

describe('computeVisibility — contradiction edges', () => {
  it('flags contradicts/disagrees_with/conflicts_with as contradiction edges', () => {
    const graph: MimirGraph = {
      nodes: [node('a'), node('b'), node('c'), node('d')],
      edges: [
        { source: 'a', target: 'b', type: 'contradicts' },
        { source: 'b', target: 'c', type: 'disagrees_with' },
        { source: 'c', target: 'd', type: 'depends_on' },
      ],
    };
    const result = computeVisibility(graph, {});
    expect(result.edges[0]?.contradiction).toBe(true);
    expect(result.edges[1]?.contradiction).toBe(true);
    expect(result.edges[2]?.contradiction).toBe(false);
  });
});
