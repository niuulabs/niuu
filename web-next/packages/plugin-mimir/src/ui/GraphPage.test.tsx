import { describe, it, expect } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import {
  GraphPage,
  layoutForceDirected,
  nodeRadius,
  buildDegreeMap,
  zoomViewBox,
  panViewBox,
} from './GraphPage';
import { createMimirMockAdapter } from '../adapters/mock';
import type { IMimirService } from '../ports';
import { renderWithMimir } from '../testing/renderWithMimir';
import type { GraphNode } from '../domain/api-types';

const wrap = renderWithMimir;

describe('GraphPage', () => {
  it('shows loading state initially', () => {
    wrap(<GraphPage />);
    expect(screen.getByText(/loading graph/)).toBeInTheDocument();
  });

  it('renders the SVG graph canvas', async () => {
    wrap(<GraphPage />);
    await waitFor(() =>
      expect(screen.getByRole('img', { name: /knowledge graph/i })).toBeInTheDocument(),
    );
  });

  it('renders graph legend with category label', async () => {
    wrap(<GraphPage />);
    await waitFor(() => screen.getByRole('img', { name: /knowledge graph/i }));
    expect(screen.getByLabelText(/graph legend/i)).toBeInTheDocument();
    expect(screen.getByText('Category')).toBeInTheDocument();
  });

  it('renders graph info card with counts', async () => {
    wrap(<GraphPage />);
    await waitFor(() => screen.getByTestId('graph-info'));
    expect(screen.getByText(/pages/)).toBeInTheDocument();
    expect(screen.getByText(/edges/)).toBeInTheDocument();
  });

  it('shows the active mount in the graph info card', async () => {
    wrap(<GraphPage />, undefined, { tweaks: { activeMount: 'local' } });
    await waitFor(() => screen.getByTestId('graph-info'));
    expect(screen.getByText('local')).toBeInTheDocument();
  });

  it('SVG contains glow filter definition', async () => {
    wrap(<GraphPage />);
    await waitFor(() => screen.getByRole('img', { name: /knowledge graph/i }));
    const svg = screen.getByRole('img', { name: /knowledge graph/i });
    expect(svg.querySelector('filter#niuu-node-glow')).toBeInTheDocument();
  });

  it('unfocused edges have low-opacity class', async () => {
    wrap(<GraphPage />);
    await waitFor(() => screen.getByRole('img', { name: /knowledge graph/i }));
    const svg = screen.getByRole('img', { name: /knowledge graph/i });
    const lines = svg.querySelectorAll('line');
    expect(lines.length).toBeGreaterThan(0);
    expect(lines[0]!.classList.toString()).toContain('niuu:opacity-40');
  });

  it('clicking a node toggles focus', async () => {
    wrap(<GraphPage />);
    await waitFor(() => screen.getByRole('img', { name: /knowledge graph/i }));
    const svg = screen.getByRole('img', { name: /knowledge graph/i });
    const nodeGroups = svg.querySelectorAll('g[role="button"]');
    expect(nodeGroups.length).toBeGreaterThan(0);
    fireEvent.click(nodeGroups[0]!);
    expect(nodeGroups[0]!.getAttribute('aria-pressed')).toBe('true');
  });

  it('shows error state when graph load fails', async () => {
    const failing: IMimirService = {
      ...createMimirMockAdapter(),
      pages: {
        ...createMimirMockAdapter().pages,
        getGraph: async () => {
          throw new Error('graph service unavailable');
        },
      },
    };
    wrap(<GraphPage />, failing);
    await waitFor(() => expect(screen.getByText('graph service unavailable')).toBeInTheDocument());
  });

  it('legend shows Edges section', async () => {
    wrap(<GraphPage />);
    await waitFor(() => screen.getByRole('img', { name: /knowledge graph/i }));
    expect(screen.getByText('Edges')).toBeInTheDocument();
    expect(screen.getByText('shared source')).toBeInTheDocument();
    expect(screen.getByText('wikilink')).toBeInTheDocument();
  });

  it('scrolling zooms the viewBox in and out', async () => {
    wrap(<GraphPage />);
    await waitFor(() => screen.getByRole('img', { name: /knowledge graph/i }));
    const svg = screen.getByRole('img', { name: /knowledge graph/i });
    const initial = svg.getAttribute('viewBox')!;
    const initialW = Number(initial.split(' ')[2]);

    fireEvent.wheel(svg, { deltaY: -100 });
    const zoomedIn = Number(svg.getAttribute('viewBox')!.split(' ')[2]);
    expect(zoomedIn).toBeLessThan(initialW);

    fireEvent.wheel(svg, { deltaY: 100 });
    fireEvent.wheel(svg, { deltaY: 100 });
    const zoomedOut = Number(svg.getAttribute('viewBox')!.split(' ')[2]);
    expect(zoomedOut).toBeGreaterThan(initialW);
  });

  it('dragging pans the viewBox', async () => {
    wrap(<GraphPage />);
    await waitFor(() => screen.getByRole('img', { name: /knowledge graph/i }));
    const svg = screen.getByRole('img', { name: /knowledge graph/i });
    const initial = svg.getAttribute('viewBox')!;

    fireEvent.pointerDown(svg, { pointerId: 1, clientX: 200, clientY: 200 });
    fireEvent.pointerMove(svg, { pointerId: 1, clientX: 150, clientY: 170 });
    fireEvent.pointerUp(svg, { pointerId: 1 });

    const panned = svg.getAttribute('viewBox')!;
    expect(panned).not.toBe(initial);
    const [x, y] = panned.split(' ').map(Number);
    // Dragging left/up moves the viewBox origin right/down.
    expect(x).toBeGreaterThan(0);
    expect(y).toBeGreaterThan(0);
  });

  it('pointer moves without a preceding pointer down do not pan', async () => {
    wrap(<GraphPage />);
    await waitFor(() => screen.getByRole('img', { name: /knowledge graph/i }));
    const svg = screen.getByRole('img', { name: /knowledge graph/i });
    const initial = svg.getAttribute('viewBox')!;
    fireEvent.pointerMove(svg, { pointerId: 1, clientX: 50, clientY: 50 });
    expect(svg.getAttribute('viewBox')).toBe(initial);
  });

  it('node radius scales with edge count', async () => {
    wrap(<GraphPage />);
    await waitFor(() => screen.getByRole('img', { name: /knowledge graph/i }));
    const svg = screen.getByRole('img', { name: /knowledge graph/i });
    const radii = [...svg.querySelectorAll('g[role="button"] circle')].map((c) =>
      Number(c.getAttribute('r')),
    );
    expect(radii.length).toBeGreaterThan(0);
    // The mock graph has both connected and unconnected nodes.
    expect(new Set(radii).size).toBeGreaterThan(1);
    expect(Math.min(...radii)).toBeGreaterThanOrEqual(4);
    expect(Math.max(...radii)).toBeLessThanOrEqual(10);
  });
});

