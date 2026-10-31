/**
 * Forge session notifications feed — pure domain logic (no framework imports).
 *
 * A `SessionNotification` is one row of the cursor-ordered feed Forge projects
 * from session logs (contract §3/§4). Through the Guild facade every row carries
 * the `instance_id` of the Forge node that owns it; seqs are only comparable
 * within one instance, so identity and watermarks are always per instance.
 */
import {
  isForgeNotificationKind,
  isForgeNotificationSeverity,
  isForgeNotificationSource,
  severityRank,
  type ForgeNotificationKind,
  type ForgeNotificationLink,
  type ForgeNotificationSeverity,
  type ForgeNotificationSource,
} from '@niuulabs/domain';

/** Instance key for rows from a Forge that is not behind the Guild facade. */
export const LOCAL_INSTANCE = '';

export interface SessionNotification {
  id: string;
  /** Owning Forge node (facade tag); `null` when talking to one Forge directly. */
  instanceId: string | null;
  /** The node's display name, as the facade reports it. */
  instanceName: string | null;
  seq: number;
  sessionId: string | null;
  sessionSeq: number | null;
  sessionName: string | null;
  ownerId: string;
  projectId: string | null;
  kind: ForgeNotificationKind;
  severity: ForgeNotificationSeverity;
  source: ForgeNotificationSource;
  title: string;
  body: string;
  links: ForgeNotificationLink[];
  engine: string | null;
  model: string | null;
  correlationId: string | null;
  createdAt: string;
  /** Server-computed read flag at fetch time; live rows arrive without it. */
  read: boolean;
}

/** Filters the server applies (contract §4 query parameters). */
export interface NotificationServerFilter {
  kinds: ForgeNotificationKind[];
  /** `null` means any severity. */
  minSeverity: ForgeNotificationSeverity | null;
  sources: ForgeNotificationSource[];
  sessionId: string | null;
  projectId: string | null;
  unreadOnly: boolean;
}

/** The feed filter: server filters plus the ones applied to loaded rows. */
export interface NotificationFilter extends NotificationServerFilter {
  /** Host filter; `null` means every host. Applied client-side (the feed fans out). */
  instanceId: string | null;
  /** Free-text search over loaded rows. */
  query: string;
}

export const DEFAULT_NOTIFICATION_FILTER: NotificationFilter = {
  kinds: [],
  minSeverity: null,
  sources: [],
  sessionId: null,
  projectId: null,
  unreadOnly: false,
  instanceId: null,
  query: '',
};

export interface InstanceReadState {
  readThroughSeq: number;
  revision: number;
  unreadCount: number;
  headSeq: number;
}

export interface NotificationReadState {
  unreadCount: number;
  /** Keyed by instance id (`LOCAL_INSTANCE` for a single Forge). */
  instances: Record<string, InstanceReadState>;
}

/** Per-instance seq watermark, e.g. "read through" or "seen through". */
export type InstanceSeqMap = Record<string, number>;

export class NotificationReadStateConflictError extends Error {
  constructor(message = 'Notifications were marked read elsewhere. Refresh and try again.') {
    super(message);
    this.name = 'NotificationReadStateConflictError';
  }
}

export interface NotificationRuleMatch {
  kinds: ForgeNotificationKind[];
  minSeverity: ForgeNotificationSeverity;
  sources: ForgeNotificationSource[];
  projectIds: string[];
  sessionIds: string[];
}

export interface NotificationQuietHours {
  /** `HH:MM`, local to `timezone`. */
  start: string;
  end: string;
  timezone: string;
  /** Messages at or above this severity still go out during quiet hours. */
  allowMinSeverity: ForgeNotificationSeverity;
}

export interface NotificationRuleDraft {
  name: string;
  enabled: boolean;
  match: NotificationRuleMatch;
  sink: string;
  integrationConnectionId: string | null;
  config: Record<string, unknown>;
  quietHours: NotificationQuietHours | null;
}

export interface NotificationRule extends NotificationRuleDraft {
  id: string;
}

export interface NotificationSinkOption {
  name: string;
  label: string;
  requiresIntegration: boolean;
}

export const NOTIFICATION_DELIVERY_STATUSES = [
  'pending',
  'claimed',
  'delivered',
  'failed',
  'dead',
  'suppressed',
] as const;
export type NotificationDeliveryStatus = (typeof NOTIFICATION_DELIVERY_STATUSES)[number];

