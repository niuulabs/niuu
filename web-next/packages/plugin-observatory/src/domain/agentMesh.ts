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

/** How a mesh came to exist, which decides how long it lasts. */
export type MeshKind = 'workflow' | 'standing';

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
  /**
   * Whether this flock runs for a workflow or stands on its own.
   *
   * A workflow's flock is created with its session and dies with it; a
   * standing flock of residents outlives any one piece of work. They read
   * identically on the canvas otherwise, which made a transient room look
   * like part of the estate.
   */
  kind?: MeshKind;
  /**
   * The transport its members actually talk over — `nats`, `nng`, whatever
   * comes next. Absent when no source declared one; never guessed, because a
   * mesh drawn as peering over a protocol it does not use is worse than a
   * mesh that admits it does not know.
   */
  transport?: string;
  /**
   * What the mesh is for, where its source says so — a flock's domain reads
   * better than three residents' specialties stitched together.
   */
  purpose?: string;
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
  const flockNodes = new Map(
    topology.nodes
      .filter((node) => node.typeId === 'flock' && node.flockId)
      .map((node) => [node.flockId as string, node]),
  );

  const meshes: AgentMesh[] = [];
  for (const [id, memberIds] of byFlock) {
    if (memberIds.length < 2) continue;
    // The flock's own node speaks for the mesh where there is one; otherwise
    // the members do, and they all carry the same declaration.
    const speakers = [flockNodes.get(id), ...memberIds.map((memberId) => byId.get(memberId))];
    const kind = firstDeclared(speakers, (node) => node.meshKind);
    const transport = firstDeclared(speakers, (node) => node.meshTransport);
    const purpose = firstDeclared(speakers, (node) => node.purpose);
    meshes.push({
      id,
      label: meshLabel(id, memberIds, byId, flockNodes),
      memberIds,
      ...(kind === 'workflow' || kind === 'standing' ? { kind } : {}),
      ...(transport ? { transport } : {}),
      ...(purpose ? { purpose } : {}),
    });
  }
  return meshes;
}

/** The first value any of these nodes actually declared, if any did. */
function firstDeclared(
  nodes: Array<TopologyNode | undefined>,
  read: (node: TopologyNode) => string | undefined,
): string | undefined {
  for (const node of nodes) {
    if (!node) continue;
    const value = read(node)?.trim();
    if (value) return value;
  }
  return undefined;
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
  flockNodes: Map<string, TopologyNode>,
): string {
  const declared = flockNodes.get(id)?.label;
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
