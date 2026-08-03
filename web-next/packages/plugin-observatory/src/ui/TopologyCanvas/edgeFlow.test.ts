import { describe, it, expect } from 'vitest';
import { drawEdges } from './renderer';
import { makeCtxMock } from './test-helpers';
import { EDGE_FLOW } from './config';
import type { Topology, TopologyEdge } from '../../domain';

function topology(edge: Partial<TopologyEdge>): Topology {
  return {
    timestamp: '2026-08-03T00:00:00Z',
    nodes: [
      { id: 'bifrost', typeId: 'bifrost', label: 'Bifröst', parentId: null, status: 'healthy' },
      { id: 'model', typeId: 'model', label: 'nemotron', parentId: null, status: 'healthy' },
    ],
    edges: [
      {
        id: 'e1',
        sourceId: 'bifrost',
        targetId: 'model',
        kind: 'solid',
        relationType: 'routes_to',
        ...edge,
      },
    ],
  };
}

const POSITIONS = new Map([
  ['bifrost', { x: 0, y: 0 }],
  ['model', { x: 400, y: 0 }],
]);

function dashPatterns(edge: Partial<TopologyEdge>, now = 1000) {
  const ctx = makeCtxMock();
  drawEdges(ctx as unknown as CanvasRenderingContext2D, topology(edge), POSITIONS, now, {
    zoom: 1,
  });
  return ctx.lineDashes;
}

describe('edge flow', () => {
  it('draws nothing along an edge nobody measured', () => {
    // The whole point: a canvas that animates every edge is asserting traffic
    // it has not seen, sixty times a second.
    const patterns = dashPatterns({});
    expect(patterns.some((p) => p[0] === EDGE_FLOW.DASH_PX)).toBe(false);
  });

  it('draws travelling marks along an edge that reported a rate', () => {
    const patterns = dashPatterns({ ratePerMinute: 12 });
    expect(patterns.some((p) => p[0] === EDGE_FLOW.DASH_PX)).toBe(true);
  });

  it('packs the marks closer as the measured rate rises', () => {
    const quiet = dashPatterns({ ratePerMinute: 1 }).find((p) => p[0] === EDGE_FLOW.DASH_PX);
    const busy = dashPatterns({ ratePerMinute: 30 }).find((p) => p[0] === EDGE_FLOW.DASH_PX);

    expect(quiet?.[1]).toBeGreaterThan(busy?.[1] ?? 0);
    expect(busy?.[1]).toBe(EDGE_FLOW.MIN_GAP_PX);
  });

  it('stops tightening past saturation, so a busy edge never reads as a solid line', () => {
    const busy = dashPatterns({ ratePerMinute: 30 }).find((p) => p[0] === EDGE_FLOW.DASH_PX);
    const busier = dashPatterns({ ratePerMinute: 3000 }).find((p) => p[0] === EDGE_FLOW.DASH_PX);

    expect(busier?.[1]).toBe(busy?.[1]);
  });

  it('holds the marks still when motion is not wanted', () => {
    const ctx = makeCtxMock();
    drawEdges(
      ctx as unknown as CanvasRenderingContext2D,
      topology({ ratePerMinute: 12 }),
      POSITIONS,
      99_000,
      { zoom: 1, reducedMotion: true },
    );

    expect(ctx.lineDashOffsets).toContain(-0);
  });

  it('ignores a rate of zero — measured as quiet is still quiet', () => {
    const patterns = dashPatterns({ ratePerMinute: 0 });
    expect(patterns.some((p) => p[0] === EDGE_FLOW.DASH_PX)).toBe(false);
  });
});
