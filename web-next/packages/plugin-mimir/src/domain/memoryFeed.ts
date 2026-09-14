/**
 * Memory feed — "what it learned lately".
 *
 * Two backend feeds describe the same activity from different angles: the
 * mount recent-writes stream (`/mounts/recent-writes`) and the lint/activity
 * log (`/activity`). Both are merged into one list of plainly-named events,
 * de-duplicated on the (timestamp, mount, message) triple they share.
 */

import type { RecentWrite } from '../ports/IMountAdapter';
import type { ActivityEvent } from './lint';

export type MemoryEventKind =
  | 'fact-added'
  | 'belief-revised'
  | 'page-compiled'
  | 'source-ingested'
  | 'tidied'
  | 'dreamed'
  | 'asked';

/** What the operator reads in the feed. */
export const MEMORY_EVENT_LABEL: Record<MemoryEventKind, string> = {
  'fact-added': 'fact added',
  'belief-revised': 'belief revised',
  'page-compiled': 'page compiled',
  'source-ingested': 'source ingested',
  tidied: 'tidied up',
  dreamed: 'dreamed',
  asked: 'asked',
};

export interface MemoryEvent {
  id: string;
  /** ISO-8601. */
  timestamp: string;
  kind: MemoryEventKind;
  label: string;
  mount: string;
  /** Page path, or '' for events that touch no single page. */
  page: string;
  /** The ravn that did it. */
  who: string;
  message: string;
}

/** A write whose message describes a revision is a revision, not a new fact. */
function writeKind(message: string): MemoryEventKind {
  return /revis|rewrote|replaced/i.test(message) ? 'belief-revised' : 'fact-added';
}

function recentWriteKind(write: RecentWrite): MemoryEventKind {
  switch (write.kind) {
    case 'write':
      return writeKind(write.message);
    case 'compile':
      return 'page-compiled';
    case 'lint-fix':
      return 'tidied';
    case 'dream':
      return 'dreamed';
  }
}

function activityKind(event: ActivityEvent): MemoryEventKind {
  switch (event.kind) {
    case 'write':
      return writeKind(event.message);
    case 'ingest':
      return 'source-ingested';
    case 'lint':
      return 'tidied';
    case 'dream':
      return 'dreamed';
    case 'query':
      return 'asked';
  }
}

function toEvent(
  id: string,
  kind: MemoryEventKind,
  source: { timestamp: string; mount: string; ravn: string; message: string; page?: string },
): MemoryEvent {
  return {
    id,
    timestamp: source.timestamp,
    kind,
    label: MEMORY_EVENT_LABEL[kind],
    mount: source.mount,
    page: source.page ?? '',
    who: source.ravn,
    message: source.message,
  };
}

/** The same happening reported by both feeds carries the same fingerprint. */
function fingerprint(event: MemoryEvent): string {
  return `${event.timestamp}|${event.mount}|${event.message}`;
}

/**
 * Merge both feeds, newest first. Recent writes win on a collision because
 * they carry the page path the activity log sometimes omits.
 */
export function mergeMemoryFeed(
  writes: RecentWrite[],
  activity: ActivityEvent[],
  limit: number,
): MemoryEvent[] {
  const merged = new Map<string, MemoryEvent>();
  for (const write of writes) {
    const event = toEvent(`write-${write.id}`, recentWriteKind(write), write);
    merged.set(fingerprint(event), event);
  }
  for (const entry of activity) {
    const event = toEvent(`activity-${entry.id}`, activityKind(entry), entry);
    const key = fingerprint(event);
    if (merged.has(key)) continue;
    merged.set(key, event);
  }
  return [...merged.values()]
    .sort((left, right) => right.timestamp.localeCompare(left.timestamp))
    .slice(0, limit);
}

/** Distinct page paths touched by the feed, newest first. */
export function pagesInFeed(events: MemoryEvent[], limit: number): string[] {
  const seen: string[] = [];
  for (const event of events) {
    if (!event.page || seen.includes(event.page)) continue;
    seen.push(event.page);
    if (seen.length === limit) break;
  }
  return seen;
}
