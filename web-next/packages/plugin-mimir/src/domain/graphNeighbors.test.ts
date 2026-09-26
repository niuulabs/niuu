import { describe, it, expect } from 'vitest';
import { neighborsWithinDepth, linkedNeighborCount } from './graphNeighbors';
import { FAKE_GRAPH, fakeNodeId } from '../testing/fakeMimirService';

const GATEWAY = fakeNodeId('platform', '/platform/gateway-routing');
const CEDAR = fakeNodeId('platform', '/platform/cedar-authorization');
const GUILDS = fakeNodeId('platform', '/platform/guilds-gateway');
const OPENBAO = fakeNodeId('shared', '/shared/openbao-policy');
const VOLUNDR = fakeNodeId('shared', '/shared/volundr');

describe('neighborsWithinDepth', () => {
  it('returns [] for an unknown focus node', () => {
    expect(neighborsWithinDepth(FAKE_GRAPH, 'missing', 1)).toEqual([]);
  });

  it('returns direct neighbours at depth 1 with relation labels', () => {
    const result = neighborsWithinDepth(FAKE_GRAPH, GATEWAY, 1);
    expect(result.map((r) => r.node.id).sort()).toEqual([CEDAR, GUILDS, OPENBAO].sort());
    const toCedar = result.find((r) => r.node.id === CEDAR)!;
    expect(toCedar.relation).toBe('depends on');
    expect(toCedar.hops).toBe(1);
  });

  it('expands to further hops without revisiting nodes', () => {
    const result = neighborsWithinDepth(FAKE_GRAPH, GATEWAY, 3);
    const ids = result.map((r) => r.node.id);
    expect(ids).toEqual(expect.arrayContaining([CEDAR, GUILDS, OPENBAO, VOLUNDR]));
    expect(new Set(ids).size).toBe(ids.length);
    expect(result.find((r) => r.node.id === VOLUNDR)?.hops).toBe(2);
  });

  it('does not reach nodes beyond the requested depth', () => {
    const result = neighborsWithinDepth(FAKE_GRAPH, GATEWAY, 1);
    expect(result.some((r) => r.node.id === VOLUNDR)).toBe(false);
  });
});

describe('linkedNeighborCount', () => {
  it('counts unique 1-hop neighbours across several nodes, excluding the nodes themselves', () => {
    // GATEWAY -> {CEDAR, GUILDS, OPENBAO}; OPENBAO -> {GATEWAY, VOLUNDR}
    expect(linkedNeighborCount(FAKE_GRAPH, [GATEWAY, OPENBAO])).toBe(3); // CEDAR, GUILDS, VOLUNDR
  });

  it('returns 0 for an isolated set', () => {
    expect(linkedNeighborCount({ nodes: FAKE_GRAPH.nodes, edges: [] }, [GATEWAY])).toBe(0);
  });
});
