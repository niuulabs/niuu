/**
 * GraphPage — full-bleed knowledge graph canvas with floating overlays.
 *
 * Matches web2 layout: graph fills the content area, category legend floats
 * top-left, graph info card floats top-right. Click a node to focus, click
 * again to deselect. Drag to pan, scroll to zoom (viewBox-based). Node
 * radius scales with edge count.
 */

import { useMemo, useRef, useState } from 'react';
import { StateDot } from '@niuulabs/ui';
import { useActiveMount } from '../application/useActiveMount';
import { useGraph } from '../application/useGraph';
import type { MimirGraph, GraphNode } from '../domain/api-types';
import './GraphPage.css';

const SVG_W = 1100;
const SVG_H = 750;

// Node radius scaling — radius grows with edge count, capped.
const NODE_RADIUS_MIN = 4;
const NODE_RADIUS_MAX = 10;
const NODE_RADIUS_PER_EDGE = 1.2;
const FOCUS_RADIUS_BONUS = 3;

// Zoom limits as multiples of the base viewBox width.
const ZOOM_MIN_SCALE = 1 / 8;
const ZOOM_MAX_SCALE = 4;
const ZOOM_STEP = 1.1;

const CATEGORY_COLORS = [
  'var(--brand-300)',
  'var(--brand-400)',
  'var(--brand-500)',
  'var(--brand-200)',
  'var(--color-text-secondary)',
  'var(--brand-600)',
  'var(--color-text-muted)',
  'var(--brand-700)',
] as const;

function getCategoryIndex(category: string, categories: string[]): number {
  const idx = categories.indexOf(category);
  return (idx < 0 ? 0 : idx) % CATEGORY_COLORS.length;
}

function getCategoryFill(category: string, categories: string[]): string {
  return CATEGORY_COLORS[getCategoryIndex(category, categories)] ?? CATEGORY_COLORS[0];
}

// ---------------------------------------------------------------------------
// SVG layout
// ---------------------------------------------------------------------------

interface NodePosition {
  node: GraphNode;
  x: number;
  y: number;
}

// Force-directed layout: edges act as springs so linked pages cluster and
// their connections stay short enough to read. Deterministic (hash-seeded
// initial ring, fixed iteration count) so tests and reloads are stable.
const LAYOUT_ITERATIONS = 260;
const LAYOUT_PADDING = 70;
const SPRING_LENGTH = 110;
const SPRING_K = 0.02;
const REPULSION = 22000;
const GRAVITY = 0.012;
const COOLING = 0.96;
const INITIAL_STEP = 26;

/** Deterministic [0, 1) value from a string (FNV-1a). */
function hash01(value: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < value.length; i++) {
    h ^= value.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0) / 0x100000000;
}

