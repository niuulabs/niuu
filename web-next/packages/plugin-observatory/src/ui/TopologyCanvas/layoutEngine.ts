/**
 * Deterministic, sibling-aware layout engine.
 *
 * Realm placement stays intentionally curated and stable, while local
 * containment inside realms and clusters uses D3 pack so dense groups breathe
 * more naturally than the old hand-authored orbit bands.
 */

import { forceCollide, forceManyBody, forceSimulation, forceX, forceY } from 'd3-force';
import { packSiblings } from 'd3-hierarchy';
import type { Topology, TopologyNode } from '../../domain';
import { LAYOUT, NODE_SIZE } from './config';

export interface NodePosition {
  x: number;
  y: number;
  zoneRadius?: number;
  containerWidth?: number;
  containerHeight?: number;
}

export interface LayoutBounds {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

/**
 * Convert an arbitrary string to a stable angle in [0, 2π].
 * Uses a simple djb2-style hash so the result is deterministic and
 * well-distributed across the circle.
 */
export function hashAngle(id: string): number {
  let h = 5381;
  for (let i = 0; i < id.length; i++) {
    h = (((h << 5) + h) ^ id.charCodeAt(i)) >>> 0;
  }
  return ((h % 10000) / 10000) * Math.PI * 2;
}

/** Host rounded-rect half-width in world units. */
export const HOST_HALF_W = 34;
/** Host rounded-rect half-height in world units. */
export const HOST_HALF_H = 20;
const HOST_CONTAINER_PAD_X = 26;
const HOST_CONTAINER_PAD_Y = 24;
const HOST_CORE_CLEARANCE = 52;

const TYPE_PRIORITY: Record<string, number> = {
  realm: 1,
  cluster: 2,
  namespace: 3,
  host: 4,
  ting: 5,
  bifrost: 6,
  volundr: 7,
  ravn_long: 8,
  warden: 8,
  ravn_run: 9,
  run: 10,
  trigger: 11,
  resource: 12,
  stage: 13,
  gate: 14,
  cond: 15,
  end: 16,
  service: 17,
  model: 18,
};

export function sortedNodes(nodes: TopologyNode[]): TopologyNode[] {
  return [...nodes].sort((a, b) => {
    const aOrder = a.layoutHints?.order;
    const bOrder = b.layoutHints?.order;
    if (aOrder != null || bOrder != null) {
      const resolvedAOrder = aOrder ?? Number.POSITIVE_INFINITY;
      const resolvedBOrder = bOrder ?? Number.POSITIVE_INFINITY;
      if (resolvedAOrder !== resolvedBOrder) return resolvedAOrder - resolvedBOrder;
    }
    const priority = (TYPE_PRIORITY[a.typeId] ?? 100) - (TYPE_PRIORITY[b.typeId] ?? 100);
    if (priority !== 0) return priority;
    const label = a.label.localeCompare(b.label, undefined, { sensitivity: 'base' });
    if (label !== 0) return label;
    return a.id.localeCompare(b.id);
  });
}

function nodeExtent(node: TopologyNode): number {
  if (node.typeId === 'host') return Math.max(HOST_HALF_W, HOST_HALF_H) + 12;
  if (node.typeId === 'run') return 72;
  if (node.typeId === 'namespace') return LAYOUT.NAMESPACE_INNER_RADIUS;
  return (NODE_SIZE[node.typeId] ?? 8) + 18;
}

/**
 * How deep each node sits in the containment chain.
 *
 * Sizing a container needs its children's sizes already known, so the walk has
 * to run deepest-first. Ordering by child count instead let a namespace be
 * packed while the sessions inside it still measured as bare dots — each then
 * grew to hold six agents and drove straight through its neighbours.
 */
function containmentDepth(nodes: TopologyNode[]): Map<string, number> {
  const parentOf = new Map(nodes.map((node) => [node.id, node.parentId ?? null]));
  const depths = new Map<string, number>();

  function resolve(id: string, visiting: Set<string>): number {
    const known = depths.get(id);
    if (known != null) return known;
    // A parent cycle is malformed data, not a reason to recurse forever.
    if (visiting.has(id)) return 0;
    visiting.add(id);
    const parent = parentOf.get(id) ?? null;
    const depth = parent != null && parentOf.has(parent) ? resolve(parent, visiting) + 1 : 0;
    depths.set(id, depth);
    return depth;
  }

  for (const node of nodes) resolve(node.id, new Set());
  return depths;
}

function buildChildrenIndex(nodes: TopologyNode[]): Map<string | null, TopologyNode[]> {
  const index = new Map<string | null, TopologyNode[]>();
  for (const node of nodes) {
    const key = node.parentId ?? null;
    const siblings = index.get(key);
    if (siblings) siblings.push(node);
    else index.set(key, [node]);
  }
  for (const [key, siblings] of index) {
    index.set(key, sortedNodes(siblings));
  }
  return index;
}

interface ArcPlacementOptions {
  baseRadius: number;
  radialStep: number;
  minSpacing: number;
  arcCenter?: number;
  arcSpan?: number;
}

interface PackLeaf {
  kind: 'node';
  node: TopologyNode;
  order: number;
}

interface PackGroup {
  kind: 'group';
  id: string;
  order: number;
  children: PackDatum[];
}

type PackDatum = PackGroup | PackLeaf;

interface PackedLayout {
  radius: number;
  positions: Map<string, NodePosition>;
}

interface HostPackedLayout {
  layout: PackedLayout;
  halfW: number;
  halfH: number;
  outerRadius: number;
}

type NodeExtentResolver = (node: TopologyNode) => number;

interface PackedCircle {
  leaf: PackLeaf;
  order: number;
  r: number;
  x: number;
  y: number;
  targetX: number;
  targetY: number;
  vx?: number;
  vy?: number;
}

function ringCapacity(radius: number, arcSpan: number, minSpacing: number): number {
  const circumference = Math.max(radius * arcSpan, minSpacing);
  return Math.max(1, Math.floor(circumference / Math.max(minSpacing, 1)));
}

export function placeArcChildren(
  children: TopologyNode[],
  anchor: NodePosition | undefined,
  options: ArcPlacementOptions,
): Map<string, NodePosition> {
  const placements = new Map<string, NodePosition>();
  if (children.length === 0) return placements;

  const center = options.arcCenter ?? 0;
  const span = options.arcSpan ?? Math.PI * 2;
  const base = anchor ?? { x: 0, y: 0 };
  let radius = options.baseRadius;
  let cursor = 0;

  while (cursor < children.length) {
    const capacity = ringCapacity(radius, span, options.minSpacing);
    const ring = children.slice(cursor, cursor + capacity);
    const denom = Math.max(ring.length - 1, 1);

    ring.forEach((node, index) => {
      const offset = ring.length === 1 ? 0 : -span / 2 + (span * index) / denom;
      const angle = center + offset;
      placements.set(node.id, {
        x: base.x + Math.cos(angle) * radius,
        y: base.y + Math.sin(angle) * radius,
      });
    });

    cursor += ring.length;
    radius += options.radialStep;
  }

  return placements;
}

function packLeaf(node: TopologyNode, order: number): PackLeaf {
  return { kind: 'node', node, order };
}

function packGroup(id: string, order: number, children: TopologyNode[]): PackGroup | null {
  if (children.length === 0) return null;
  return {
    kind: 'group',
    id,
    order,
    children: sortedNodes(children).map((child, index) => packLeaf(child, index)),
  };
}

export function packGroupsForNode(
  node: TopologyNode,
  children: TopologyNode[],
): Array<PackGroup | null> {
  if (node.typeId !== 'run') {
    return [packGroup(node.id, 0, children)];
  }

  const grouped = new Map<string, TopologyNode[]>();
  for (const child of sortedNodes(children)) {
    const groupId = child.layoutHints?.packGroup ?? 'run';
    const bucket = grouped.get(groupId);
    if (bucket) bucket.push(child);
    else grouped.set(groupId, [child]);
  }

  return [...grouped.entries()]
    .map(([groupId, groupChildren], index) => {
      const firstOrder = groupChildren
        .map((child) => child.layoutHints?.order)
        .find((order): order is number => order != null);
      return packGroup(`${node.id}:${groupId}`, firstOrder ?? index, groupChildren);
    })
    .sort((a, b) => (a?.order ?? 0) - (b?.order ?? 0));
}

function packChildren(
  groups: Array<PackGroup | null>,
  {
    padding,
    fallbackRadius,
    extentForNode = nodeExtent,
  }: {
    padding: number;
    fallbackRadius: number;
    extentForNode?: NodeExtentResolver;
  },
): PackedLayout {
  const activeGroups = groups.filter((group): group is PackGroup => group !== null);
  if (activeGroups.length === 0) {
    return { radius: fallbackRadius, positions: new Map() };
  }

  if (
    activeGroups.length === 1 &&
    activeGroups[0]!.children.length === 1 &&
    activeGroups[0]!.children[0]!.kind === 'node'
  ) {
    const onlyLeaf = activeGroups[0]!.children[0] as PackLeaf;
    return {
      radius: Math.max(fallbackRadius, extentForNode(onlyLeaf.node) + padding * 2),
      positions: new Map([[onlyLeaf.node.id, { x: 0, y: 0 }]]),
    };
  }

  const inflatedPadding = padding / 2;
  const circles = activeGroups
    .flatMap((group) =>
      group.children
        .filter((child): child is PackLeaf => child.kind === 'node')
        .map((child) => ({
          leaf: child,
          order: group.order * 1000 + child.order,
          r: extentForNode(child.node) + inflatedPadding,
          x: 0,
          y: 0,
          targetX: 0,
          targetY: 0,
        })),
    )
    .sort((a, b) => a.order - b.order);

  packSiblings(circles);
  for (const circle of circles) {
    circle.targetX = circle.x;
    circle.targetY = circle.y;
  }

  if (circles.length > 1) {
    const simulation = forceSimulation(circles)
      .alpha(0.22)
      .alphaDecay(0.12)
      .force('x', forceX<PackedCircle>((circle) => circle.targetX).strength(0.16))
      .force('y', forceY<PackedCircle>((circle) => circle.targetY).strength(0.16))
      .force('collide', forceCollide<PackedCircle>((circle) => circle.r + 2).iterations(2))
      .force('charge', forceManyBody<PackedCircle>().strength(-4))
      .stop();

    for (let tick = 0; tick < 18; tick += 1) {
      simulation.tick();
    }
  }

  const positions = new Map<string, NodePosition>();
  let radius = fallbackRadius;

  for (const circle of circles) {
    positions.set(circle.leaf.node.id, { x: circle.x, y: circle.y });
    radius = Math.max(radius, Math.hypot(circle.x, circle.y) + circle.r);
  }

  return { radius, positions };
}

function translatePackedLayout(
  layout: PackedLayout,
  anchor: NodePosition | undefined,
): Map<string, NodePosition> {
  const translated = new Map<string, NodePosition>();
  const base = anchor ?? { x: 0, y: 0 };
  for (const [id, pos] of layout.positions) {
    translated.set(id, {
      x: base.x + pos.x,
      y: base.y + pos.y,
    });
  }
  return translated;
}

function placePackedChildren(
  children: TopologyNode[],
  anchor: NodePosition | undefined,
  {
    padding,
    fallbackRadius,
    extentForNode,
  }: {
    padding: number;
    fallbackRadius: number;
    extentForNode?: NodeExtentResolver;
  },
): Map<string, NodePosition> {
  return translatePackedLayout(
    packChildren([packGroup('group', 0, children)], {
      padding,
      fallbackRadius,
      extentForNode,
    }),
    anchor,
  );
}

function withNestedContainer(
  node: TopologyNode,
  pos: NodePosition,
  nestedLayouts: Map<string, PackedLayout>,
): NodePosition {
  const layout = nestedLayouts.get(node.id);
  if (!layout) return pos;
  const radius =
    node.typeId === 'namespace'
      ? Math.max(layout.radius, LAYOUT.NAMESPACE_INNER_RADIUS)
      : layout.radius;
  return {
    ...pos,
    containerWidth: radius * 2,
    containerHeight: radius * 2,
  };
}

function packedRadius(
  children: TopologyNode[],
  {
    padding,
    fallbackRadius,
    extentForNode,
  }: {
    padding: number;
    fallbackRadius: number;
    extentForNode?: NodeExtentResolver;
  },
): number {
  return packChildren([packGroup('group', 0, children)], {
    padding,
    fallbackRadius,
    extentForNode,
  }).radius;
}

function pushPackedChildrenAwayFromCenter(
  layout: PackedLayout,
  children: TopologyNode[],
  extentForNode: NodeExtentResolver,
  clearance: number,
): PackedLayout {
  const adjusted = new Map<string, NodePosition>();
  let radius = 0;

  for (const child of children) {
    const pos = layout.positions.get(child.id);
    if (!pos) continue;

    const extent = extentForNode(child);
    const minDistance = clearance + extent;
    const distance = Math.hypot(pos.x, pos.y);
    const angle = distance > 0 ? Math.atan2(pos.y, pos.x) : hashAngle(child.id);
    const targetDistance = Math.max(distance, minDistance);
    const next = {
      x: Math.cos(angle) * targetDistance,
      y: Math.sin(angle) * targetDistance,
    };

    adjusted.set(child.id, next);
    radius = Math.max(radius, targetDistance + extent);
  }

  return { radius, positions: adjusted };
}

function buildHostPackedLayout(
  host: TopologyNode,
  children: TopologyNode[],
  extentForNode: NodeExtentResolver,
): HostPackedLayout {
  if (children.length === 0) {
    const halfW = HOST_HALF_W + 10;
    const halfH = HOST_HALF_H + 8;
    return {
      layout: { radius: Math.max(halfW, halfH), positions: new Map() },
      halfW,
      halfH,
      outerRadius: Math.hypot(halfW, halfH),
    };
  }

  const baseLayout = packChildren([packGroup(host.id, 0, children)], {
    padding: 14,
    fallbackRadius: Math.max(LAYOUT.HOST_CHILD_ORBIT - 18, 60),
    extentForNode,
  });
  const layout = pushPackedChildrenAwayFromCenter(
    baseLayout,
    children,
    extentForNode,
    HOST_CORE_CLEARANCE,
  );
  const halfW = Math.max(HOST_HALF_W + 10, layout.radius + HOST_CONTAINER_PAD_X);
  const halfH = Math.max(HOST_HALF_H + 10, layout.radius + HOST_CONTAINER_PAD_Y);

  return {
    layout,
    halfW,
    halfH,
    outerRadius: Math.hypot(halfW, halfH),
  };
}

function buildNestedPackedLayout(
  node: TopologyNode,
  children: TopologyNode[],
  extentForNode: NodeExtentResolver,
): PackedLayout {
  if (children.length === 0) {
    return { radius: nodeExtent(node), positions: new Map() };
  }

  if (node.typeId === 'run') {
    return buildRunWorkflowLayout(children, extentForNode);
  }

  const baseLayout = packChildren(packGroupsForNode(node, children), {
    padding: 14,
    fallbackRadius: Math.max(nodeExtent(node) + 34, 72),
    extentForNode,
  });

  return pushPackedChildrenAwayFromCenter(
    baseLayout,
    children,
    extentForNode,
    Math.max(34, nodeExtent(node) + 10),
  );
}

function buildRunWorkflowLayout(
  children: TopologyNode[],
  extentForNode: NodeExtentResolver,
): PackedLayout {
  const positions = new Map<string, NodePosition>();
  const groups = new Map<string, TopologyNode[]>();
  for (const child of sortedNodes(children)) {
    const key = child.layoutHints?.packGroup ?? 'main';
    const bucket = groups.get(key);
    if (bucket) bucket.push(child);
    else groups.set(key, [child]);
  }

  const baseRadius = Math.max(142, LAYOUT.RUN_CHILD_ORBIT + Math.max(0, children.length - 4) * 18);
  const anchors: Record<string, { x: number; y: number }> = {
    entry: { x: 0, y: -baseRadius * 0.76 },
    resource: { x: -baseRadius * 0.82, y: 0 },
    main: { x: 0, y: 0 },
    decision: { x: baseRadius * 0.82, y: 0 },
    exit: { x: 0, y: baseRadius * 0.76 },
    run: { x: 0, y: 0 },
  };
  const fallbackAnchor = { x: 0, y: 0 };

  let radius = baseRadius * 0.78;
  for (const [groupId, groupChildren] of groups) {
    const anchor = anchors[groupId] ?? fallbackAnchor;
    const spacing = groupId === 'main' ? 72 : 62;
    const startY = anchor.y - ((groupChildren.length - 1) * spacing) / 2;

    for (const [index, child] of groupChildren.entries()) {
      const offsetX =
        groupId === 'main' && groupChildren.length > 2 ? (index % 2 === 0 ? -14 : 14) : 0;
      const next = {
        x: anchor.x + offsetX,
        y: startY + index * spacing,
      };
      positions.set(child.id, next);
      radius = Math.max(radius, Math.hypot(next.x, next.y) + extentForNode(child));
    }
  }

  return { radius, positions };
}

/**
 * Compute layout positions for all nodes in a topology snapshot.
 */
export function computeLayout(topology: Topology): Map<string, NodePosition> {
  const positions = new Map<string, NodePosition>();
  const { nodes } = topology;
  const nodeIds = new Set(nodes.map((node) => node.id));
  const childrenByParent = buildChildrenIndex(nodes);
  const nestedRadii = new Map<string, number>();
  const clusterRadii = new Map<string, number>();
  const realmRadii = new Map<string, number>();
  const hostLayouts = new Map<string, HostPackedLayout>();
  const nestedLayouts = new Map<string, PackedLayout>();

  const realmNodes = sortedNodes(nodes.filter((node) => node.typeId === 'realm'));
  const clusterNodes = sortedNodes(nodes.filter((node) => node.typeId === 'cluster'));
  const hostNodes = sortedNodes(nodes.filter((node) => node.typeId === 'host'));
  const runNodes = sortedNodes(nodes.filter((node) => node.typeId === 'run'));
  const depthOf = containmentDepth(nodes);
  const nestedNodes = sortedNodes(
    nodes.filter((node) => {
      if (node.typeId === 'realm' || node.typeId === 'cluster') return false;
      return (childrenByParent.get(node.id)?.length ?? 0) > 0;
    }),
  ).sort((a, b) => (depthOf.get(b.id) ?? 0) - (depthOf.get(a.id) ?? 0));

  const nestedExtent: NodeExtentResolver = (node) => nestedRadii.get(node.id) ?? nodeExtent(node);

  const clusterContainerExtent: NodeExtentResolver = (node) => {
    if (node.typeId !== 'cluster') return nodeExtent(node);
    return (clusterRadii.get(node.id) ?? LAYOUT.CLUSTER_INNER_RADIUS) + 18;
  };

  const worldContainerExtent: NodeExtentResolver = (node) => {
    if (node.typeId === 'realm') {
      // A realm is *drawn* as a rectangle bounding its contents, so the space
      // it occupies is that rectangle's circumscribing circle — half-diagonal,
      // not half-width. Reserving only the radius let the corners of a wide
      // realm cross into its neighbour, which is what put SVARTALFHEIM
      // through ASGARD.
      const inner = realmRadii.get(node.id) ?? LAYOUT.REALM_INNER_RADIUS;
      return (inner + LAYOUT.REALM_HULL_PADDING) * Math.SQRT2;
    }
    if (node.typeId === 'cluster') {
      return (clusterRadii.get(node.id) ?? LAYOUT.CLUSTER_INNER_RADIUS) + 20;
    }
    return nestedExtent(node) + 14;
  };

  for (const node of nestedNodes) {
    const children = childrenByParent.get(node.id) ?? [];
    const contentExtent =
      node.typeId === 'host'
        ? (() => {
            const hostLayout = buildHostPackedLayout(node, children, nestedExtent);
            hostLayouts.set(node.id, hostLayout);
            return hostLayout.outerRadius;
          })()
        : (() => {
            const nestedLayout = buildNestedPackedLayout(node, children, nestedExtent);
            nestedLayouts.set(node.id, nestedLayout);
            return nestedLayout.radius;
          })();
    nestedRadii.set(node.id, Math.max(nodeExtent(node), contentExtent));
  }

  for (const node of clusterNodes) {
    const children = (childrenByParent.get(node.id) ?? []).filter(
      (child) => child.typeId !== 'cluster',
    );
    const contentExtent =
      children.length === 0
        ? 0
        : packedRadius(children, {
            padding: 18,
            fallbackRadius: 72,
            extentForNode: nestedExtent,
          });
    clusterRadii.set(node.id, Math.max(LAYOUT.CLUSTER_INNER_RADIUS, contentExtent + 18));
  }

  for (const node of realmNodes) {
    const children = childrenByParent.get(node.id) ?? [];
    const realmChildExtent: NodeExtentResolver = (child) =>
      child.typeId === 'cluster' ? clusterContainerExtent(child) : nestedExtent(child);
    const contentExtent =
      children.length === 0
        ? 0
        : children.length === 1 && children[0]!.typeId === 'cluster'
          ? (clusterRadii.get(children[0]!.id) ?? LAYOUT.CLUSTER_INNER_RADIUS) + 18
          : packedRadius(children, {
              padding: 22,
              fallbackRadius: 72,
              extentForNode: realmChildExtent,
            });
    realmRadii.set(node.id, Math.max(LAYOUT.REALM_INNER_RADIUS, contentExtent + 18));
  }

  const rootNodes = sortedNodes(
    nodes.filter((node) => !node.parentId || !nodeIds.has(node.parentId)),
  );
  const rootPlacements = placePackedChildren(
    rootNodes,
    { x: 0, y: 0 },
    {
      padding: 48,
      fallbackRadius: LAYOUT.REALM_INNER_RADIUS + 120,
      extentForNode: worldContainerExtent,
    },
  );

  for (const node of rootNodes) {
    const pos = rootPlacements.get(node.id);
    if (!pos) continue;
    if (node.typeId === 'realm') {
      positions.set(node.id, {
        ...pos,
        zoneRadius: realmRadii.get(node.id) ?? LAYOUT.REALM_INNER_RADIUS,
      });
      continue;
    }
    if (node.typeId === 'cluster') {
      positions.set(node.id, {
        ...pos,
        zoneRadius: clusterRadii.get(node.id) ?? LAYOUT.CLUSTER_INNER_RADIUS,
      });
      continue;
    }
    positions.set(node.id, withNestedContainer(node, pos, nestedLayouts));
  }

  for (const realm of realmNodes) {
    const children = childrenByParent.get(realm.id) ?? [];
    const clusters = children.filter((child) => child.typeId === 'cluster');
    const parentPos = positions.get(realm.id);
    const realmChildExtent: NodeExtentResolver = (child) =>
      child.typeId === 'cluster' ? clusterContainerExtent(child) : nestedExtent(child);
    if (children.length === 1 && clusters.length === 1 && parentPos) {
      const onlyCluster = clusters[0]!;
      positions.set(onlyCluster.id, {
        ...parentPos,
        zoneRadius: clusterRadii.get(onlyCluster.id) ?? LAYOUT.CLUSTER_INNER_RADIUS,
      });
    } else {
      const childPlacements = placePackedChildren(children, parentPos, {
        padding: 22,
        fallbackRadius: 72,
        extentForNode: realmChildExtent,
      });
      for (const node of children) {
        const pos = childPlacements.get(node.id);
        if (!pos) continue;
        if (node.typeId === 'cluster') {
          positions.set(node.id, {
            ...pos,
            zoneRadius: clusterRadii.get(node.id) ?? LAYOUT.CLUSTER_INNER_RADIUS,
          });
          continue;
        }
        positions.set(node.id, withNestedContainer(node, pos, nestedLayouts));
      }
    }
  }

  for (const cluster of clusterNodes) {
    const children = (childrenByParent.get(cluster.id) ?? []).filter(
      (child) => child.typeId !== 'cluster',
    );
    const clusterAnchor = positions.get(cluster.id);

    const childPlacements = placePackedChildren(children, clusterAnchor, {
      padding: 18,
      fallbackRadius: 72,
      extentForNode: nestedExtent,
    });
    for (const child of children) {
      const pos = childPlacements.get(child.id);
      if (!pos) continue;
      if (child.typeId === 'host') {
        const hostLayout = hostLayouts.get(child.id);
        positions.set(child.id, {
          ...pos,
          containerWidth: hostLayout ? hostLayout.halfW * 2 : undefined,
          containerHeight: hostLayout ? hostLayout.halfH * 2 : undefined,
        });
        continue;
      }
      if (child.typeId === 'run') {
        const runLayout = nestedLayouts.get(child.id);
        positions.set(child.id, {
          ...pos,
          containerWidth: runLayout ? runLayout.radius * 2 : undefined,
          containerHeight: runLayout ? runLayout.radius * 2 : undefined,
        });
        continue;
      }
      positions.set(child.id, withNestedContainer(child, pos, nestedLayouts));
    }
  }

  for (const host of hostNodes) {
    const children = (childrenByParent.get(host.id) ?? []).filter(
      (child) => child.typeId !== 'host',
    );
    const anchor = positions.get(host.id);
    const hostLayout =
      hostLayouts.get(host.id) ?? buildHostPackedLayout(host, children, nestedExtent);
    if (anchor) {
      positions.set(host.id, {
        ...anchor,
        containerWidth: hostLayout.halfW * 2,
        containerHeight: hostLayout.halfH * 2,
      });
    }
    const placements = translatePackedLayout(hostLayout.layout, anchor);
    for (const child of children) {
      const pos = placements.get(child.id);
      if (pos) positions.set(child.id, pos);
    }
  }

  for (const run of runNodes) {
    const children = childrenByParent.get(run.id) ?? [];
    const runLayout =
      nestedLayouts.get(run.id) ?? buildNestedPackedLayout(run, children, nestedExtent);
    const anchor = positions.get(run.id);
    if (anchor) {
      positions.set(run.id, {
        ...anchor,
        containerWidth: runLayout.radius * 2,
        containerHeight: runLayout.radius * 2,
      });
    }
    const placements = translatePackedLayout(runLayout, anchor);
    for (const child of children) {
      const pos = placements.get(child.id);
      if (pos) positions.set(child.id, pos);
    }
  }

  for (const node of sortedNodes(nodes)) {
    if (!positions.has(node.id)) continue;
    if (
      node.typeId === 'realm' ||
      node.typeId === 'cluster' ||
      node.typeId === 'host' ||
      node.typeId === 'run'
    ) {
      continue;
    }
    const children = (childrenByParent.get(node.id) ?? []).filter(
      (child) => !positions.has(child.id),
    );
    if (children.length === 0) continue;
    const placements = translatePackedLayout(
      nestedLayouts.get(node.id) ?? buildNestedPackedLayout(node, children, nestedExtent),
      positions.get(node.id),
    );
    for (const child of children) {
      const pos = placements.get(child.id);
      if (pos) positions.set(child.id, pos);
    }
  }

  for (const node of nodes) {
    if (positions.has(node.id)) continue;
    const parentPos = node.parentId ? positions.get(node.parentId) : undefined;
    const placement = placeArcChildren([node], parentPos, {
      baseRadius: LAYOUT.NODE_SCATTER_DIST,
      radialStep: LAYOUT.NODE_SCATTER_STEP,
      minSpacing: Math.max(68, nodeExtent(node) * 1.6),
      arcCenter: hashAngle(node.id),
      arcSpan: Math.PI / 3,
    }).get(node.id);
    if (placement) positions.set(node.id, placement);
  }

  return positions;
}

/**
 * Return the display radius used for a realm or cluster zone circle.
 * Exported so the renderer can draw it without re-computing.
 */
export function zoneRadius(typeId: 'realm' | 'cluster'): number {
  return typeId === 'realm' ? LAYOUT.REALM_INNER_RADIUS : LAYOUT.CLUSTER_INNER_RADIUS;
}

export function computeLayoutBounds(
  topology: Topology,
  positions: Map<string, NodePosition>,
): LayoutBounds | null {
  if (topology.nodes.length === 0) return null;

  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;

  for (const node of topology.nodes) {
    const pos = positions.get(node.id);
    if (!pos) continue;

    let extentX = nodeExtent(node);
    let extentY = nodeExtent(node);

    if (node.typeId === 'realm' || node.typeId === 'cluster') {
      const radius = pos.zoneRadius ?? zoneRadius(node.typeId);
      extentX = radius + 36;
      extentY = radius + 48;
    } else if (node.typeId === 'namespace') {
      extentX = Math.max((pos.containerWidth ?? LAYOUT.NAMESPACE_INNER_RADIUS * 2) / 2, 48) + 28;
      extentY = Math.max((pos.containerHeight ?? LAYOUT.NAMESPACE_INNER_RADIUS * 2) / 2, 48) + 38;
    } else if (node.typeId === 'host' || node.typeId === 'run') {
      extentX = (pos.containerWidth ?? HOST_HALF_W * 2) / 2 + 18;
      extentY = (pos.containerHeight ?? HOST_HALF_H * 2) / 2 + 24;
    }

    minX = Math.min(minX, pos.x - extentX);
    minY = Math.min(minY, pos.y - extentY);
    maxX = Math.max(maxX, pos.x + extentX);
    maxY = Math.max(maxY, pos.y + extentY);
  }

  if (!Number.isFinite(minX) || !Number.isFinite(minY)) return null;
  return { minX, minY, maxX, maxY };
}
