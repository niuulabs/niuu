/**
 * The 3D view's description of the estate, as plain data.
 *
 * This is the whole translation from "what the topology is" to "what stands
 * where in the model": the 2D layout supplies the plan, `elevationFor`
 * supplies the deck, and everything downstream — geometry, materials, picking
 * — reads these records rather than the topology.
 *
 * Keeping it pure is what lets the interesting parts be tested: an arc that
 * dives through the floor or a realm plate that does not contain its clusters
 * is a wrong number in here, and a wrong number in here is a failing
 * assertion, not something you have to catch by eye in a spinning canvas.
 */

import type { EdgeLayer, Topology, TopologyEdge, TopologyNode } from '../../domain';
import { edgeLayer } from '../../domain';
import type { ComputeClass } from '../../domain/computeClass';
import { humanizeObservatoryText } from '../displayLabels';
import type { NodePosition } from '../TopologyCanvas/layoutEngine';
import type { NodeStyle, Rgb } from '../TopologyCanvas/nodeStyle';
import {
  LAYER_COLOUR,
  OUTSIDE_COLOUR,
  edgeRelationLane,
  nodeDetailLine,
  labelTier,
  structureLabel,
} from '../TopologyCanvas/renderer';
import { elevationFor, isContainerType } from './elevation';
import { nodeFormOf, type NodeForm } from './nodeForm';
import { EDGE3D, NODE3D, RISER, TIER, ZONE3D } from './scene3dConfig';
import {
  boundsCentre,
  distance as distanceBetween,
  emptyBounds,
  growBounds,
  isEmptyBounds,
  normalize,
  quadraticBezier,
  subtract,
  type Bounds3,
  type Vec3,
} from './vec3';

// ── Records ───────────────────────────────────────────────────────────────────

export interface Node3D {
  id: string;
  typeId: string;
  /** Registry shape, so the model uses the same vocabulary the plan does. */
  shape: string;
  position: Vec3;
  /** Half-extent of the solid drawn for this node, in world units. */
  radius: number;
  colour: Rgb;
  computeClass: ComputeClass;
  label: string;
  /** Second line, where the adapters supplied one. */
  detail: string;
  labelTier: 'primary' | 'secondary';
  /**
   * True for the types the plan draws as a boundary rather than as a mark —
   * realms, clusters, namespaces, clouds. They get a deck and nothing else:
   * a solid at the centre of a region would be a second mark for one thing,
   * standing in the middle of its own contents.
   */
  isBoundary: boolean;
  /**
   * True for hosts and workflow sessions, which are both a place and a thing.
   * They keep their body, drawn see-through, because an opaque one hides the
   * agents it is there to show you.
   */
  isContainer: boolean;
  /** Which body this node is built from. */
  form: NodeForm;
  /**
   * The flock this agent peers in, when it is in one.
   *
   * Worn as a collar, so "these agents talk directly to each other" is visible
   * without having to select one of them and watch what lights up.
   */
  meshId: string | null;
}

/**
 * The regions that get a shell.
 *
 * Hosts and workflow sessions are containers too, but they are also objects:
 * they draw a body of their own, and the risers already tie their contents
 * down to them. Wrapping each of them in a shell as well put a second surface
 * around every rack in the estate and turned the inside of a cluster into a
 * thicket of overlapping outlines.
 */
export type Zone3DKind = 'realm' | 'cloud' | 'cluster' | 'namespace';

/** The solid a region is drawn as. */
export type Zone3DShape = 'box' | 'sphere';

/**
 * A region, drawn as the volume it encloses.
 *
 * A plate under a region's contents only says "these things sit above this
 * patch of floor", and from a raking angle it does not even say that — the
 * plate is edge-on and the contents float free of it. A volume that actually
 * wraps its contents says "these, and only these, are inside", which is the
 * one claim a container exists to make.
 */
export interface Zone3D {
  id: string;
  kind: Zone3DKind;
  shape: Zone3DShape;
  /** Centre of the volume. */
  position: Vec3;
  /** Half-extent on each axis. All three are equal for a sphere. */
  half: Vec3;
  label: string;
  colour: Rgb;
  fillAlpha: number;
  edgeAlpha: number;
}