export function layoutForceDirected(
  nodes: GraphNode[],
  edges: { source: string; target: string }[],
  width = SVG_W,
  height = SVG_H,
): NodePosition[] {
  if (nodes.length === 0) return [];
  const cx = width / 2;
  const cy = height / 2;
  if (nodes.length === 1) return [{ node: nodes[0]!, x: cx, y: cy }];

  // Hash-seeded ring start: stable, and spread enough that repulsion
  // doesn't have to untangle a degenerate cluster.
  const initRadius = Math.min(width, height) / 3;
  const xs = nodes.map(
    (n, i) =>
      cx +
      initRadius *
        (0.6 + 0.4 * hash01(`${n.id}:r`)) *
        Math.cos((2 * Math.PI * i) / nodes.length + hash01(n.id)),
  );
  const ys = nodes.map(
    (n, i) =>
      cy +
      initRadius *
        (0.6 + 0.4 * hash01(`${n.id}:r`)) *
        Math.sin((2 * Math.PI * i) / nodes.length + hash01(n.id)),
  );

  const index = new Map(nodes.map((n, i) => [n.id, i]));
  const springs = edges
    .map((e) => [index.get(e.source), index.get(e.target)] as const)
    .filter((pair): pair is [number, number] => pair[0] !== undefined && pair[1] !== undefined);

  let step = INITIAL_STEP;
  for (let iter = 0; iter < LAYOUT_ITERATIONS; iter++) {
    const fx = new Array<number>(nodes.length).fill(0);
    const fy = new Array<number>(nodes.length).fill(0);

    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        let dx = xs[i]! - xs[j]!;
        let dy = ys[i]! - ys[j]!;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) {
          // Coincident points: nudge apart deterministically.
          dx = hash01(`${i}:${j}`) - 0.5;
          dy = hash01(`${j}:${i}`) - 0.5;
          d2 = dx * dx + dy * dy;
        }
        const f = REPULSION / d2;
        const d = Math.sqrt(d2);
        fx[i]! += (dx / d) * f;
        fy[i]! += (dy / d) * f;
        fx[j]! -= (dx / d) * f;
        fy[j]! -= (dy / d) * f;
      }
    }

    for (const [a, b] of springs) {
      const dx = xs[b]! - xs[a]!;
      const dy = ys[b]! - ys[a]!;
      const d = Math.max(1, Math.sqrt(dx * dx + dy * dy));
      const f = SPRING_K * (d - SPRING_LENGTH);
      fx[a]! += (dx / d) * f * d;
      fy[a]! += (dy / d) * f * d;
      fx[b]! -= (dx / d) * f * d;
      fy[b]! -= (dy / d) * f * d;
    }

    for (let i = 0; i < nodes.length; i++) {
      fx[i]! += (cx - xs[i]!) * GRAVITY;
      fy[i]! += (cy - ys[i]!) * GRAVITY;
      const mag = Math.sqrt(fx[i]! * fx[i]! + fy[i]! * fy[i]!) || 1;
      const clamp = Math.min(step, mag);
      xs[i] = xs[i]! + (fx[i]! / mag) * clamp;
      ys[i] = ys[i]! + (fy[i]! / mag) * clamp;
    }
    step *= COOLING;
  }

  // Fit the settled layout into the canvas with padding.
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const spanX = Math.max(1, maxX - minX);
  const spanY = Math.max(1, maxY - minY);
  const scale = Math.min((width - 2 * LAYOUT_PADDING) / spanX, (height - 2 * LAYOUT_PADDING) / spanY);

  return nodes.map((node, i) => ({
    node,
    x: LAYOUT_PADDING + (xs[i]! - minX) * scale + (width - 2 * LAYOUT_PADDING - spanX * scale) / 2,
    y: LAYOUT_PADDING + (ys[i]! - minY) * scale + (height - 2 * LAYOUT_PADDING - spanY * scale) / 2,
  }));
}

// ---------------------------------------------------------------------------
// Pan / zoom helpers (viewBox-based)
// ---------------------------------------------------------------------------

export interface ViewBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

const BASE_VIEWBOX: ViewBox = { x: 0, y: 0, w: SVG_W, h: SVG_H };

/** Radius for a node with the given edge count (degree). */
export function nodeRadius(edgeCount: number): number {
  return Math.min(NODE_RADIUS_MAX, NODE_RADIUS_MIN + edgeCount * NODE_RADIUS_PER_EDGE);
}

/** Edge count (inbound + outbound) per node id. */
export function buildDegreeMap(graph: MimirGraph): Map<string, number> {
  const degree = new Map<string, number>();
  for (const edge of graph.edges) {
    degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
  }
  return degree;
}

/**
 * Zoom the viewBox by `factor` (>1 zooms out, <1 zooms in), keeping the
 * anchor point (in SVG coordinates) stationary. Clamped to the zoom limits.
 */
export function zoomViewBox(
  vb: ViewBox,
  factor: number,
  anchorX: number,
  anchorY: number,
): ViewBox {
  const w = Math.min(SVG_W * ZOOM_MAX_SCALE, Math.max(SVG_W * ZOOM_MIN_SCALE, vb.w * factor));
  const applied = w / vb.w;
  const h = vb.h * applied;
  return {
    x: anchorX - (anchorX - vb.x) * applied,
    y: anchorY - (anchorY - vb.y) * applied,
    w,
    h,
  };
}

/** Shift the viewBox by a pixel delta, scaled to SVG units. */
export function panViewBox(vb: ViewBox, dxPx: number, dyPx: number, clientWidth: number): ViewBox {
  const scale = vb.w / (clientWidth || SVG_W);
  return { ...vb, x: vb.x - dxPx * scale, y: vb.y - dyPx * scale };
}

// ---------------------------------------------------------------------------
// Graph SVG
// ---------------------------------------------------------------------------

interface GraphSvgProps {
  graph: MimirGraph;
  focusId: string | null;
  onNodeClick: (id: string) => void;
  categories: string[];
}

