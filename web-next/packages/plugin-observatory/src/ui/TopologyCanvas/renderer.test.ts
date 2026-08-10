import { describe, it, expect, vi } from 'vitest';
import {
  drawStars,
  drawZones,
  drawEdges,
  drawNode,
  drawMinimap,
  labelTier,
  labelTierThreshold,
  shouldDrawLabel,
  shouldDrawNodeDetail,
  worldFontSize,
  regionFontSize,
  drawAgentMesh,
  LAYER_COLOUR,
} from './renderer';
import { LOD, MESH_PULSE } from './config';
import { nodeStyle } from './nodeStyle';
import type { Topology, TopologyNode } from '../../domain';
import type { NodePosition } from './layoutEngine';
import { makeCtxMock } from './test-helpers';

/** A zoom at which every label tier is visible, so label assertions below
 *  test placement and content rather than level-of-detail gating. */
const DETAIL_ZOOM = 1.5;

// ── Shared test data ──────────────────────────────────────────────────────────

const NODES: TopologyNode[] = [
  { id: 'mimir-0', typeId: 'mimir', label: 'mímir', parentId: null, status: 'healthy' },
  { id: 'realm-asgard', typeId: 'realm', label: 'asgard', parentId: null, status: 'healthy' },
  {
    id: 'cluster-vk',
    typeId: 'cluster',
    label: 'valaskjálf',
    parentId: 'realm-asgard',
    status: 'healthy',
  },
  {
    id: 'host-mjolnir',
    typeId: 'host',
    label: 'mjölnir',
    parentId: 'realm-asgard',
    status: 'healthy',
  },
  { id: 'ting-0', typeId: 'ting', label: 'ting-0', parentId: 'cluster-vk', status: 'healthy' },
  {
    id: 'bifrost-0',
    typeId: 'bifrost',
    label: 'bifröst',
    parentId: 'cluster-vk',
    status: 'healthy',
  },
  {
    id: 'volundr-0',
    typeId: 'volundr',
    label: 'völundr',
    parentId: 'cluster-vk',
    status: 'healthy',
  },
  { id: 'ravn-huginn', typeId: 'ravn_long', label: 'huginn', parentId: null, status: 'healthy' },
  { id: 'ravn-coord', typeId: 'ravn_run', label: 'coord', parentId: null, status: 'healthy' },
  { id: 'skuld-0', typeId: 'skuld', label: 'skuld', parentId: null, status: 'healthy' },
  { id: 'valk-0', typeId: 'valkyrie', label: 'brynhildr', parentId: null, status: 'healthy' },
  { id: 'printer-0', typeId: 'printer', label: 'gungnir', parentId: null, status: 'healthy' },
  { id: 'vaettir-0', typeId: 'vaettir', label: 'chatterbox', parentId: null, status: 'healthy' },
  { id: 'beacon-0', typeId: 'beacon', label: 'espresense', parentId: null, status: 'healthy' },
  { id: 'svc-0', typeId: 'service', label: 'grafana', parentId: null, status: 'healthy' },
  { id: 'model-0', typeId: 'model', label: 'claude', parentId: null, status: 'healthy' },
  { id: 'run-0', typeId: 'run', label: 'run-0', parentId: null, status: 'observing' },
];

const TOPOLOGY: Topology = {
  timestamp: '2026-04-19T00:00:00Z',
  nodes: NODES,
  edges: [
    { id: 'e-solid', sourceId: 'ting-0', targetId: 'volundr-0', kind: 'solid' },
    { id: 'e-dashed-anim', sourceId: 'ting-0', targetId: 'run-0', kind: 'dashed-anim' },
    { id: 'e-dashed-long', sourceId: 'ravn-huginn', targetId: 'mimir-0', kind: 'dashed-long' },
    { id: 'e-soft', sourceId: 'bifrost-0', targetId: 'mimir-0', kind: 'soft' },
    { id: 'e-run', sourceId: 'run-0', targetId: 'ravn-coord', kind: 'run' },
  ],
};

