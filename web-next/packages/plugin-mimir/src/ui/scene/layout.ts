/**
 * Deterministic 3D layout for the memory scene.
 *
 * Nodes cluster by mount: each mount gets a centre spread out in 3D, pages
 * settle around their own mount's centre, and cross-mount links pull their
 * two clusters loosely together through the same spring forces that hold
 * same-mount pages near each other. Repulsion uses a spatial hash so the
 * simulation stays roughly O(n) per iteration instead of O(n²) — this is
 * what keeps 5,000 nodes / 15,000 edges under the layout's performance
 * budget (see `layout.test.ts`).
 *
 * Pure and deterministic: the same graph (by content, not just reference)
 * always settles into the same positions. `computeLayout` additionally
 * memoises by graph *identity* (the object reference), since recomputing a
 * multi-thousand-node force simulation on every render would be wasteful
 * when the graph prop hasn't actually changed.
 */

import type { GraphNode, MimirGraph } from '../../domain/api-types';
import { LAYOUT } from './scene3dConfig';
import { add, scale, subtract, type Vec3 } from './vec3';

export interface NodeLayout {
  id: string;
  position: Vec3;
  /** Total in+out edge count — drives node radius and hub-label selection. */
  degree: number;
  mount: string;
}

export interface SceneLayout {
  nodes: Map<string, NodeLayout>;
  mountCentres: Map<string, Vec3>;
}

// ---------------------------------------------------------------------------
// Deterministic PRNG (same algorithm as the mock adapter's — kept local so
// this module has no dependency on adapter code).
// ---------------------------------------------------------------------------

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return function next(): number {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Small, fast string hash (FNV-1a) — turns a node id into a stable PRNG seed. */
function hashString(value: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < value.length; i += 1) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return hash >>> 0;
}

/**
 * A node's mount, for clustering purposes.
 *
 * Prefers the explicit `mount` field the adapters now populate; falls back
 * to a mount-qualified id's prefix for nodes that predate it, and finally
 * to a shared "default" bucket so an id/mount-less node still lays out
 * instead of throwing.
 */
export function mountOf(node: Pick<GraphNode, 'id' | 'mount'>): string {
  if (node.mount) return node.mount;
  const colon = node.id.indexOf(':');
  return colon === -1 ? 'default' : node.id.slice(0, colon);
}

/** A point on a Fibonacci sphere — an even 3D spread for any number of mounts. */
function fibonacciSpherePoint(index: number, count: number, radius: number): Vec3 {
  if (count <= 1) return { x: 0, y: 0, z: 0 };
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  const y = 1 - (index / (count - 1)) * 2;
  const r = Math.sqrt(Math.max(0, 1 - y * y));
  const theta = goldenAngle * index;
  return {
    x: Math.cos(theta) * r * radius,
    y: y * radius * 0.6,
    z: Math.sin(theta) * r * radius,
  };
}

// ---------------------------------------------------------------------------
// Spatial hash — O(1) average insert/lookup for "nodes near this point".
// ---------------------------------------------------------------------------

// Cell coordinates are packed into a single integer key (10 bits per axis,
// offset to stay non-negative) rather than a template-literal string: at
// thousands of nodes over dozens of iterations, string allocation for every
// cell lookup dominates the cost of the whole simulation.
const CELL_AXIS_BITS = 10;
const CELL_AXIS_OFFSET = 1 << (CELL_AXIS_BITS - 1);
const CELL_AXIS_MASK = (1 << CELL_AXIS_BITS) - 1;

function packCell(cx: number, cy: number, cz: number): number {
  const px = (cx + CELL_AXIS_OFFSET) & CELL_AXIS_MASK;
  const py = (cy + CELL_AXIS_OFFSET) & CELL_AXIS_MASK;
  const pz = (cz + CELL_AXIS_OFFSET) & CELL_AXIS_MASK;
  return (px << (CELL_AXIS_BITS * 2)) | (py << CELL_AXIS_BITS) | pz;
}

function buildSpatialHash(
  positions: Float64Array,
  count: number,
  cellSize: number,
): Map<number, number[]> {
  const grid = new Map<number, number[]>();
  const inv = 1 / cellSize;
  for (let i = 0; i < count; i += 1) {
    const cx = Math.floor(positions[i * 3]! * inv);
    const cy = Math.floor(positions[i * 3 + 1]! * inv);
    const cz = Math.floor(positions[i * 3 + 2]! * inv);
    const key = packCell(cx, cy, cz);
    const bucket = grid.get(key);
    if (bucket) bucket.push(i);
    else grid.set(key, [i]);
  }
  return grid;
}

