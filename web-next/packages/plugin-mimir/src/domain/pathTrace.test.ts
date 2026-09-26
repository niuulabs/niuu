import { describe, it, expect } from 'vitest';
import { shortestPath } from './pathTrace';
import { FAKE_GRAPH } from '../testing/fakeMimirService';

describe('shortestPath', () => {
  it('finds the shortest undirected path between two nodes', () => {
    expect(shortestPath(FAKE_GRAPH, '/platform/gateway-routing', '/shared/volundr')).toEqual([
      '/platform/gateway-routing',
      '/shared/openbao-policy',
      '/shared/volundr',
    ]);
  });
  it('returns [] for the same node', () => {
    expect(
      shortestPath(FAKE_GRAPH, '/platform/gateway-routing', '/platform/gateway-routing'),
    ).toEqual([]);
  });
  it('returns [] when either endpoint is missing from the graph', () => {
    expect(shortestPath(FAKE_GRAPH, '/platform/gateway-routing', '/missing')).toEqual([]);
    expect(shortestPath(FAKE_GRAPH, '/missing', '/platform/gateway-routing')).toEqual([]);
  });
  it('returns [] when there is no path', () => {
    const disconnected = { nodes: FAKE_GRAPH.nodes, edges: [] };
    expect(shortestPath(disconnected, '/platform/gateway-routing', '/shared/volundr')).toEqual([]);
  });
});
