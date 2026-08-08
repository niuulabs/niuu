import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { EdgeLayer, Registry, Topology, TopologyNode } from '../../domain';
import { visibleEdges } from '../../domain';
import { regionReadout, type RegionReadout } from '../../domain/regionStats';
import { deriveAgentMeshes, findMeshForNode } from '../../domain/agentMesh';
import { computeClassMap, type ComputeClass } from '../../domain/computeClass';
import { computeLayout } from '../TopologyCanvas/layoutEngine';
import { buildTypeStyles, nodeStyle, type NodeStyle } from '../TopologyCanvas/nodeStyle';
import { drawMinimap } from '../TopologyCanvas/renderer';
import { CANVAS } from '../TopologyCanvas/config';
import '../TopologyCanvas/TopologyCanvas.css';

import { CAMERA3D } from './scene3dConfig';
import {
  createWebGLRenderer,
  supportsWebGL,
  type Scene3DRenderer,
  type Scene3DRendererFactory,
} from './webglRenderer';
import { buildScene3DModel } from './sceneModel';
import { createObservatoryScene, type ObservatoryScene } from './observatoryScene';
import {
  applyKeyNavigation,
  defaultOrbitCamera,
  dollyBy,
  driftCamera,
  easeOrbitCamera,
  fitOrbitCamera,
  focusOrbitCamera,
  orbitBy,
  panBy,
  worldUnitsPerPixel,
  type OrbitCamera,
} from './orbitCamera';
import './TopologyScene3D.css';

/** Pointer travel, in pixels, past which a press is a drag rather than a click. */
const CLICK_SLOP_PX = 6;

/** The types that wear an instrument. Mirrors the scene's own shell vocabulary. */
const REGION_TYPES: ReadonlySet<string> = new Set(['realm', 'cloud', 'cluster', 'namespace']);

export interface TopologyScene3DProps {
  topology: Topology | null;
  /** Entity registry — supplies each type's shape and size, as it does in 2D. */
  registry?: Registry | null;
  /** Fired with the node clicked, or `null` when empty space was clicked. */
  onNodeClick?: (nodeId: string | null) => void;
  selectedId?: string | null;
  /** The node the camera should travel to. Defaults to the selection. */
  focusId?: string | null;
  hiddenLayers?: ReadonlySet<EdgeLayer>;
  hiddenCompute?: ReadonlySet<ComputeClass>;
  showMinimap?: boolean;
  className?: string;
  style?: React.CSSProperties;
  /**
   * The surface to draw on. Swapped out by tests and stories, which have no
   * GPU — and which is what lets every gesture, every pick and every camera
   * move in here be tested at all.
   */
  createRenderer?: Scene3DRendererFactory;
}

/**
 * TopologyScene3D — the estate as a model you can walk around.
 *
 * Same data, same layout and same palette as `TopologyCanvas`; what changes is
 * that containment becomes height. In plan, "this agent runs on that host"
 * has to be read off a ring drawn around a cluster of dots, and once three
 * levels of containment overlap the ring stops carrying the claim. Here each
 * level of the chain stands on its own deck, so the claim is a direction you
 * can see by moving the camera.
 *
 * Drag orbits, right-drag or shift-drag slides, the wheel dollies, arrows turn
 * and shift+arrows slide. Clicking a thing selects it exactly as it does in
 * plan, and the same inspector answers.
 */
