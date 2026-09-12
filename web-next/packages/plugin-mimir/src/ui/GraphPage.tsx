/** Spatial exploration of backend-neutral knowledge, with accessible page navigation. */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from '@tanstack/react-router';
import { usePluginCtx } from '@niuulabs/plugin-sdk';
import { StateDot } from '@niuulabs/ui';
import { useActiveMount } from '../application/useActiveMount';
import { useGraph } from '../application/useGraph';
import { nHopSubgraph } from '../application/nHopSubgraph';
import { filterGraph } from '../domain/filterGraph';
import type { MimirGraph, GraphNode } from '../domain/api-types';
import {
  BASE_VIEWBOX,
  fitViewBox,
  layoutForceDirected,
  nodeRadius,
  buildDegreeMap,
  zoomViewBox,
  panViewBox,
  projectPosition,
  clearForeground,
} from '../domain/graphGeometry';
import './GraphPage.css';
import { categoryColor } from './graphColors';

export {
  layoutForceDirected,
  nodeRadius,
  buildDegreeMap,
  zoomViewBox,
  panViewBox,
} from '../domain/graphGeometry';

const ORBIT_PER_PIXEL = 0.006;
const IDLE_RADIANS_PER_MS = 0.000045;
const IDLE_DELAY_MS = 2500;
const FRAME_INTERVAL_MS = 33;
const DRAG_THRESHOLD = 4;
const LABEL_DEGREE = 6;

