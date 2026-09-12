import type { GraphNode, MimirGraph } from './api-types';

export const SVG_W = 1100;
export const SVG_H = 750;

// Node radius scaling — radius grows with edge count, capped.
const NODE_RADIUS_MIN = 4;
const NODE_RADIUS_MAX = 10;
const NODE_RADIUS_PER_EDGE = 1.2;

// Zoom limits as multiples of the base viewBox width.
const ZOOM_MIN_SCALE = 1 / 8;
const ZOOM_MAX_SCALE = 4;

// ---------------------------------------------------------------------------
// SVG layout
// ---------------------------------------------------------------------------

export interface NodePosition {
  node: GraphNode;
  x: number;
  y: number;
  z: number;
}

// ponytail: pairwise repulsion is quadratic; move to an indexed worker layout for large corpora.
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
  if (nodes.length === 1) return [{ node: nodes[0]!, x: cx, y: cy, z: 0 }];

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

  const zs = nodes.map((n) => initRadius * (hash01(`${n.id}:z`) - 0.5));

  const index = new Map(nodes.map((n, i) => [n.id, i]));
  const springs = edges
    .map((e) => [index.get(e.source), index.get(e.target)] as const)
    .filter((pair): pair is [number, number] => pair[0] !== undefined && pair[1] !== undefined);

  let step = INITIAL_STEP;
  for (let iter = 0; iter < LAYOUT_ITERATIONS; iter++) {
    const fx = new Array<number>(nodes.length).fill(0);
    const fy = new Array<number>(nodes.length).fill(0);
    const fz = new Array<number>(nodes.length).fill(0);

    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        let dx = xs[i]! - xs[j]!;
        let dy = ys[i]! - ys[j]!;
        const dz = zs[i]! - zs[j]!;
        let d2 = dx * dx + dy * dy + dz * dz;
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
        fz[i]! += (dz / d) * f;
        fz[j]! -= (dz / d) * f;
      }
    }

    for (const [a, b] of springs) {
      const dx = xs[b]! - xs[a]!;
      const dy = ys[b]! - ys[a]!;
      const dz = zs[b]! - zs[a]!;
      const d = Math.max(1, Math.sqrt(dx * dx + dy * dy + dz * dz));
      const f = SPRING_K * (d - SPRING_LENGTH);
      fx[a]! += (dx / d) * f * d;
      fy[a]! += (dy / d) * f * d;
      fx[b]! -= (dx / d) * f * d;
      fy[b]! -= (dy / d) * f * d;
      fz[a]! += (dz / d) * f * d;
      fz[b]! -= (dz / d) * f * d;
    }

    for (let i = 0; i < nodes.length; i++) {
      fx[i]! += (cx - xs[i]!) * GRAVITY;
      fy[i]! += (cy - ys[i]!) * GRAVITY;
      fz[i]! -= zs[i]! * GRAVITY;
      const mag = Math.hypot(fx[i]!, fy[i]!, fz[i]!) || 1;
      const clamp = Math.min(step, mag);
      xs[i] = xs[i]! + (fx[i]! / mag) * clamp;
      ys[i] = ys[i]! + (fy[i]! / mag) * clamp;
      zs[i] = zs[i]! + (fz[i]! / mag) * clamp;
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
  const scale = Math.min(
    (width - 2 * LAYOUT_PADDING) / spanX,
    (height - 2 * LAYOUT_PADDING) / spanY,
  );

  return nodes.map((node, i) => ({
    node,
    x: LAYOUT_PADDING + (xs[i]! - minX) * scale + (width - 2 * LAYOUT_PADDING - spanX * scale) / 2,
    z: zs[i]! * scale,
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

export const BASE_VIEWBOX: ViewBox = { x: 0, y: 0, w: SVG_W, h: SVG_H };

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

const CAMERA_DISTANCE = 1800;
export function projectPosition(
  p: NodePosition,
  yaw: number,
  pitch: number,
  view: ViewBox = BASE_VIEWBOX,
) {
  const x = p.x - SVG_W / 2;
  const y = p.y - SVG_H / 2;
  const rx = x * Math.cos(yaw) + p.z * Math.sin(yaw);
  const rz = -x * Math.sin(yaw) + p.z * Math.cos(yaw);
  const ry = y * Math.cos(pitch) - rz * Math.sin(pitch);
  const depth = y * Math.sin(pitch) + rz * Math.cos(pitch);
  // Zoom dollies the camera into the scene rather than only magnifying the SVG.
  const cameraDistance = (CAMERA_DISTANCE * view.w) / SVG_W;
  const scale = cameraDistance / Math.max(100, cameraDistance - depth);
  return { ...p, x: SVG_W / 2 + rx * scale, y: SVG_H / 2 + ry * scale, depth, scale };
}

/** Fit visible projected nodes with enough room for labels. */
export function fitViewBox(points: { x: number; y: number }[]): ViewBox {
  if (!points.length) return BASE_VIEWBOX;
  const padding = LAYOUT_PADDING * 2;
  const minWidth = SVG_W / 3;
  const xs = points.map((p) => p.x),
    ys = points.map((p) => p.y);
  const minX = Math.min(...xs),
    maxX = Math.max(...xs);
  const minY = Math.min(...ys),
    maxY = Math.max(...ys);
  const w = Math.max(minWidth, maxX - minX + padding, ((maxY - minY + padding) * SVG_W) / SVG_H);
  const h = (w * SVG_H) / SVG_W;
  return { x: (minX + maxX - w) / 2, y: (minY + maxY - h) / 2, w, h };
}

/** Clear a viewing corridor as zoom brings foreground nodes close to the viewer.
 * Only projection changes: stored positions and relationships stay intact.
 */
export function clearForeground(p: ReturnType<typeof projectPosition>, view: ViewBox) {
  const cameraDistance = (CAMERA_DISTANCE * view.w) / SVG_W;
  const gap = cameraDistance - p.depth;
  // A camera-relative near zone works on either side of the world origin.
  const proximity = Math.min(1, Math.max(0, (700 - gap) / 500));
  if (!proximity) return p;
  const dx = p.x - (view.x + view.w / 2);
  const dy = p.y - (view.y + view.h / 2);
  const distance = Math.hypot(dx, dy);
  const corridor = view.w * 0.36;
  const overlap = Math.max(0, 1 - distance / corridor);
  const strength = proximity * proximity * (3 - 2 * proximity);
  const shift = corridor * overlap * strength;
  const angle = distance > 0.001 ? Math.atan2(dy, dx) : hash01(p.node.id) * Math.PI * 2;
  return { ...p, x: p.x + Math.cos(angle) * shift, y: p.y + Math.sin(angle) * shift };
}
