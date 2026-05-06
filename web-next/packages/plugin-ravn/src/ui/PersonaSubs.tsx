import { useMemo, useState, useCallback, useRef } from 'react';
import { usePersonas } from './usePersonas';
import { usePersona } from './usePersona';
import './PersonaSubs.css';

// ── Layout constants ───────────────────────────────────────────────────────

const COL_W = 160;
const NODE_H = 32;
const NODE_GAP = 12;
const COL_GAP = 120;
const PAD_H = 40;
const PAD_V = 40;

// ── Types ──────────────────────────────────────────────────────────────────

interface GraphNode {
  id: string;
  label: string;
  personaName: string; // actual persona name (for navigation)
  col: number; // 0 = producers, 1 = focus, 2 = consumers
  row: number;
}

interface GraphEdge {
  from: string;
  to: string;
  label: string;
  fieldCount: number;
}

// ── Helpers ────────────────────────────────────────────────────────────────

function nodeX(col: number): number {
  return PAD_H + col * (COL_W + COL_GAP);
}

function nodeY(row: number): number {
  return PAD_V + row * (NODE_H + NODE_GAP);
}

function nodeCx(col: number): number {
  return nodeX(col) + COL_W / 2;
}

function nodeCy(row: number): number {
  return nodeY(row) + NODE_H / 2;
}

// ── Component ─────────────────────────────────────────────────────────────

export interface PersonaSubsProps {
  name: string;
}

/**
 * PersonaSubs — subscription graph for a persona.
 *
 * Layout: producers (col 0) → this persona (col 1) → consumers (col 2).
 * Each edge is labelled with the event name that connects the nodes.
 * Hovering a node dims unconnected nodes and edges.
 * Clicking a non-focus node dispatches `ravn:persona-selected`.
 */