export interface Edge3D {
  id: string;
  sourceId: string;
  targetId: string;
  layer: EdgeLayer;
  colour: Rgb;
  /** The arc, already sampled. */
  points: Vec3[];
  /** Messages a minute, when something measured it. */
  ratePerMinute: number;
}

/** The line that ties a node down to the deck its parent stands on. */
export interface Riser3D {
  nodeId: string;
  from: Vec3;
  to: Vec3;
}

export interface Scene3DModel {
  nodes: Node3D[];
  nodeById: Map<string, Node3D>;
  zones: Zone3D[];
  edges: Edge3D[];
  risers: Riser3D[];
  /**
   * The points the camera has to keep in frame: every node's extremes and
   * every plate's corners.
   *
   * A point set rather than a bounding box, because the box's corners include
   * a great deal of empty sky — the top of the deck stack out at the far edge
   * of the widest realm — and a camera framed to contain that lands much
   * further back than one framed to contain the estate.
   */
  framePoints: Vec3[];
}

/**
 * Region colours, taken from the compute ramp the plan already uses.
 *
 * Saturated, not the near-white the plan draws its thin outlines in. A hairline
 * an inch long can be almost white and still read as blue; a frame the size of
 * a realm, at that hue, is simply grey — and enough grey surfaces make the
 * whole picture grey.
 */
const PLATE_COLOUR: Readonly<Record<Zone3DKind, Rgb>> = {
  realm: [56, 189, 248],
  cloud: [...OUTSIDE_COLOUR] as Rgb,
  cluster: [86, 200, 255],
  namespace: [125, 211, 252],
};

const PLATE_ALPHA: Readonly<Record<Zone3DKind, { fill: number; edge: number }>> = {
  realm: { fill: ZONE3D.REALM_FILL_ALPHA, edge: ZONE3D.REALM_EDGE_ALPHA },
  cloud: { fill: ZONE3D.CLOUD_FILL_ALPHA, edge: ZONE3D.CLOUD_EDGE_ALPHA },
  cluster: { fill: ZONE3D.CLUSTER_FILL_ALPHA, edge: ZONE3D.CLUSTER_EDGE_ALPHA },
  namespace: { fill: ZONE3D.NAMESPACE_FILL_ALPHA, edge: ZONE3D.NAMESPACE_EDGE_ALPHA },
};

/**
 * What each region is drawn as.
 *
 * Follows the plan's vocabulary: what a realm or a namespace draws as a
 * rectangle becomes a box, and what a cluster or a cloud draws as a circle
 * becomes a sphere. An operator who has learned that boxes are addressable
 * regions and circles are pools of compute should not have to learn it twice.
 */
const ZONE_SHAPE: Readonly<Record<Zone3DKind, Zone3DShape>> = {
  realm: 'box',
  namespace: 'box',
  cluster: 'sphere',
  cloud: 'sphere',
};

/** The types drawn as a region rather than as an object standing in one. */
const BOUNDARY_TYPES: ReadonlySet<string> = new Set(['realm', 'cloud', 'cluster', 'namespace']);

// ── Nodes ─────────────────────────────────────────────────────────────────────

/**
 * How big a node draws in the model.
 *
 * Scaled up from the plan's glyph radius because a 4-unit dot that reads fine
 * as a mark on paper is invisible as a solid seen at an angle from across the
 * estate, and clamped from below for the same reason.
 */
export function node3dRadius(style: NodeStyle): number {
  return Math.max(style.radius * NODE3D.SCALE, NODE3D.MIN_RADIUS);
}

export function buildNode3D(node: TopologyNode, position: NodePosition, style: NodeStyle): Node3D {
  return {
    id: node.id,
    typeId: node.typeId,
    shape: style.shape,
    // The plan's y is a ground-plane coordinate; height is a separate axis.
    position: { x: position.x, y: elevationFor(node), z: position.y },
    radius: node3dRadius(style),
    colour: style.colour,
    computeClass: style.computeClass,
    label: humanizeObservatoryText(node.label),
    detail: nodeDetailLine(node),
    labelTier: labelTier(node.typeId),
    isBoundary: BOUNDARY_TYPES.has(node.typeId),
    isContainer: node.typeId === 'host' || node.typeId === 'run',
    form: nodeFormOf(node),
    meshId: node.flockId ?? null,
  };
}