// Build a positions map with simple values so rendering doesn't crash.
const POSITIONS = new Map<string, NodePosition>(
  NODES.map((n, i) => [n.id, { x: i * 60, y: i * 40 }]),
);

// ── drawStars ─────────────────────────────────────────────────────────────────

describe('drawStars', () => {
  it('calls save and restore', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    drawStars(ctx, 800, 600, 0);
    expect(ctx.save).toHaveBeenCalled();
    expect(ctx.restore).toHaveBeenCalled();
  });

  it('draws fillRect calls for star pixels', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    drawStars(ctx, 800, 600, 0);
    expect((ctx.fillRect as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(0);
  });

  it('does not throw for zero-size canvas', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    expect(() => drawStars(ctx, 0, 0, 0)).not.toThrow();
  });
});

// ── drawZones ─────────────────────────────────────────────────────────────────

describe('drawZones', () => {
  it('does not throw with realm and cluster nodes', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    expect(() => drawZones(ctx, NODES, POSITIONS, 0, DETAIL_ZOOM)).not.toThrow();
  });

  it('calls arc for realm circles', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    drawZones(ctx, NODES, POSITIONS, 0, DETAIL_ZOOM);
    expect((ctx.arc as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(0);
  });

  it('draws realm labels with fillText', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    drawZones(ctx, NODES, POSITIONS, 0, DETAIL_ZOOM);
    expect((ctx.fillText as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(0);
  });

  it('handles empty node list without throwing', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    expect(() => drawZones(ctx, [], new Map(), 0, DETAIL_ZOOM)).not.toThrow();
  });

  it('skips nodes with no position entry', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    const orphan: TopologyNode = {
      id: 'orphan',
      typeId: 'realm',
      label: 'orphan',
      parentId: null,
      status: 'healthy',
    };
    // orphan has no entry in POSITIONS — should not throw
    expect(() => drawZones(ctx, [orphan], POSITIONS, 0, DETAIL_ZOOM)).not.toThrow();
  });
});

// ── drawEdges ─────────────────────────────────────────────────────────────────