function GraphSvg({ graph, focusId, onNodeClick, categories }: GraphSvgProps) {
  const positions = useMemo(
    () => layoutForceDirected(graph.nodes, graph.edges),
    [graph.nodes, graph.edges],
  );
  const posMap = new Map(positions.map((p) => [p.node.id, p]));
  const degree = buildDegreeMap(graph);

  const [viewBox, setViewBox] = useState<ViewBox>(BASE_VIEWBOX);
  const [isPanning, setIsPanning] = useState(false);
  const drag = useRef<{ pointerId: number; lastX: number; lastY: number } | null>(null);

  function svgPointAt(
    svg: SVGSVGElement,
    clientX: number,
    clientY: number,
  ): { x: number; y: number } {
    const rect = svg.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) {
      return { x: viewBox.x + viewBox.w / 2, y: viewBox.y + viewBox.h / 2 };
    }
    return {
      x: viewBox.x + ((clientX - rect.left) / rect.width) * viewBox.w,
      y: viewBox.y + ((clientY - rect.top) / rect.height) * viewBox.h,
    };
  }

  function handleWheel(e: React.WheelEvent<SVGSVGElement>) {
    const factor = e.deltaY > 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
    const anchor = svgPointAt(e.currentTarget, e.clientX, e.clientY);
    setViewBox((vb) => zoomViewBox(vb, factor, anchor.x, anchor.y));
  }

  function handlePointerDown(e: React.PointerEvent<SVGSVGElement>) {
    drag.current = { pointerId: e.pointerId, lastX: e.clientX, lastY: e.clientY };
    setIsPanning(true);
    e.currentTarget.setPointerCapture?.(e.pointerId);
  }

  function handlePointerMove(e: React.PointerEvent<SVGSVGElement>) {
    if (!drag.current || drag.current.pointerId !== e.pointerId) return;
    const dx = e.clientX - drag.current.lastX;
    const dy = e.clientY - drag.current.lastY;
    drag.current = { pointerId: e.pointerId, lastX: e.clientX, lastY: e.clientY };
    const width = e.currentTarget.getBoundingClientRect().width;
    setViewBox((vb) => panViewBox(vb, dx, dy, width));
  }

  function handlePointerEnd(e: React.PointerEvent<SVGSVGElement>) {
    if (!drag.current || drag.current.pointerId !== e.pointerId) return;
    drag.current = null;
    setIsPanning(false);
  }

  return (
    <svg
      className={`niuu-graph-canvas ${isPanning ? 'niuu:cursor-grabbing' : 'niuu:cursor-grab'}`}
      viewBox={`${viewBox.x} ${viewBox.y} ${viewBox.w} ${viewBox.h}`}
      role="img"
      aria-label="Knowledge graph"
      preserveAspectRatio="xMidYMid meet"
      onWheel={handleWheel}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerEnd}
      onPointerLeave={handlePointerEnd}
    >
      <defs>
        <filter id="niuu-node-glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="3" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      <g>
        {graph.edges.map((edge) => {
          const src = posMap.get(edge.source);
          const tgt = posMap.get(edge.target);
          if (!src || !tgt) return null;
          const isFocusEdge =
            focusId !== null && (edge.source === focusId || edge.target === focusId);
          const isWikilink = edge.type === 'wikilink';
          return (
            <line
              key={`${edge.source}-${edge.target}`}
              x1={src.x}
              y1={src.y}
              x2={tgt.x}
              y2={tgt.y}
              stroke={isFocusEdge ? 'var(--brand-400)' : 'var(--color-border)'}
              strokeWidth={isFocusEdge ? 1.75 : 1.25}
              strokeDasharray={isWikilink ? '4 3' : undefined}
              className={isFocusEdge ? 'niuu:opacity-90' : 'niuu:opacity-40'}
            />
          );
        })}
      </g>

      <g>
        {positions.map(({ node, x, y }) => {
          const isFocus = node.id === focusId;
          const fill = isFocus
            ? 'var(--color-brand, var(--color-accent-cyan))'
            : getCategoryFill(node.category, categories);
          const radius = nodeRadius(degree.get(node.id) ?? 0);
          return (
            <g
              key={node.id}
              transform={`translate(${x},${y})`}
              onClick={() => onNodeClick(node.id)}
              className="niuu-graph-node niuu:cursor-pointer"
              role="button"
              aria-pressed={isFocus}
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') onNodeClick(node.id);
              }}
            >
              <circle
                r={isFocus ? radius + FOCUS_RADIUS_BONUS : radius}
                className="niuu-graph-node-circle"
                fill={fill}
                stroke={isFocus ? fill : 'none'}
                strokeWidth={1}
              />
            </g>
          );
        })}
      </g>
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Legend overlay (top-left)
// ---------------------------------------------------------------------------