// ── Zones ─────────────────────────────────────────────────────────────────────

/**
 * The space a region has to enclose: everything inside it, at every depth.
 *
 * Computed from what is actually placed rather than from the layout's packed
 * radius, because the packed radius is a plan measurement — it knows how wide
 * a cluster's contents are and nothing about the fact that they climb four
 * decks above it. A volume sized from it would cut straight through its own
 * agents.
 *
 * Nested regions contribute their finished volume rather than their raw
 * contents, so a realm encloses its clusters' spheres and not merely the
 * points inside them.
 */
export function enclosedExtent(
  node: TopologyNode,
  childrenByParent: Map<string, TopologyNode[]>,
  nodeById: Map<string, Node3D>,
  volumes: Map<string, Zone3D>,
): { bounds: Bounds3; points: Vec3[] } | null {
  const bounds = emptyBounds();
  const points: Vec3[] = [];

  /**
   * Record a solid.
   *
   * Its own bounding corners, not its centre and not the six points where it
   * pokes furthest along each axis. A shell is a convex volume, so containing
   * all eight corners contains the whole body — and containing only the axis
   * extremes does not: those sit on the faces of the box, and an ellipsoid
   * through them still slices the corners off.
   */
  const takeBall = (centre: Vec3, radius: number): void => {
    growBounds(bounds, centre, radius);
    for (const dx of [-radius, radius]) {
      for (const dy of [-radius, radius]) {
        for (const dz of [-radius, radius]) {
          points.push({ x: centre.x + dx, y: centre.y + dy, z: centre.z + dz });
        }
      }
    }
  };

  /** Record a finished region: every corner of it has to fit. */
  const takeVolume = (volume: Zone3D): void => {
    for (const dx of [-volume.half.x, volume.half.x]) {
      for (const dy of [-volume.half.y, volume.half.y]) {
        for (const dz of [-volume.half.z, volume.half.z]) {
          const corner = {
            x: volume.position.x + dx,
            y: volume.position.y + dy,
            z: volume.position.z + dz,
          };
          growBounds(bounds, corner);
          points.push(corner);
        }
      }
    }
  };

  // A region's own position is a layout anchor, not a body — nothing is drawn
  // there. Seeding the shell with it stretched every cloud from the floor,
  // where its anchor sits, up to the models it holds four decks above.
  const own = nodeById.get(node.id);
  if (own && !own.isBoundary) takeBall(own.position, own.radius);

  const visit = (parentId: string, seen: Set<string>): void => {
    for (const child of childrenByParent.get(parentId) ?? []) {
      // A parent cycle is malformed data, not a reason to recurse forever.
      if (seen.has(child.id)) continue;
      seen.add(child.id);

      const volume = volumes.get(child.id);
      if (volume) {
        // Already solved, and it already contains everything below it.
        takeVolume(volume);
        continue;
      }

      const built = nodeById.get(child.id);
      if (built) takeBall(built.position, built.radius);
      visit(child.id, seen);
    }
  };

  visit(node.id, new Set([node.id]));
  return isEmptyBounds(bounds) ? null : { bounds, points };
}

/**
 * Turn the space a region encloses into the volume drawn around it.
 */
export function volumeFor(
  shape: Zone3DShape,
  bounds: Bounds3,
  points: readonly Vec3[],
  padding: number,
): { centre: Vec3; half: Vec3 } {
  const centre = boundsCentre(bounds);

  if (shape === 'box') {
    return {
      centre,
      half: {
        x: Math.max((bounds.max.x - bounds.min.x) / 2 + padding, ZONE3D.MIN_HALF_EXTENT),
        y: Math.max((bounds.max.y - bounds.min.y) / 2 + padding, ZONE3D.MIN_HALF_EXTENT),
        z: Math.max((bounds.max.z - bounds.min.z) / 2 + padding, ZONE3D.MIN_HALF_EXTENT),
      },
    };
  }

  // A real sphere — the distance to the furthest thing it has to hold. Fitting
  // it per axis instead makes it cheaper to place, and makes it an egg: a
  // cluster whose contents climb four decks comes out visibly squashed, which
  // says something about the shape of the estate that is not true. The room it
  // needs is found by opening the plan out, not by deforming the region.
  let radius = 0;
  for (const point of points) {
    radius = Math.max(radius, distanceBetween(point, centre));
  }
  const padded = Math.max(radius + padding, ZONE3D.MIN_HALF_EXTENT);
  return { centre, half: { x: padded, y: padded, z: padded } };
}