describe('drawEdges', () => {
  it('does not throw with all 5 edge kinds', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    expect(() => drawEdges(ctx, TOPOLOGY, POSITIONS, 0)).not.toThrow();
  });

  it('calls beginPath for each drawable edge', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    drawEdges(ctx, TOPOLOGY, POSITIONS, 0);
    // At least one beginPath per edge
    expect((ctx.beginPath as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(0);
    expect((ctx.bezierCurveTo as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(0);
  });

  it('handles edges with missing source position gracefully', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    const topo: Topology = {
      ...TOPOLOGY,
      edges: [{ id: 'e-missing', sourceId: 'does-not-exist', targetId: 'ting-0', kind: 'solid' }],
    };
    expect(() => drawEdges(ctx, topo, POSITIONS, 0)).not.toThrow();
  });

  it('handles edges with missing target position gracefully', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    const topo: Topology = {
      ...TOPOLOGY,
      edges: [{ id: 'e-missing', sourceId: 'ting-0', targetId: 'does-not-exist', kind: 'solid' }],
    };
    expect(() => drawEdges(ctx, topo, POSITIONS, 0)).not.toThrow();
  });

  it('ignores self-loop edges from stale discovery snapshots', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    const topo: Topology = {
      ...TOPOLOGY,
      edges: [
        {
          id: 'edge:cluster-vk:cluster-vk',
          sourceId: 'cluster-vk',
          targetId: 'cluster-vk',
          kind: 'soft',
        },
      ],
    };
    expect(() => drawEdges(ctx, topo, POSITIONS, 0)).not.toThrow();
    expect(ctx.bezierCurveTo).not.toHaveBeenCalled();
  });

  it('ignores legacy containment edges between parent and child nodes', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    const topo: Topology = {
      ...TOPOLOGY,
      edges: [
        {
          id: 'edge:cluster-vk:ting-0',
          sourceId: 'cluster-vk',
          targetId: 'ting-0',
          kind: 'soft',
        },
        {
          id: 'edge:contains',
          sourceId: 'cluster-vk',
          targetId: 'volundr-0',
          kind: 'soft',
          relationType: 'contains',
        },
      ],
    };
    drawEdges(ctx, topo, POSITIONS, 0);
    expect(ctx.bezierCurveTo).not.toHaveBeenCalled();
  });

  it('animates dashed-anim edges with lineDashOffset', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    const topo: Topology = {
      ...TOPOLOGY,
      edges: [{ id: 'e-da', sourceId: 'ting-0', targetId: 'run-0', kind: 'dashed-anim' }],
    };
    drawEdges(ctx, topo, POSITIONS, 1000);
    expect(
      (ctx.setLineDash as ReturnType<typeof vi.fn>).mock.calls.some(
        (call: unknown[]) => Array.isArray(call[0]) && (call[0] as number[]).length > 0,
      ),
    ).toBe(true);
  });

  it('accepts legacy dashed-short edges without throwing', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    const topo: Topology = {
      ...TOPOLOGY,
      edges: [{ id: 'e-ds', sourceId: 'ting-0', targetId: 'run-0', kind: 'dashed-short' as never }],
    };
    expect(() => drawEdges(ctx, topo, POSITIONS, 1000)).not.toThrow();
    expect(
      (ctx.setLineDash as ReturnType<typeof vi.fn>).mock.calls.some(
        (call: unknown[]) => Array.isArray(call[0]) && (call[0] as number[]).length > 0,
      ),
    ).toBe(true);
  });

  it('falls back safely for unknown edge kinds', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    const topo: Topology = {
      ...TOPOLOGY,
      edges: [{ id: 'e-unknown', sourceId: 'ting-0', targetId: 'run-0', kind: 'mystery' as never }],
    };
    expect(() => drawEdges(ctx, topo, POSITIONS, 0)).not.toThrow();
  });

  it('uses setLineDash([]) for solid edges (no dash)', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    const topo: Topology = {
      ...TOPOLOGY,
      edges: [{ id: 'e-s', sourceId: 'ting-0', targetId: 'volundr-0', kind: 'solid' }],
    };
    drawEdges(ctx, topo, POSITIONS, 0);
    // Called at least with empty array to reset dashes
    expect(
      (ctx.setLineDash as ReturnType<typeof vi.fn>).mock.calls.some(
        (call: unknown[]) => Array.isArray(call[0]) && (call[0] as number[]).length === 0,
      ),
    ).toBe(true);
  });

  it('does not draw inline labels for semantic edges', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    const topo: Topology = {
      timestamp: TOPOLOGY.timestamp,
      nodes: [
        {
          id: 'namespace-ymir-volundr',
          typeId: 'namespace',
          label: 'volundr',
          parentId: 'cluster-ymir',
          status: 'healthy',
        },
        {
          id: 'warden',
          typeId: 'warden',
          label: 'warden',
          parentId: 'namespace-ymir-volundr',
          status: 'healthy',
        },
        {
          id: 'mimir',
          typeId: 'mimir',
          label: 'mimir',
          parentId: 'namespace-ymir-volundr',
          status: 'healthy',
        },
      ],
      edges: [
        {
          id: 'edge:writes',
          sourceId: 'warden',
          targetId: 'mimir',
          kind: 'dashed-long',
          relationType: 'writes',
          label: 'writes',
        },
      ],
    };
    const positions = new Map<string, NodePosition>([
      ['namespace-ymir-volundr', { x: 100, y: 300 }],
      ['warden', { x: 0, y: 0 }],
      ['mimir', { x: 200, y: 0 }],
    ]);

    drawEdges(ctx, topo, positions, 0);

    expect(ctx.roundRect).not.toHaveBeenCalled();
    expect(ctx.fillText).not.toHaveBeenCalled();
  });
});