export function PersonaSubs({ name }: PersonaSubsProps) {
  const { data: allPersonas } = usePersonas();
  const { data: persona, isLoading, isError, error } = usePersona(name);
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const graph = useMemo<{ nodes: GraphNode[]; edges: GraphEdge[] } | null>(() => {
    if (!persona || !allPersonas) return null;

    const consumedEventNames = persona.consumes.events.map((e) => e.name);
    const producedEvent = persona.produces.eventType;

    // Producers: personas whose producesEvent is consumed by this persona
    const producers = allPersonas.filter(
      (p) => p.name !== name && consumedEventNames.includes(p.producesEvent),
    );

    // Consumers: personas that consume this persona's event
    const consumers = allPersonas.filter(
      (p) => p.name !== name && producedEvent && p.consumesEvents.includes(producedEvent),
    );

    const nodes: GraphNode[] = [];
    const edges: GraphEdge[] = [];

    // Col 0: producers
    producers.forEach((p, i) => {
      nodes.push({ id: p.name, label: p.name, personaName: p.name, col: 0, row: i });
      // PersonaSummary has no schemaDef — field count unavailable for producers
      edges.push({ from: p.name, to: name, label: p.producesEvent, fieldCount: 0 });
    });

    // Col 1: focus persona
    const focusRow = Math.max(
      0,
      Math.floor((Math.max(producers.length, consumers.length) - 1) / 2),
    );
    nodes.push({ id: name, label: name, personaName: name, col: 1, row: focusRow });

    // Col 2: consumers
    const producedFieldCount = persona.produces.schemaDef
      ? Object.keys(persona.produces.schemaDef).length
      : 0;
    consumers.forEach((p, i) => {
      nodes.push({ id: `consumer-${p.name}`, label: p.name, personaName: p.name, col: 2, row: i });
      edges.push({
        from: name,
        to: `consumer-${p.name}`,
        label: producedEvent,
        fieldCount: producedFieldCount,
      });
    });

    return { nodes, edges };
  }, [persona, allPersonas, name]);

  const handleNodeClick = useCallback(
    (node: GraphNode) => {
      if (node.personaName === name) return;
      window.dispatchEvent(new CustomEvent('ravn:persona-selected', { detail: node.personaName }));
    },
    [name],
  );

  const handleZoomToFit = useCallback(() => {
    const el = containerRef.current;
    if (el && typeof el.scrollTo === 'function') {
      el.scrollTo({ top: 0, left: 0, behavior: 'smooth' });
    }
  }, []);

  if (isLoading) {
    return (
      <div
        data-testid="persona-subs-loading"
        className="niuu-p-6 niuu-text-sm niuu-text-text-muted"
      >
        Loading subscription graph…
      </div>
    );
  }

  if (isError) {
    return (
      <div data-testid="persona-subs-error" className="niuu-p-6 niuu-text-sm niuu-text-critical">
        {error instanceof Error ? error.message : 'Failed to load persona'}
      </div>
    );
  }

  if (!graph) return null;

  const { nodes, edges } = graph;

  if (nodes.length === 1) {
    // Only the focus persona — no connections
    return (
      <div
        data-testid="persona-subs-empty"
        className="niuu-flex niuu-items-center niuu-justify-center niuu-h-full niuu-p-6 niuu-text-sm niuu-text-text-muted"
      >
        No event subscriptions — this persona neither consumes nor produces events connected to
        others.
      </div>
    );
  }

  const maxRow = Math.max(...nodes.map((n) => n.row));
  const svgW = nodeX(2) + COL_W + PAD_H;
  const svgH = nodeY(maxRow) + NODE_H + PAD_V;

  // Determine which edges/nodes are connected to the hovered node.
  // Track by edge key so edge opacity and node opacity are derived consistently.
  const connectedEdgeKeys = hoveredNodeId
    ? new Set<string>(
        edges
          .filter((e) => e.from === hoveredNodeId || e.to === hoveredNodeId)
          .map((e) => `${e.from}-${e.to}`),
      )
    : null;
  const connectedNodeIds = connectedEdgeKeys
    ? new Set<string>(
        edges
          .filter((e) => connectedEdgeKeys.has(`${e.from}-${e.to}`))
          .flatMap((e) => [e.from, e.to]),
      )
    : null;

  return (
    <div
      ref={containerRef}
      data-testid="persona-subs"
      className="niuu-overflow-auto niuu-h-full niuu-p-4 niuu-flex niuu-flex-col niuu-gap-4"
    >
      {/* Zoom to fit button */}
      <div className="niuu-flex niuu-justify-end">
        <button
          type="button"
          onClick={handleZoomToFit}
          data-testid="subs-zoom-fit"
          className="niuu-text-xs niuu-text-text-muted niuu-bg-bg-secondary niuu-border niuu-border-border niuu-rounded niuu-px-2 niuu-py-1 niuu-cursor-pointer hover:niuu-text-text-primary hover:niuu-border-brand niuu-transition-colors"
        >
          Zoom to fit
        </button>
      </div>

      <svg
        width={svgW}
        height={svgH}
        role="img"
        aria-label={`Event subscription graph for ${name}`}
        className="niuu-block niuu-shrink-0"
      >
        <title>Event subscription graph for {name}</title>

        {/* Edges */}
        {edges.map((edge) => {
          const fromNode = nodes.find((n) => n.id === edge.from);
          const toNode = nodes.find((n) => n.id === edge.to);
          if (!fromNode || !toNode) return null;

          const isConnectedToHovered =
            !connectedEdgeKeys || connectedEdgeKeys.has(`${edge.from}-${edge.to}`);

          const x1 = nodeX(fromNode.col) + COL_W;
          const y1 = nodeCy(fromNode.row);
          const x2 = nodeX(toNode.col);
          const y2 = nodeCy(toNode.row);
          const mx = (x1 + x2) / 2;
          const midY = (y1 + y2) / 2;

          return (
            <g key={`${edge.from}-${edge.to}`} opacity={isConnectedToHovered ? 1 : 0.2}>
              <path
                d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`}
                fill="none"
                stroke="var(--color-border)"
                strokeWidth={1.5}
              />
              <text
                x={mx}
                y={midY - 4}
                textAnchor="middle"
                fontSize={9}
                fontFamily="var(--font-mono)"
                fill="var(--color-text-muted)"
              >
                {edge.label}
              </text>
              {edge.fieldCount > 0 && (
                <text
                  x={mx}
                  y={midY + 8}
                  textAnchor="middle"
                  fontSize={8}
                  fontFamily="var(--font-mono)"
                  fill="var(--color-text-muted)"
                  opacity={0.7}
                  data-testid="edge-field-count"
                >
                  {edge.fieldCount}f
                </text>
              )}
            </g>
          );
        })}

        {/* Nodes */}
        {nodes.map((node) => {
          const x = nodeX(node.col);
          const y = nodeY(node.row);
          const isFocus = node.id === name;
          const isDimmed = connectedNodeIds !== null && !connectedNodeIds.has(node.id);
          const isClickable = !isFocus;

          return (
            <g
              key={node.id}
              opacity={isDimmed ? 0.25 : 1}
              className={isClickable ? 'niuu-cursor-pointer' : undefined}
              onMouseEnter={() => setHoveredNodeId(node.id)}
              onMouseLeave={() => setHoveredNodeId(null)}
              onClick={() => handleNodeClick(node)}
              data-testid={`subs-node-${node.personaName}`}
            >
              <rect
                x={x}
                y={y}
                width={COL_W}
                height={NODE_H}
                rx={4}
                fill={
                  isFocus
                    ? 'color-mix(in srgb, var(--brand-500) 18%, transparent)'
                    : 'var(--color-bg-secondary)'
                }
                stroke={isFocus ? 'var(--brand-400)' : 'var(--color-border)'}
                strokeWidth={isFocus ? 1.5 : 1}
              />
              <text
                x={nodeCx(node.col)}
                y={nodeCy(node.row) + 1}
                textAnchor="middle"
                dominantBaseline="middle"
                fontSize={11}
                fontFamily="var(--font-sans)"
                fill={isFocus ? 'var(--brand-300)' : 'var(--color-text-secondary)'}
                fontWeight={isFocus ? 600 : 400}
              >
                {node.label.length > 16 ? `${node.label.slice(0, 14)}…` : node.label}
              </text>
            </g>
          );
        })}

        {/* Column headers */}
        {[
          { col: 0, label: 'Producers' },
          { col: 1, label: 'This persona' },
          { col: 2, label: 'Consumers' },
        ].map(({ col, label }) => (
          <text
            key={col}
            x={nodeCx(col)}
            y={14}
            textAnchor="middle"
            fontSize={10}
            fontFamily="var(--font-mono)"
            fill="var(--color-text-muted)"
            letterSpacing="0.06em"
          >
            {label.toUpperCase()}
          </text>
        ))}
      </svg>

      {/* Legend */}
      <div
        data-testid="subs-legend"
        className="niuu-flex niuu-items-center niuu-gap-4 niuu-text-xs niuu-text-text-muted niuu-font-mono niuu-border-t niuu-border-border-subtle niuu-pt-3 niuu-flex-wrap"
      >
        <span className="niuu-font-sans niuu-text-text-muted">Legend:</span>
        <span className="niuu-flex niuu-items-center niuu-gap-1">
          <span className="niuu-inline-block niuu-w-8 niuu-h-3 niuu-rounded-sm niuu-border rv-subs-legend-focus" />
          focus (this persona)
        </span>
        <span className="niuu-flex niuu-items-center niuu-gap-1">
          <span className="niuu-inline-block niuu-w-8 niuu-h-3 niuu-rounded-sm niuu-border niuu-border-border niuu-bg-bg-secondary" />
          producer / consumer
        </span>
        <span className="niuu-flex niuu-items-center niuu-gap-1">
          <svg width={20} height={8} className="niuu-shrink-0">
            <line x1="0" y1="4" x2="20" y2="4" stroke="var(--color-border)" strokeWidth={1.5} />
          </svg>
          event link
        </span>
        <span>
          edge label = event name · <span className="niuu-font-mono">Nf</span> = schema field count
        </span>
      </div>
    </div>
  );
}
