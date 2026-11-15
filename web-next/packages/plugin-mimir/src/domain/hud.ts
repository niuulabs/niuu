/**
 * Pure rules behind the memory heads-up display: finding a node by name,
 * what counts as a recent change, the focused item's "this week" line, the
 * replay scrubber's dated marks, and the kind filters.
 */
import type { GraphNode, MimirGraph } from './api-types';
import { neighborsWithinDepth } from './graphNeighbors';
import { relationLabel } from './relationLabel';

const DAY_MS = 24 * 60 * 60 * 1000;

/** "Recent" is "this week": a change within this many days of the time shown glows amber. */
export const RECENT_WINDOW_DAYS = 7;
export const RECENT_WINDOW_MS = RECENT_WINDOW_DAYS * DAY_MS;

/** True when `iso` falls within the recent window ending at `refMs` (and not after it). */
export function isRecent(iso: string | null | undefined, refMs: number): boolean {
  if (!iso) return false;
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return false;
  return at <= refMs && refMs - at <= RECENT_WINDOW_MS;
}

/** A node changed (its newest timeline row) or first appeared within the recent window. */
export function isRecentNode(
  node: Pick<GraphNode, 'changedAt' | 'firstSeen'>,
  refMs: number,
): boolean {
  return isRecent(node.changedAt, refMs) || isRecent(node.firstSeen, refMs);
}

function compact(value: string): string {
  return value.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '');
}

function words(value: string): string[] {
  return value
    .toLowerCase()
    .split(/[^\p{L}\p{N}]+/u)
    .filter((w) => w.length > 0);
}

/** How well `node` matches `query`: lower is better, null is no match. */
function matchRank(node: GraphNode, query: string, queryCompact: string): number | null {
  const title = compact(node.title);
  if (title === queryCompact) return 0;
  if (title.startsWith(queryCompact)) return 1;
  const queryWords = words(query);
  const titleWords = words(node.title);
  if (queryWords.every((qw) => titleWords.some((tw) => tw.startsWith(qw)))) return 2;
  if (title.includes(queryCompact)) return 3;
  if (compact(node.path).includes(queryCompact)) return 4;
  return null;
}

/**
 * Every node whose name matches `query`, best first: exact name, name
 * prefix, every word a word prefix, name substring, then path substring.
 * Punctuation and case are ignored, so "booking cutover" finds "Booking
 * cut-over". An empty query matches nothing.
 */
export function searchNodes(nodes: readonly GraphNode[], query: string): GraphNode[] {
  const queryCompact = compact(query);
  if (queryCompact.length === 0) return [];
  const ranked: Array<{ node: GraphNode; rank: number }> = [];
  for (const node of nodes) {
    const rank = matchRank(node, query, queryCompact);
    if (rank !== null) ranked.push({ node, rank });
  }
  ranked.sort(
    (a, b) =>
      a.rank - b.rank ||
      a.node.title.length - b.node.title.length ||
      a.node.title.localeCompare(b.node.title),
  );
  return ranked.map((r) => r.node);
}

export interface Neighbourhood {
  /** Nodes one link away. */
  connections: number;
  /** Nodes two links away that are not also one link away. */
  nearby: number;
}

export function neighbourhood(graph: MimirGraph, nodeId: string): Neighbourhood {
  const links = neighborsWithinDepth(graph, nodeId, 2);
  return {
    connections: links.filter((l) => l.hops === 1).length,
    nearby: links.filter((l) => l.hops === 2).length,
  };
}

/** "12 Nov" for an ISO date or time, read in UTC so a date never shifts by a day. */
export function formatDay(iso: string): string {
  return new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso).toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
    timeZone: 'UTC',
  });
}

/** How a link reads from the focused node's side: "LIN-501 moved it", "replaces Legacy booking API". */
function linkPhrase(focusId: string, source: string, relation: string, other: GraphNode): string {
  return source === focusId ? `${relation} ${other.title}` : `${other.title} ${relation} it`;
}

/**
 * The focused item's "this week" line: links introduced within the recent
 * window, newest first, else when the page itself changed or first
 * appeared. Null when nothing changed recently.
 */
export function recentChangeLine(graph: MimirGraph, focusId: string, refMs: number): string | null {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const focus = byId.get(focusId);
  if (!focus) return null;

  const phrases = graph.edges
    .filter((e) => (e.source === focusId || e.target === focusId) && isRecent(e.since, refMs))
    .sort((a, b) => (b.since ?? '').localeCompare(a.since ?? ''))
    .flatMap((e) => {
      const other = byId.get(e.source === focusId ? e.target : e.source);
      const relation = relationLabel(e.relation) ?? 'linked';
      return other
        ? [`${linkPhrase(focusId, e.source, relation, other)} (${formatDay(e.since!)})`]
        : [];
    });
  if (phrases.length > 0) return `This week: ${phrases.join('; ')}.`;
  if (isRecent(focus.firstSeen, refMs))
    return `New this week: first seen ${formatDay(focus.firstSeen)}.`;
  if (isRecent(focus.changedAt, refMs)) return `This week: changed ${formatDay(focus.changedAt!)}.`;
  return null;
}

export interface ReplayMark {
  /** ISO date (YYYY-MM-DD). */
  date: string;
  /** What happened that day, e.g. "planned in, owns". */
  label: string;
  recent: boolean;
}

/**
 * The focused node's dated history for the replay scrubber: the day it
 * was first seen and the day each of its links was introduced, one mark
 * per day, oldest first.
 */
export function replayMarks(graph: MimirGraph, focusId: string, refMs: number): ReplayMark[] {
  const focus = graph.nodes.find((n) => n.id === focusId);
  if (!focus) return [];
  const byDay = new Map<string, string[]>();
  const add = (date: string, label: string): void => {
    const labels = byDay.get(date) ?? byDay.set(date, []).get(date)!;
    if (!labels.includes(label)) labels.push(label);
  };
  if (focus.firstSeen) add(focus.firstSeen.slice(0, 10), 'first seen');
  for (const edge of graph.edges) {
    if (edge.source !== focusId && edge.target !== focusId) continue;
    if (!edge.since) continue;
    add(edge.since.slice(0, 10), relationLabel(edge.relation) ?? 'linked');
  }
  return [...byDay.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, labels]) => ({ date, label: labels.join(', '), recent: isRecent(date, refMs) }));
}

export type HudFilterId = 'all' | 'people' | 'decisions' | 'tickets';

export interface HudFilter {
  id: HudFilterId;
  label: string;
  /** The node kinds kept; null keeps every kind. */
  kinds: readonly string[] | null;
}

export const HUD_FILTERS: readonly HudFilter[] = [
  { id: 'all', label: 'Everything', kinds: null },
  { id: 'people', label: 'People', kinds: ['person'] },
  { id: 'decisions', label: 'Decisions', kinds: ['decision'] },
  { id: 'tickets', label: 'Tickets', kinds: ['ticket'] },
];

/** The kinds a filter keeps, as the set the scene takes; null for Everything. */
export function filterKinds(id: HudFilterId): ReadonlySet<string> | null {
  const kinds = HUD_FILTERS.find((f) => f.id === id)!.kinds;
  return kinds ? new Set(kinds) : null;
}