function buildZone3D(
  node: TopologyNode,
  childrenByParent: Map<string, TopologyNode[]>,
  nodeById: Map<string, Node3D>,
  volumes: Map<string, Zone3D>,
): Zone3D | null {
  const kind = node.typeId as Zone3DKind;
  const colour = PLATE_COLOUR[kind];
  const alpha = PLATE_ALPHA[kind];
  const shape = ZONE_SHAPE[kind];
  if (!colour || !alpha || !shape) return null;

  // A boundary around nothing is a claim that something is missing.
  if ((childrenByParent.get(node.id)?.length ?? 0) === 0) return null;

  const enclosed = enclosedExtent(node, childrenByParent, nodeById, volumes);
  if (!enclosed) return null;

  const { centre, half } = volumeFor(
    shape,
    enclosed.bounds,
    enclosed.points,
    ZONE3D.VOLUME_PADDING[kind],
  );
  return {
    id: node.id,
    kind,
    shape,
    position: centre,
    half,
    label: kind === 'realm' ? structureLabel(node).toUpperCase() : structureLabel(node),
    colour,
    fillAlpha: alpha.fill,
    edgeAlpha: alpha.edge,
  };
}

// ── Edges ─────────────────────────────────────────────────────────────────────

/**
 * The arc one connection takes through the model.
 *
 * Sampled from a quadratic Bézier lifted above the straight line between its
 * ends. A straight segment between two decks is readable on its own and
 * unreadable in a bundle — a hundred of them cross at every angle and the eye
 * cannot follow one. Lifting each arc by a share of its own length separates
 * long connections from short ones by height, and the relation lane spreads
 * parallel connections sideways so `manages` and `observes` between the same
 * pair do not draw as one line.
 */
export function edgeArc(
  edge: TopologyEdge,
  from: Node3D,
  to: Node3D,
  segments: number = EDGE3D.SEGMENTS,
): Vec3[] {
  const direction = subtract(to.position, from.position);
  const span = Math.hypot(direction.x, direction.y, direction.z);
  const unit = normalize(direction);
  // Stop at each end's surface rather than its centre, so an arc leaves a
  // solid at its rim the way the plan's edges leave a glyph at its outline.
  const start: Vec3 = {
    x: from.position.x + unit.x * from.radius,
    y: from.position.y + unit.y * from.radius,
    z: from.position.z + unit.z * from.radius,
  };
  const end: Vec3 = {
    x: to.position.x - unit.x * to.radius,
    y: to.position.y - unit.y * to.radius,
    z: to.position.z - unit.z * to.radius,
  };

  const lift = Math.min(Math.max(span * EDGE3D.BOW_RATIO, EDGE3D.BOW_MIN), EDGE3D.BOW_MAX);
  // Perpendicular in the ground plane: lanes spread sideways, never vertically,
  // so the height of an arc keeps meaning "how far it travels".
  const planar = Math.hypot(direction.x, direction.z) || 1;
  const lane = edgeRelationLane(edge) * EDGE3D.LANE_OFFSET;
  const control: Vec3 = {
    x: (start.x + end.x) / 2 + (-direction.z / planar) * lane,
    y: (start.y + end.y) / 2 + lift,
    z: (start.z + end.z) / 2 + (direction.x / planar) * lane,
  };

  const points: Vec3[] = [];
  for (let i = 0; i <= segments; i += 1) {
    points.push(quadraticBezier(start, control, end, i / segments));
  }
  return points;
}

