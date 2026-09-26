/**
 * Replay-mode timeline helpers — per-day histogram of pages by `firstSeen`,
 * and the named playback-speed constants for the scrubber.
 */
import type { GraphNode } from './api-types';

/** Playback speed multipliers offered by the replay timeline. */
export const REPLAY_SPEEDS = [1, 8, 32] as const;
export type ReplaySpeed = (typeof REPLAY_SPEEDS)[number];

/** Wall-clock interval between playback ticks. */
export const REPLAY_TICK_MS = 400;

/** Simulated days advanced per tick at 1x speed. */
export const REPLAY_BASE_DAYS_PER_TICK = 1;

/** Simulated days advanced in one playback tick at the given speed. */
export function daysPerTick(speed: ReplaySpeed): number {
  return REPLAY_BASE_DAYS_PER_TICK * speed;
}

export interface DayCount {
  /** ISO date (YYYY-MM-DD). */
  date: string;
  count: number;
}

function toDateKey(iso: string): string {
  return iso.slice(0, 10);
}

/** Earliest `firstSeen` date across all nodes, as an ISO date. Null when `nodes` is empty. */
export function earliestFirstSeen(nodes: Pick<GraphNode, 'firstSeen'>[]): string | null {
  if (nodes.length === 0) return null;
  return nodes.reduce(
    (earliest, node) =>
      toDateKey(node.firstSeen) < earliest ? toDateKey(node.firstSeen) : earliest,
    toDateKey(nodes[0]!.firstSeen),
  );
}

/**
 * Per-day counts of pages by `firstSeen`, covering every day from `from` to
 * `to` inclusive (both ISO dates), even days with zero pages.
 */
export function perDayHistogram(
  nodes: Pick<GraphNode, 'firstSeen'>[],
  from: string,
  to: string,
): DayCount[] {
  const counts = new Map<string, number>();
  for (const node of nodes) {
    const key = toDateKey(node.firstSeen);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }

  const days: DayCount[] = [];
  const cursor = new Date(`${from}T00:00:00Z`);
  const end = new Date(`${to}T00:00:00Z`);
  while (cursor.getTime() <= end.getTime()) {
    const key = cursor.toISOString().slice(0, 10);
    days.push({ date: key, count: counts.get(key) ?? 0 });
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
  return days;
}

/** Number of pages known as of `asOf` (firstSeen <= asOf), inclusive. */
export function pagesKnownByDate(nodes: Pick<GraphNode, 'firstSeen'>[], asOf: string): number {
  const cutoff = toDateKey(asOf);
  return nodes.filter((n) => toDateKey(n.firstSeen) <= cutoff).length;
}

/** Nodes whose `firstSeen` falls on exactly `date` (an ISO date). */
export function nodesFirstSeenOn<T extends Pick<GraphNode, 'firstSeen'>>(
  nodes: T[],
  date: string,
): T[] {
  return nodes.filter((n) => toDateKey(n.firstSeen) === date);
}