// ── drawNode ──────────────────────────────────────────────────────────────────

describe('drawNode', () => {
  const pos: NodePosition = { x: 100, y: 100 };

  // Each typeId should render without throwing
  const TYPES = [
    'ting',
    'bifrost',
    'volundr',
    'ravn_long',
    'ravn_run',
    'skuld',
    'valkyrie',
    'printer',
    'vaettir',
    'beacon',
    'service',
    'model',
    'run',
    'unknown-type',
  ];

  for (const typeId of TYPES) {
    it(`renders typeId="${typeId}" without throwing`, () => {
      const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
      const node: TopologyNode = {
        id: `n-${typeId}`,
        typeId,
        label: typeId,
        parentId: null,
        status: 'healthy',
      };
      expect(() => drawNode(ctx, node, pos, false, DETAIL_ZOOM)).not.toThrow();
    });

    it(`renders typeId="${typeId}" hovered without throwing`, () => {
      const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
      const node: TopologyNode = {
        id: `n-${typeId}`,
        typeId,
        label: typeId,
        parentId: null,
        status: 'healthy',
      };
      expect(() => drawNode(ctx, node, pos, true, DETAIL_ZOOM)).not.toThrow();
    });
  }

  it('draws a Mímir like any other node, as a store rather than a well', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    const node: TopologyNode = {
      id: 'mimir-0',
      typeId: 'mimir',
      label: 'mímir',
      parentId: null,
      status: 'healthy',
    };
    drawNode(ctx, node, pos, false, DETAIL_ZOOM);
    expect((ctx.beginPath as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(0);
  });

  it('draws host as a circular container', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    const host: TopologyNode = {
      id: 'h',
      typeId: 'host',
      label: 'tanngrisnir',
      parentId: null,
      status: 'healthy',
    };
    drawNode(ctx, host, pos, false, DETAIL_ZOOM);
    expect((ctx.arc as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(0);
    const calls = (ctx.fillText as ReturnType<typeof vi.fn>).mock.calls as [string, ...unknown[]][];
    expect(calls.some(([text]) => text === 'tanngrisnir')).toBe(true);
    expect((ctx.strokeRect as ReturnType<typeof vi.fn>).mock.calls.length).toBe(0);
  });

  it('halos a hovered node so the selection reads against the field', () => {
    const plain = makeCtxMock() as unknown as CanvasRenderingContext2D;
    const hovered = makeCtxMock() as unknown as CanvasRenderingContext2D;
    const node: TopologyNode = {
      id: 'ting-0',
      typeId: 'ting',
      label: 'ting',
      parentId: null,
      status: 'healthy',
    };
    drawNode(plain, node, pos, false, DETAIL_ZOOM);
    drawNode(hovered, node, pos, true, DETAIL_ZOOM);
    expect((hovered.arc as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(
      (plain.arc as ReturnType<typeof vi.fn>).mock.calls.length,
    );
  });

  /**
   * The glyph is whatever the registry says it is. These assert the dispatch,
   * not the geometry — `shapes.test.ts` covers what each mark actually draws.
   */
  it('draws the glyph the registry declares for the type', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    const node: TopologyNode = {
      id: 'bf-0',
      typeId: 'bifrost',
      label: 'bifröst',
      parentId: null,
      status: 'healthy',
    };
    const typeStyles = new Map([['bifrost', { shape: 'pentagon', size: 15 }]]);
    drawNode(ctx, node, pos, false, DETAIL_ZOOM, { style: nodeStyle(node, typeStyles) });
    // A pentagon is five straight sides — no arcs, no rounded corners.
    expect((ctx.lineTo as ReturnType<typeof vi.fn>).mock.calls.length).toBe(4);
    expect((ctx.arcTo as ReturnType<typeof vi.fn>).mock.calls.length).toBe(0);
  });

  it('falls back to the boxed dot when the registry declares no glyph', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    const node: TopologyNode = {
      id: 'svc-0',
      typeId: 'service',
      label: 'observatory',
      parentId: null,
      status: 'healthy',
    };
    drawNode(ctx, node, pos, false, DETAIL_ZOOM, { style: nodeStyle(node, new Map()) });
    expect((ctx.arcTo as ReturnType<typeof vi.fn>).mock.calls.length).toBe(4);
  });

  it('labels a node with its own name, not a rune', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    const node: TopologyNode = {
      id: 'svc-0',
      typeId: 'service',
      label: 'observatory',
      parentId: null,
      status: 'healthy',
    };
    drawNode(ctx, node, pos, false, DETAIL_ZOOM);
    const calls = (ctx.fillText as ReturnType<typeof vi.fn>).mock.calls as [string, ...unknown[]][];
    expect(calls.map(([text]) => text)).toContain('observatory');
  });
});

// ── drawMinimap ───────────────────────────────────────────────────────────────

describe('drawMinimap', () => {
  it('does not throw with full topology and valid camera', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    expect(() =>
      drawMinimap(ctx, 220, 165, TOPOLOGY, POSITIONS, 0, 0, 1, 800, 600, 4200, 3600),
    ).not.toThrow();
  });

  it('clears and fills the background', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    drawMinimap(ctx, 220, 165, TOPOLOGY, POSITIONS, 0, 0, 1, 800, 600, 4200, 3600);
    expect(ctx.clearRect).toHaveBeenCalledWith(0, 0, 220, 165);
    expect(ctx.fillRect).toHaveBeenCalledWith(0, 0, 220, 165);
  });

  it('draws realm outline arcs', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    drawMinimap(ctx, 220, 165, TOPOLOGY, POSITIONS, 0, 0, 1, 800, 600, 4200, 3600);
    expect((ctx.arc as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(0);
  });

  it('renders node dots with fillRect', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    drawMinimap(ctx, 220, 165, TOPOLOGY, POSITIONS, 0, 0, 1, 800, 600, 4200, 3600);
    // fillRect calls > 1 (background + node dots)
    expect((ctx.fillRect as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(1);
  });

  it('draws viewport rect with strokeRect', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    drawMinimap(ctx, 220, 165, TOPOLOGY, POSITIONS, 0, 0, 1, 800, 600, 4200, 3600);
    expect(ctx.strokeRect).toHaveBeenCalled();
  });

  it('skips viewport rect when camZoom is 0', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    drawMinimap(ctx, 220, 165, TOPOLOGY, POSITIONS, 0, 0, 0, 800, 600, 4200, 3600);
    expect(ctx.strokeRect).not.toHaveBeenCalled();
  });

  it('renders entity count caption', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    drawMinimap(ctx, 220, 165, TOPOLOGY, POSITIONS, 0, 0, 1, 800, 600, 4200, 3600);
    const calls = (ctx.fillText as ReturnType<typeof vi.fn>).mock.calls as [string, ...unknown[]][];
    expect(calls.some(([text]) => /entities/.test(text))).toBe(true);
  });
});

