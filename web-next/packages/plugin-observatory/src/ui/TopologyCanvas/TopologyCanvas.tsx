import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { select } from 'd3-selection';
import { zoom, zoomIdentity, type D3ZoomEvent, type ZoomBehavior } from 'd3-zoom';
import type { EdgeLayer, Topology } from '../../domain';
import { visibleEdges } from '../../domain';
import { deriveAgentMeshes, findMeshForNode } from '../../domain/agentMesh';
import {
  clampZoom,
  applyKeyPan,
  defaultCamera,
  fitCameraToBounds,
  screenToWorld,
  type Camera,
} from './canvasMath';
import { computeLayout, computeLayoutBounds, HOST_HALF_W, HOST_HALF_H } from './layoutEngine';
import {
  drawAgentMesh,
  drawStars,
  drawZones,
  drawEdges,
  drawNode,
  drawMimir,
  drawMinimap,
  getStructureLabelBounds,
} from './renderer';
import { CANVAS, HIT_RADIUS } from './config';
import './TopologyCanvas.css';

// ── Types ─────────────────────────────────────────────────────────────────────

export interface TopologyCanvasProps {
  topology: Topology | null;
  /** Called when the user clicks a node. */
  onNodeClick?: (nodeId: string) => void;
  /** Currently selected node — drives which agent mesh is outlined. */
  selectedId?: string | null;
  /** Connection layers the operator has switched off. */
  hiddenLayers?: ReadonlySet<EdgeLayer>;
  /** Show the minimap panel (default true). */
  showMinimap?: boolean;
  /** Extra CSS class applied to the wrapper div. */
  className?: string;
  /** Inline style applied to the wrapper div. */
  style?: React.CSSProperties;
}

// ── Component ─────────────────────────────────────────────────────────────────

/**
 * TopologyCanvas — SVG-over-Canvas renderer for the live topology graph.
 *
 * Data comes in via the `topology` prop (driven by useTopology in the parent).
 * Pan via drag, scroll-wheel zoom clamped 0.3×–3×, arrow-key pan.
 * Minimap in the bottom-right corner.
 */