export interface NotificationDelivery {
  id: string;
  ruleId: string;
  sink: string;
  status: NotificationDeliveryStatus;
  attempts: number;
  lastError: string | null;
  deliveredAt: string | null;
  nextAttemptAt: string | null;
  updatedAt: string | null;
}

export function emptyRuleDraft(sink = ''): NotificationRuleDraft {
  return {
    name: '',
    enabled: true,
    match: { kinds: [], minSeverity: 'info', sources: [], projectIds: [], sessionIds: [] },
    sink,
    integrationConnectionId: null,
    config: {},
    quietHours: null,
  };
}

export function instanceKey(instanceId: string | null | undefined): string {
  return instanceId ?? LOCAL_INSTANCE;
}

/** Identity across the fan-out: ids are per node, so dedupe on (instance, id). */
export function notificationKey(notification: Pick<SessionNotification, 'instanceId' | 'id'>) {
  return `${instanceKey(notification.instanceId)}:${notification.id}`;
}

/** Newest first by `(created_at, instance_id, seq)`, the facade's merge order. */
export function compareNewestFirst(a: SessionNotification, b: SessionNotification): number {
  const time = Date.parse(b.createdAt) - Date.parse(a.createdAt);
  if (Number.isFinite(time) && time !== 0) return time;
  const instance = instanceKey(b.instanceId).localeCompare(instanceKey(a.instanceId));
  if (instance !== 0) return instance;
  return b.seq - a.seq;
}

/** Merge rows into a newest-first list; a known row keeps its place and flags. */
export function mergeNotifications(
  existing: readonly SessionNotification[],
  incoming: readonly SessionNotification[],
): SessionNotification[] {
  const known = new Set(existing.map(notificationKey));
  const fresh = incoming.filter((notification) => {
    const key = notificationKey(notification);
    if (known.has(key)) return false;
    known.add(key);
    return true;
  });
  if (fresh.length === 0) return existing as SessionNotification[];
  return [...existing, ...fresh].sort(compareNewestFirst);
}

/** Highest seq seen per instance. */
export function seqWatermarks(notifications: readonly SessionNotification[]): InstanceSeqMap {
  const marks: InstanceSeqMap = {};
  for (const notification of notifications) {
    const key = instanceKey(notification.instanceId);
    marks[key] = Math.max(marks[key] ?? 0, notification.seq);
  }
  return marks;
}

export function maxSeqMaps(...maps: InstanceSeqMap[]): InstanceSeqMap {
  const merged: InstanceSeqMap = {};
  for (const map of maps) {
    for (const [key, seq] of Object.entries(map)) merged[key] = Math.max(merged[key] ?? 0, seq);
  }
  return merged;
}

export function headSeqs(readState: NotificationReadState | undefined): InstanceSeqMap {
  const heads: InstanceSeqMap = {};
  for (const [key, state] of Object.entries(readState?.instances ?? {})) heads[key] = state.headSeq;
  return heads;
}

export function isNotificationRead(
  notification: SessionNotification,
  readState: NotificationReadState | undefined,
): boolean {
  const state = readState?.instances[instanceKey(notification.instanceId)];
  if (!state) return notification.read;
  return notification.seq <= state.readThroughSeq;
}

/**
 * "Mark read through here" for a row of the merged feed: for every instance, the
 * highest seq among this row and everything older below it in the list.
 */
export function readThroughFrom(
  notifications: readonly SessionNotification[],
  index: number,
): InstanceSeqMap {
  return seqWatermarks(notifications.slice(Math.max(index, 0)));
}

/** Only the instances whose watermark actually moves forward. */
export function advancingReadThrough(
  through: InstanceSeqMap,
  readState: NotificationReadState | undefined,
): InstanceSeqMap {
  const next: InstanceSeqMap = {};
  for (const [key, seq] of Object.entries(through)) {
    if (seq > (readState?.instances[key]?.readThroughSeq ?? 0)) next[key] = seq;
  }
  return next;
}

/**
 * Count one live notification into the read state, idempotently: a row is
 * unread when it is past both the read watermark and the head we already knew.
 */
