/**
 * Scene markers — the latest live activity per node, within a recency
 * window, mapped from `LiveActivity` (keyed by page `path`) to the scene's
 * `SceneMarker` (keyed by graph node `id`).
 */
import type { LiveActivity, LiveActivityKind, MimirGraph } from './api-types';
import { nodeIndex } from './graphIndex';

/**
 * A live "someone is reading/writing this page right now" marker. Shaped to
 * match (but not imported from) `ui/scene/types.ts`'s `SceneMarker` —
 * domain does not depend on ui.
 */
export interface LiveMarker {
  id: string;
  nodeId: string;
  kind: LiveActivityKind;
  actor: string | null;
  timestamp: string;
}

/** How recent a live-activity event must be to still show as a scene marker. */
export const MARKER_RECENCY_WINDOW_MS = 10 * 60 * 1000;

/**
 * The latest activity per node, limited to events within `windowMs` of
 * `now`, newest first. An activity event is matched to a graph node by the
 * exact (`mount`, `path`) pair only — never by path alone, since two mounts
 * can carry the same page path. An event with no matching node is dropped.
 */
export function recentMarkers(
  activity: LiveActivity[],
  graph: MimirGraph,
  now: Date = new Date(),
  windowMs: number = MARKER_RECENCY_WINDOW_MS,
): LiveMarker[] {
  const cutoff = now.getTime() - windowMs;
  const index = nodeIndex(graph);

  const latestByNode = new Map<string, LiveActivity>();
  for (const entry of activity) {
    if (new Date(entry.timestamp).getTime() < cutoff) continue;
    const node = index.byMountPath(entry.mount, entry.path);
    if (!node) continue;
    const existing = latestByNode.get(node.id);
    if (!existing || existing.timestamp < entry.timestamp) {
      latestByNode.set(node.id, entry);
    }
  }

  return [...latestByNode.entries()]
    .sort((a, b) => b[1].timestamp.localeCompare(a[1].timestamp))
    .map(([nodeId, entry]) => ({
      id: entry.id,
      nodeId,
      kind: entry.kind,
      actor: entry.actor,
      timestamp: entry.timestamp,
    }));
}
