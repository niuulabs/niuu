/**
 * Deterministic layout engine.
 *
 * Computes world-space (x, y) positions for every node in a Topology
 * snapshot using only the node's id and parentId — no random values.
 * Identical inputs always produce identical outputs across calls and renders.
 */

import type { Topology, TopologyNode } from '../../domain';
import { LAYOUT } from './config';

export interface NodePosition {
  x: number;
  y: number;
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

function placeNearParent(
  node: TopologyNode,
  parentPos: NodePosition | undefined,
  dist: number,
): NodePosition {
  const anchor = parentPos ?? { x: 0, y: 0 };
  const angle = hashAngle(node.id);
  return {
    x: anchor.x + Math.cos(angle) * dist,
    y: anchor.y + Math.sin(angle) * dist,
  };
}

/**
 * Compute layout positions for all nodes in a topology snapshot.
 *
 * Pass order (each pass resolves parents before children):
 * 1. Mímir  → anchored at (0, 0)
 * 2. Realms → hash-based angle, fixed radius from origin
 * 3. Clusters → inside parent realm
 * 4. Hosts  → around parent realm perimeter
 * 5. Sub-Mímir → orbiting at SUB_MIMIR_RING from primary Mímir
 * 6. Everything else → scattered near parent (cluster / host / realm)
 */
export function computeLayout(topology: Topology): Map<string, NodePosition> {
  const positions = new Map<string, NodePosition>();
  const { nodes } = topology;

  // Pass 1 — Mímir at origin
  for (const node of nodes) {
    if (node.typeId === 'mimir') {
      positions.set(node.id, { x: 0, y: 0 });
    }
  }

  // Pass 2 — Realms at hash-based angle, REALM_RING_RADIUS from origin
  for (const node of nodes) {
    if (node.typeId !== 'realm') continue;
    const angle = hashAngle(node.id);
    positions.set(node.id, {
      x: Math.cos(angle) * LAYOUT.REALM_RING_RADIUS,
      y: Math.sin(angle) * LAYOUT.REALM_RING_RADIUS,
    });
  }

  // Pass 3 — Clusters inside parent realm
  for (const node of nodes) {
    if (node.typeId !== 'cluster') continue;
    const parentPos = node.parentId ? positions.get(node.parentId) : undefined;
    positions.set(node.id, placeNearParent(node, parentPos, LAYOUT.CLUSTER_RING_DIST));
  }

  // Pass 4 — Hosts around parent realm perimeter
  for (const node of nodes) {
    if (node.typeId !== 'host') continue;
    const parentPos = node.parentId ? positions.get(node.parentId) : undefined;
    positions.set(node.id, placeNearParent(node, parentPos, LAYOUT.HOST_RING_DIST));
  }

  // Pass 5 — Sub-Mímir nodes orbit at SUB_MIMIR_RING from the primary Mímir
  for (const node of nodes) {
    if (node.typeId !== 'mimir_sub') continue;
    const parentPos = node.parentId ? positions.get(node.parentId) : undefined;
    positions.set(node.id, placeNearParent(node, parentPos, LAYOUT.SUB_MIMIR_RING));
  }

  // Pass 6 — Remaining nodes (tyr, bifrost, volundr, ravn_long, ravn_raid,
  //           skuld, valkyrie, service, model, printer, vaettir, beacon,
  //           raid, …) near their parent.
  for (const node of nodes) {
    if (positions.has(node.id)) continue;
    const parentPos = node.parentId ? positions.get(node.parentId) : undefined;
    positions.set(node.id, placeNearParent(node, parentPos, LAYOUT.NODE_SCATTER_DIST));
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
