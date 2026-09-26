import { describe, it, expect } from 'vitest';
import { recentMarkers, MARKER_RECENCY_WINDOW_MS } from './liveMarkers';
import { FAKE_GRAPH } from '../testing/fakeMimirService';
import type { LiveActivity } from './api-types';

const NOW = new Date('2026-04-19T14:00:00Z');

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
      path: '/shared/volundr',
      timestamp: new Date(NOW.getTime() - MARKER_RECENCY_WINDOW_MS - 1000).toISOString(),
    });
    expect(recentMarkers([inWindow, stale], FAKE_GRAPH, NOW).map((m) => m.nodeId)).toEqual([
      '/platform/gateway-routing',
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
});
