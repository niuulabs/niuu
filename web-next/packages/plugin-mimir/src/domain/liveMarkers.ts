/**
 * Scene markers — the latest live activity per node, within a recency
 * window, mapped from `LiveActivity` (keyed by page `path`) to the scene's
 * `SceneMarker` (keyed by graph node `id`).
 */
import type { LiveActivity, LiveActivityKind, MimirGraph } from './api-types';

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
 * `now`, newest first. An activity event without a matching graph node
 * (by `path` within its `mount`, falling back to any node with that path)
 * is dropped — a marker cannot point nowhere.
 */
export function recentMarkers(
  activity: LiveActivity[],
  graph: MimirGraph,
  now: Date = new Date(),
  windowMs: number = MARKER_RECENCY_WINDOW_MS,
): LiveMarker[] {
  const cutoff = now.getTime() - windowMs;
  const nodeIdForPath = new Map<string, string>();
  for (const node of graph.nodes) {
    const key = node.path ?? node.id;
    if (!nodeIdForPath.has(key)) nodeIdForPath.set(key, node.id);
  }

  const latestByNode = new Map<string, LiveActivity>();
  for (const entry of activity) {
    if (new Date(entry.timestamp).getTime() < cutoff) continue;
    const nodeId = nodeIdForPath.get(entry.path);
    if (!nodeId) continue;
    const existing = latestByNode.get(nodeId);
    if (!existing || existing.timestamp < entry.timestamp) {
      latestByNode.set(nodeId, entry);
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