function buildEdge3D(edge: TopologyEdge, nodeById: Map<string, Node3D>): Edge3D | null {
  if (edge.sourceId === edge.targetId) return null;
  // Containment is drawn as a deck and a riser. Drawing it as an arc as well
  // would say the same thing twice, in the view where it is already obvious.
  if (edge.relationType === 'contains') return null;
  const from = nodeById.get(edge.sourceId);
  const to = nodeById.get(edge.targetId);
  if (!from || !to) return null;

  const layer = edgeLayer(edge);
  const outsideCall = layer === 'inference' && to.computeClass === 'outside';
  return {
    id: edge.id,
    sourceId: edge.sourceId,
    targetId: edge.targetId,
    layer,
    colour: outsideCall ? ([...OUTSIDE_COLOUR] as Rgb) : LAYER_COLOUR[layer],
    points: edgeArc(edge, from, to),
    ratePerMinute: edge.ratePerMinute ?? 0,
  };
}

// ── Making room ───────────────────────────────────────────────────────────────

/**
 * Open the plan out so the region shells have somewhere to be.
 *
 * A sphere that genuinely encloses a cluster is wider than the plan ever
 * needed that cluster to be, because the plan measures the ground it covers
 * and the sphere also has to swallow four decks of height. Packed at plan
 * spacing the shells intersect, which draws regions as overlapping when the
 * estate says they are not.
 *
 * Only the gaps *between* regions are stretched. What is packed inside a host
 * — its agents, its models — keeps the exact arrangement the plan gave it, so
 * the two views still show the same estate and not two different ones.
 */
export function spreadPositions(
  nodes: readonly TopologyNode[],
  positions: Map<string, NodePosition>,
  spread: number,
): Map<string, NodePosition> {
  if (spread === 1) return positions;

  const spreadOut = new Map<string, NodePosition>();
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const depths = containmentDepth(nodes);

  for (const node of [...nodes].sort((a, b) => (depths.get(a.id) ?? 0) - (depths.get(b.id) ?? 0))) {
    const original = positions.get(node.id);
    if (!original) continue;

    // Only a shell-bearing region is moved; everything else keeps the offset
    // from its parent that the plan gave it.
    const factor = BOUNDARY_TYPES.has(node.typeId) ? spread : 1;
    const parent = node.parentId ? byId.get(node.parentId) : undefined;
    const parentOriginal = parent ? positions.get(parent.id) : undefined;
    const parentMoved = parent ? spreadOut.get(parent.id) : undefined;

    if (!parentOriginal || !parentMoved) {
      spreadOut.set(node.id, { ...original, x: original.x * factor, y: original.y * factor });
      continue;
    }

    spreadOut.set(node.id, {
      ...original,
      x: parentMoved.x + (original.x - parentOriginal.x) * factor,
      y: parentMoved.y + (original.y - parentOriginal.y) * factor,
    });
  }

  return spreadOut;
}

/**
 * How much further apart the regions have to be before none of them intersect.
 *
 * Returns 1 when they already clear each other. Only sibling pairs are tested:
 * a region is *supposed* to be inside its parent, and two regions in different
 * parents are separated by their parents.
 */
export function separationFactor(
  volumes: Map<string, Zone3D>,
  childrenByParent: Map<string, TopologyNode[]>,
  roots: readonly TopologyNode[],
): number {
  let needed = 1;

  const check = (siblings: readonly TopologyNode[]): void => {
    const shells = siblings
      .map((node) => volumes.get(node.id))
      .filter((zone): zone is Zone3D => zone !== undefined);

    for (let i = 0; i < shells.length; i += 1) {
      for (let j = i + 1; j < shells.length; j += 1) {
        needed = Math.max(needed, pairSeparation(shells[i]!, shells[j]!));
      }
    }
  };

  check(roots);
  for (const siblings of childrenByParent.values()) check(siblings);
  return needed;
}

/**
 * The factor two shells' spacing must grow by before they clear each other.
 *
 * Only the ground plane is ever stretched, so only the ground plane is
 * measured — two shells stacked on the same spot cannot be pulled apart by
 * opening the plan out, and asking for it would spread the estate for ever.
 */