// ── Level of detail ───────────────────────────────────────────────────────────

describe('labelTier', () => {
  it('treats the entities an operator scans for first as primary', () => {
    for (const typeId of ['ravn_long', 'valkyrie', 'run']) {
      expect(labelTier(typeId)).toBe('primary');
    }
  });

  it('treats supporting infrastructure as secondary', () => {
    for (const typeId of ['service', 'model', 'host', 'ravn_run', 'bifrost']) {
      expect(labelTier(typeId)).toBe('secondary');
    }
  });

  it('gives primary types a lower zoom threshold than secondary ones', () => {
    expect(labelTierThreshold('ravn_long')).toBe(LOD.PRIMARY);
    expect(labelTierThreshold('service')).toBe(LOD.SECONDARY);
    expect(labelTierThreshold('ravn_long')).toBeLessThan(labelTierThreshold('service'));
  });
});

describe('shouldDrawLabel', () => {
  it('hides every label at overview zoom', () => {
    expect(shouldDrawLabel('ravn_long', LOD.PRIMARY - 0.01, false)).toBe(false);
    expect(shouldDrawLabel('service', LOD.PRIMARY, false)).toBe(false);
  });

  it('reveals primary labels before secondary ones as the camera comes in', () => {
    const between = (LOD.PRIMARY + LOD.SECONDARY) / 2;
    expect(shouldDrawLabel('ravn_long', between, false)).toBe(true);
    expect(shouldDrawLabel('service', between, false)).toBe(false);
    expect(shouldDrawLabel('service', LOD.SECONDARY, false)).toBe(true);
  });

  it('always labels an emphasised node so detail is never unreachable', () => {
    expect(shouldDrawLabel('service', 0.01, true)).toBe(true);
    expect(shouldDrawLabel('beacon', 0, true)).toBe(true);
  });
});

