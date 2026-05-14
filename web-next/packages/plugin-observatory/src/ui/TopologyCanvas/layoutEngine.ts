/**
 * Deterministic, sibling-aware layout engine.
 *
 * Instead of scattering children from hashes alone, this layout uses
 * containment-aware arcs and concentric bands so dense local clusters can
 * breathe without overlapping or collapsing into a single knot.
 */

import type { Topology, TopologyNode } from '../../domain';
import { LAYOUT, NODE_SIZE } from './config';

export interface NodePosition {
  x: number;
  y: number;
  zoneRadius?: number;
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
export const HOST_HALF_W = 50;
/** Host rounded-rect half-height in world units. */
export const HOST_HALF_H = 30;

const TYPE_PRIORITY: Record<string, number> = {
  mimir: 0,
  realm: 1,
  cluster: 2,
  host: 3,
  tyr: 4,
  bifrost: 5,
  volundr: 6,
  ravn_long: 7,
  ravn_raid: 8,
  raid: 9,
  service: 10,
  model: 11,
};

function sortedNodes(nodes: TopologyNode[]): TopologyNode[] {
  return [...nodes].sort((a, b) => {
    const priority = (TYPE_PRIORITY[a.typeId] ?? 100) - (TYPE_PRIORITY[b.typeId] ?? 100);
    if (priority !== 0) return priority;
    const label = a.label.localeCompare(b.label, undefined, { sensitivity: 'base' });
    if (label !== 0) return label;
    return a.id.localeCompare(b.id);
  });
}

function nodeExtent(node: TopologyNode): number {
  if (node.typeId === 'host') return Math.max(HOST_HALF_W, HOST_HALF_H) + 18;
  if (node.typeId === 'raid') return 72;
  return (NODE_SIZE[node.typeId] ?? 8) + 18;
}

function maxNodeExtent(nodes: TopologyNode[]): number {
  return nodes.reduce((max, node) => Math.max(max, nodeExtent(node)), 0);
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

const CLUSTER_SERVICE_SLOT_ANGLES: Partial<Record<string, number>> = {
  observatory: -Math.PI / 2,
  niuu: (-3 * Math.PI) / 4,
  volundr: Math.PI,
  mimir: (3 * Math.PI) / 4,
  tyr: Math.PI / 2,
  bifrost: Math.PI / 4,
  ravn: 0,
};

function ringCapacity(radius: number, arcSpan: number, minSpacing: number): number {
  const circumference = Math.max(radius * arcSpan, minSpacing);
  return Math.max(1, Math.floor(circumference / Math.max(minSpacing, 1)));
}

function ringCount(
  count: number,
  {
    baseRadius,
    radialStep,
    minSpacing,
    arcSpan = Math.PI * 2,
  }: {
    baseRadius: number;
    radialStep: number;
    minSpacing: number;
    arcSpan?: number;
  },
): number {
  let remaining = count;
  let radius = baseRadius;
  let rings = 0;

  while (remaining > 0) {
    rings += 1;
    remaining -= ringCapacity(radius, arcSpan, minSpacing);
    radius += radialStep;
  }

  return rings;
}

function containerRadius(
  children: TopologyNode[],
  options: ArcPlacementOptions,
  fallback: number,
  padding: number,
): number {
  if (children.length === 0) return fallback;
  const arcSpan = options.arcSpan ?? Math.PI * 2;
  const rings = ringCount(children.length, { ...options, arcSpan });
  const outerRadius = options.baseRadius + Math.max(0, rings - 1) * options.radialStep;
  return Math.max(fallback, outerRadius + maxNodeExtent(children) + padding);
}

function placeArcChildren(
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

function clusterServiceRole(node: TopologyNode): string | null {
  if (node.typeId === 'mimir' || node.typeId === 'tyr' || node.typeId === 'bifrost' || node.typeId === 'volundr') {
    return node.typeId;
  }
  if (node.typeId === 'service' && node.svcType) {
    return node.svcType;
  }
  return null;
}

function clusterPartitions(children: TopologyNode[]) {
  const core: TopologyNode[] = [];
  const ravens: TopologyNode[] = [];
  const raids: TopologyNode[] = [];
  const generic: TopologyNode[] = [];

  for (const child of children) {
    if (child.typeId === 'raid') {
      raids.push(child);
      continue;
    }
    if (child.typeId === 'ravn_long' || child.typeId === 'ravn_raid') {
      ravens.push(child);
      continue;
    }
    if (clusterServiceRole(child)) {
      core.push(child);
      continue;
    }
    generic.push(child);
  }

  return { core, ravens, raids, generic };
}

function placeClusterCoreChildren(
  children: TopologyNode[],
  anchor: NodePosition | undefined,
): Map<string, NodePosition> {
  const placements = new Map<string, NodePosition>();
  if (children.length === 0) return placements;

  const base = anchor ?? { x: 0, y: 0 };
  const fallback: TopologyNode[] = [];

  for (const child of children) {
    const role = clusterServiceRole(child);
    const slotAngle = role ? CLUSTER_SERVICE_SLOT_ANGLES[role] : undefined;
    if (slotAngle == null) {
      fallback.push(child);
      continue;
    }
    placements.set(child.id, {
      x: base.x + Math.cos(slotAngle) * LAYOUT.CLUSTER_CORE_ORBIT,
      y: base.y + Math.sin(slotAngle) * LAYOUT.CLUSTER_CORE_ORBIT,
    });
  }

  if (fallback.length > 0) {
    const fallbackPlacements = placeArcChildren(fallback, anchor, {
      baseRadius: LAYOUT.CLUSTER_CORE_ORBIT,
      radialStep: LAYOUT.CLUSTER_CHILD_STEP,
      minSpacing: Math.max(104, maxNodeExtent(fallback) * 1.8),
      arcCenter: -Math.PI / 2,
      arcSpan: Math.PI * 0.9,
    });
    for (const child of fallback) {
      const pos = fallbackPlacements.get(child.id);
      if (pos) placements.set(child.id, pos);
    }
  }

  return placements;
}

/**
 * Compute layout positions for all nodes in a topology snapshot.
 */
export function computeLayout(topology: Topology): Map<string, NodePosition> {
  const positions = new Map<string, NodePosition>();
  const { nodes } = topology;
  const childrenByParent = buildChildrenIndex(nodes);
  const clusterRadii = new Map<string, number>();
  const realmRadii = new Map<string, number>();

  const realmNodes = sortedNodes(nodes.filter((node) => node.typeId === 'realm'));
  const clusterNodes = sortedNodes(nodes.filter((node) => node.typeId === 'cluster'));
  const hostNodes = sortedNodes(nodes.filter((node) => node.typeId === 'host'));
  const raidNodes = sortedNodes(nodes.filter((node) => node.typeId === 'raid'));
  for (const node of clusterNodes) {
    const children = (childrenByParent.get(node.id) ?? []).filter(
      (child) => child.typeId !== 'host' && child.typeId !== 'cluster',
    );
    const { core, ravens, raids, generic } = clusterPartitions(children);
    const coreExtent =
      core.length === 0 ? 0 : LAYOUT.CLUSTER_CORE_ORBIT + maxNodeExtent(core) + 34;
    const ravenExtent =
      ravens.length === 0
        ? 0
        : containerRadius(
            ravens,
            {
              baseRadius: LAYOUT.CLUSTER_RAVEN_ORBIT,
              radialStep: LAYOUT.CLUSTER_RAVEN_STEP,
              minSpacing: Math.max(92, maxNodeExtent(ravens) * 1.9),
              arcCenter: 0,
              arcSpan: Math.PI * 0.92,
            },
            0,
            maxNodeExtent(ravens) + 30,
          );
    const raidExtent =
      raids.length === 0
        ? 0
        : containerRadius(
            raids,
            {
              baseRadius: LAYOUT.CLUSTER_RAID_ORBIT,
              radialStep: LAYOUT.CLUSTER_RAID_STEP,
              minSpacing: Math.max(132, maxNodeExtent(raids) * 1.9),
              arcCenter: Math.PI / 2,
              arcSpan: Math.PI * 0.96,
            },
            0,
            maxNodeExtent(raids) + 34,
          );
    const genericExtent =
      generic.length === 0
        ? 0
        : containerRadius(
            generic,
            {
              baseRadius: LAYOUT.CLUSTER_GENERIC_ORBIT,
              radialStep: LAYOUT.CLUSTER_GENERIC_STEP,
              minSpacing: Math.max(104, maxNodeExtent(generic) * 1.9),
              arcCenter: -Math.PI / 2,
              arcSpan: Math.PI * 0.98,
            },
            0,
            maxNodeExtent(generic) + 30,
          );
    clusterRadii.set(
      node.id,
      Math.max(
        LAYOUT.CLUSTER_INNER_RADIUS,
        coreExtent,
        ravenExtent,
        raidExtent,
        genericExtent,
      ),
    );
  }

  for (const node of realmNodes) {
    const children = childrenByParent.get(node.id) ?? [];
    const clusters = children.filter((child) => child.typeId === 'cluster');
    const hosts = children.filter((child) => child.typeId === 'host');
    const directOthers = children.filter(
      (child) => child.typeId !== 'cluster' && child.typeId !== 'host',
    );

    const clusterSpacing = Math.max(
      220,
      clusters.reduce(
        (max, child) =>
          Math.max(max, (clusterRadii.get(child.id) ?? LAYOUT.CLUSTER_INNER_RADIUS) * 1.55),
        220,
      ),
    );
    const clusterExtent =
      clusters.length === 0
        ? 0
        : containerRadius(
            clusters,
            {
              baseRadius: LAYOUT.REALM_CLUSTER_ORBIT,
              radialStep: LAYOUT.REALM_CLUSTER_STEP,
              minSpacing: clusterSpacing,
              arcCenter: Math.PI / 2,
              arcSpan: Math.PI * 1.82,
            },
            0,
            clusters.reduce(
              (max, child) =>
                Math.max(max, clusterRadii.get(child.id) ?? LAYOUT.CLUSTER_INNER_RADIUS),
              0,
            ) + 18,
          );

    const hostExtent =
      hosts.length === 0
        ? 0
        : containerRadius(
            hosts,
            {
              baseRadius: LAYOUT.REALM_HOST_ORBIT,
              radialStep: LAYOUT.REALM_HOST_STEP,
              minSpacing: Math.max(180, maxNodeExtent(hosts) * 1.85),
              arcCenter: Math.PI / 2,
              arcSpan: Math.PI * 1.58,
            },
            0,
            HOST_HALF_W + 28,
          );

    const directExtent =
      directOthers.length === 0
        ? 0
        : containerRadius(
            directOthers,
            {
              baseRadius: LAYOUT.REALM_DEVICE_ORBIT,
              radialStep: LAYOUT.REALM_DEVICE_STEP,
              minSpacing: Math.max(120, maxNodeExtent(directOthers) * 1.7),
              arcCenter: Math.PI / 2,
              arcSpan: Math.PI * 1.6,
            },
            0,
            maxNodeExtent(directOthers) + 24,
          );

    realmRadii.set(
      node.id,
      Math.max(LAYOUT.REALM_INNER_RADIUS, clusterExtent, hostExtent, directExtent),
    );
  }

  for (const node of nodes) {
    if (node.typeId === 'mimir' && (!node.parentId || !nodes.some((candidate) => candidate.id === node.parentId))) {
      positions.set(node.id, { x: 0, y: 0 });
    }
  }

  if (realmNodes.length === 1) {
    const [node] = realmNodes;
    if (!node) return positions;
    positions.set(node.id, {
      x: 0,
      y: 0,
      zoneRadius: realmRadii.get(node.id) ?? LAYOUT.REALM_INNER_RADIUS,
    });
  } else {
    const largestRealmRadius = realmNodes.reduce<number>(
      (max, node) => Math.max(max, realmRadii.get(node.id) ?? LAYOUT.REALM_INNER_RADIUS),
      LAYOUT.REALM_INNER_RADIUS,
    );
    const realmRingBase = Math.max(560, largestRealmRadius + 260);
    const realmRingStep = Math.max(260, largestRealmRadius * 0.9);
    const realmSpacing = Math.max(360, largestRealmRadius * 1.75);
    const realmPlacements = placeArcChildren(realmNodes, { x: 0, y: 0 }, {
      baseRadius: realmRingBase,
      radialStep: realmRingStep,
      minSpacing: realmSpacing,
      arcCenter: Math.PI / 2,
      arcSpan: Math.PI * 1.92,
    });
    for (const node of realmNodes) {
      const pos = realmPlacements.get(node.id);
      if (!pos) continue;
      positions.set(node.id, {
        ...pos,
        zoneRadius: realmRadii.get(node.id) ?? LAYOUT.REALM_INNER_RADIUS,
      });
    }
  }

  for (const realm of realmNodes) {
    const clusters = (childrenByParent.get(realm.id) ?? []).filter((child) => child.typeId === 'cluster');
    const parentPos = positions.get(realm.id);
    if (clusters.length === 1 && parentPos) {
      const onlyCluster = clusters[0]!;
      positions.set(onlyCluster.id, {
        ...parentPos,
        zoneRadius: clusterRadii.get(onlyCluster.id) ?? LAYOUT.CLUSTER_INNER_RADIUS,
      });
      continue;
    }
    const clusterSpacing = Math.max(
      220,
      clusters.reduce(
        (max, child) =>
          Math.max(max, (clusterRadii.get(child.id) ?? LAYOUT.CLUSTER_INNER_RADIUS) * 1.55),
        220,
      ),
    );
    const clusterPlacements = placeArcChildren(clusters, parentPos, {
      baseRadius: LAYOUT.REALM_CLUSTER_ORBIT,
      radialStep: LAYOUT.REALM_CLUSTER_STEP,
      minSpacing: clusterSpacing,
      arcCenter: Math.PI / 2,
      arcSpan: Math.PI * 1.82,
    });
    for (const node of clusters) {
      const pos = clusterPlacements.get(node.id);
      if (!pos) continue;
      positions.set(node.id, {
        ...pos,
        zoneRadius: clusterRadii.get(node.id) ?? LAYOUT.CLUSTER_INNER_RADIUS,
      });
    }
  }

  for (const realm of realmNodes) {
    const hosts = (childrenByParent.get(realm.id) ?? []).filter((child) => child.typeId === 'host');
    const hostPlacements = placeArcChildren(hosts, positions.get(realm.id), {
      baseRadius: LAYOUT.REALM_HOST_ORBIT,
      radialStep: LAYOUT.REALM_HOST_STEP,
      minSpacing: Math.max(180, maxNodeExtent(hosts) * 1.85),
      arcCenter: Math.PI / 2,
      arcSpan: Math.PI * 1.58,
    });
    for (const node of hosts) {
      const pos = hostPlacements.get(node.id);
      if (pos) positions.set(node.id, pos);
    }

    const directOthers = (childrenByParent.get(realm.id) ?? []).filter(
      (child) => child.typeId !== 'cluster' && child.typeId !== 'host',
    );
    const directPlacements = placeArcChildren(directOthers, positions.get(realm.id), {
      baseRadius: LAYOUT.REALM_DEVICE_ORBIT,
      radialStep: LAYOUT.REALM_DEVICE_STEP,
      minSpacing: Math.max(120, maxNodeExtent(directOthers) * 1.7),
      arcCenter: Math.PI / 2,
      arcSpan: Math.PI * 1.6,
    });
    for (const node of directOthers) {
      const pos = directPlacements.get(node.id);
      if (pos) positions.set(node.id, pos);
    }
  }

  for (const cluster of clusterNodes) {
    const children = (childrenByParent.get(cluster.id) ?? []).filter(
      (child) => child.typeId !== 'host' && child.typeId !== 'cluster',
    );
    const { core, ravens, raids, generic } = clusterPartitions(children);

    const corePlacements = placeClusterCoreChildren(core, positions.get(cluster.id));
    for (const child of core) {
      const pos = corePlacements.get(child.id);
      if (pos) positions.set(child.id, pos);
    }

    const ravenPlacements = placeArcChildren(ravens, positions.get(cluster.id), {
      baseRadius: LAYOUT.CLUSTER_RAVEN_ORBIT,
      radialStep: LAYOUT.CLUSTER_RAVEN_STEP,
      minSpacing: Math.max(92, maxNodeExtent(ravens) * 1.9),
      arcCenter: 0,
      arcSpan: Math.PI * 0.92,
    });
    for (const child of ravens) {
      const pos = ravenPlacements.get(child.id);
      if (pos) positions.set(child.id, pos);
    }

    const raidPlacements = placeArcChildren(raids, positions.get(cluster.id), {
      baseRadius: LAYOUT.CLUSTER_RAID_ORBIT,
      radialStep: LAYOUT.CLUSTER_RAID_STEP,
      minSpacing: Math.max(132, maxNodeExtent(raids) * 1.9),
      arcCenter: Math.PI / 2,
      arcSpan: Math.PI * 0.96,
    });
    for (const child of raids) {
      const pos = raidPlacements.get(child.id);
      if (pos) positions.set(child.id, pos);
    }

    const genericPlacements = placeArcChildren(generic, positions.get(cluster.id), {
      baseRadius: LAYOUT.CLUSTER_GENERIC_ORBIT,
      radialStep: LAYOUT.CLUSTER_GENERIC_STEP,
      minSpacing: Math.max(104, maxNodeExtent(generic) * 1.9),
      arcCenter: -Math.PI / 2,
      arcSpan: Math.PI * 0.98,
    });
    for (const child of generic) {
      const pos = genericPlacements.get(child.id);
      if (pos) positions.set(child.id, pos);
    }
  }

  for (const host of hostNodes) {
    const children = (childrenByParent.get(host.id) ?? []).filter((child) => child.typeId !== 'host');
    const placements = placeArcChildren(children, positions.get(host.id), {
      baseRadius: LAYOUT.HOST_CHILD_ORBIT,
      radialStep: LAYOUT.HOST_CHILD_STEP,
      minSpacing: Math.max(72, maxNodeExtent(children) * 1.75),
      arcCenter: 0,
      arcSpan: Math.PI * 1.7,
    });
    for (const child of children) {
      const pos = placements.get(child.id);
      if (pos) positions.set(child.id, pos);
    }
  }

  for (const raid of raidNodes) {
    const children = childrenByParent.get(raid.id) ?? [];
    const placements = placeArcChildren(children, positions.get(raid.id), {
      baseRadius: LAYOUT.RAID_CHILD_ORBIT,
      radialStep: LAYOUT.RAID_CHILD_STEP,
      minSpacing: Math.max(64, maxNodeExtent(children) * 1.7),
      arcCenter: Math.PI / 2,
      arcSpan: Math.PI * 1.75,
    });
    for (const child of children) {
      const pos = placements.get(child.id);
      if (pos) positions.set(child.id, pos);
    }
  }

  for (const node of sortedNodes(nodes)) {
    if (!positions.has(node.id)) continue;
    if (node.typeId === 'realm' || node.typeId === 'cluster' || node.typeId === 'host' || node.typeId === 'raid' || node.typeId === 'mimir') {
      continue;
    }
    const children = (childrenByParent.get(node.id) ?? []).filter((child) => !positions.has(child.id));
    if (children.length === 0) continue;
    const placements = placeArcChildren(children, positions.get(node.id), {
      baseRadius: Math.max(132, nodeExtent(node) + 72),
      radialStep: 76,
      minSpacing: Math.max(96, maxNodeExtent(children) * 2),
      arcCenter: -Math.PI / 2,
      arcSpan: Math.PI * 2,
    });
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
    } else if (node.typeId === 'host') {
      extentX = HOST_HALF_W + 18;
      extentY = HOST_HALF_H + 18;
    }

    minX = Math.min(minX, pos.x - extentX);
    minY = Math.min(minY, pos.y - extentY);
    maxX = Math.max(maxX, pos.x + extentX);
    maxY = Math.max(maxY, pos.y + extentY);
  }

  if (!Number.isFinite(minX) || !Number.isFinite(minY)) return null;
  return { minX, minY, maxX, maxY };
}
