import { describe, it, expect } from 'vitest';
import { degreeOf, topConnected } from './graphDegree';
import { FAKE_GRAPH } from '../testing/fakeMimirService';

describe('degreeOf', () => {
  it('counts edges touching a node as either source or target', () => {
    expect(degreeOf(FAKE_GRAPH, '/platform/gateway-routing')).toBe(3);
    expect(degreeOf(FAKE_GRAPH, '/shared/volundr')).toBe(1);
  });
  it('is 0 for an isolated node', () => {
    expect(degreeOf({ nodes: FAKE_GRAPH.nodes, edges: [] }, '/platform/gateway-routing')).toBe(0);
  });
});

describe('topConnected', () => {
  it('ranks nodes by degree, highest first, excluding zero-degree nodes', () => {
    const top = topConnected(FAKE_GRAPH, 2);
    expect(top[0]!.node.id).toBe('/platform/gateway-routing');
    expect(top[0]!.degree).toBe(3);
    expect(top).toHaveLength(2);
  });
  it('respects the requested limit', () => {
    expect(topConnected(FAKE_GRAPH, 1)).toHaveLength(1);
  });
});
