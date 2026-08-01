/**
 * Agent meshes — the groups of long-lived agents that collaborate directly.
 *
 * Membership is already carried on the topology as `flockId`; nothing derived
 * it into a first-class group before. A mesh is not a container: its members
 * are scattered across clusters and realms, which is precisely the point —
 * a finding by one member becomes evidence for the others.
 */

import type { Topology, TopologyNode } from './index';

/** Node types that participate in an agent mesh. */
const MESH_MEMBER_TYPES: ReadonlySet<string> = new Set([
  'ravn_long',
  'valkyrie',
  'ravn_run',
  'run',
]);

export interface AgentMesh {
  /** The `flockId` shared by every member. */
  id: string;
  /** Member node ids, in stable topology order. */
  memberIds: string[];
}

/** True when a node can belong to an agent mesh. */
export function isMeshMember(node: TopologyNode): boolean {
  return MESH_MEMBER_TYPES.has(node.typeId) && !!node.flockId;
}

/**
 * Group agent nodes by `flockId`.
 *
 * A single-member group is dropped: one agent on its own is not a mesh, and
 * drawing a hull around it would imply a collaboration that does not exist.
 * Order is stable so the rendered hull does not shuffle between frames.
 */
export function deriveAgentMeshes(topology: Topology | null): AgentMesh[] {
  if (!topology) return [];

  const byFlock = new Map<string, string[]>();
  for (const node of topology.nodes) {
    if (!isMeshMember(node)) continue;
    const flockId = node.flockId as string;
    const members = byFlock.get(flockId);
    if (members) members.push(node.id);
    else byFlock.set(flockId, [node.id]);
  }

  const meshes: AgentMesh[] = [];
  for (const [id, memberIds] of byFlock) {
    if (memberIds.length < 2) continue;
    meshes.push({ id, memberIds });
  }
  return meshes;
}

/** The mesh a given node belongs to, if any. */
export function findMeshForNode(meshes: AgentMesh[], nodeId: string | null): AgentMesh | null {
  if (!nodeId) return null;
  return meshes.find((mesh) => mesh.memberIds.includes(nodeId)) ?? null;
}