export function TopologyCanvas({
  topology,
  onNodeClick,
  selectedId = null,
  hiddenLayers,
  showMinimap = true,
  className,
  style,
}: TopologyCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const minimapRef = useRef<HTMLCanvasElement>(null);
  const sizeRef = useRef({ w: 0, h: 0 });
  const camRef = useRef<Camera>(defaultCamera());
  const zoomBehaviorRef = useRef<ZoomBehavior<HTMLCanvasElement, unknown> | null>(null);
  const userAdjustedCameraRef = useRef(false);
  const lastTopologySignatureRef = useRef('');
  const lastFitKeyRef = useRef('');
  const suppressZoomAdjustmentRef = useRef(false);
  const isDraggingRef = useRef(false);
  const hoveredIdRef = useRef<string | null>(null);
  const [zoomPct, setZoomPct] = useState(Math.round(CANVAS.INITIAL_ZOOM * 100));
  const [viewportSize, setViewportSize] = useState({ w: 0, h: 0 });

  // Compute layout whenever topology changes (memoised — pure function)
  const positions = useMemo(() => (topology ? computeLayout(topology) : new Map()), [topology]);
  const agentMeshes = useMemo(() => deriveAgentMeshes(topology), [topology]);

  // Layers filter what is drawn, never the layout: hiding a connection must not
  // make the nodes jump.
  const drawnTopology = useMemo<Topology | null>(() => {
    if (!topology) return null;
    if (!hiddenLayers || hiddenLayers.size === 0) return topology;
    return { ...topology, edges: visibleEdges(topology.edges, hiddenLayers) };
  }, [hiddenLayers, topology]);
  const topologySignature = useMemo(() => {
    if (!topology) return 'empty';
    return topology.nodes
      .map((node) => `${node.id}:${node.parentId ?? ''}:${node.typeId}`)
      .sort()
      .join('|');
  }, [topology]);

  // Stable reference to drawing data so the rAF loop always reads fresh values
  // without being re-subscribed on every state tick.
  const drawRef = useRef({
    topology: drawnTopology,
    positions,
    hoveredId: null as string | null,
    agentMeshes,
    selectedId,
  });

  useEffect(() => {
    drawRef.current = {
      topology: drawnTopology,
      positions,
      hoveredId: hoveredIdRef.current,
      agentMeshes,
      selectedId,
    };
  }, [agentMeshes, drawnTopology, positions, selectedId]);

  const cameraToZoomTransform = useCallback((cam: Camera) => {
    const { w, h } = sizeRef.current;
    return zoomIdentity
      .translate(w / 2 - cam.x * cam.zoom, h / 2 - cam.y * cam.zoom)
      .scale(cam.zoom);
  }, []);

  const zoomTransformToCamera = useCallback(
    (transform: { x: number; y: number; k: number }): Camera => {
      const { w, h } = sizeRef.current;
      return {
        x: (w / 2 - transform.x) / transform.k,
        y: (h / 2 - transform.y) / transform.k,
        zoom: clampZoom(transform.k),
      };
    },
    [],
  );

  const syncCamera = useCallback(
    (camera: Camera, markAdjusted: boolean) => {
      camRef.current = camera;
      if (markAdjusted) userAdjustedCameraRef.current = true;
      setZoomPct(Math.round(camera.zoom * 100));

      const canvas = canvasRef.current;
      const zoomBehavior = zoomBehaviorRef.current;
      if (!canvas || !zoomBehavior) return;
      suppressZoomAdjustmentRef.current = !markAdjusted;
      select(canvas).call(zoomBehavior.transform, cameraToZoomTransform(camera));
      suppressZoomAdjustmentRef.current = false;
    },
    [cameraToZoomTransform],
  );

  const fitCamera = useCallback(() => {
    if (!topology) {
      syncCamera(defaultCamera(), false);
      return;
    }
    const bounds = computeLayoutBounds(topology, positions);
    syncCamera(fitCameraToBounds(bounds, sizeRef.current.w, sizeRef.current.h, 86), false);
  }, [positions, syncCamera, topology]);

  // ── Canvas sizing ───────────────────────────────────────────────────────────

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const apply = (width: number, height: number) => {
      if (!width || !height) return;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = width * dpr;
      canvas.height = height * dpr;
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      sizeRef.current = { w: width, h: height };
      setViewportSize((prev) =>
        prev.w === width && prev.h === height ? prev : { w: width, h: height },
      );
    };

    apply(canvas.clientWidth, canvas.clientHeight);
    const ro = new ResizeObserver((entries) => {
      const { width, height } = entries[0]!.contentRect;
      apply(width, height);
    });
    ro.observe(canvas);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (!viewportSize.w || !viewportSize.h) return;

    if (lastTopologySignatureRef.current !== topologySignature) {
      userAdjustedCameraRef.current = false;
      lastTopologySignatureRef.current = topologySignature;
    }

    const fitKey = `${topologySignature}:${viewportSize.w}x${viewportSize.h}`;
    if (!userAdjustedCameraRef.current && lastFitKeyRef.current !== fitKey) {
      fitCamera();
      lastFitKeyRef.current = fitKey;
    }
  }, [fitCamera, topologySignature, viewportSize.h, viewportSize.w]);

  // ── D3 zoom / pan ───────────────────────────────────────────────────────────

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const behavior = zoom<HTMLCanvasElement, unknown>()
      .scaleExtent([CANVAS.ZOOM_MIN, CANVAS.ZOOM_MAX])
      .clickDistance(8)
      .filter((event) => !(event.type === 'dblclick'))
      .on('start', (event: D3ZoomEvent<HTMLCanvasElement, unknown>) => {
        if (event.sourceEvent?.type === 'mousedown') {
          isDraggingRef.current = true;
          canvas.style.cursor = 'grabbing';
        }
      })
      .on('zoom', (event: D3ZoomEvent<HTMLCanvasElement, unknown>) => {
        camRef.current = zoomTransformToCamera(event.transform);
        if (!suppressZoomAdjustmentRef.current) {
          userAdjustedCameraRef.current = true;
        }
        setZoomPct(Math.round(camRef.current.zoom * 100));
      })
      .on('end', () => {
        isDraggingRef.current = false;
        canvas.style.cursor = hoveredIdRef.current ? 'pointer' : 'grab';
      });

    zoomBehaviorRef.current = behavior;
    const selection = select(canvas);
    selection.call(behavior);
    selection.on('dblclick.zoom', null);
    suppressZoomAdjustmentRef.current = true;
    selection.call(behavior.transform, cameraToZoomTransform(camRef.current));
    suppressZoomAdjustmentRef.current = false;

    return () => {
      selection.on('.zoom', null);
      zoomBehaviorRef.current = null;
    };
  }, [cameraToZoomTransform, zoomTransformToCamera]);

  // ── Keyboard pan (arrow keys) ───────────────────────────────────────────────

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const onKeyDown = (e: KeyboardEvent) => {
      const panKeys = ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'];
      if (!panKeys.includes(e.key)) return;
      e.preventDefault();
      syncCamera(applyKeyPan(camRef.current, e.key, CANVAS.PAN_KEY_STEP), true);
    };

    canvas.addEventListener('keydown', onKeyDown);
    return () => canvas.removeEventListener('keydown', onKeyDown);
  }, [syncCamera]);

  // ── Hit detection ───────────────────────────────────────────────────────────

  const hitTest = useCallback((sx: number, sy: number): string | null => {
    const { w, h } = sizeRef.current;
    const cam = camRef.current;
    const { x: wx, y: wy } = screenToWorld(sx, sy, cam, w, h);

    const { topology: topo, positions: pos } = drawRef.current;
    if (!topo) return null;

    for (const node of topo.nodes) {
      if (
        node.typeId !== 'realm' &&
        node.typeId !== 'cluster' &&
        node.typeId !== 'namespace' &&
        node.typeId !== 'run'
      ) {
        continue;
      }
      const p = pos.get(node.id);
      if (!p) continue;
      const bounds = getStructureLabelBounds(node, p);
      if (!bounds) continue;
      if (
        wx >= bounds.x &&
        wx <= bounds.x + bounds.width &&
        wy >= bounds.y &&
        wy <= bounds.y + bounds.height
      ) {
        return node.id;
      }
    }

    const orderedNodes = [...topo.nodes].sort((a, b) => {
      const aContainer = a.typeId === 'host' || a.typeId === 'namespace';
      const bContainer = b.typeId === 'host' || b.typeId === 'namespace';
      if (aContainer && !bContainer) return 1;
      if (bContainer && !aContainer) return -1;
      return 0;
    });

    for (const node of orderedNodes) {
      const p = pos.get(node.id);
      if (!p) continue;
      if (node.typeId === 'host' || node.typeId === 'namespace') {
        const fallbackRadius = node.typeId === 'namespace' ? HIT_RADIUS.namespace : HOST_HALF_W;
        const containerRadius = Math.max(
          (p.containerWidth ?? HOST_HALF_W * 2) / 2,
          (p.containerHeight ?? HOST_HALF_H * 2) / 2,
          fallbackRadius ?? HOST_HALF_W,
        );
        if ((wx - p.x) ** 2 + (wy - p.y) ** 2 < containerRadius * containerRadius) return node.id;
      } else {
        const r = HIT_RADIUS[node.typeId] ?? HIT_RADIUS['ting']!;
        if ((wx - p.x) ** 2 + (wy - p.y) ** 2 < r * r) return node.id;
      }
    }
    return null;
  }, []);

  // ── Mouse event handlers ────────────────────────────────────────────────────

  const handleMouseMove = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      if (isDraggingRef.current) return;
      const rect = canvasRef.current!.getBoundingClientRect();
      const sx = e.clientX - rect.left;
      const sy = e.clientY - rect.top;

      const hitId = hitTest(sx, sy);
      hoveredIdRef.current = hitId;
      drawRef.current = { ...drawRef.current, hoveredId: hitId };
      canvasRef.current!.style.cursor = hitId ? 'pointer' : 'grab';
    },
    [hitTest],
  );

  const handleClick = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      const rect = canvasRef.current!.getBoundingClientRect();
      const hitId = hitTest(e.clientX - rect.left, e.clientY - rect.top);
      if (hitId) onNodeClick?.(hitId);
    },
    [hitTest, onNodeClick],
  );

  const handleMouseLeave = useCallback(() => {
    isDraggingRef.current = false;
    hoveredIdRef.current = null;
    drawRef.current = { ...drawRef.current, hoveredId: null };
    if (canvasRef.current) canvasRef.current.style.cursor = 'grab';
  }, []);

  // ── Minimap click-to-pan ────────────────────────────────────────────────────

  const handleMinimapClick = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      const mm = minimapRef.current;
      if (!mm) return;
      const rect = mm.getBoundingClientRect();
      const fx = (e.clientX - rect.left) / rect.width;
      const fy = (e.clientY - rect.top) / rect.height;
      syncCamera(
        {
          ...camRef.current,
          x: fx * CANVAS.WORLD_W - CANVAS.WORLD_W / 2,
          y: fy * CANVAS.WORLD_H - CANVAS.WORLD_H / 2,
        },
        true,
      );
    },
    [syncCamera],
  );

  // ── Camera controls ─────────────────────────────────────────────────────────

  const zoomIn = useCallback(() => {
    const canvas = canvasRef.current;
    const zoomBehavior = zoomBehaviorRef.current;
    if (!canvas || !zoomBehavior) return;
    select(canvas).call(zoomBehavior.scaleBy, CANVAS.ZOOM_STEP);
  }, []);

  const zoomOut = useCallback(() => {
    const canvas = canvasRef.current;
    const zoomBehavior = zoomBehaviorRef.current;
    if (!canvas || !zoomBehavior) return;
    select(canvas).call(zoomBehavior.scaleBy, 1 / CANVAS.ZOOM_STEP);
  }, []);

  const resetCamera = useCallback(() => {
    userAdjustedCameraRef.current = false;
    fitCamera();
  }, [fitCamera]);

  // ── Animation loop ──────────────────────────────────────────────────────────

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let cancelled = false;
    let rafId = 0;

    const render = (now: number) => {
      if (cancelled) return;
      const { topology: topo, positions: pos, hoveredId } = drawRef.current;
      const { w, h } = sizeRef.current;
      if (!w || !h) {
        rafId = requestAnimationFrame(render);
        return;
      }

      const dpr = window.devicePixelRatio || 1;
      const cam = camRef.current;

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);

      // Background gradient
      const bg = ctx.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, Math.max(w, h) * 0.7);
      bg.addColorStop(0, '#0a0c12');
      bg.addColorStop(1, '#050509');
      ctx.fillStyle = bg;
      ctx.fillRect(0, 0, w, h);

      drawStars(ctx, w, h, now);

      // Apply camera transform
      ctx.save();
      ctx.translate(w / 2, h / 2);
      ctx.scale(cam.zoom, cam.zoom);
      ctx.translate(-cam.x, -cam.y);

      if (topo) {
        drawZones(ctx, topo.nodes, pos, now, cam.zoom);

        // Only the mesh being engaged with is outlined; several at once would
        // stack overlapping hulls across every cluster.
        const focusedMesh =
          findMeshForNode(drawRef.current.agentMeshes, hoveredId) ??
          findMeshForNode(drawRef.current.agentMeshes, drawRef.current.selectedId);
        if (focusedMesh) {
          const memberPoints = focusedMesh.memberIds
            .map((id) => pos.get(id))
            .filter((p): p is NonNullable<typeof p> => !!p)
            .map((p) => ({ x: p.x, y: p.y }));
          drawAgentMesh(ctx, memberPoints, focusedMesh.id, cam.zoom);
        }

        drawEdges(ctx, topo, pos, now);

        // Draw hosts first (background layer), then other nodes
        for (const node of topo.nodes) {
          if (node.typeId !== 'host') continue;
          const p = pos.get(node.id);
          if (p) drawNode(ctx, node, p, node.id === hoveredId, cam.zoom);
        }
        for (const node of topo.nodes) {
          if (node.typeId === 'host' || node.typeId === 'mimir') continue;
          const p = pos.get(node.id);
          if (p) drawNode(ctx, node, p, node.id === hoveredId, cam.zoom);
        }

        // Mímir last (always on top)
        for (const node of topo.nodes) {
          if (node.typeId !== 'mimir') continue;
          const p = pos.get(node.id);
          if (p) drawMimir(ctx, p, now, node.parentId ? 0.8 : 1, node.label.toUpperCase());
        }
      }

      ctx.restore();

      // Minimap (off-screen canvas approach — draw directly here)
      const mm = minimapRef.current;
      if (mm && showMinimap && topo) {
        const mmCtx = mm.getContext('2d');
        if (mmCtx) {
          drawMinimap(
            mmCtx,
            CANVAS.MINIMAP_W,
            CANVAS.MINIMAP_H,
            topo,
            pos,
            cam.x,
            cam.y,
            cam.zoom,
            w,
            h,
            CANVAS.WORLD_W,
            CANVAS.WORLD_H,
          );
        }
      }

      rafId = requestAnimationFrame(render);
    };

    rafId = requestAnimationFrame(render);
    return () => {
      cancelled = true;
      cancelAnimationFrame(rafId);
    };
  }, [showMinimap]);

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div className={['topology-canvas-wrapper', className].filter(Boolean).join(' ')} style={style}>
      <canvas
        ref={canvasRef}
        data-testid="topology-canvas"
        tabIndex={0}
        aria-label="Live topology canvas — drag to pan, scroll to zoom, arrow keys to pan"
        className="topology-canvas"
        onMouseMove={handleMouseMove}
        onMouseLeave={handleMouseLeave}
        onClick={handleClick}
      />

      {/* Camera controls — vertical pill stack, top-right (web2 parity) */}
      <div
        data-testid="camera-controls"
        style={{
          position: 'absolute',
          top: 12,
          right: 12,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          background: 'rgba(9,9,11,0.82)',
          border: '1px solid rgba(147,197,253,0.2)',
          borderRadius: 8,
          fontFamily: 'var(--font-mono, monospace)',
          fontSize: 11,
          color: 'rgba(186,230,253,0.8)',
          userSelect: 'none',
          overflow: 'hidden',
          zIndex: 40,
        }}
      >
        <button aria-label="Zoom in" onClick={zoomIn} className="camera-btn">
          +
        </button>
        <span data-testid="zoom-display" className="zoom-display">
          {zoomPct}%
        </span>
        <button aria-label="Zoom out" onClick={zoomOut} className="camera-btn">
          −
        </button>
        <div className="camera-divider" />
        <button
          aria-label="Reset camera"
          data-testid="camera-reset"
          onClick={resetCamera}
          className="camera-btn"
        >
          ⊙
        </button>
      </div>

      {/* Minimap — bottom-right overlay */}
      {showMinimap && (
        <div data-testid="minimap-panel" className="minimap-panel">
          <canvas
            ref={minimapRef}
            width={CANVAS.MINIMAP_W}
            height={CANVAS.MINIMAP_H}
            className="minimap-canvas"
            onClick={handleMinimapClick}
            role="img"
            aria-label="Topology minimap — click to pan"
          />
        </div>
      )}
    </div>
  );
}