function pairSeparation(a: Zone3D, b: Zone3D): number {
  const dx = Math.abs(a.position.x - b.position.x);
  const dz = Math.abs(a.position.z - b.position.z);

  if (a.shape === 'sphere' && b.shape === 'sphere') {
    const gap = Math.hypot(dx, dz);
    if (gap <= 0) return 1;
    return Math.max(1, (a.half.x + b.half.x + ZONE3D.SIBLING_GAP) / gap);
  }

  // Boxes clear each other as soon as *one* axis separates them, so the answer
  // is the cheaper of the two.
  const onX = dx > 0 ? (a.half.x + b.half.x + ZONE3D.SIBLING_GAP) / dx : Infinity;
  const onZ = dz > 0 ? (a.half.z + b.half.z + ZONE3D.SIBLING_GAP) / dz : Infinity;
  const cheapest = Math.min(onX, onZ);
  return Number.isFinite(cheapest) ? Math.max(1, cheapest) : 1;
}

// ── Model ─────────────────────────────────────────────────────────────────────

/**
 * How deep each node sits in the containment chain.
 *
 * Regions have to be sized deepest-first, and the only way to know which is
 * deepest is to walk the chain.
 */
function containmentDepth(nodes: readonly TopologyNode[]): Map<string, number> {
  const parentOf = new Map(nodes.map((node) => [node.id, node.parentId ?? null]));
  const depths = new Map<string, number>();

  const resolve = (id: string, visiting: Set<string>): number => {
    const known = depths.get(id);
    if (known != null) return known;
    if (visiting.has(id)) return 0;
    visiting.add(id);
    const parent = parentOf.get(id) ?? null;
    const depth = parent != null && parentOf.has(parent) ? resolve(parent, visiting) + 1 : 0;
    depths.set(id, depth);
    return depth;
  };

  for (const node of nodes) resolve(node.id, new Set());
  return depths;
}

/**
 * Build everything the 3D view draws from one topology snapshot.
 *
 * `positions` is the *same* plan the 2D canvas uses, passed in rather than
 * recomputed: the two views must not disagree about where anything is, or
 * switching between them stops being a change of viewpoint and becomes a
 * change of subject.
 */
