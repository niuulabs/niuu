/**
 * Graph node identity — node ids are opaque, mount-qualified strings built
 * by the backend (`src/mimir/router.py`'s `/graph` endpoint) as
 * `quote(mount, safe='') + ':' + quote(path, safe='')`, NOT page paths.
 * Two different mounts can carry the same page path, so every lookup must
 * key on (mount, path), never on path alone — and a node's `id` must never
 * be treated as a path or handed to `IPageStore.getPage`/`getEvidence`.
 */
import type { GraphNode, MimirGraph } from './api-types';

/** Percent-encode exactly as Python's `urllib.parse.quote(s, safe='')` does. */
export function pyQuote(value: string): string {
  return encodeURIComponent(value).replace(
    /[!'()*]/g,
    (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`,
  );
}

/** The graph node id the backend builds for a (mount, path) pair. */
export function encodeNodeId(mount: string, path: string): string {
  return `${pyQuote(mount)}:${pyQuote(path)}`;
}

export interface NodeIndex {
  byId(id: string): GraphNode | undefined;
  byMountPath(mount: string, path: string): GraphNode | undefined;
}

const KEY_SEP = '\u0000';

/** Build id- and (mount,path)-keyed lookup maps over a graph's nodes. */
export function nodeIndex(graph: MimirGraph): NodeIndex {
  const byIdMap = new Map<string, GraphNode>();
  const byMountPathMap = new Map<string, GraphNode>();
  for (const node of graph.nodes) {
    byIdMap.set(node.id, node);
    if (node.mount && node.path) {
      byMountPathMap.set(`${node.mount}${KEY_SEP}${node.path}`, node);
    }
  }
  return {
    byId: (id) => byIdMap.get(id),
    byMountPath: (mount, path) => byMountPathMap.get(`${mount}${KEY_SEP}${path}`),
  };
}

export interface MountPageCount {
  mount: string;
  pages: number;
}

/** Pages per mount in the graph, most pages first (ties by name). */
export function pagesPerMount(graph: MimirGraph): MountPageCount[] {
  const counts = new Map<string, number>();
  for (const node of graph.nodes) counts.set(node.mount, (counts.get(node.mount) ?? 0) + 1);
  return [...counts.entries()]
    .map(([mount, pages]) => ({ mount, pages }))
    .sort((a, b) => b.pages - a.pages || a.mount.localeCompare(b.mount));
}
