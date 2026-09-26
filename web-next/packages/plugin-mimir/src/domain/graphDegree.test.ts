import { describe, it, expect } from 'vitest';
import { degreeOf, topConnected } from './graphDegree';
import { FAKE_GRAPH, fakeNodeId } from '../testing/fakeMimirService';

const GATEWAY = fakeNodeId('platform', '/platform/gateway-routing');
const VOLUNDR = fakeNodeId('shared', '/shared/volundr');

describe('degreeOf', () => {
  it('counts edges touching a node as either source or target', () => {
    expect(degreeOf(FAKE_GRAPH, GATEWAY)).toBe(3);
    expect(degreeOf(FAKE_GRAPH, VOLUNDR)).toBe(1);
  });
  it('is 0 for an isolated node', () => {
    expect(degreeOf({ nodes: FAKE_GRAPH.nodes, edges: [] }, GATEWAY)).toBe(0);
  });
});

describe('topConnected', () => {
  it('ranks nodes by degree, highest first, excluding zero-degree nodes', () => {
    const top = topConnected(FAKE_GRAPH, 2);
    expect(top[0]!.node.id).toBe(GATEWAY);
    expect(top[0]!.degree).toBe(3);
    expect(top).toHaveLength(2);
  });
  it('respects the requested limit', () => {
    expect(topConnected(FAKE_GRAPH, 1)).toHaveLength(1);
  });
});
