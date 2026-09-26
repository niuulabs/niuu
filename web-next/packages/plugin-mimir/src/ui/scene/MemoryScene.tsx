/**
 * `MemoryScene` — the 3D/2D memory-graph engine.
 *
 * A thin React component over a pure, testable core: `layout.ts` places
 * nodes, `visibility.ts` decides what's lit/dim/hidden, `colour.ts` +
 * `palette.ts` decide what colour, `picking.ts` decides what a click hit.
 * This file's only job is to turn that data into a three.js scene graph and
 * a DOM overlay, and to turn pointer/wheel/resize/visibility events into
 * prop callbacks and camera moves. Every module it calls into is exercised
 * directly in its own test file; this component's tests (`MemoryScene.test.tsx`)
 * exercise the wiring, against an injected fake `Scene3DRenderer`.
 */

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import * as THREE from 'three';
import { relTime } from '@niuulabs/ui';
import type { GraphNode } from '../../domain/api-types';
import type { MemorySceneProps, SceneAnswer, SceneMarker, SceneQuestion } from './types';
import { createWebGLRenderer, supportsWebGL, type Scene3DRenderer } from './webglRenderer';
import { computeLayout, flattenTo2D, layoutPoints, radiusForDegree } from './layout';
import type { SceneLayout } from './layout';
import { computeVisibility } from './visibility';
import type { LitLevel, SceneVisibility } from './visibility';
import { memoryPalette } from './palette';
import type { MemoryPalette } from './palette';
import { nodeColour } from './colour';
import { pickNearestNode, isDragGesture } from './picking';
import type { ProjectedPoint } from './picking';
import { layoutLabels } from './labelLayout';
import type { LabelCandidate, PlacedLabel } from './labelLayout';
import {
  defaultOrbitCamera,
  lockTo2D,
  eyePosition,
  orbitBy,
  panBy,
  dollyBy,
  fitOrbitCamera,
  easeOrbitCamera,
} from './orbitCamera';
import type { OrbitCamera } from './orbitCamera';
import {
  CAMERA3D,
  CLICK_DRAG_THRESHOLD_PX,
  FOG,
  HUB_LABEL_COUNT,
  NODE3D,
  PICK_RADIUS_PX,
} from './scene3dConfig';
import type { Vec3 } from './vec3';
import './MemoryScene.css';

const LIT_ALPHA: Record<LitLevel, number> = {
  lit: 1,
  normal: 0.92,
  'dim-soft': 0.4,
  'dim-strong': 0.12,
};

/**
 * Edges are the scene's texture, not its subject: at rest they are a faint
 * web behind the spheres, and only lit links (focus, answers, a traced path)
 * are drawn bright enough to read.
 */
const EDGE_BRIGHTNESS: Record<LitLevel, number> = {
  lit: 0.85,
  normal: 0.045,
  'dim-soft': 0.03,
  'dim-strong': 0.015,
};

/** Sharper than this costs fill rate for no visible gain on a glowing point cloud. */
const MAX_PIXEL_RATIO = 2;

/**
 * Device pixels per world unit at unit distance, for the current canvas: a
 * sprite of world size w at distance d is w * scale / d pixels across, so
 * spheres keep their true size relative to the layout at every zoom.
 */
function projectionScale(heightCss: number): number {
  const ratio = Math.min(window.devicePixelRatio || 1, MAX_PIXEL_RATIO);
  return (heightCss * ratio) / (2 * Math.tan((CAMERA3D.FOV * Math.PI) / 360));
}

/** Node sprite size, in world units per unit of layout radius (the halo reaches well past the core). */
const NODE_SPRITE_SCALE = 5;

const PARTICLE_SPEED_PER_MS = 1 / 1400;
const MAX_PARTICLES = 300;

/** A lit link's relation, drawn at the link's midpoint. */
interface EdgeLabelTarget {
  key: string;
  source: string;
  target: string;
  label: string;
}

/** Most relation labels drawn at once; beyond this a spotlight is a tangle, not a reading. */
const MAX_EDGE_LABELS = 40;

interface LatestData {
  graph: MemorySceneProps['graph'];
  layout: SceneLayout;
  visibility: SceneVisibility;
  view: '2d' | '3d';
  colourBy: 'type' | 'proof' | 'age';
  maxDegree: number;
  /** Read from the tokens once the container mounts; null before that. */
  palette: MemoryPalette | null;
  nodeById: Map<string, GraphNode>;
  labelledIds: Set<string>;
  markerTargets: readonly SceneMarker[];
  questionTargets: readonly SceneQuestion[];
  answerTargets: readonly SceneAnswer[];
  edgeLabelTargets: readonly EdgeLabelTarget[];
  onSelectNode: MemorySceneProps['onSelectNode'];
  onBackgroundClick: MemorySceneProps['onBackgroundClick'];
}

