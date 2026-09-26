import { describe, it, expect } from 'vitest';
import { recentMarkers, MARKER_RECENCY_WINDOW_MS } from './liveMarkers';
import { FAKE_GRAPH, fakeNodeId } from '../testing/fakeMimirService';
import type { GraphNode, LiveActivity, MimirGraph } from './api-types';

const NOW = new Date('2026-04-19T14:00:00Z');
const GATEWAY = fakeNodeId('platform', '/platform/gateway-routing');

function activity(overrides: Partial<LiveActivity>): LiveActivity {
  return {
    id: 'a',
    timestamp: '2026-04-19T13:59:00Z',
    kind: 'read',
    mount: 'platform',
    path: '/platform/gateway-routing',
    actor: 'muninn',
    ...overrides,
  };
}

describe('recentMarkers', () => {
  it('keeps only events within the recency window', () => {
    const inWindow = activity({ id: '1', path: '/platform/gateway-routing' });
    const stale = activity({
      id: '2',
      mount: 'shared',
      path: '/shared/volundr',
      timestamp: new Date(NOW.getTime() - MARKER_RECENCY_WINDOW_MS - 1000).toISOString(),
    });
    expect(recentMarkers([inWindow, stale], FAKE_GRAPH, NOW).map((m) => m.nodeId)).toEqual([
      GATEWAY,
    ]);
  });

  it('keeps only the latest event per node', () => {
    const older = activity({ id: '1', timestamp: '2026-04-19T13:55:00Z', kind: 'read' });
    const newer = activity({ id: '2', timestamp: '2026-04-19T13:58:00Z', kind: 'write' });
    const markers = recentMarkers([older, newer], FAKE_GRAPH, NOW);
    expect(markers).toHaveLength(1);
    expect(markers[0]!.kind).toBe('write');
  });

  it('drops activity for a path with no matching graph node', () => {
    const orphan = activity({ path: '/no/such/page' });
    expect(recentMarkers([orphan], FAKE_GRAPH, NOW)).toEqual([]);
  });

  it('returns [] for empty activity', () => {
    expect(recentMarkers([], FAKE_GRAPH, NOW)).toEqual([]);
  });

  it('matches on (mount, path) — never on path alone, since two mounts can share a path', () => {
    // Two mounts each carry a page at the same path; only the (mount, path)
    // pair that actually matches a node should ever produce a marker.
    const sharedPath = '/gateway';
    const platformNode: GraphNode = {
      id: fakeNodeId('platform', sharedPath),
      title: 'Gateway (platform)',
      category: 'infra',
      kind: 'topic',
      path: sharedPath,
      mount: 'platform',
      updatedAt: '2026-01-01T00:00:00Z',
      firstSeen: '2026-01-01T00:00:00Z',
      confidence: 'high',
    };
    const sharedNode: GraphNode = {
      ...platformNode,
      id: fakeNodeId('shared', sharedPath),
      title: 'Gateway (shared)',
      mount: 'shared',
    };
    const graph: MimirGraph = { nodes: [platformNode, sharedNode], edges: [] };

    const onPlatform = activity({ id: '1', mount: 'platform', path: sharedPath });
    const markers = recentMarkers([onPlatform], graph, NOW);

    expect(markers).toHaveLength(1);
    expect(markers[0]!.nodeId).toBe(platformNode.id);
    expect(markers[0]!.nodeId).not.toBe(sharedNode.id);
  });
});