export function buildScene3DModel(
  topology: Topology | null,
  positions: Map<string, NodePosition>,
  styleFor: (node: TopologyNode) => NodeStyle,
): Scene3DModel {
  if (!topology) {
    return {
      nodes: [],
      nodeById: new Map(),
      zones: [],
      edges: [],
      risers: [],
      framePoints: [],
    };
  }

  const childrenByParent = new Map<string, TopologyNode[]>();
  const roots: TopologyNode[] = [];
  const nodeIds = new Set(topology.nodes.map((node) => node.id));
  for (const node of topology.nodes) {
    if (!node.parentId || !nodeIds.has(node.parentId)) {
      roots.push(node);
      continue;
    }
    const siblings = childrenByParent.get(node.parentId);
    if (siblings) siblings.push(node);
    else childrenByParent.set(node.parentId, [node]);
  }

  const depths = containmentDepth(topology.nodes);
  const deepestFirst = topology.nodes
    .filter((node) => isContainerType(node.typeId))
    .sort((a, b) => (depths.get(b.id) ?? 0) - (depths.get(a.id) ?? 0));

  /** Bodies and shells for one candidate spacing of the plan. */
  const layOut = (spread: number) => {
    const laidOut = spreadPositions(topology.nodes, positions, spread);
    const nodeById = new Map<string, Node3D>();
    const nodes: Node3D[] = [];

    for (const node of topology.nodes) {
      const position = laidOut.get(node.id);
      if (!position) continue;
      const node3d = buildNode3D(node, position, styleFor(node));
      nodes.push(node3d);
      nodeById.set(node.id, node3d);
    }

    // Regions are solved deepest-first, so a parent encloses its children's
    // finished shells rather than re-deriving what is inside them. The other
    // way round, a realm gets sized to its clusters' contents and then has
    // their spheres — which stand proud of those contents — burst out of it.
    const volumes = new Map<string, Zone3D>();
    for (const node of deepestFirst) {
      if (!laidOut.has(node.id)) continue;
      const zone = buildZone3D(node, childrenByParent, nodeById, volumes);
      if (zone) volumes.set(node.id, zone);
    }

    return { nodes, nodeById, volumes };
  };

  // Open the plan out until no two sibling regions intersect. It converges
  // because moving them apart scales the distance between them in full while
  // their shells, which are mostly sized by a height that is not being
  // stretched, grow by much less.
  let spread = 1;
  let laid = layOut(spread);
  for (let pass = 0; pass < ZONE3D.SPREAD_PASSES; pass += 1) {
    const needed = separationFactor(laid.volumes, childrenByParent, roots);
    if (needed <= 1.001 || spread >= ZONE3D.MAX_SPREAD) break;
    spread = Math.min(spread * needed, ZONE3D.MAX_SPREAD);
    laid = layOut(spread);
  }

  const { nodes, nodeById, volumes } = laid;
  const zones: Zone3D[] = [];
  const risers: Riser3D[] = [];
  const framePoints: Vec3[] = [];

  for (const node3d of nodes) {
    // The extremes of the body, not just its centre: a node framed to its
    // centre is framed with half of it off the edge.
    for (const axis of ['x', 'y', 'z'] as const) {
      for (const sign of [-1, 1]) {
        framePoints.push({
          ...node3d.position,
          [axis]: node3d.position[axis] + sign * node3d.radius,
        });
      }
    }
  }

  // Back into the order the topology reported, so the scene is built the same
  // way twice for the same snapshot.
  for (const node of topology.nodes) {
    const zone = volumes.get(node.id);
    if (!zone) continue;
    zones.push(zone);
    for (const dx of [-zone.half.x, zone.half.x]) {
      for (const dy of [-zone.half.y, zone.half.y]) {
        for (const dz of [-zone.half.z, zone.half.z]) {
          framePoints.push({
            x: zone.position.x + dx,
            y: zone.position.y + dy,
            z: zone.position.z + dz,
          });
        }
      }
    }
  }

  const tierOf = new Map<string, number>();
  for (const node of topology.nodes) tierOf.set(node.id, elevationFor(node));

  for (const node of topology.nodes) {
    if (!node.parentId) continue;
    const node3d = nodeById.get(node.id);
    const parentY = tierOf.get(node.parentId);
    if (!node3d || parentY == null) continue;
    // A riser that would point upward means the child sits at or below its
    // parent's deck, and a line drawn up into it says nothing worth the ink.
    const top = node3d.position.y - node3d.radius - RISER.CLEARANCE;
    if (top <= parentY) continue;
    risers.push({
      nodeId: node.id,
      from: { x: node3d.position.x, y: top, z: node3d.position.z },
      to: { x: node3d.position.x, y: parentY, z: node3d.position.z },
    });
  }

  const edges = topology.edges
    .map((edge) => buildEdge3D(edge, nodeById))
    .filter((edge): edge is Edge3D => edge !== null);

  return { nodes, nodeById, zones, edges, risers, framePoints };
}

/**
 * How many travelling motes an edge carries.
 *
 * Rate changes the *number* of marks, never their speed — more messages should
 * read as more traffic, not as traffic in a hurry. An edge nobody measured
 * gets none: the view must not animate flow that was never observed.
 */
export function moteCountFor(ratePerMinute: number): number {
  if (ratePerMinute <= 0) return 0;
  const saturation = Math.min(1, ratePerMinute / EDGE3D.FLOW_SATURATION_PER_MINUTE);
  return Math.max(1, Math.round(saturation * EDGE3D.FLOW_MAX_MOTES));
}

/** Point at `t` along an already-sampled arc, `t` wrapping into [0, 1). */
export function pointAlongArc(points: readonly Vec3[], t: number): Vec3 {
  if (points.length === 0) return { x: 0, y: 0, z: 0 };
  if (points.length === 1) return points[0]!;
  const wrapped = t - Math.floor(t);
  const scaled = wrapped * (points.length - 1);
  const index = Math.min(Math.floor(scaled), points.length - 2);
  const a = points[index]!;
  const b = points[index + 1]!;
  const local = scaled - index;
  return {
    x: a.x + (b.x - a.x) * local,
    y: a.y + (b.y - a.y) * local,
    z: a.z + (b.z - a.z) * local,
  };
}

/** Deck heights, exported so a caller can label the tiers without guessing. */
export { TIER };