interface ThreeBag {
  renderer: Scene3DRenderer;
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  nodePoints: THREE.Points | null;
  nodeIdByIndex: string[];
  edgeLines: THREE.LineSegments | null;
  dashedEdgeLines: THREE.LineSegments | null;
  particlePoints: THREE.Points | null;
  rings: THREE.Group;
}

function projectToScreen(
  camera: THREE.Camera,
  position: Vec3,
  width: number,
  height: number,
): { x: number; y: number; inFront: boolean } {
  const v = new THREE.Vector3(position.x, position.y, position.z).project(camera);
  return {
    x: ((v.x + 1) / 2) * width,
    y: ((1 - v.y) / 2) * height,
    inFront: v.z < 1,
  };
}

/** Deterministic "free spot" for a question with no anchor node, hashed from its id. */
function freeQuestionSpot(id: string, radius: number): Vec3 {
  let hash = 0x811c9dc5;
  for (let i = 0; i < id.length; i += 1) {
    hash ^= id.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  const angle = ((hash >>> 0) % 360) * (Math.PI / 180);
  return { x: Math.cos(angle) * radius, y: 0, z: Math.sin(angle) * radius };
}

function disposeThreeBag(bag: ThreeBag): void {
  bag.nodePoints?.geometry.dispose();
  (bag.nodePoints?.material as THREE.Material | undefined)?.dispose();
  bag.edgeLines?.geometry.dispose();
  (bag.edgeLines?.material as THREE.Material | undefined)?.dispose();
  bag.dashedEdgeLines?.geometry.dispose();
  (bag.dashedEdgeLines?.material as THREE.Material | undefined)?.dispose();
  bag.particlePoints?.geometry.dispose();
  (bag.particlePoints?.material as THREE.Material | undefined)?.dispose();
  for (const child of [...bag.rings.children]) {
    bag.rings.remove(child);
    if (child instanceof THREE.Line || child instanceof THREE.LineLoop) {
      child.geometry.dispose();
      (child.material as THREE.Material).dispose();
    }
  }
}

/** Blend a hex/CSS colour toward the background colour by `1 - alpha`, for edges (no per-vertex alpha channel). */
function dimColour(colour: THREE.Color, bgColour: THREE.Color, alpha: number): THREE.Color {
  return colour.clone().lerp(bgColour, 1 - alpha);
}

export function MemoryScene(props: MemorySceneProps): React.JSX.Element {
  const {
    graph,
    view,
    colourBy,
    hiddenGroups,
    focus,
    answers,
    path,
    asOf,
    markers,
    questions,
    showQuestions,
    disputedIds,
    camera,
    onSelectNode,
    onBackgroundClick,
    createRenderer,
  } = props;

  const containerRef = useRef<HTMLDivElement | null>(null);
  const hostRef = useRef<HTMLDivElement | null>(null);

  const [unsupported] = useState(() => !createRenderer && !supportsWebGL());

  const layout3D = useMemo(() => computeLayout(graph), [graph]);
  const layout = useMemo(
    () => (view === '2d' ? flattenTo2D(layout3D) : layout3D),
    [layout3D, view],
  );
  const visibility = useMemo(
    () => computeVisibility(graph, { hiddenGroups, focus, answers, path, asOf, disputedIds }),

    [graph, hiddenGroups, focus, answers, path, asOf, disputedIds],
  );

  const nodeById = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n])), [graph]);

  const maxDegree = useMemo(() => {
    let max = 0;
    for (const n of layout.nodes.values()) max = Math.max(max, n.degree);
    return max;
  }, [layout]);

  const visibleNodeCount = useMemo(
    () => [...visibility.nodes.values()].filter((v) => v.visible).length,
    [visibility],
  );
  const visibleEdgeCount = useMemo(
    () => visibility.edges.filter((e) => e.visible).length,
    [visibility],
  );

  const labelledIds = useMemo(() => {
    const ranked = [...layout.nodes.values()]
      .filter((n) => visibility.nodes.get(n.id)?.visible)
      .sort((a, b) => b.degree - a.degree)
      .slice(0, HUB_LABEL_COUNT)
      .map((n) => n.id);
    const ids = new Set(ranked);
    if (visibility.hasSpotlight) {
      for (const [id, v] of visibility.nodes) {
        if (v.visible && v.litLevel === 'lit') ids.add(id);
      }
    }
    return ids;
  }, [layout, visibility]);

  const markerTargets = useMemo(
    () => markers.filter((m) => layout.nodes.has(m.nodeId)),
    [markers, layout],
  );

  const questionTargets = useMemo(
    () => (showQuestions ? questions : []),
    [showQuestions, questions],
  );
  const answerTargets = useMemo(
    () => answers.filter((a) => layout.nodes.has(a.nodeId)),
    [answers, layout],
  );
  const edgeLabelTargets = useMemo<EdgeLabelTarget[]>(
    () =>
      visibility.edges
        .filter((e) => e.visible && e.litLevel === 'lit' && e.label !== null)
        .slice(0, MAX_EDGE_LABELS)
        .map((e) => ({ key: e.key, source: e.source, target: e.target, label: e.label! })),
    [visibility],
  );
  /** The pages a spotlight lights, as a stable key: the camera frames them when it changes. */
  const spotlightKey = useMemo(() => {
    if (!visibility.hasSpotlight) return '';
    return [...visibility.nodes.entries()]
      .filter(([, v]) => v.visible && v.litLevel === 'lit')
      .map(([id]) => id)
      .sort()
      .join('\n');
  }, [visibility]);

  // ---- refs the imperative code reads; always current, never captured stale ----
  const latestRef = useRef<LatestData>({
    graph,
    layout,
    visibility,
    view,
    colourBy,
    maxDegree,
    palette: null,
    nodeById,
    labelledIds,
    markerTargets,
    questionTargets,
    answerTargets,
    edgeLabelTargets,
    onSelectNode,
    onBackgroundClick,
  });

  const threeRef = useRef<ThreeBag | null>(null);
  const cameraStateRef = useRef<OrbitCamera>(defaultOrbitCamera());
  const destinationRef = useRef<OrbitCamera | null>(null);
  const rafRef = useRef<number | null>(null);
  const hasAutoFitRef = useRef(false);
  const lastCameraKeyRef = useRef<string | number | null>(null);
  const nodeScreenRef = useRef<ProjectedPoint[]>([]);
  const dragStartRef = useRef<{ x: number; y: number; isPanning: boolean } | null>(null);
  /** The original press position, kept for click-vs-drag classification even as `dragStartRef` tracks the latest move. */
  const pressOriginRef = useRef<{ x: number; y: number } | null>(null);
  const renderFrameRef = useRef<(() => void) | null>(null);
  const questionWorldRef = useRef<Map<string, Vec3>>(new Map());

  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const hoverPosRef = useRef<{ x: number; y: number }>({ x: 0, y: 0 });

  const labelElsRef = useRef(new Map<string, HTMLDivElement>());
  const markerElsRef = useRef(new Map<string, HTMLDivElement>());
  const questionElsRef = useRef(new Map<string, HTMLDivElement>());
  const badgeElsRef = useRef(new Map<string, HTMLDivElement>());
  const edgeLabelElsRef = useRef(new Map<string, HTMLDivElement>());
  const lastSpotlightKeyRef = useRef('');
  const hoverCardElRef = useRef<HTMLDivElement | null>(null);

  // Keep `latestRef` current every render — a layout effect (not a plain
  // assignment during render, which React's hook linter now flags) so it
  // commits before the other effects below read it, and before the next
  // animation frame can.
  useLayoutEffect(() => {
    latestRef.current = {
      graph,
      layout,
      visibility,
      view,
      colourBy,
      maxDegree,
      palette: latestRef.current.palette,
      nodeById,
      labelledIds,
      markerTargets,
      questionTargets,
      answerTargets,
      edgeLabelTargets,
      onSelectNode,
      onBackgroundClick,
    };
  });

  // ---- one-time setup: renderer, scene, camera, listeners, rAF loop ----
  useEffect(() => {
    if (unsupported) return undefined;
    const host = hostRef.current;
    if (!host) return undefined;

    const renderer = (createRenderer ?? createWebGLRenderer)();
    const scene = new THREE.Scene();
    const camera3 = new THREE.PerspectiveCamera(CAMERA3D.FOV, 1, 0.1, 2000);
    const rings = new THREE.Group();
    scene.add(rings);
    host.appendChild(renderer.domElement);

    threeRef.current = {
      renderer,
      scene,
      camera: camera3,
      nodePoints: null,
      nodeIdByIndex: [],
      edgeLines: null,
      dashedEdgeLines: null,
      particlePoints: null,
      rings,
    };

    if (containerRef.current) {
      latestRef.current.palette = memoryPalette(containerRef.current);
    }

    const resize = (): void => {
      const el = containerRef.current;
      const bag = threeRef.current;
      if (!el || !bag) return;
      const width = Math.max(1, el.clientWidth);
      const height = Math.max(1, el.clientHeight);
      const ratio = Math.min(window.devicePixelRatio || 1, MAX_PIXEL_RATIO);
      bag.renderer.setPixelRatio(ratio);
      bag.renderer.setSize(width, height);
      bag.camera.aspect = width / height;
      bag.camera.updateProjectionMatrix();
      const nodeMaterial = bag.nodePoints?.material as THREE.ShaderMaterial | undefined;
      if (nodeMaterial?.uniforms.uProjScale) {
        nodeMaterial.uniforms.uProjScale.value = projectionScale(height);
      }
    };
    resize();

    const observer = new ResizeObserver(() => resize());
    if (containerRef.current) observer.observe(containerRef.current);

    const pick = (clientX: number, clientY: number): string | null => {
      const rect = containerRef.current?.getBoundingClientRect();
      if (!rect) return null;
      return pickNearestNode(
        nodeScreenRef.current,
        clientX - rect.left,
        clientY - rect.top,
        PICK_RADIUS_PX,
      );
    };

    const onPointerDown = (event: PointerEvent): void => {
      pressOriginRef.current = { x: event.clientX, y: event.clientY };
      dragStartRef.current = {
        x: event.clientX,
        y: event.clientY,
        isPanning: event.shiftKey || event.button === 2,
      };
    };
    const onPointerMove = (event: PointerEvent): void => {
      const start = dragStartRef.current;
      if (start && event.buttons > 0) {
        const dx = event.clientX - start.x;
        const dy = event.clientY - start.y;
        const height = containerRef.current?.clientHeight ?? 1;
        const pan = start.isPanning || latestRef.current.view === '2d';
        cameraStateRef.current = pan
          ? panBy(cameraStateRef.current, dx, dy, height)
          : orbitBy(cameraStateRef.current, dx, dy);
        destinationRef.current = null;
        dragStartRef.current = { ...start, x: event.clientX, y: event.clientY };
        return;
      }
      const rect = containerRef.current?.getBoundingClientRect();
      if (!rect) return;
      hoverPosRef.current = { x: event.clientX - rect.left, y: event.clientY - rect.top };
      setHoveredId(pick(event.clientX, event.clientY));
    };
    const onPointerUp = (event: PointerEvent): void => {
      dragStartRef.current = null;
      const origin = pressOriginRef.current;
      pressOriginRef.current = null;
      if (!origin) return;
      if (isDragGesture(origin.x, origin.y, event.clientX, event.clientY, CLICK_DRAG_THRESHOLD_PX))
        return;
      const hit = pick(event.clientX, event.clientY);
      if (hit) latestRef.current.onSelectNode?.(hit, { shift: event.shiftKey });
      else latestRef.current.onBackgroundClick?.();
    };
    const onWheel = (event: WheelEvent): void => {
      event.preventDefault();
      cameraStateRef.current = dollyBy(cameraStateRef.current, Math.exp(event.deltaY * 0.001));
      destinationRef.current = null;
    };
    const onContextMenu = (event: MouseEvent): void => event.preventDefault();

    const el = renderer.domElement;
    el.addEventListener('pointerdown', onPointerDown);
    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', onPointerUp);
    el.addEventListener('wheel', onWheel, { passive: false });
    el.addEventListener('contextmenu', onContextMenu);

    let disposed = false;

    const renderFrame = (): void => {
      const bag = threeRef.current;
      if (!bag) return;
      const now = typeof performance !== 'undefined' ? performance.now() : Date.now();

      if (destinationRef.current) {
        const step = easeOrbitCamera(cameraStateRef.current, destinationRef.current);
        cameraStateRef.current = step.camera;
        if (step.arrived) destinationRef.current = null;
      }

      const eye = eyePosition(cameraStateRef.current);
      bag.camera.position.set(eye.x, eye.y, eye.z);
      bag.camera.lookAt(
        cameraStateRef.current.target.x,
        cameraStateRef.current.target.y,
        cameraStateRef.current.target.z,
      );

      const width = containerRef.current?.clientWidth || 1;
      const height = containerRef.current?.clientHeight || 1;

      // Node screen positions, for picking and label/marker/badge overlay placement.
      const screenPoints: ProjectedPoint[] = [];
      for (const id of bag.nodeIdByIndex) {
        const node = latestRef.current.layout.nodes.get(id);
        if (!node) continue;
        const projected = projectToScreen(bag.camera, node.position, width, height);
        if (projected.inFront) screenPoints.push({ id, x: projected.x, y: projected.y });
      }
      nodeScreenRef.current = screenPoints;
      const screenById = new Map(screenPoints.map((p) => [p.id, p]));

      // Particle animation along lit edges.
      if (bag.particlePoints) {
        const geometry = bag.particlePoints.geometry;
        const positions = geometry.getAttribute('position') as THREE.BufferAttribute;
        const segments =
          (bag.particlePoints.userData.segments as Array<{ a: Vec3; b: Vec3; phase: number }>) ??
          [];
        const t = now * PARTICLE_SPEED_PER_MS;
        segments.forEach((segment, i) => {
          const local = (t + segment.phase) % 1;
          positions.setXYZ(
            i,
            segment.a.x + (segment.b.x - segment.a.x) * local,
            segment.a.y + (segment.b.y - segment.a.y) * local,
            segment.a.z + (segment.b.z - segment.a.z) * local,
          );
        });
        positions.needsUpdate = true;
      }

      updateOverlay(screenById, width, height);

      bag.renderer.render(bag.scene, bag.camera);
    };

    const updateOverlay = (
      screenById: Map<string, ProjectedPoint>,
      width: number,
      height: number,
    ): void => {
      const {
        layout: currentLayout,
        labelledIds: currentLabelledIds,
        markerTargets: currentMarkers,
        questionTargets: currentQuestions,
        answerTargets: currentAnswers,
        edgeLabelTargets: currentEdgeLabels,
      } = latestRef.current;

      // Labels — collision-avoided screen placement for hub/lit nodes.
      const candidates: LabelCandidate[] = [];
      for (const id of currentLabelledIds) {
        const p = screenById.get(id);
        const node = currentLayout.nodes.get(id);
        if (!p || !node) continue;
        candidates.push({ id, anchorX: p.x, anchorY: p.y, priority: node.degree });
      }
      const placedLabels = layoutLabels(candidates);
      applyPlacements(labelElsRef.current, placedLabels);

      // Answer badges — anchored directly on the node.
      for (const answer of currentAnswers) {
        const p = screenById.get(answer.nodeId);
        const el = badgeElsRef.current.get(answer.nodeId);
        if (!el) continue;
        if (p) {
          el.style.display = '';
          el.style.transform = `translate(${p.x - 9}px, ${p.y - 9}px)`;
        } else {
          el.style.display = 'none';
        }
      }

      // Relation labels — at the midpoint of each lit link.
      for (const edge of currentEdgeLabels) {
        const a = screenById.get(edge.source);
        const b = screenById.get(edge.target);
        const el = edgeLabelElsRef.current.get(edge.key);
        if (!el) continue;
        if (a && b) {
          el.style.display = '';
          el.style.transform = `translate(-50%, -50%) translate(${(a.x + b.x) / 2}px, ${(a.y + b.y) / 2}px)`;
        } else {
          el.style.display = 'none';
        }
      }

      // Live-activity markers.
      for (const marker of currentMarkers) {
        const p = screenById.get(marker.nodeId);
        const el = markerElsRef.current.get(marker.id);
        if (!el) continue;
        if (p) {
          el.style.display = '';
          el.style.transform = `translate(${p.x + 10}px, ${p.y - 8}px)`;
        } else {
          el.style.display = 'none';
        }
      }

      // Question markers — near their anchor, or a deterministic free spot.
      for (const question of currentQuestions) {
        const world = questionWorldRef.current.get(question.id);
        const el = questionElsRef.current.get(question.id);
        if (!el || !world) continue;
        const projected = projectToScreen(threeRef.current!.camera, world, width, height);
        if (projected.inFront) {
          el.style.display = '';
          el.style.transform = `translate(${projected.x - 10}px, ${projected.y - 10}px)`;
        } else {
          el.style.display = 'none';
        }
      }

      // Hover card — follows the pointer, not the node (avoids jumping under the cursor).
      const card = hoverCardElRef.current;
      if (card) {
        card.style.transform = `translate(${hoverPosRef.current.x + 14}px, ${hoverPosRef.current.y + 14}px)`;
      }
    };

    const applyPlacements = (els: Map<string, HTMLDivElement>, placed: PlacedLabel[]): void => {
      for (const p of placed) {
        const el = els.get(p.id);
        if (!el) continue;
        if (p.visible) {
          el.style.display = '';
          el.style.transform = `translate(${p.x}px, ${p.y}px)`;
        } else {
          el.style.display = 'none';
        }
      }
    };

    const loop = (): void => {
      if (disposed) return;
      rafRef.current = requestAnimationFrame(loop);
      if (typeof document !== 'undefined' && document.hidden) return;
      renderFrame();
    };
    // Exposed so the content-rebuild effect (which runs after this one, and
    // is what applies the auto-fit camera move) can paint immediately
    // rather than waiting for a real animation frame — matters for tests,
    // which never advance rAF, and avoids one visibly stale frame at the
    // default camera position in a real browser too.
    renderFrameRef.current = renderFrame;
    rafRef.current = requestAnimationFrame(loop);
    renderFrame();

    return () => {
      disposed = true;
      renderFrameRef.current = null;
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
      observer.disconnect();
      el.removeEventListener('pointerdown', onPointerDown);
      window.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('pointerup', onPointerUp);
      el.removeEventListener('wheel', onWheel);
      el.removeEventListener('contextmenu', onContextMenu);
      const bag = threeRef.current;
      if (bag) disposeThreeBag(bag);
      renderer.dispose();
      if (host.contains(renderer.domElement)) host.removeChild(renderer.domElement);
      threeRef.current = null;
    };
    // Mount-only: every value this effect needs is read through `latestRef`.
  }, [unsupported, createRenderer]);

  // ---- rebuild scene content whenever layout/visibility/colour change ----
  useEffect(() => {
    const bag = threeRef.current;
    if (!bag) return;

    if (bag.nodePoints) bag.scene.remove(bag.nodePoints);
    if (bag.edgeLines) bag.scene.remove(bag.edgeLines);
    if (bag.dashedEdgeLines) bag.scene.remove(bag.dashedEdgeLines);
    if (bag.particlePoints) bag.scene.remove(bag.particlePoints);
    disposeThreeBag(bag);
    while (bag.rings.children.length > 0) bag.rings.remove(bag.rings.children[0]!);

    const palette = latestRef.current.palette;
    if (!palette) return;
    const bgColour = new THREE.Color(0x000000);
    const now = Date.now();

    const visibleIds = [...layout.nodes.keys()].filter((id) => visibility.nodes.get(id)?.visible);

    // ---- nodes: a point-sprite per visible node, glow drawn procedurally in the fragment shader ----
    const positions = new Float32Array(visibleIds.length * 3);
    const colours = new Float32Array(visibleIds.length * 3);
    const sizes = new Float32Array(visibleIds.length);
    const alphas = new Float32Array(visibleIds.length);
    const nodeIdByIndex: string[] = [];

    visibleIds.forEach((id, i) => {
      const node = layout.nodes.get(id)!;
      const graphNode = nodeById.get(id)!;
      const v = visibility.nodes.get(id)!;
      positions[i * 3] = node.position.x;
      positions[i * 3 + 1] = node.position.y;
      positions[i * 3 + 2] = node.position.z;
      const colourHex = nodeColour(graphNode, colourBy, palette, now);
      const colour = new THREE.Color(colourHex);
      colours[i * 3] = colour.r;
      colours[i * 3 + 1] = colour.g;
      colours[i * 3 + 2] = colour.b;
      sizes[i] =
        radiusForDegree(node.degree, NODE3D.MIN_RADIUS, NODE3D.MAX_RADIUS, maxDegree) *
        NODE_SPRITE_SCALE;
      alphas[i] = LIT_ALPHA[v.litLevel];
      nodeIdByIndex.push(id);

      if (v.disputed)
        addRing(bag.rings, node.position, sizes[i]! * NODE3D.DISPUTE_RING_SCALE, palette.dispute);
      if (v.bornRing)
        addRing(bag.rings, node.position, sizes[i]! * NODE3D.BORN_RING_SCALE, colourHex);
    });

    const nodeGeometry = new THREE.BufferGeometry();
    nodeGeometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    nodeGeometry.setAttribute('aColor', new THREE.BufferAttribute(colours, 3));
    nodeGeometry.setAttribute('aSize', new THREE.BufferAttribute(sizes, 1));
    nodeGeometry.setAttribute('aAlpha', new THREE.BufferAttribute(alphas, 1));

    const nodeMaterial = new THREE.ShaderMaterial({
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      vertexShader: NODE_VERTEX_SHADER,
      fragmentShader: NODE_FRAGMENT_SHADER,
      uniforms: {
        uProjScale: {
          value: projectionScale(Math.max(1, containerRef.current?.clientHeight ?? 1)),
        },
        uFogNear: { value: view === '2d' ? Number.MAX_VALUE : FOG.NEAR },
        uFogFar: { value: view === '2d' ? Number.MAX_VALUE : FOG.FAR },
      },
    });

    const nodePoints = new THREE.Points(nodeGeometry, nodeMaterial);
    bag.scene.add(nodePoints);
    bag.nodePoints = nodePoints;
    bag.nodeIdByIndex = nodeIdByIndex;

    // ---- edges: solid lines + a second dashed pass for contradictions ----
    const solidPositions: number[] = [];
    const solidColours: number[] = [];
    const dashedPositions: number[] = [];
    const dashedColours: number[] = [];
    const litSegments: Array<{ a: Vec3; b: Vec3; phase: number }> = [];

    for (const edge of visibility.edges) {
      if (!edge.visible) continue;
      const a = layout.nodes.get(edge.source);
      const b = layout.nodes.get(edge.target);
      if (!a || !b) continue;
      const alpha = edge.contradiction
        ? Math.max(EDGE_BRIGHTNESS[edge.litLevel], LIT_ALPHA['dim-soft'])
        : EDGE_BRIGHTNESS[edge.litLevel];
      const baseHex = edge.contradiction ? palette.dispute : palette.kind.topic;
      const colour = dimColour(new THREE.Color(baseHex), bgColour, alpha);
      const target = edge.contradiction ? dashedPositions : solidPositions;
      const targetColour = edge.contradiction ? dashedColours : solidColours;
      target.push(
        a.position.x,
        a.position.y,
        a.position.z,
        b.position.x,
        b.position.y,
        b.position.z,
      );
      targetColour.push(colour.r, colour.g, colour.b, colour.r, colour.g, colour.b);

      if (edge.litLevel === 'lit' && litSegments.length < MAX_PARTICLES) {
        litSegments.push({
          a: a.position,
          b: b.position,
          phase: litSegments.length / MAX_PARTICLES,
        });
      }
    }

    if (solidPositions.length > 0) {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(solidPositions, 3));
      geometry.setAttribute('color', new THREE.Float32BufferAttribute(solidColours, 3));
      const material = new THREE.LineBasicMaterial({
        vertexColors: true,
        transparent: true,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      });
      const lines = new THREE.LineSegments(geometry, material);
      bag.scene.add(lines);
      bag.edgeLines = lines;
    }

    if (dashedPositions.length > 0) {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(dashedPositions, 3));
      geometry.setAttribute('color', new THREE.Float32BufferAttribute(dashedColours, 3));
      const material = new THREE.LineDashedMaterial({
        vertexColors: true,
        transparent: true,
        dashSize: 0.6,
        gapSize: 0.4,
      });
      const lines = new THREE.LineSegments(geometry, material);
      lines.computeLineDistances();
      bag.scene.add(lines);
      bag.dashedEdgeLines = lines;
    }

    if (litSegments.length > 0) {
      const particleGeometry = new THREE.BufferGeometry();
      particleGeometry.setAttribute(
        'position',
        new THREE.BufferAttribute(new Float32Array(litSegments.length * 3), 3),
      );
      const particleMaterial = new THREE.PointsMaterial({
        size: 4,
        sizeAttenuation: false,
        color: new THREE.Color(palette.kind.topic),
        transparent: true,
        opacity: 0.9,
      });
      const particlePoints = new THREE.Points(particleGeometry, particleMaterial);
      particlePoints.userData.segments = litSegments;
      bag.scene.add(particlePoints);
      bag.particlePoints = particlePoints;
    }

    // ---- question markers: a deterministic world position per question ----
    const questionWorld = new Map<string, Vec3>();
    for (const question of questionTargets) {
      const anchor = question.nearNodeId ? layout.nodes.get(question.nearNodeId)?.position : null;
      questionWorld.set(
        question.id,
        anchor
          ? { x: anchor.x + 1.2, y: anchor.y + 1.2, z: anchor.z }
          : freeQuestionSpot(question.id, 12),
      );
    }
    questionWorldRef.current = questionWorld;

    // ---- auto-fit on the very first graph the scene ever renders ----
    if (!hasAutoFitRef.current) {
      hasAutoFitRef.current = true;
      const points = layoutPoints(layout);
      let fitted = fitOrbitCamera(points, bag.camera.aspect || 1, cameraStateRef.current);
      if (view === '2d') fitted = lockTo2D(fitted);
      cameraStateRef.current = fitted;
    }

    // Paint immediately with the content/camera state just computed, rather
    // than waiting for the next animation frame.
    renderFrameRef.current?.();
  }, [layout, visibility, colourBy, view, nodeById, maxDegree, questionTargets]);

  // ---- explicit camera commands from the caller ----
  useEffect(() => {
    if (!camera || camera.key === lastCameraKeyRef.current) return;
    const bag = threeRef.current;
    if (!bag) return;
    lastCameraKeyRef.current = camera.key;
    const points =
      camera.kind === 'fly-to'
        ? camera.nodeIds
            .map((id) => layout.nodes.get(id)?.position)
            .filter((p): p is Vec3 => Boolean(p))
        : layoutPoints(layout);
    let destination = fitOrbitCamera(points, bag.camera.aspect || 1, cameraStateRef.current);
    if (view === '2d') destination = lockTo2D(destination);
    destinationRef.current = destination;
  }, [camera, layout, view]);

  // ---- a spotlight (focus, answers, traced path) frames the pages it lights ----
  useEffect(() => {
    if (spotlightKey === lastSpotlightKeyRef.current) return;
    const bag = threeRef.current;
    if (!bag) return;
    const hadSpotlight = lastSpotlightKeyRef.current !== '';
    lastSpotlightKeyRef.current = spotlightKey;
    // Leaving a spotlight goes back to the whole of memory; entering one flies in.
    if (spotlightKey === '' && !hadSpotlight) return;
    const points =
      spotlightKey === ''
        ? layoutPoints(layout)
        : spotlightKey
            .split('\n')
            .map((id) => layout.nodes.get(id)?.position)
            .filter((p): p is Vec3 => Boolean(p));
    let destination = fitOrbitCamera(points, bag.camera.aspect || 1, cameraStateRef.current);
    if (view === '2d') destination = lockTo2D(destination);
    destinationRef.current = destination;
  }, [spotlightKey, layout, view]);

  if (unsupported) {
    return (
      <div
        ref={containerRef}
        className="niuu-memory-fallback"
        role="img"
        aria-label="Memory scene unavailable: this browser does not support WebGL"
      >
        This browser can&apos;t render the 3D memory view (no WebGL support). Open Mímir in a
        browser with WebGL enabled; Find a page and Ask still work from the panels.
      </div>
    );
  }

  const ariaLabel = `Memory: ${visibleNodeCount.toLocaleString()} pages, ${visibleEdgeCount.toLocaleString()} links`;
  const hoveredNode = hoveredId ? nodeById.get(hoveredId) : null;
  const hoveredLayoutNode = hoveredId ? layout.nodes.get(hoveredId) : null;

  return (
    <div ref={containerRef} className="niuu-memory-scene" role="img" aria-label={ariaLabel}>
      <div
        ref={hostRef}
        className="niuu-memory-scene-canvas-host"
        data-testid="memory-scene-canvas-host"
      />

      <div className="niuu-memory-overlay">
        {[...labelledIds].map((id) => {
          const node = nodeById.get(id);
          if (!node) return null;
          return (
            <div
              key={id}
              ref={(el) => {
                if (el) labelElsRef.current.set(id, el);
                else labelElsRef.current.delete(id);
              }}
              className="niuu-memory-label"
              data-testid={`memory-label-${id}`}
            >
              {node.title}
            </div>
          );
        })}

        {answerTargets.map((answer) => (
          <div
            key={answer.nodeId}
            ref={(el) => {
              if (el) badgeElsRef.current.set(answer.nodeId, el);
              else badgeElsRef.current.delete(answer.nodeId);
            }}
            className="niuu-memory-badge"
            data-testid={`memory-answer-${answer.nodeId}`}
          >
            {answer.n}
          </div>
        ))}

        {edgeLabelTargets.map((edge) => (
          <div
            key={edge.key}
            ref={(el) => {
              if (el) edgeLabelElsRef.current.set(edge.key, el);
              else edgeLabelElsRef.current.delete(edge.key);
            }}
            className="niuu-memory-relation"
            data-testid={`memory-relation-${edge.key}`}
          >
            {edge.label}
          </div>
        ))}

        {markerTargets.map((marker) => (
          <div
            key={marker.id}
            ref={(el) => {
              if (el) markerElsRef.current.set(marker.id, el);
              else markerElsRef.current.delete(marker.id);
            }}
            className={`niuu-memory-marker niuu-memory-marker--${marker.kind}`}
            data-testid={`memory-marker-${marker.id}`}
          >
            {marker.kind === 'read'
              ? `${marker.actor ?? 'Someone'} is reading this`
              : `${marker.actor ?? 'Someone'} wrote here · ${relTime(marker.timestamp)}`}
          </div>
        ))}

        {questionTargets.map((question) => (
          <div
            key={question.id}
            ref={(el) => {
              if (el) questionElsRef.current.set(question.id, el);
              else questionElsRef.current.delete(question.id);
            }}
            className="niuu-memory-question"
            data-testid={`memory-question-${question.id}`}
          >
            ? {question.label}
          </div>
        ))}

        {hoveredId && hoveredNode ? (
          <div
            ref={hoverCardElRef}
            className="niuu-memory-hover-card"
            data-testid="memory-hover-card"
          >
            <div className="niuu-memory-hover-card-title">{hoveredNode.title}</div>
            <div className="niuu-memory-hover-card-meta">
              {hoveredNode.kind ?? hoveredNode.category} · {hoveredNode.mount} ·{' '}
              {hoveredLayoutNode?.degree ?? 0} links · click to focus
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function addRing(group: THREE.Group, centre: Vec3, radiusPx: number, colourHex: string): void {
  const radius = Math.max(0.4, radiusPx / 6);
  const points: THREE.Vector3[] = [];
  const segments = 32;
  for (let i = 0; i <= segments; i += 1) {
    const angle = (i / segments) * Math.PI * 2;
    points.push(
      new THREE.Vector3(
        centre.x + Math.cos(angle) * radius,
        centre.y,
        centre.z + Math.sin(angle) * radius,
      ),
    );
  }
  const geometry = new THREE.BufferGeometry().setFromPoints(points);
  const material = new THREE.LineBasicMaterial({
    color: new THREE.Color(colourHex),
    transparent: true,
    opacity: 0.8,
  });
  group.add(new THREE.LineLoop(geometry, material));
}

const NODE_VERTEX_SHADER = `
  attribute float aSize;
  attribute float aAlpha;
  attribute vec3 aColor;
  varying vec3 vColor;
  varying float vAlpha;
  uniform float uProjScale;
  uniform float uFogNear;
  uniform float uFogFar;
  void main() {
    vColor = aColor;
    vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
    // Depth fog: far spheres fade toward the backdrop, never quite vanishing.
    float fog = 1.0 - smoothstep(uFogNear, uFogFar, -mvPosition.z);
    vAlpha = aAlpha * max(0.18, fog);
    gl_PointSize = aSize * uProjScale / max(0.001, -mvPosition.z);
    gl_Position = projectionMatrix * mvPosition;
  }
`;

const NODE_FRAGMENT_SHADER = `
  varying vec3 vColor;
  varying float vAlpha;
  void main() {
    float d = length(gl_PointCoord - vec2(0.5));
    if (d > 0.5) discard;
    // A lit sphere: a solid core with a pale highlight, inside a soft halo.
    float core = smoothstep(0.2, 0.14, d);
    float highlight = smoothstep(0.1, 0.0, length(gl_PointCoord - vec2(0.44, 0.42)));
    float halo = pow(1.0 - smoothstep(0.0, 0.5, d), 2.2) * 0.55;
    vec3 colour = mix(vColor, vec3(1.0), highlight * 0.55);
    gl_FragColor = vec4(colour, (core + halo) * vAlpha);
  }
`;