describe('shouldDrawNodeDetail', () => {
  it('withholds the secondary line until there is room for it', () => {
    expect(shouldDrawNodeDetail(LOD.NODE_DETAIL - 0.01, false)).toBe(false);
    expect(shouldDrawNodeDetail(LOD.NODE_DETAIL, false)).toBe(true);
  });

  it('shows it for an emphasised node at any zoom', () => {
    expect(shouldDrawNodeDetail(0.1, true)).toBe(true);
  });
});

describe('worldFontSize', () => {
  it('keeps text a constant screen size by cancelling the camera scale', () => {
    expect(worldFontSize(10, 0.5)).toBe(20);
    expect(worldFontSize(10, 2)).toBe(5);
    expect(worldFontSize(10, 1)).toBe(10);
  });

  it('falls back to the screen size for a degenerate zoom rather than dividing by zero', () => {
    expect(worldFontSize(10, 0)).toBe(10);
    expect(worldFontSize(10, Number.NaN)).toBe(10);
    expect(worldFontSize(10, Number.POSITIVE_INFINITY)).toBe(10);
  });
});

describe('drawNode level of detail', () => {
  const pos: NodePosition = { x: 0, y: 0 };

  function labelsDrawnAt(zoom: number, typeId: string): string[] {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    const node: TopologyNode = {
      id: `n-${typeId}`,
      typeId,
      label: 'sample-label',
      parentId: null,
      status: 'healthy',
    };
    drawNode(ctx, node, pos, false, zoom);
    const calls = (ctx.fillText as ReturnType<typeof vi.fn>).mock.calls as [string, ...unknown[]][];
    return calls.map(([text]) => text);
  }

  it('draws no service label at overview zoom', () => {
    expect(labelsDrawnAt(LOD.PRIMARY - 0.01, 'service')).not.toContain('sample-label');
  });

  it('draws the service label once zoomed past its tier', () => {
    expect(labelsDrawnAt(LOD.SECONDARY, 'service')).toContain('sample-label');
  });

  it('draws a resident label at a zoom where a service still has none', () => {
    const between = (LOD.PRIMARY + LOD.SECONDARY) / 2;
    expect(labelsDrawnAt(between, 'ravn_long')).toContain('sample-label');
    expect(labelsDrawnAt(between, 'service')).not.toContain('sample-label');
  });
});