interface LegendProps {
  categories: string[];
}

function GraphLegend({ categories }: LegendProps) {
  if (categories.length === 0) return null;
  return (
    <div className="niuu-graph-overlay niuu-graph-overlay--legend" aria-label="Graph legend">
      <span className="niuu:text-[10px] niuu:uppercase niuu:tracking-widest niuu:text-text-muted niuu:font-semibold niuu:mb-1">
        Category
      </span>
      {categories.map((cat, i) => (
        <div key={cat} className="niuu:flex niuu:items-center niuu:gap-2">
          <span
            className="niuu-graph-legend-dot niuu:w-2 niuu:h-2 niuu:rounded-full niuu:shrink-0"
            data-color-idx={String(i % CATEGORY_COLORS.length)}
            aria-hidden
          />
          <span className="niuu:text-xs niuu:text-text-secondary niuu:font-mono">{cat}</span>
        </div>
      ))}
      <span className="niuu:text-[10px] niuu:uppercase niuu:tracking-widest niuu:text-text-muted niuu:font-semibold niuu:mt-2 niuu:mb-1">
        Edges
      </span>
      <div className="niuu:flex niuu:items-center niuu:gap-2">
        <svg width="20" height="2" className="niuu:shrink-0">
          <line x1="0" y1="1" x2="20" y2="1" stroke="var(--color-border)" strokeWidth="1.5" />
        </svg>
        <span className="niuu:text-xs niuu:text-text-secondary niuu:font-mono">shared source</span>
      </div>
      <div className="niuu:flex niuu:items-center niuu:gap-2">
        <svg width="20" height="2" className="niuu:shrink-0">
          <line
            x1="0"
            y1="1"
            x2="20"
            y2="1"
            stroke="var(--color-border)"
            strokeWidth="1.5"
            strokeDasharray="4 3"
          />
        </svg>
        <span className="niuu:text-xs niuu:text-text-secondary niuu:font-mono">wikilink</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Info card overlay (top-right)
// ---------------------------------------------------------------------------

interface InfoCardProps {
  nodeCount: number;
  edgeCount: number;
  mountLabel: string;
}

function GraphInfo({ nodeCount, edgeCount, mountLabel }: InfoCardProps) {
  return (
    <div className="niuu-graph-overlay niuu-graph-overlay--info" data-testid="graph-info">
      <span className="niuu:text-[10px] niuu:uppercase niuu:tracking-widest niuu:text-text-muted niuu:font-semibold">
        Graph
      </span>
      <span className="niuu:text-sm niuu:font-semibold niuu:text-text-primary niuu:font-mono">
        {nodeCount} pages · {edgeCount} edges
      </span>
      <span className="niuu:text-xs niuu:text-brand-300 niuu:font-mono">{mountLabel}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// GraphPage
// ---------------------------------------------------------------------------

export function GraphPage() {
  const { activeMount, mountName } = useActiveMount();
  const { graph, focusId, setFocusId, isLoading, isError, error } = useGraph(mountName);
  const mountLabel = activeMount === 'all' ? 'all mounts' : activeMount;

  const displayGraph = graph;
  const categories = displayGraph
    ? [...new Set(displayGraph.nodes.map((n) => n.category))].filter(Boolean).sort()
    : [];

  if (isLoading) {
    return (
      <div className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-text-secondary niuu:text-sm niuu:p-6">
        <StateDot state="processing" pulse />
        <span>loading graph…</span>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-text-secondary niuu:text-sm niuu:p-6">
        <StateDot state="failed" />
        <span>{error instanceof Error ? error.message : 'graph load failed'}</span>
      </div>
    );
  }

  if (!displayGraph) return null;

  return (
    <div className="niuu-graph-wrap">
      <GraphLegend categories={categories} />
      <GraphInfo
        nodeCount={displayGraph.nodes.length}
        edgeCount={displayGraph.edges.length}
        mountLabel={mountLabel}
      />
      <GraphSvg
        graph={displayGraph}
        focusId={focusId}
        onNodeClick={(id) => setFocusId(focusId === id ? null : id)}
        categories={categories}
      />
    </div>
  );
}