describe('nodeRadius', () => {
  it('returns the minimum radius for isolated nodes', () => {
    expect(nodeRadius(0)).toBe(4);
  });

  it('grows with edge count', () => {
    expect(nodeRadius(2)).toBeGreaterThan(nodeRadius(1));
  });

  it('caps at the maximum radius', () => {
    expect(nodeRadius(100)).toBe(10);
  });
});

describe('buildDegreeMap', () => {
  it('counts inbound and outbound edges per node', () => {
    const degree = buildDegreeMap({
      nodes: [],
      edges: [
        { source: 'a', target: 'b' },
        { source: 'a', target: 'c' },
        { source: 'b', target: 'c' },
      ],
    });
    expect(degree.get('a')).toBe(2);
    expect(degree.get('b')).toBe(2);
    expect(degree.get('c')).toBe(2);
    expect(degree.get('d')).toBeUndefined();
  });
});

describe('zoomViewBox', () => {
  const base = { x: 0, y: 0, w: 1100, h: 750 };

  it('shrinks the viewBox when zooming in', () => {
    const zoomed = zoomViewBox(base, 0.9, 550, 375);
    expect(zoomed.w).toBeCloseTo(990);
    expect(zoomed.h).toBeCloseTo(675);
  });

  it('keeps the anchor point stationary', () => {
    const anchorX = 200;
    const anchorY = 100;
    const zoomed = zoomViewBox(base, 0.5, anchorX, anchorY);
    // The anchor's relative position must be unchanged.
    const relBefore = (anchorX - base.x) / base.w;
    const relAfter = (anchorX - zoomed.x) / zoomed.w;
    expect(relAfter).toBeCloseTo(relBefore);
    const relBeforeY = (anchorY - base.y) / base.h;
    const relAfterY = (anchorY - zoomed.y) / zoomed.h;
    expect(relAfterY).toBeCloseTo(relBeforeY);
  });

  it('clamps zoom-in to the minimum width', () => {
    let vb = base;
    for (let i = 0; i < 50; i++) vb = zoomViewBox(vb, 0.5, 550, 375);
    expect(vb.w).toBeCloseTo(1100 / 8);
  });

  it('clamps zoom-out to the maximum width', () => {
    let vb = base;
    for (let i = 0; i < 50; i++) vb = zoomViewBox(vb, 2, 550, 375);
    expect(vb.w).toBeCloseTo(1100 * 4);
  });
});