describe('drawAgentMesh', () => {
  const A = { x: 0, y: 0, radius: 10 };
  const B = { x: 100, y: 0, radius: 6 };
  const C2 = { x: 100, y: 100, radius: 10 };

  it('draws nothing when the mesh has no placed members', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    drawAgentMesh(ctx, [], 0, 1);
    expect(ctx.stroke).not.toHaveBeenCalled();
  });

  it('marks a single member, unlike the hull it replaced', () => {
    // A hull needs two points to exist at all, so a one-member mesh used to
    // draw nothing and the selection read as having no effect.
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    drawAgentMesh(ctx, [A], 0, 1);
    expect(ctx.stroke).toHaveBeenCalled();
  });

  it('rings every member rather than enclosing the space between them', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    drawAgentMesh(ctx, [A, B, C2], 0, 1);
    const centres = (ctx.arc as ReturnType<typeof vi.fn>).mock.calls.map(([x, y]) => `${x},${y}`);
    for (const member of [A, B, C2]) {
      expect(centres).toContain(`${member.x},${member.y}`);
    }
    // No path is traced *between* members: the hull this replaced joined them
    // with lines and curves, which is what enclosed everything in between.
    expect(ctx.moveTo).not.toHaveBeenCalled();
    expect(ctx.lineTo).not.toHaveBeenCalled();
    expect(ctx.quadraticCurveTo).not.toHaveBeenCalled();
  });

  it('sizes each ring off that member, so one radius does not fit all', () => {
    const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
    drawAgentMesh(ctx, [A, B], 0, 1, true);
    const radiiFor = (m: { x: number; y: number }) =>
      (ctx.arc as ReturnType<typeof vi.fn>).mock.calls
        .filter(([x, y]) => x === m.x && y === m.y)
        .map(([, , r]) => r as number);
    expect(Math.min(...radiiFor(A))).toBeGreaterThan(Math.min(...radiiFor(B)));
  });

  it('expands the ring as the cycle advances', () => {
    // Per member: glow wash, halo, leading ring, trailing ring.
    const LEADING_RING = 2;
    const leadingRingRadiusAt = (now: number): number => {
      const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
      drawAgentMesh(ctx, [A], now, 1);
      const calls = (ctx.arc as ReturnType<typeof vi.fn>).mock.calls;
      return calls[LEADING_RING]![2] as number;
    };
    const early = leadingRingRadiusAt(MESH_PULSE.PERIOD_MS * 0.1);
    const late = leadingRingRadiusAt(MESH_PULSE.PERIOD_MS * 0.7);
    expect(late).toBeGreaterThan(early);
    // And it comes back rather than growing without bound.
    expect(leadingRingRadiusAt(0)).toBeLessThan(early);
  });

  it('holds the rings still under reduced motion', () => {
    const framesFor = (reduced: boolean) =>
      [0, MESH_PULSE.PERIOD_MS * 0.5].map((now) => {
        const ctx = makeCtxMock() as unknown as CanvasRenderingContext2D;
        drawAgentMesh(ctx, [A], now, 1, reduced);
        return JSON.stringify((ctx.arc as ReturnType<typeof vi.fn>).mock.calls);
      });
    const [still1, still2] = framesFor(true);
    expect(still1).toBe(still2);
    const [moving1, moving2] = framesFor(false);
    expect(moving1).not.toBe(moving2);
  });

  it('draws the mesh in amber, the colour its edges already use', () => {
    const mock = makeCtxMock();
    drawAgentMesh(mock as unknown as CanvasRenderingContext2D, [A], 0, 1);
    const amber = LAYER_COLOUR.mesh.join(',');
    expect(mock.strokeStyles.length).toBeGreaterThan(0);
    expect(mock.strokeStyles.every((s) => s.includes(amber))).toBe(true);
  });
});

describe('regionFontSize', () => {
  it('holds a region label at a fixed world size while zoomed in', () => {
    // A realm's name belongs to the region, not to the screen: at and above
    // the reference scale it stays the same size as the thing it names.
    expect(regionFontSize(22, 1)).toBe(22);
    expect(regionFontSize(22, 2)).toBe(22);
  });

  it('grows it only enough to stay legible once zoomed out', () => {
    // Held at a constant *screen* size the name outgrew its own realm, which
    // shrinks as the camera pulls back.
    expect(regionFontSize(22, 0.25)).toBe(44);
    expect(regionFontSize(22, 0.25)).toBeLessThan(worldFontSize(22, 0.25));
  });

  it('survives a nonsense zoom', () => {
    expect(regionFontSize(22, 0)).toBe(22);
    expect(regionFontSize(22, Number.NaN)).toBe(22);
  });
});