export function countLiveNotification(
  readState: NotificationReadState,
  notification: SessionNotification,
): NotificationReadState {
  const key = instanceKey(notification.instanceId);
  const current = readState.instances[key] ?? {
    readThroughSeq: 0,
    revision: 0,
    unreadCount: 0,
    headSeq: 0,
  };
  if (notification.seq <= current.headSeq) return readState;
  const unread = notification.seq > current.readThroughSeq ? 1 : 0;
  return {
    unreadCount: readState.unreadCount + unread,
    instances: {
      ...readState.instances,
      [key]: {
        ...current,
        headSeq: notification.seq,
        unreadCount: current.unreadCount + unread,
      },
    },
  };
}

function includesText(notification: SessionNotification, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return [notification.title, notification.body, notification.sessionName ?? '']
    .join('\n')
    .toLowerCase()
    .includes(needle);
}

/** Whether a row (loaded or live) belongs in the feed under `filter`. */
export function matchesNotificationFilter(
  notification: SessionNotification,
  filter: NotificationFilter,
  readState?: NotificationReadState,
): boolean {
  if (filter.kinds.length > 0 && !filter.kinds.includes(notification.kind)) return false;
  if (filter.minSeverity && severityRank(notification.severity) < severityRank(filter.minSeverity))
    return false;
  if (filter.sources.length > 0 && !filter.sources.includes(notification.source)) return false;
  if (filter.sessionId && notification.sessionId !== filter.sessionId) return false;
  if (filter.projectId && notification.projectId !== filter.projectId) return false;
  if (filter.instanceId !== null && instanceKey(notification.instanceId) !== filter.instanceId)
    return false;
  if (filter.unreadOnly && isNotificationRead(notification, readState)) return false;
  return includesText(notification, filter.query);
}

export function serverFilterOf(filter: NotificationFilter): NotificationServerFilter {
  return {
    kinds: [...filter.kinds].sort(),
    minSeverity: filter.minSeverity,
    sources: [...filter.sources].sort(),
    sessionId: filter.sessionId,
    projectId: filter.projectId,
    unreadOnly: filter.unreadOnly,
  };
}

export function isFilterActive(filter: NotificationFilter): boolean {
  return (
    filter.kinds.length > 0 ||
    filter.minSeverity !== null ||
    filter.sources.length > 0 ||
    filter.sessionId !== null ||
    filter.projectId !== null ||
    filter.unreadOnly ||
    filter.instanceId !== null ||
    filter.query.trim() !== ''
  );
}

function stringOrNull(value: unknown): string | null {
  return typeof value === 'string' && value ? value : null;
}

/**
 * Read a persisted filter back, dropping anything this build does not know so
 * an old or hand-edited preference can never break the page.
 */
export function parseNotificationFilter(raw: string | null | undefined): NotificationFilter {
  if (!raw) return DEFAULT_NOTIFICATION_FILTER;
  let parsed: Record<string, unknown>;
  try {
    const value = JSON.parse(raw) as unknown;
    if (!value || typeof value !== 'object' || Array.isArray(value))
      return DEFAULT_NOTIFICATION_FILTER;
    parsed = value as Record<string, unknown>;
  } catch {
    return DEFAULT_NOTIFICATION_FILTER;
  }
  const list = (value: unknown) => (Array.isArray(value) ? value : []);
  return {
    kinds: list(parsed.kinds).filter(isForgeNotificationKind),
    minSeverity: isForgeNotificationSeverity(parsed.minSeverity) ? parsed.minSeverity : null,
    sources: list(parsed.sources).filter(isForgeNotificationSource),
    sessionId: stringOrNull(parsed.sessionId),
    projectId: stringOrNull(parsed.projectId),
    unreadOnly: parsed.unreadOnly === true,
    instanceId: typeof parsed.instanceId === 'string' ? parsed.instanceId : null,
    query: typeof parsed.query === 'string' ? parsed.query : '',
  };
}

/** A one-line plain-text preview of a markdown body. */
export function notificationExcerpt(body: string, limit: number): string {
  const plain = body
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/[`*_>#~]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  return plain.length > limit ? `${plain.slice(0, Math.max(limit - 1, 0)).trimEnd()}…` : plain;
}

/** An href safe to render: an in-app path or an http(s)/mailto URL. */
export function safeLinkHref(url: string | null | undefined): string | null {
  if (!url) return null;
  if (url.startsWith('/') && !url.startsWith('//')) return url;
  try {
    return ['http:', 'https:', 'mailto:'].includes(new URL(url).protocol) ? url : null;
  } catch {
    return null;
  }
}