describe('panViewBox', () => {
  it('shifts the origin opposite to the drag direction, scaled to SVG units', () => {
    const vb = { x: 0, y: 0, w: 1100, h: 750 };
    const panned = panViewBox(vb, -110, 55, 1100);
    expect(panned.x).toBeCloseTo(110);
    expect(panned.y).toBeCloseTo(-55);
    expect(panned.w).toBe(1100);
    expect(panned.h).toBe(750);
  });

  it('falls back to the base width when the client width is zero', () => {
    const vb = { x: 0, y: 0, w: 1100, h: 750 };
    const panned = panViewBox(vb, -10, 0, 0);
    expect(panned.x).toBeCloseTo(10);
  });
});

describe('layoutForceDirected', () => {
  const box = { w: 1100, h: 750 };

  it('returns empty array for empty input', () => {
    expect(layoutForceDirected([], [])).toEqual([]);
  });

  it('returns single node at center', () => {
    const nodes: GraphNode[] = [{ id: 'n1', title: 'Node 1', category: 'a' }];
    const result = layoutForceDirected(nodes, []);
    expect(result).toHaveLength(1);
    expect(result[0]!.x).toBe(box.w / 2);
    expect(result[0]!.y).toBe(box.h / 2);
  });

  it('keeps every node inside the canvas with padding', () => {
    const nodes: GraphNode[] = Array.from({ length: 24 }, (_, i) => ({
      id: `n${i}`,
      title: `N${i}`,
      category: i % 2 ? 'alpha' : 'beta',
    }));
    const edges = nodes.slice(1).map((n, i) => ({ source: `n${i}`, target: n.id }));
    for (const pos of layoutForceDirected(nodes, edges)) {
      expect(pos.x).toBeGreaterThanOrEqual(0);
      expect(pos.x).toBeLessThanOrEqual(box.w);
      expect(pos.y).toBeGreaterThanOrEqual(0);
      expect(pos.y).toBeLessThanOrEqual(box.h);
    }
  });

  it('produces deterministic output for the same input', () => {
    const nodes: GraphNode[] = Array.from({ length: 12 }, (_, i) => ({
      id: `n${i}`,
      title: `N${i}`,
      category: 'a',
    }));
    const edges = [{ source: 'n0', target: 'n1' }];
    expect(layoutForceDirected(nodes, edges)).toEqual(layoutForceDirected(nodes, edges));
  });

  it('pulls connected nodes closer than unconnected ones', () => {
    // A tight pair plus scattered singletons: the spring must beat repulsion.
    const nodes: GraphNode[] = Array.from({ length: 10 }, (_, i) => ({
      id: `n${i}`,
      title: `N${i}`,
      category: 'a',
    }));
    const edges = [{ source: 'n0', target: 'n1' }];
    const positions = layoutForceDirected(nodes, edges);
    const at = new Map(positions.map((p) => [p.node.id, p]));
    const dist = (a: string, b: string) => {
      const pa = at.get(a)!;
      const pb = at.get(b)!;
      return Math.hypot(pa.x - pb.x, pa.y - pb.y);
    };
    const connected = dist('n0', 'n1');
    const unconnected = positions
      .filter((p) => p.node.id !== 'n0' && p.node.id !== 'n1')
      .map((p) => dist('n0', p.node.id));
    const meanUnconnected = unconnected.reduce((a, b) => a + b, 0) / unconnected.length;
    expect(connected).toBeLessThan(meanUnconnected);
  });

  it('ignores edges referencing unknown node ids', () => {
    const nodes: GraphNode[] = [
      { id: 'n1', title: 'N1', category: 'a' },
      { id: 'n2', title: 'N2', category: 'a' },
    ];
    const edges = [{ source: 'n1', target: 'ghost' }];
    expect(layoutForceDirected(nodes, edges)).toHaveLength(2);
  });
});
