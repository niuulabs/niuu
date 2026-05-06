/**
 * GraphPage — full-bleed knowledge graph canvas with floating overlays.
 *
 * Matches web2 layout: graph fills the content area, category legend floats
 * top-left, graph info card floats top-right. No visible controls — click
 * a node to focus, click again to deselect.
 */

import { StateDot } from '@niuulabs/ui';
import { useGraph } from '../application/useGraph';
import type { MimirGraph, GraphNode } from '../domain/api-types';
import './GraphPage.css';

const SVG_W = 1100;
const SVG_H = 750;
const SVG_CX = SVG_W / 2;
const SVG_CY = SVG_H / 2;
const SVG_R = 300;

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

export function layoutCategoryRadial(
  nodes: GraphNode[],
  cx = SVG_CX,
  cy = SVG_CY,
  r = SVG_R,
): NodePosition[] {
  if (nodes.length === 0) return [];
  if (nodes.length === 1) return [{ node: nodes[0]!, x: cx, y: cy }];

  const categories = [...new Set(nodes.map((n) => n.category))];
  const byCategory = new Map<string, GraphNode[]>();
  for (const node of nodes) {
    const list = byCategory.get(node.category) ?? [];
    list.push(node);
    byCategory.set(node.category, list);
  }

  const result: NodePosition[] = [];
  let sectorStart = -Math.PI / 2;

  for (const cat of categories) {
    const catNodes = byCategory.get(cat) ?? [];
    const sectorSize = (catNodes.length / nodes.length) * 2 * Math.PI;
    const sectorCenter = sectorStart + sectorSize / 2;

    for (let i = 0; i < catNodes.length; i++) {
      const node = catNodes[i]!;
      const spread = catNodes.length <= 1 ? 0 : 0.8;
      const innerAngle = catNodes.length <= 1 ? 0 : (i / catNodes.length) * spread - spread / 2;
      const jitter = 0.5 + 0.5 * (Math.abs(Math.sin(i * 2.1)) * 0.9 + 0.1);
      const radius = r * jitter;
      result.push({
        node,
        x: cx + radius * Math.cos(sectorCenter + innerAngle),
        y: cy + radius * Math.sin(sectorCenter + innerAngle),
      });
    }

    sectorStart += sectorSize;
  }

  return result;
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
  const positions = layoutCategoryRadial(graph.nodes);
  const posMap = new Map(positions.map((p) => [p.node.id, p]));

  return (
    <svg
      className="niuu-graph-canvas"
      viewBox={`0 0 ${SVG_W} ${SVG_H}`}
      role="img"
      aria-label="Knowledge graph"
      preserveAspectRatio="xMidYMid meet"
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
              stroke="var(--color-border)"
              strokeWidth={1}
              strokeDasharray={isWikilink ? '4 3' : undefined}
              className={isFocusEdge ? 'niuu-opacity-50' : 'niuu-opacity-15'}
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
          return (
            <g
              key={node.id}
              transform={`translate(${x},${y})`}
              onClick={() => onNodeClick(node.id)}
              className="niuu-graph-node niuu-cursor-pointer"
              role="button"
              aria-pressed={isFocus}
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') onNodeClick(node.id);
              }}
            >
              <circle
                r={isFocus ? 8 : 5}
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
      <span className="niuu-text-[10px] niuu-uppercase niuu-tracking-widest niuu-text-text-muted niuu-font-semibold niuu-mb-1">
        Category
      </span>
      {categories.map((cat, i) => (
        <div key={cat} className="niuu-flex niuu-items-center niuu-gap-2">
          <span
            className="niuu-graph-legend-dot niuu-w-2 niuu-h-2 niuu-rounded-full niuu-shrink-0"
            data-color-idx={String(i % CATEGORY_COLORS.length)}
            aria-hidden
          />
          <span className="niuu-text-xs niuu-text-text-secondary niuu-font-mono">{cat}</span>
        </div>
      ))}
      <span className="niuu-text-[10px] niuu-uppercase niuu-tracking-widest niuu-text-text-muted niuu-font-semibold niuu-mt-2 niuu-mb-1">
        Edges
      </span>
      <div className="niuu-flex niuu-items-center niuu-gap-2">
        <svg width="20" height="2" className="niuu-shrink-0">
          <line x1="0" y1="1" x2="20" y2="1" stroke="var(--color-border)" strokeWidth="1.5" />
        </svg>
        <span className="niuu-text-xs niuu-text-text-secondary niuu-font-mono">shared source</span>
      </div>
      <div className="niuu-flex niuu-items-center niuu-gap-2">
        <svg width="20" height="2" className="niuu-shrink-0">
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
        <span className="niuu-text-xs niuu-text-text-secondary niuu-font-mono">wikilink</span>
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
}

function GraphInfo({ nodeCount, edgeCount }: InfoCardProps) {
  return (
    <div className="niuu-graph-overlay niuu-graph-overlay--info" data-testid="graph-info">
      <span className="niuu-text-[10px] niuu-uppercase niuu-tracking-widest niuu-text-text-muted niuu-font-semibold">
        Graph
      </span>
      <span className="niuu-text-sm niuu-font-semibold niuu-text-text-primary niuu-font-mono">
        {nodeCount} pages · {edgeCount} edges
      </span>
      <span className="niuu-text-xs niuu-text-brand-300 niuu-font-mono">all mounts</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// GraphPage
// ---------------------------------------------------------------------------

export function GraphPage() {
  const { graph, focusId, setFocusId, isLoading, isError, error } = useGraph();

  const displayGraph = graph;
  const categories = displayGraph
    ? [...new Set(displayGraph.nodes.map((n) => n.category))].filter(Boolean).sort()
    : [];

  if (isLoading) {
    return (
      <div className="niuu-flex niuu-items-center niuu-gap-2 niuu-text-text-secondary niuu-text-sm niuu-p-6">
        <StateDot state="processing" pulse />
        <span>loading graph…</span>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="niuu-flex niuu-items-center niuu-gap-2 niuu-text-text-secondary niuu-text-sm niuu-p-6">
        <StateDot state="failed" />
        <span>{error instanceof Error ? error.message : 'graph load failed'}</span>
      </div>
    );
  }

  if (!displayGraph) return null;

  return (
    <div className="niuu-graph-wrap">
      <GraphLegend categories={categories} />
      <GraphInfo nodeCount={displayGraph.nodes.length} edgeCount={displayGraph.edges.length} />
      <GraphSvg
        graph={displayGraph}
        focusId={focusId}
        onNodeClick={(id) => setFocusId(focusId === id ? null : id)}
        categories={categories}
      />
    </div>
  );
}