export function TopologyScene3D({
  topology,
  registry = null,
  onNodeClick,
  selectedId = null,
  focusId,
  hiddenLayers,
  hiddenCompute,
  showMinimap = true,
  className,
  style,
  createRenderer = createWebGLRenderer,
}: TopologyScene3DProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const minimapRef = useRef<HTMLCanvasElement>(null);
  const rendererRef = useRef<Scene3DRenderer | null>(null);
  const sceneRef = useRef<ObservatoryScene | null>(null);
  const cameraRef = useRef<OrbitCamera>(defaultOrbitCamera());
  const sizeRef = useRef({ w: 0, h: 0 });
  const hoveredIdRef = useRef<string | null>(null);
  const focusTargetRef = useRef<OrbitCamera | null>(null);
  const lastFocusedIdRef = useRef<string | null>(null);
  const userAdjustedCameraRef = useRef(false);
  const lastFitKeyRef = useRef('');
  const lastSignatureRef = useRef('');
  /** When the operator last did anything, so the idle drift knows to wait. */
  const lastTouchRef = useRef(0);
  const lastFrameRef = useRef(0);
  const onNodeClickRef = useRef(onNodeClick);

  const dragRef = useRef<{
    pointerId: number;
    mode: 'orbit' | 'pan';
    lastX: number;
    lastY: number;
    travel: number;
  } | null>(null);

  const [viewportSize, setViewportSize] = useState({ w: 0, h: 0 });
  const [dragging, setDragging] = useState(false);
  const [hovering, setHovering] = useState(false);
  const [zoomPct, setZoomPct] = useState(100);

  // A caller who supplied their own factory has told us it can render; only
  // the built-in WebGL path has to ask the browser.
  const canRender = useMemo(
    () => createRenderer !== createWebGLRenderer || supportsWebGL(),
    [createRenderer],
  );

  useEffect(() => {
    onNodeClickRef.current = onNodeClick;
  }, [onNodeClick]);

  // ── Model ───────────────────────────────────────────────────────────────────

  const positions = useMemo(() => (topology ? computeLayout(topology) : new Map()), [topology]);

  // Layers filter what is drawn, never the layout: hiding a connection must not
  // make the decks rearrange under the operator.
  const drawnTopology = useMemo<Topology | null>(() => {
    if (!topology) return null;
    if (!hiddenLayers || hiddenLayers.size === 0) return topology;
    return { ...topology, edges: visibleEdges(topology.edges, hiddenLayers) };
  }, [hiddenLayers, topology]);

  const styleFor = useMemo(() => {
    const typeStyles = buildTypeStyles(registry);
    const classes = computeClassMap(topology?.nodes ?? []);
    const byNode = new Map<string, NodeStyle>(
      (topology?.nodes ?? []).map((node) => [
        node.id,
        nodeStyle(node, typeStyles, classes.get(node.id)),
      ]),
    );
    return (node: TopologyNode): NodeStyle => byNode.get(node.id) ?? nodeStyle(node, typeStyles);
  }, [registry, topology]);

  const model = useMemo(
    () => buildScene3DModel(drawnTopology, positions, styleFor),
    [drawnTopology, positions, styleFor],
  );

  // What each region has to say about itself, derived once per snapshot. The
  // full topology, not the layer-filtered one: hiding a connection is a
  // question about what is drawn, not about how many residents a realm holds.
  const readouts = useMemo(() => {
    const byRegion = new Map<string, RegionReadout | null>();
    for (const node of topology?.nodes ?? []) {
      if (!REGION_TYPES.has(node.typeId)) continue;
      byRegion.set(node.id, regionReadout(topology, node.id));
    }
    return byRegion;
  }, [topology]);

  const agentMeshes = useMemo(() => deriveAgentMeshes(topology), [topology]);

  const neighbours = useMemo(() => {
    const map = new Map<string, Set<string>>();
    for (const edge of drawnTopology?.edges ?? []) {
      if (!map.has(edge.sourceId)) map.set(edge.sourceId, new Set());
      if (!map.has(edge.targetId)) map.set(edge.targetId, new Set());
      map.get(edge.sourceId)!.add(edge.targetId);
      map.get(edge.targetId)!.add(edge.sourceId);
    }
    return map;
  }, [drawnTopology]);

  const reducedMotion = useMemo(
    () =>
      typeof window !== 'undefined' &&
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches,
    [],
  );

  // Read by the animation loop, so it never has to be re-subscribed when the
  // parent hands down new props.
  const frameRef = useRef({ model, agentMeshes, neighbours, selectedId, hiddenCompute });
  useEffect(() => {
    frameRef.current = { model, agentMeshes, neighbours, selectedId, hiddenCompute };
  }, [agentMeshes, hiddenCompute, model, neighbours, selectedId]);

  // ── Renderer ────────────────────────────────────────────────────────────────

  useEffect(() => {
    const host = hostRef.current;
    if (!host || !canRender) return;

    // Not wrapped in a try: the capability was already asked for and answered.
    // A context that fails to open after the browser said it could is a real
    // fault, and swallowing it would leave a silently dead stage.
    const renderer = createRenderer();
    rendererRef.current = renderer;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    const { w, h } = sizeRef.current;
    if (w && h) renderer.setSize(w, h, true);
    host.appendChild(renderer.domElement);

    return () => {
      renderer.domElement.remove();
      renderer.dispose();
      rendererRef.current = null;
    };
  }, [canRender, createRenderer]);

  // ── Sizing ──────────────────────────────────────────────────────────────────

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const apply = (width: number, height: number) => {
      if (!width || !height) return;
      sizeRef.current = { w: width, h: height };
      rendererRef.current?.setSize(width, height, true);
      setViewportSize((prev) =>
        prev.w === width && prev.h === height ? prev : { w: width, h: height },
      );
    };

    apply(host.clientWidth, host.clientHeight);
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (rect) apply(rect.width, rect.height);
    });
    observer.observe(host);
    return () => observer.disconnect();
  }, []);

  // ── Scene ───────────────────────────────────────────────────────────────────

  useEffect(() => {
    const scene = createObservatoryScene(model, {
      reducedMotion,
      readoutFor: (regionId) => readouts.get(regionId) ?? null,
    });
    sceneRef.current = scene;
    return () => {
      scene.dispose();
      sceneRef.current = null;
    };
  }, [model, readouts, reducedMotion]);

  const fitCamera = useCallback(() => {
    const { w, h } = sizeRef.current;
    cameraRef.current = fitOrbitCamera(model.framePoints, w && h ? w / h : 1, cameraRef.current);
    focusTargetRef.current = null;
  }, [model.framePoints]);

  // Frame the estate whenever what is on show changes shape, unless the
  // operator has taken the camera somewhere themselves.
  const topologySignature = useMemo(
    () =>
      model.nodes
        .map((node) => node.id)
        .sort()
        .join('|'),
    [model.nodes],
  );

  useEffect(() => {
    if (!viewportSize.w || !viewportSize.h) return;

    // A different estate is a different subject, and whatever the operator had
    // framed no longer exists — so the camera is theirs again. Tracked as its
    // own value rather than parsed back out of the fit key: node ids are
    // opaque strings, and one containing the separator would silently turn
    // every resize into a re-frame.
    if (lastSignatureRef.current !== topologySignature) {
      lastSignatureRef.current = topologySignature;
      userAdjustedCameraRef.current = false;
    }
    if (userAdjustedCameraRef.current) return;

    const fitKey = `${topologySignature}\u0000${viewportSize.w}x${viewportSize.h}`;
    if (lastFitKeyRef.current === fitKey) return;
    lastFitKeyRef.current = fitKey;
    fitCamera();
  }, [fitCamera, topologySignature, viewportSize.h, viewportSize.w]);

  // ── Selection focus ─────────────────────────────────────────────────────────

  const travelTo = focusId === undefined ? selectedId : focusId;

  useEffect(() => {
    if (!travelTo) {
      lastFocusedIdRef.current = null;
      return;
    }
    if (lastFocusedIdRef.current === travelTo) return;
    const node = model.nodeById.get(travelTo);
    if (!node) return;
    lastFocusedIdRef.current = travelTo;
    focusTargetRef.current = focusOrbitCamera(cameraRef.current, node.position);
    // Flying to a selection counts as the operator choosing where to look. Not
    // marking it left the camera at the mercy of the next resize: dock the
    // inspector, or drag the window edge, and the view snapped back out to the
    // whole estate having just travelled to one agent.
    userAdjustedCameraRef.current = true;
  }, [model, travelTo]);

  // ── Camera helpers ──────────────────────────────────────────────────────────

  const adjustCamera = useCallback((next: OrbitCamera) => {
    cameraRef.current = next;
    userAdjustedCameraRef.current = true;
    focusTargetRef.current = null;
    lastTouchRef.current = performance.now();
  }, []);

  const dolly = useCallback(
    (factor: number) => adjustCamera(dollyBy(cameraRef.current, factor)),
    [adjustCamera],
  );

  const resetCamera = useCallback(() => {
    userAdjustedCameraRef.current = false;
    lastFitKeyRef.current = '';
    cameraRef.current = { ...cameraRef.current, ...defaultOrbitCamera() };
    fitCamera();
  }, [fitCamera]);

  // ── Pointer interaction ─────────────────────────────────────────────────────

  /** Cursor position in normalised device coordinates, or null when off-stage. */
  const toNdc = useCallback((clientX: number, clientY: number) => {
    const host = hostRef.current;
    if (!host) return null;
    const rect = host.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    return {
      x: ((clientX - rect.left) / rect.width) * 2 - 1,
      y: -((clientY - rect.top) / rect.height) * 2 + 1,
    };
  }, []);

  const handlePointerDown = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    // Right and middle drag slide rather than turn, matching every other 3D
    // viewer the operator has used; shift+left does the same for trackpads.
    const pan = event.button === 1 || event.button === 2 || event.shiftKey;
    dragRef.current = {
      pointerId: event.pointerId,
      mode: pan ? 'pan' : 'orbit',
      lastX: event.clientX,
      lastY: event.clientY,
      travel: 0,
    };
    setDragging(true);
    lastTouchRef.current = performance.now();
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }, []);

  const handlePointerMove = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const drag = dragRef.current;
      if (drag && drag.pointerId === event.pointerId) {
        const dx = event.clientX - drag.lastX;
        const dy = event.clientY - drag.lastY;
        drag.lastX = event.clientX;
        drag.lastY = event.clientY;
        drag.travel += Math.hypot(dx, dy);
        adjustCamera(
          drag.mode === 'pan'
            ? panBy(cameraRef.current, dx, dy, sizeRef.current.h)
            : orbitBy(cameraRef.current, dx, dy),
        );
        return;
      }

      lastTouchRef.current = performance.now();
      const ndc = toNdc(event.clientX, event.clientY);
      const hit = ndc ? (sceneRef.current?.pick(ndc) ?? null) : null;
      hoveredIdRef.current = hit;
      setHovering(hit !== null);
    },
    [adjustCamera, toNdc],
  );

  const endDrag = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    dragRef.current = null;
    setDragging(false);
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    return drag;
  }, []);

  const handlePointerUp = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const drag = endDrag(event);
      // A press that travelled is a camera move, not a choice.
      if (drag && drag.travel > CLICK_SLOP_PX) return;
      const ndc = toNdc(event.clientX, event.clientY);
      if (!ndc) return;
      // Empty space reports null rather than nothing, so clicking away clears
      // the selection — the same contract the plan's canvas honours.
      onNodeClickRef.current?.(sceneRef.current?.pick(ndc) ?? null);
    },
    [endDrag, toNdc],
  );

  const handlePointerLeave = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      endDrag(event);
      hoveredIdRef.current = null;
      setHovering(false);
    },
    [endDrag],
  );

  // Wheel is bound natively rather than through React: a passive listener
  // cannot call preventDefault, and without that the page scrolls under the
  // dolly.
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const factor = event.deltaY > 0 ? CAMERA3D.DOLLY_STEP : 1 / CAMERA3D.DOLLY_STEP;
      adjustCamera(dollyBy(cameraRef.current, factor));
    };
    host.addEventListener('wheel', onWheel, { passive: false });
    return () => host.removeEventListener('wheel', onWheel);
  }, [adjustCamera]);

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onNodeClickRef.current?.(null);
        return;
      }
      if (event.key === '+' || event.key === '=') {
        event.preventDefault();
        dolly(1 / CAMERA3D.DOLLY_STEP);
        return;
      }
      if (event.key === '-' || event.key === '_') {
        event.preventDefault();
        dolly(CAMERA3D.DOLLY_STEP);
        return;
      }
      if (!event.key.startsWith('Arrow')) return;
      event.preventDefault();
      adjustCamera(
        applyKeyNavigation(cameraRef.current, event.key, event.shiftKey, sizeRef.current.h),
      );
    },
    [adjustCamera, dolly],
  );

  // ── Animation loop ──────────────────────────────────────────────────────────

  useEffect(() => {
    if (!canRender) return;
    let cancelled = false;
    let rafId = 0;

    const render = (now: number) => {
      if (cancelled) return;
      rafId = requestAnimationFrame(render);

      const renderer = rendererRef.current;
      const scene = sceneRef.current;
      const { w, h } = sizeRef.current;
      if (!renderer || !scene || !w || !h) return;

      const sinceLastFrame = lastFrameRef.current === 0 ? 0 : (now - lastFrameRef.current) / 1000;
      lastFrameRef.current = now;

      const destination = focusTargetRef.current;
      if (destination) {
        const { camera, arrived } = easeOrbitCamera(cameraRef.current, destination);
        cameraRef.current = camera;
        if (arrived) focusTargetRef.current = null;
        lastTouchRef.current = now;
      } else if (
        !reducedMotion &&
        !dragRef.current &&
        now - lastTouchRef.current > CAMERA3D.IDLE_DELAY_MS
      ) {
        // Left alone, the camera takes a slow turn of its own. It does not
        // count as the operator adjusting anything, so an estate that changes
        // shape while nobody is watching is still re-framed.
        cameraRef.current = driftCamera(cameraRef.current, sinceLastFrame, now);
      }

      const camera = cameraRef.current;
      const { model: current, agentMeshes: meshes, neighbours: adjacency } = frameRef.current;
      const hoveredId = hoveredIdRef.current;
      const focusedMesh =
        findMeshForNode(meshes, hoveredId) ?? findMeshForNode(meshes, frameRef.current.selectedId);

      scene.applyCamera(camera, w / h);
      scene.update({
        now,
        hoveredId,
        selectedId: frameRef.current.selectedId ?? null,
        litIds: hoveredId ? (adjacency.get(hoveredId) ?? new Set<string>()) : null,
        meshMemberIds: focusedMesh ? new Set(focusedMesh.memberIds) : null,
        hiddenCompute: frameRef.current.hiddenCompute,
        reducedMotion,
        camera,
        viewportWidth: w,
        viewportHeight: h,
      });
      renderer.render(scene.scene, scene.camera);

      // The plan reports a zoom percentage; here the equivalent honest number
      // is how much world one pixel covers, expressed the same way so the two
      // readouts can be compared.
      const perPixel = worldUnitsPerPixel(camera, h);
      setZoomPct(perPixel > 0 ? Math.round((1 / perPixel) * 100) : 100);

      const minimap = minimapRef.current;
      if (minimap && showMinimap && current.nodes.length > 0) {
        const ctx = minimap.getContext('2d');
        if (ctx && topology) {
          drawMinimap(
            ctx,
            CANVAS.MINIMAP_W,
            CANVAS.MINIMAP_H,
            topology,
            positions,
            camera.target.x,
            camera.target.z,
            perPixel > 0 ? 1 / perPixel : 1,
            w,
            h,
            CANVAS.WORLD_W,
            CANVAS.WORLD_H,
          );
        }
      }
    };

    rafId = requestAnimationFrame(render);
    return () => {
      cancelled = true;
      cancelAnimationFrame(rafId);
    };
  }, [canRender, positions, reducedMotion, showMinimap, topology]);

  // ── Minimap click-to-travel ─────────────────────────────────────────────────

  const handleMinimapClick = useCallback(
    (event: React.MouseEvent<HTMLCanvasElement>) => {
      const minimap = minimapRef.current;
      if (!minimap) return;
      const rect = minimap.getBoundingClientRect();
      const fx = (event.clientX - rect.left) / rect.width;
      const fy = (event.clientY - rect.top) / rect.height;
      adjustCamera({
        ...cameraRef.current,
        target: {
          x: fx * CANVAS.WORLD_W - CANVAS.WORLD_W / 2,
          y: cameraRef.current.target.y,
          z: fy * CANVAS.WORLD_H - CANVAS.WORLD_H / 2,
        },
      });
    },
    [adjustCamera],
  );

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div
      className={['topology-scene3d', className].filter(Boolean).join(' ')}
      style={style}
      data-testid="topology-scene3d"
    >
      <div
        ref={hostRef}
        className="topology-scene3d__canvas-host"
        data-testid="topology-scene3d-host"
        data-dragging={dragging}
        data-hovering={hovering}
        tabIndex={0}
        role="application"
        aria-label="Live topology, in three dimensions — drag to orbit, shift-drag to slide, scroll to zoom, arrow keys to turn"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerLeave={handlePointerLeave}
        onKeyDown={handleKeyDown}
        onContextMenu={(event) => event.preventDefault()}
      />

      {!canRender && (
        <div className="topology-scene3d__fallback" data-testid="scene3d-fallback" role="status">
          <strong>This browser cannot open a 3D view.</strong>
          <span>WebGL is unavailable or disabled. The 2D topology has the same data.</span>
        </div>
      )}

      {canRender && (
        <>
          <div
            data-testid="camera-controls-3d"
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
            <button
              aria-label="Zoom in"
              onClick={() => dolly(1 / CAMERA3D.DOLLY_STEP)}
              className="camera-btn"
            >
              +
            </button>
            <span data-testid="zoom-display-3d" className="zoom-display">
              {zoomPct}%
            </span>
            <button
              aria-label="Zoom out"
              onClick={() => dolly(CAMERA3D.DOLLY_STEP)}
              className="camera-btn"
            >
              −
            </button>
            <div className="camera-divider" />
            <button
              aria-label="Reset camera"
              data-testid="camera-reset-3d"
              onClick={resetCamera}
              className="camera-btn"
            >
              ⊙
            </button>
          </div>

          <div className="topology-scene3d__hint" data-testid="scene3d-hint">
            <span>drag · orbit</span>
            <span>shift+drag · slide</span>
            <span>scroll · zoom</span>
          </div>

          {showMinimap && (
            <div data-testid="minimap-panel-3d" className="minimap-panel">
              <canvas
                ref={minimapRef}
                width={CANVAS.MINIMAP_W}
                height={CANVAS.MINIMAP_H}
                className="minimap-canvas"
                onClick={handleMinimapClick}
                role="img"
                aria-label="Topology minimap — click to travel"
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}
