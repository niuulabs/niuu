/**
 * The lines the rail puts under each row.
 *
 * Every figure here is derived from the topology the adapters actually sent —
 * a cluster's node and GPU counts come from the hosts discovered inside it,
 * not from a field somebody has to remember to stamp. Where nothing was
 * discovered the value is `null` and the row says nothing rather than zero.
 */

import type { Topology, TopologyNode } from './index';
import { computeClassMap, type ComputeClass } from './computeClass';

function numberField(node: TopologyNode, key: string): number | null {
  const value = (node as unknown as Record<string, unknown>)[key];
  return typeof value === 'number' ? value : null;
}

function textField(node: TopologyNode, key: string): string {
  const value = (node as unknown as Record<string, unknown>)[key];
  return typeof value === 'string' ? value : '';
}

/** Sum a per-host field across a set, or null when no host reported it. */
function sumOf(nodes: readonly TopologyNode[], key: string): number | null {
  const values = nodes
    .map((node) => numberField(node, key))
    .filter((value): value is number => value !== null);
  return values.length ? values.reduce((total, value) => total + value, 0) : null;
}

/**
 * The cluster a node belongs to, resolved through containment.
 *
 * Not every adapter stamps `cluster` on a leaf, but the parent chain always
 * says the same thing. Without this a resident discovered inside a cluster
 * reads as "local", which is the one label that must not be guessed — it is
 * the difference between a Kubernetes workload and a box under a desk.
 */
export function clusterNameOf(node: TopologyNode, byId: Map<string, TopologyNode>): string {
  if (node.cluster) return node.cluster;
  const seen = new Set<string>([node.id]);
  let current: TopologyNode | undefined = node;
  while (current?.parentId && !seen.has(current.parentId)) {
    seen.add(current.parentId);
    current = byId.get(current.parentId);
    if (!current) return '';
    if (current.typeId === 'cluster') return current.label;
    if (current.cluster) return current.cluster;
  }
  return '';
}

/** Index a topology once, for the resolvers above. */
export function nodeIndex(topology: Topology | null): Map<string, TopologyNode> {
  return new Map((topology?.nodes ?? []).map((node) => [node.id, node]));
}

export interface ClusterSummary {
  node: TopologyNode;
  /** Realm the cluster sits in, by name. Empty when it belongs to none. */
  realm: string;
  /** Hosts discovered inside it. */
  hosts: number;
  /** GPUs across those hosts, or null when no host reported any. */
  gpus: number | null;
  /** Pods across those hosts, or null when nothing reported a count. */
  pods: number | null;
  computeClass: ComputeClass;
}

/**
 * One row per cluster, with its realm and what was found inside it.
 *
 * Realms and clusters were two rail sections listing the same estate twice —
 * a realm on its own says only that a VLAN exists. Naming the realm on the
 * cluster's own row says the same thing in half the space.
 */
export function clusterSummaries(topology: Topology | null): ClusterSummary[] {
  const nodes = topology?.nodes ?? [];
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const classes = computeClassMap(nodes);

  const realmOf = (cluster: TopologyNode): string => {
    if (cluster.realm) return cluster.realm;
    const parent = cluster.parentId ? byId.get(cluster.parentId) : undefined;
    return parent?.typeId === 'realm' ? parent.label : '';
  };

  const hostsByCluster = new Map<string, TopologyNode[]>();
  for (const node of nodes) {
    if (node.typeId !== 'host') continue;
    const key = clusterNameOf(node, byId);
    if (!key) continue;
    const bucket = hostsByCluster.get(key);
    if (bucket) bucket.push(node);
    else hostsByCluster.set(key, [node]);
  }

  return nodes
    .filter((node) => node.typeId === 'cluster')
    .map((node) => {
      const hosts = hostsByCluster.get(node.label) ?? [];
      return {
        node,
        realm: realmOf(node),
        hosts: hosts.length,
        gpus: sumOf(hosts, 'gpuCount'),
        pods: sumOf(hosts, 'pods') ?? numberField(node, 'pods'),
        computeClass: classes.get(node.id) ?? 'k8s',
      };
    })
    .sort((a, b) => a.node.label.localeCompare(b.node.label));
}

/**
 * `ravn · saehrimnir` — what a resident runs on, and the box it runs on.
 *
 * The host wins over the cluster when both are known: "which machine" is the
 * more specific answer, and the cluster is already one row down the rail.
 * Falls back to `local` only when containment places it nowhere at all — a
 * resident on a workstation is the case that word is for.
 */
export function residentSubtitle(node: TopologyNode, byId?: Map<string, TopologyNode>): string {
  const engine = textField(node, 'engine') || textField(node, 'runtime');
  const where =
    textField(node, 'hostId') || (byId ? clusterNameOf(node, byId) : node.cluster) || 'local';
  return [engine, where].filter(Boolean).join(' · ');
}

/** The mesh a node belongs to, if it belongs to one. */
export function meshOf(node: TopologyNode): string | null {
  const flock = textField(node, 'flockId');
  return flock || null;
}

/** `ymir · yggdrasil` — the placement line under a Mímir or a workload. */
export function placementSubtitle(node: TopologyNode, byId?: Map<string, TopologyNode>): string {
  const cluster = byId ? clusterNameOf(node, byId) : node.cluster;
  return [cluster, node.realm]
    .filter((part): part is string => typeof part === 'string' && part.length > 0)
    .join(' · ');
}

/**
 * What a Mímir holds. Pages is the figure an operator asks for first, and it
 * is one an instance actually reports — unlike the knowledge/metrics split,
 * which nothing on the wire currently distinguishes.
 */
export function mimirBadge(node: TopologyNode): string | null {
  const pages = numberField(node, 'pages');
  return pages === null ? null : `${pages.toLocaleString()}p`;
}

/**
 * What a mesh is for, read off the members it actually has.
 *
 * A mesh exists to do something, and its members say what: a resident's
 * specialty and a run agent's role are exactly the `build · verify · ship`
 * the design calls for, without anyone having to write a description that
 * would then go stale. Where members declare nothing, fall back to the
 * clusters the mesh reaches across — scattered members are the other thing
 * worth knowing about a mesh.
 */
export function meshSubtitle(memberIds: readonly string[], topology: Topology | null): string {
  const byId = nodeIndex(topology);
  const doing: string[] = [];
  const where = new Set<string>();
  let workflow = false;

  for (const id of memberIds) {
    const member = byId.get(id);
    if (!member) continue;
    if (member.typeId === 'run') workflow = true;
    where.add(clusterNameOf(member, byId) || 'local');
    const what = textField(member, 'specialty') || textField(member, 'role');
    if (what && !doing.includes(what)) doing.push(what);
  }

  const places = [...where].sort().join(' · ');
  if (doing.length > 0) return doing.slice(0, 3).join(' · ');
  return workflow ? ['workflow', places].filter(Boolean).join(' · ') : places;
}
