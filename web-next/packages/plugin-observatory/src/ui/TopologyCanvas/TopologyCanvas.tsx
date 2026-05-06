import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Topology } from '../../domain';
import {
  clampZoom,
  applyDragPan,
  applyScrollZoom,
  applyKeyPan,
  defaultCamera,
  type Camera,
} from './canvasMath';
import { computeLayout, HOST_HALF_W, HOST_HALF_H } from './layoutEngine';
import { drawStars, drawZones, drawEdges, drawNode, drawMimir, drawMinimap } from './renderer';
import { CANVAS, HIT_RADIUS } from './config';
import './TopologyCanvas.css';

// ── Types ─────────────────────────────────────────────────────────────────────

export interface TopologyCanvasProps {
  topology: Topology | null;
  /** Called when the user clicks a node. */
  onNodeClick?: (nodeId: string) => void;
  /** Show the minimap panel (default true). */
  showMinimap?: boolean;
  /** Extra CSS class applied to the wrapper div. */
  className?: string;
  /** Inline style applied to the wrapper div. */
  style?: React.CSSProperties;
}

interface DragState {
  active: boolean;
  startX: number;
  startY: number;
  startCam: Camera;
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
  showMinimap = true,
  className,
  style,
}: TopologyCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const minimapRef = useRef<HTMLCanvasElement>(null);
  const sizeRef = useRef({ w: 0, h: 0 });
  const camRef = useRef<Camera>(defaultCamera());
  const dragRef = useRef<DragState>({
    active: false,
    startX: 0,
    startY: 0,
    startCam: defaultCamera(),
  });
  const hoveredIdRef = useRef<string | null>(null);
  const [zoomPct, setZoomPct] = useState(Math.round(CANVAS.INITIAL_ZOOM * 100));

  // Compute layout whenever topology changes (memoised — pure function)
  const positions = useMemo(() => (topology ? computeLayout(topology) : new Map()), [topology]);

  // Stable reference to drawing data so the rAF loop always reads fresh values
  // without being re-subscribed on every state tick.
  const drawRef = useRef({ topology, positions, hoveredId: null as string | null });
  drawRef.current = { topology, positions, hoveredId: hoveredIdRef.current };

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
    };

    apply(canvas.clientWidth, canvas.clientHeight);
    const ro = new ResizeObserver((entries) => {
      const { width, height } = entries[0]!.contentRect;
      apply(width, height);
    });
    ro.observe(canvas);
    return () => ro.disconnect();
  }, []);

  // ── Scroll-wheel zoom ───────────────────────────────────────────────────────

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const { w, h } = sizeRef.current;
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      camRef.current = applyScrollZoom(camRef.current, e.deltaY, mx, my, w, h);
      setZoomPct(Math.round(camRef.current.zoom * 100));
    };

    canvas.addEventListener('wheel', onWheel, { passive: false });
    return () => canvas.removeEventListener('wheel', onWheel);
  }, []);

  // ── Keyboard pan (arrow keys) ───────────────────────────────────────────────

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const onKeyDown = (e: KeyboardEvent) => {
      const panKeys = ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'];
      if (!panKeys.includes(e.key)) return;
      e.preventDefault();
      camRef.current = applyKeyPan(camRef.current, e.key, CANVAS.PAN_KEY_STEP);
    };

    canvas.addEventListener('keydown', onKeyDown);
    return () => canvas.removeEventListener('keydown', onKeyDown);
  }, []);

  // ── Hit detection ───────────────────────────────────────────────────────────

  const hitTest = useCallback((sx: number, sy: number): string | null => {
    const { w, h } = sizeRef.current;
    const cam = camRef.current;
    const wx = (sx - w / 2) / cam.zoom + cam.x;
    const wy = (sy - h / 2) / cam.zoom + cam.y;

    const { topology: topo, positions: pos } = drawRef.current;
    if (!topo) return null;

    for (const node of topo.nodes) {
      const p = pos.get(node.id);
      if (!p) continue;
      if (node.typeId === 'host') {
        if (Math.abs(wx - p.x) < HOST_HALF_W && Math.abs(wy - p.y) < HOST_HALF_H) return node.id;
      } else {
        const r = HIT_RADIUS[node.typeId] ?? HIT_RADIUS['tyr']!;
        if ((wx - p.x) ** 2 + (wy - p.y) ** 2 < r * r) return node.id;
      }
    }
    return null;
  }, []);

  // ── Mouse event handlers ────────────────────────────────────────────────────

  const handleMouseMove = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      const rect = canvasRef.current!.getBoundingClientRect();
      const sx = e.clientX - rect.left;
      const sy = e.clientY - rect.top;
      const drag = dragRef.current;

      if (drag.active) {
        const dx = sx - drag.startX;
        const dy = sy - drag.startY;
        const { x, y } = applyDragPan(drag.startCam, dx, dy);
        camRef.current = { ...camRef.current, x, y };
        canvasRef.current!.style.cursor = 'grabbing';
        return;
      }

      const hitId = hitTest(sx, sy);
      hoveredIdRef.current = hitId;
      canvasRef.current!.style.cursor = hitId ? 'pointer' : 'grab';
    },
    [hitTest],
  );

  const handleMouseDown = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      const rect = canvasRef.current!.getBoundingClientRect();
      const sx = e.clientX - rect.left;
      const sy = e.clientY - rect.top;
      if (hitTest(sx, sy)) return; // let the click handler deal with it
      dragRef.current = {
        active: true,
        startX: sx,
        startY: sy,
        startCam: { ...camRef.current },
      };
      canvasRef.current!.style.cursor = 'grabbing';
    },
    [hitTest],
  );

  const handleMouseUp = useCallback(() => {
    dragRef.current.active = false;
    canvasRef.current!.style.cursor = 'grab';
  }, []);

  const handleClick = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      const rect = canvasRef.current!.getBoundingClientRect();
      const hitId = hitTest(e.clientX - rect.left, e.clientY - rect.top);
      if (hitId) onNodeClick?.(hitId);
    },
    [hitTest, onNodeClick],
  );

  const handleMouseLeave = useCallback(() => {
    dragRef.current.active = false;
    hoveredIdRef.current = null;
    if (canvasRef.current) canvasRef.current.style.cursor = 'grab';
  }, []);

  // ── Minimap click-to-pan ────────────────────────────────────────────────────

  const handleMinimapClick = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const mm = minimapRef.current;
    if (!mm) return;
    const rect = mm.getBoundingClientRect();
    const fx = (e.clientX - rect.left) / rect.width;
    const fy = (e.clientY - rect.top) / rect.height;
    camRef.current = {
      ...camRef.current,
      x: fx * CANVAS.WORLD_W - CANVAS.WORLD_W / 2,
      y: fy * CANVAS.WORLD_H - CANVAS.WORLD_H / 2,
    };
  }, []);

  // ── Camera controls ─────────────────────────────────────────────────────────

  const zoomIn = useCallback(() => {
    camRef.current = { ...camRef.current, zoom: clampZoom(camRef.current.zoom * CANVAS.ZOOM_STEP) };
    setZoomPct(Math.round(camRef.current.zoom * 100));
  }, []);

  const zoomOut = useCallback(() => {
    camRef.current = { ...camRef.current, zoom: clampZoom(camRef.current.zoom / CANVAS.ZOOM_STEP) };
    setZoomPct(Math.round(camRef.current.zoom * 100));
  }, []);

  const resetCamera = useCallback(() => {
    camRef.current = defaultCamera();
    setZoomPct(Math.round(defaultCamera().zoom * 100));
  }, []);

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
        drawZones(ctx, topo.nodes, pos, now);
        drawEdges(ctx, topo, pos, now);

        // Draw hosts first (background layer), then other nodes
        for (const node of topo.nodes) {
          if (node.typeId !== 'host') continue;
          const p = pos.get(node.id);
          if (p) drawNode(ctx, node, p, node.id === hoveredId);
        }
        for (const node of topo.nodes) {
          if (node.typeId === 'host' || node.typeId === 'mimir') continue;
          const p = pos.get(node.id);
          if (p) drawNode(ctx, node, p, node.id === hoveredId);
        }

        // Mímir last (always on top)
        for (const node of topo.nodes) {
          if (node.typeId !== 'mimir') continue;
          const p = pos.get(node.id);
          if (p) drawMimir(ctx, p, now, 1, node.label.toUpperCase());
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
        onMouseDown={handleMouseDown}
        onMouseUp={handleMouseUp}
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
            aria-label="Minimap — click to pan"
          />
        </div>
      )}
    </div>
  );
}
