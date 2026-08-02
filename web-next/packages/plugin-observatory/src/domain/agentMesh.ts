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
  /**
   * What to call the mesh.
   *
   * A workflow's flock is identified by its session id, which is a UUID — an
   * honest identifier and a useless name. The graph already carries something
   * readable: the flock's own node, or the session the members run inside.
   */
  label: string;
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

  const byId = new Map(topology.nodes.map((node) => [node.id, node]));
  const flockLabels = new Map(
    topology.nodes
      .filter((node) => node.typeId === 'flock' && node.flockId)
      .map((node) => [node.flockId as string, node.label]),
  );

  const meshes: AgentMesh[] = [];
  for (const [id, memberIds] of byFlock) {
    if (memberIds.length < 2) continue;
    meshes.push({ id, label: meshLabel(id, memberIds, byId, flockLabels), memberIds });
  }
  return meshes;
}

/**
 * A readable name for a mesh, from what the graph already knows.
 *
 * The flock's own node names it when there is one. Otherwise the members of a
 * workflow flock all sit inside the session running them, and that session is
 * named after the work — `research-campaign-investigate-…` rather than the
 * UUID that identifies it. Only when neither exists does the id have to serve
 * as the name.
 */
function meshLabel(
  id: string,
  memberIds: string[],
  byId: Map<string, TopologyNode>,
  flockLabels: Map<string, string>,
): string {
  const declared = flockLabels.get(id);
  if (declared) return declared;

  const parents = new Set(memberIds.map((memberId) => byId.get(memberId)?.parentId ?? null));
  if (parents.size === 1) {
    const [only] = parents;
    const parent = only ? byId.get(only) : undefined;
    if (parent?.label) return parent.label;
  }

  return id;
}

/** The mesh a given node belongs to, if any. */
export function findMeshForNode(meshes: AgentMesh[], nodeId: string | null): AgentMesh | null {
  if (!nodeId) return null;
  return meshes.find((mesh) => mesh.memberIds.includes(nodeId)) ?? null;
}