function forEachNearby(
  grid: Map<number, number[]>,
  x: number,
  y: number,
  z: number,
  cellSize: number,
  visit: (otherIndex: number) => void,
): void {
  const inv = 1 / cellSize;
  const cx = Math.floor(x * inv);
  const cy = Math.floor(y * inv);
  const cz = Math.floor(z * inv);
  for (let dx = -1; dx <= 1; dx += 1) {
    for (let dy = -1; dy <= 1; dy += 1) {
      for (let dz = -1; dz <= 1; dz += 1) {
        const bucket = grid.get(packCell(cx + dx, cy + dy, cz + dz));
        if (!bucket) continue;
        for (const index of bucket) visit(index);
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Layout computation
// ---------------------------------------------------------------------------

/** Compute the layout, ignoring the identity memo cache. Exported for tests/benchmarks. */
export function computeLayoutUncached(graph: MimirGraph): SceneLayout {
  const nodeCount = graph.nodes.length;
  const mounts = [...new Set(graph.nodes.map((n) => mountOf(n)))].sort();
  const mountCentres = new Map(
    mounts.map((m, i) => [m, fibonacciSpherePoint(i, mounts.length, LAYOUT.MOUNT_RING_RADIUS)]),
  );

  // Degree (in + out), for radius and hub-label selection downstream.
  const degree = new Map<string, number>();
  for (const edge of graph.edges) {
    degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
  }

  // Flat typed arrays for the hot loop.
  const positions = new Float64Array(nodeCount * 3);
  const velocities = new Float64Array(nodeCount * 3);
  const nodeMount = new Array<string>(nodeCount);
  const indexById = new Map<string, number>();

  // Cluster sizes, computed once — used below so a big mount spreads wider
  // than a small one without an O(n²) filter per node.
  const clusterSize = new Map<string, number>();
  for (const node of graph.nodes) {
    const mount = mountOf(node);
    clusterSize.set(mount, (clusterSize.get(mount) ?? 0) + 1);
  }

  graph.nodes.forEach((node, i) => {
    indexById.set(node.id, i);
    const mount = mountOf(node);
    nodeMount[i] = mount;
    const centre = mountCentres.get(mount) ?? { x: 0, y: 0, z: 0 };
    const rng = mulberry32(hashString(node.id));
    // Spread proportional to cluster size, so a big mount doesn't collapse
    // into a single dense ball while a small one drifts unnecessarily wide.
    const spread = Math.max(4, Math.sqrt(clusterSize.get(mount) ?? 1) * LAYOUT.CELL_SIZE * 0.6);
    positions[i * 3] = centre.x + (rng() - 0.5) * spread;
    positions[i * 3 + 1] = centre.y + (rng() - 0.5) * spread;
    positions[i * 3 + 2] = centre.z + (rng() - 0.5) * spread;
  });

  // Edge endpoints as index pairs (skip edges to unknown nodes once, up front).
  const edgeIndexPairs: Array<[number, number]> = [];
  for (const edge of graph.edges) {
    const a = indexById.get(edge.source);
    const b = indexById.get(edge.target);
    if (a !== undefined && b !== undefined && a !== b) edgeIndexPairs.push([a, b]);
  }

  const cellSize = LAYOUT.CELL_SIZE;
  // Allocated once and cleared each iteration — 120 fresh Float64Arrays for
  // a 5,000-node graph was measurable GC pressure on its own.
  const forces = new Float64Array(nodeCount * 3);

  for (let iteration = 0; iteration < LAYOUT.ITERATIONS; iteration += 1) {
    forces.fill(0);
    const grid = buildSpatialHash(positions, nodeCount, cellSize);

    // Repulsion — spatial-hash approximated: each node only checks the up to
    // 27 nearby cells instead of every other node.
    for (let i = 0; i < nodeCount; i += 1) {
      const xi = positions[i * 3]!;
      const yi = positions[i * 3 + 1]!;
      const zi = positions[i * 3 + 2]!;
      forEachNearby(grid, xi, yi, zi, cellSize, (j) => {
        if (j === i) return;
        const dx = xi - positions[j * 3]!;
        const dy = yi - positions[j * 3 + 1]!;
        const dz = zi - positions[j * 3 + 2]!;
        const distSq = dx * dx + dy * dy + dz * dz + 0.01;
        const force = LAYOUT.REPULSION / distSq;
        const dist = Math.sqrt(distSq);
        forces[i * 3]! += (dx / dist) * force;
        forces[i * 3 + 1]! += (dy / dist) * force;
        forces[i * 3 + 2]! += (dz / dist) * force;
      });
    }

    // Springs — pull linked nodes toward SPRING_LENGTH apart.
    for (const [a, b] of edgeIndexPairs) {
      const dx = positions[b * 3]! - positions[a * 3]!;
      const dy = positions[b * 3 + 1]! - positions[a * 3 + 1]!;
      const dz = positions[b * 3 + 2]! - positions[a * 3 + 2]!;
      const dist = Math.sqrt(dx * dx + dy * dy + dz * dz) + 1e-6;
      const stretch = dist - LAYOUT.SPRING_LENGTH;
      const force = LAYOUT.SPRING_STRENGTH * stretch;
      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;
      const fz = (dz / dist) * force;
      forces[a * 3]! += fx;
      forces[a * 3 + 1]! += fy;
      forces[a * 3 + 2]! += fz;
      forces[b * 3]! -= fx;
      forces[b * 3 + 1]! -= fy;
      forces[b * 3 + 2]! -= fz;
    }

    // Gentle pull back toward the node's own mount centre.
    for (let i = 0; i < nodeCount; i += 1) {
      const centre = mountCentres.get(nodeMount[i]!)!;
      forces[i * 3]! += (centre.x - positions[i * 3]!) * LAYOUT.MOUNT_PULL;
      forces[i * 3 + 1]! += (centre.y - positions[i * 3 + 1]!) * LAYOUT.MOUNT_PULL;
      forces[i * 3 + 2]! += (centre.z - positions[i * 3 + 2]!) * LAYOUT.MOUNT_PULL;
    }

    // Integrate with damping and a max-step clamp for stability.
    for (let i = 0; i < nodeCount; i += 1) {
      for (let axis = 0; axis < 3; axis += 1) {
        const idx = i * 3 + axis;
        const v = (velocities[idx]! + forces[idx]!) * LAYOUT.DAMPING;
        const clamped = Math.max(-LAYOUT.MAX_STEP, Math.min(LAYOUT.MAX_STEP, v));
        velocities[idx] = clamped;
        positions[idx]! += clamped;
      }
    }
  }

  const nodes = new Map<string, NodeLayout>();
  graph.nodes.forEach((node, i) => {
    nodes.set(node.id, {
      id: node.id,
      position: { x: positions[i * 3]!, y: positions[i * 3 + 1]!, z: positions[i * 3 + 2]! },
      degree: degree.get(node.id) ?? 0,
      mount: nodeMount[i]!,
    });
  });

  return { nodes, mountCentres };
}

const layoutCache = new WeakMap<MimirGraph, SceneLayout>();

/** Compute the layout, memoised per graph object identity. */
export function computeLayout(graph: MimirGraph): SceneLayout {
  const cached = layoutCache.get(graph);
  if (cached) return cached;
  const layout = computeLayoutUncached(graph);
  layoutCache.set(graph, layout);
  return layout;
}

/** The same layout, flattened onto the x/z plane for the 2D view (y set to 0). */
export function flattenTo2D(layout: SceneLayout): SceneLayout {
  const nodes = new Map<string, NodeLayout>();
  for (const [id, node] of layout.nodes) {
    nodes.set(id, { ...node, position: { x: node.position.x, y: 0, z: node.position.z } });
  }
  const mountCentres = new Map<string, Vec3>();
  for (const [mount, centre] of layout.mountCentres) {
    mountCentres.set(mount, { x: centre.x, y: 0, z: centre.z });
  }
  return { nodes, mountCentres };
}

/** All node positions as a flat array — a convenience for camera-fit callers. */
export function layoutPoints(layout: SceneLayout): Vec3[] {
  return [...layout.nodes.values()].map((n) => n.position);
}

/** Node radius by degree, linearly interpolated between the configured min/max. */
export function radiusForDegree(
  degree: number,
  minRadius: number,
  maxRadius: number,
  maxDegree: number,
): number {
  if (maxDegree <= 0) return minRadius;
  const t = Math.max(0, Math.min(1, degree / maxDegree));
  return minRadius + (maxRadius - minRadius) * t;
}

// Re-exported so callers doing vector math on layout output don't need a
// second import just for `add`/`subtract`/`scale`.
export { add, subtract, scale };
