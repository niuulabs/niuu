import { describe, it, expect } from 'vitest';
import { shortestPath } from './pathTrace';
import { FAKE_GRAPH, fakeNodeId } from '../testing/fakeMimirService';

const GATEWAY = fakeNodeId('platform', '/platform/gateway-routing');
const OPENBAO = fakeNodeId('shared', '/shared/openbao-policy');
const VOLUNDR = fakeNodeId('shared', '/shared/volundr');

describe('shortestPath', () => {
  it('finds the shortest undirected path between two nodes', () => {
    expect(shortestPath(FAKE_GRAPH, GATEWAY, VOLUNDR)).toEqual([GATEWAY, OPENBAO, VOLUNDR]);
  });
  it('returns [] for the same node', () => {
    expect(shortestPath(FAKE_GRAPH, GATEWAY, GATEWAY)).toEqual([]);
  });
  it('returns [] when either endpoint is missing from the graph', () => {
    expect(shortestPath(FAKE_GRAPH, GATEWAY, 'missing')).toEqual([]);
    expect(shortestPath(FAKE_GRAPH, 'missing', GATEWAY)).toEqual([]);
  });
  it('returns [] when there is no path', () => {
    const disconnected = { nodes: FAKE_GRAPH.nodes, edges: [] };
    expect(shortestPath(disconnected, GATEWAY, VOLUNDR)).toEqual([]);
  });
});