function GraphCanvas({
  graph,
  visible,
  focusId,
  select,
  motion,
  fit,
}: {
  graph: MimirGraph;
  visible: MimirGraph;
  focusId: string | null;
  select: (id: string) => void;
  motion: boolean;
  fit: boolean;
}) {
  // Filtering preserves positions, so narrowing a cluster doesn't scramble the map.
  const positions = useMemo(() => layoutForceDirected(graph.nodes, graph.edges), [graph]);
  const degrees = useMemo(() => buildDegreeMap(graph), [graph]);
  const visibleIds = useMemo(() => new Set(visible.nodes.map((node) => node.id)), [visible]);
  const [camera, setCamera] = useState({ yaw: 0, pitch: 0 });
  const [viewBox, setViewBox] = useState(() =>
    fit
      ? fitViewBox(
          positions.filter((p) => visibleIds.has(p.node.id)).map((p) => projectPosition(p, 0, 0)),
        )
      : BASE_VIEWBOX,
  );
  const [previousFocus, setPreviousFocus] = useState(focusId);
  const [hover, setHover] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const drag = useRef<{ id: number; x: number; y: number; distance: number; pan: boolean } | null>(
    null,
  );
  const suppressClick = useRef(false);
  const lastInteraction = useRef(0);
  const frameTime = useRef(0);
  const svgRef = useRef<SVGSVGElement>(null);
  function handleInteraction() {
    lastInteraction.current = frameTime.current;
  }

  useEffect(() => {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    let frame = 0;
    let last = 0;
    function tick(now: number) {
      frameTime.current = now;
      if (now - last >= FRAME_INTERVAL_MS) {
        if (
          motion &&
          !media.matches &&
          !document.hidden &&
          !drag.current &&
          !hover &&
          !focusId &&
          now - lastInteraction.current > IDLE_DELAY_MS
        ) {
          const elapsed = Math.min(now - last, FRAME_INTERVAL_MS * 2);
          setCamera((value) => ({ ...value, yaw: value.yaw + elapsed * IDLE_RADIANS_PER_MS }));
        }
        last = now;
      }
      frame = requestAnimationFrame(tick);
    }
    if (motion) frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [motion, hover, focusId]);

  const projected = positions
    .filter((p) => visibleIds.has(p.node.id))
    .map((p) => {
      const projected = projectPosition(p, camera.yaw, camera.pitch, viewBox);
      return p.node.id === focusId ? projected : clearForeground(projected, viewBox);
    })
    .sort((a, b) => a.depth - b.depth);
  const byId = new Map(projected.map((p) => [p.node.id, p]));
  if (focusId !== previousFocus) {
    setPreviousFocus(focusId);
    const point = focusId ? byId.get(focusId) : undefined;
    if (point) setViewBox({ ...viewBox, x: point.x - viewBox.w / 2, y: point.y - viewBox.h / 2 });
  }
  const neighbors = new Set([focusId]);
  for (const edge of visible.edges) {
    if (edge.source === focusId) neighbors.add(edge.target);
    if (edge.target === focusId) neighbors.add(edge.source);
  }

  function endDrag(event: React.PointerEvent<SVGSVGElement>) {
    if (drag.current?.id !== event.pointerId) return;
    suppressClick.current = drag.current.distance > DRAG_THRESHOLD;
    drag.current = null;
    setDragging(false);
    handleInteraction();
  }

  return (
    <svg
      ref={svgRef}
      className="niuu-graph-canvas"
      data-dragging={dragging}
      viewBox={`${viewBox.x} ${viewBox.y} ${viewBox.w} ${viewBox.h}`}
      role="group"
      aria-label="Knowledge graph"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key.startsWith('Arrow')) {
          event.preventDefault();
          handleInteraction();
          setCamera((value) => ({
            yaw:
              value.yaw + (event.key === 'ArrowLeft' ? -0.1 : event.key === 'ArrowRight' ? 0.1 : 0),
            pitch:
              value.pitch + (event.key === 'ArrowUp' ? -0.1 : event.key === 'ArrowDown' ? 0.1 : 0),
          }));
        }
        if (event.key === '+' || event.key === '-') {
          event.preventDefault();
          handleInteraction();
          setViewBox((value) =>
            zoomViewBox(
              value,
              event.key === '+' ? 1 / 1.1 : 1.1,
              value.x + value.w / 2,
              value.y + value.h / 2,
            ),
          );
        }
      }}
      onWheel={(event) => {
        handleInteraction();
        const matrix = svgRef.current?.getScreenCTM();
        const point = matrix
          ? new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse())
          : { x: viewBox.x + viewBox.w / 2, y: viewBox.y + viewBox.h / 2 };
        setViewBox((value) =>
          zoomViewBox(value, event.deltaY > 0 ? 1.1 : 1 / 1.1, point.x, point.y),
        );
      }}
      onPointerDown={(event) => {
        handleInteraction();
        suppressClick.current = false;
        drag.current = {
          id: event.pointerId,
          x: event.clientX,
          y: event.clientY,
          distance: 0,
          pan: event.shiftKey,
        };
        setDragging(true);
      }}
      onPointerMove={(event) => {
        const current = drag.current;
        if (!current || current.id !== event.pointerId) return;
        const dx = event.clientX - current.x,
          dy = event.clientY - current.y;
        current.x = event.clientX;
        current.y = event.clientY;
        current.distance += Math.hypot(dx, dy);
        if (current.distance > DRAG_THRESHOLD)
          event.currentTarget.setPointerCapture?.(event.pointerId);
        if (current.pan) {
          const width = event.currentTarget.getBoundingClientRect().width;
          setViewBox((value) => panViewBox(value, dx, dy, width));
        } else
          setCamera((value) => ({
            yaw: value.yaw + dx * ORBIT_PER_PIXEL,
            pitch: value.pitch + dy * ORBIT_PER_PIXEL,
          }));
      }}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onLostPointerCapture={endDrag}
    >
      <defs>
        <filter id="niuu-node-glow">
          <feGaussianBlur stdDeviation="3" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>
      <g aria-hidden="true">
        {visible.edges.map((edge) => {
          const a = byId.get(edge.source),
            b = byId.get(edge.target);
          if (!a || !b) return null;
          const focused = edge.source === focusId || edge.target === focusId;
          return (
            <line
              key={`${edge.source}:${edge.target}:${edge.type}`}
              x1={a.x}
              y1={a.y}
              x2={b.x}
              y2={b.y}
              stroke={focused ? 'var(--brand-300)' : 'var(--color-text-muted)'}
              strokeWidth={focused ? 1.5 : 0.6}
              opacity={focusId && !focused ? 0.08 : 0.35}
              className={focused ? 'niuu:opacity-90' : 'niuu:opacity-40'}
              strokeDasharray={edge.type === 'shared_source' || !edge.type ? '3 4' : undefined}
            />
          );
        })}
      </g>
      {projected.map(({ node, x, y, scale }) => {
        const focused = node.id === focusId;
        const label = focused || hover === node.id || (degrees.get(node.id) ?? 0) >= LABEL_DEGREE;
        return (
          <g
            key={node.id}
            transform={`translate(${x},${y})`}
            role="button"
            tabIndex={0}
            aria-label={node.title}
            aria-pressed={focused}
            className="niuu-graph-node"
            opacity={focusId && !neighbors.has(node.id) ? 0.15 : Math.min(1, scale * 0.85)}
            onPointerEnter={() => setHover(node.id)}
            onPointerLeave={() => setHover(null)}
            onFocus={() => {
              handleInteraction();
              setHover(node.id);
            }}
            onBlur={() => setHover(null)}
            onClick={() => {
              if (!suppressClick.current) {
                handleInteraction();
                select(node.id);
              }
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                handleInteraction();
                select(node.id);
              }
            }}
          >
            <title>
              {node.title} · {node.category} · {node.kind ?? 'page'}
            </title>
            <circle
              r={(nodeRadius(degrees.get(node.id) ?? 0) + (focused ? 3 : 0)) * scale}
              className="niuu-graph-node-circle"
              fill={categoryColor(node.category)}
              stroke={focused ? 'var(--color-text-primary)' : 'none'}
            />
            {label && (
              <text y={-14 * scale} textAnchor="middle" className="niuu-graph-label">
                {node.title}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

export function GraphPage() {
  const { mountName } = useActiveMount();
  return <GraphExplorer key={mountName ?? 'all'} />;
}

function GraphExplorer() {
  const ctx = usePluginCtx();
  const { activeMount, mountName } = useActiveMount();
  const { graph, focusId, setFocusId, isLoading, isError, error } = useGraph(mountName);
  const [query, setQuery] = useState('');
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [kind, setKind] = useState('');
  const [neighborhood, setNeighborhood] = useState(false);
  const [motion, setMotion] = useState(true);
  const [reset, setReset] = useState(0);
  const categories = useMemo(
    () => [...new Set(graph?.nodes.map((node) => node.category))].sort(),
    [graph],
  );
  const counts = useMemo(() => {
    const result = new Map<string, number>();
    for (const node of graph?.nodes ?? [])
      result.set(node.category, (result.get(node.category) ?? 0) + 1);
    return result;
  }, [graph]);
  const kinds = [...new Set(graph?.nodes.map((node) => node.kind ?? 'page'))].sort();
  const filtered = useMemo(
    () => (graph ? filterGraph(graph, query, hidden, kind) : undefined),
    [graph, query, hidden, kind],
  );
  const visible = useMemo(
    () => (filtered && neighborhood && focusId ? nHopSubgraph(filtered, focusId, 1) : filtered),
    [filtered, neighborhood, focusId],
  );
  const selected = graph?.nodes.find((node) => node.id === focusId);
  function select(id: string) {
    setFocusId(id === focusId ? null : id);
  }
  function clear() {
    setQuery('');
    setHidden(new Set());
    setKind('');
    setFocusId(null);
    setNeighborhood(false);
  }
  function openPage(node: GraphNode) {
    ctx.setTweak('mimir.selectedPagePath', node.path || node.id);
    if (node.mount) ctx.setTweak('activeMount', node.mount);
  }
  if (isLoading)
    return (
      <div className="niuu:p-6">
        <StateDot state="processing" pulse /> loading graph…
      </div>
    );
  if (isError)
    return (
      <div role="alert" className="niuu:p-6">
        <StateDot state="failed" /> {error instanceof Error ? error.message : 'graph load failed'}
      </div>
    );
  if (!graph || !visible) return null;
  return (
    <div className="niuu-graph-wrap">
      <GraphCanvas
        key={reset}
        graph={graph}
        visible={visible}
        focusId={focusId}
        select={select}
        motion={motion}
        fit={reset > 0}
      />
      <div className="niuu-graph-overlay niuu-graph-overlay--legend" aria-label="Graph legend">
        <strong>Category</strong>
        {categories.map((category) => (
          <button
            key={category}
            type="button"
            aria-pressed={!hidden.has(category)}
            onClick={() =>
              setHidden((previous) => {
                const next = new Set(previous);
                if (next.has(category)) next.delete(category);
                else next.add(category);
                return next;
              })
            }
          >
            <svg className="niuu-graph-legend-dot" viewBox="0 0 10 10" aria-hidden="true">
              <circle cx="5" cy="5" r="5" fill={categoryColor(category)} />
            </svg>
            <span>{category || 'uncategorized'}</span>
            <span>{counts.get(category)}</span>
          </button>
        ))}
        <label>
          Kind
          <select
            aria-label="Knowledge kind"
            value={kind}
            onChange={(event) => setKind(event.target.value)}
          >
            <option value="">All kinds</option>
            {kinds.map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </label>
        <strong>Edges</strong>
        {[...new Set(graph.edges.map((edge) => edge.type ?? 'shared_source'))]
          .sort()
          .map((type) => (
            <span key={type}>{type.replaceAll('_', ' ')}</span>
          ))}
      </div>
      <div className="niuu-graph-overlay niuu-graph-overlay--info" data-testid="graph-info">
        <strong>
          {visible.nodes.length} / {graph.nodes.length} pages · {visible.edges.length} edges
        </strong>
        <span>{activeMount === 'all' ? 'all mounts' : activeMount}</span>
        <input
          type="search"
          aria-label="Search graph"
          placeholder="Search titles, summaries, categories…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <div className="niuu-graph-actions">
          <button type="button" onClick={() => setReset((value) => value + 1)}>
            Fit
          </button>
          <button type="button" aria-pressed={motion} onClick={() => setMotion(!motion)}>
            Motion {motion ? 'on' : 'off'}
          </button>
          <button type="button" onClick={clear}>
            Reset filters
          </button>
        </div>
        {(query || kind) && (
          <div className="niuu-graph-results" aria-label="Graph search results">
            {visible.nodes.map((node) => (
              <button type="button" key={node.id} onClick={() => setFocusId(node.id)}>
                {node.title}
                <small>
                  {node.mount} · {node.category}
                </small>
              </button>
            ))}
          </div>
        )}
      </div>
      {!visible.nodes.length && (
        <div className="niuu-graph-empty" role="status">
          {graph.nodes.length
            ? 'No pages match these filters.'
            : 'No knowledge pages in this mount yet.'}
        </div>
      )}
      {selected && (
        <div className="niuu-graph-overlay niuu-graph-overlay--selected">
          <strong>{selected.title}</strong>
          <span>
            {selected.kind ?? 'page'} · {selected.category} · {selected.mount}
          </span>
          <p>{selected.summary}</p>
          <div className="niuu-graph-actions">
            <button
              type="button"
              aria-pressed={neighborhood}
              onClick={() => setNeighborhood(!neighborhood)}
            >
              Only neighbors
            </button>
            <Link to="/mimir/pages" onClick={() => openPage(selected)}>
              Open page
            </Link>
            <button
              type="button"
              onClick={() => {
                setFocusId(null);
                setNeighborhood(false);
              }}
            >
              Clear selection
            </button>
          </div>
        </div>
      )}
      <p className="niuu-graph-help">
        Drag to orbit · Shift-drag to pan · Scroll to zoom · Arrow keys to rotate · + / − to zoom
      </p>
    </div>
  );
}
