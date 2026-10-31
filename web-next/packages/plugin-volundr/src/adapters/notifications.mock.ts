/**
 * In-memory INotificationFeed for demo mode and tests. Behaves like the facade:
 * newest-first pages with an opaque cursor, per-instance watermarks, forward-only
 * read state with revisions, and live rows pushed through `emit`.
 */
import { severityRank } from '@niuulabs/domain';
import {
  NotificationReadStateConflictError,
  compareNewestFirst,
  instanceKey,
  maxSeqMaps,
  seqWatermarks,
  type InstanceReadState,
  type InstanceSeqMap,
  type NotificationDelivery,
  type NotificationReadState,
  type NotificationRule,
  type NotificationServerFilter,
  type NotificationSinkOption,
  type SessionNotification,
} from '../domain/notifications';
import type {
  INotificationFeed,
  NotificationFeedSubscriber,
  NotificationStreamStatus,
} from '../ports/INotificationFeed';
import { NOTIFICATION_GAP_PAGE_SIZE, NOTIFICATION_PAGE_SIZE } from './notifications';

export interface MockNotificationFeed extends INotificationFeed {
  /** Commit a row as the server would, and push it to live subscribers. */
  emit(notification: SessionNotification): void;
  /** Commit a row without pushing it, as if the stream was down. */
  commit(notification: SessionNotification): void;
  /** Report a stream transition to live subscribers. */
  setStatus(status: NotificationStreamStatus): void;
}

export interface MockNotificationFeedOptions {
  seed?: SessionNotification[];
  rules?: NotificationRule[];
  sinks?: NotificationSinkOption[];
  deliveries?: Record<string, NotificationDelivery[]>;
  /** Watermarks already read, per instance. */
  readThrough?: Record<string, number>;
  /** Nodes to report as unreachable on every page. */
  unavailableInstances?: string[];
}

const CURSOR_PREFIX = 'mock-after:';

/** The mock's opaque `next_after`; like the facade's, it is only passed back. */
function encodeAfter(marks: InstanceSeqMap): string {
  return CURSOR_PREFIX + JSON.stringify(marks);
}

function decodeAfter(cursor: string): InstanceSeqMap {
  return cursor.startsWith(CURSOR_PREFIX)
    ? (JSON.parse(cursor.slice(CURSOR_PREFIX.length)) as InstanceSeqMap)
    : {};
}

const BASE_TIME = Date.parse('2026-09-23T09:00:00Z');

function minutesAgo(minutes: number): string {
  return new Date(BASE_TIME - minutes * 60_000).toISOString();
}

function seedNotification(
  overrides: Partial<SessionNotification> & Pick<SessionNotification, 'id' | 'seq'>,
): SessionNotification {
  return {
    instanceId: 'thor',
    instanceName: 'Thor',
    sessionId: 'sess-forge-api',
    sessionSeq: overrides.seq * 10,
    sessionName: 'forge-api',
    ownerId: 'user-1',
    projectId: 'proj-niuu',
    kind: 'info',
    severity: 'info',
    source: 'agent',
    title: 'Notification',
    body: '',
    links: [],
    engine: 'claude',
    model: 'claude-opus-5-5',
    correlationId: null,
    createdAt: minutesAgo(overrides.seq),
    read: false,
    ...overrides,
  };
}

export const MOCK_NOTIFICATIONS: SessionNotification[] = [
  seedNotification({
    id: 'n-thor-7',
    seq: 7,
    kind: 'attention',
    severity: 'warning',
    source: 'system',
    title: 'forge-api needs your input',
    body: 'The session is waiting for an answer to a permission prompt.',
    createdAt: minutesAgo(2),
  }),
  seedNotification({
    id: 'n-horde-4',
    seq: 4,
    instanceId: 'horde-1',
    instanceName: 'Horde 1',
    sessionId: 'sess-bench',
    sessionName: 'gpu-bench',
    projectId: 'proj-bench',
    kind: 'error',
    severity: 'critical',
    title: 'Benchmark run crashed',
    body: 'CUDA OOM on node 3 after 41 minutes.\n\n```\nRuntimeError: CUDA out of memory\n```',
    engine: 'codex',
    model: 'gpt-6-astra',
    createdAt: minutesAgo(9),
  }),
  seedNotification({
    id: 'n-thor-6',
    seq: 6,
    kind: 'milestone',
    severity: 'success',
    title: 'Migration 000069 applied',
    body: 'All **four** notification tables created; tests pass.',
    links: [
      {
        label: 'PR #412',
        url: 'https://github.com/niuulabs/niuu/pull/412',
        kind: 'pr',
        fileId: null,
      },
    ],
    createdAt: minutesAgo(15),
  }),
  seedNotification({
    id: 'n-thor-5',
    seq: 5,
    kind: 'reply_ready',
    severity: 'success',
    source: 'system',
    title: 'Reply ready: the facade now fans out',
    body: 'I wired GET /notifications through the Guild facade and merged pages by created_at.',
    createdAt: minutesAgo(31),
  }),
  seedNotification({
    id: 'n-horde-3',
    seq: 3,
    instanceId: 'horde-1',
    instanceName: 'Horde 1',
    sessionId: 'sess-bench',
    sessionName: 'gpu-bench',
    projectId: 'proj-bench',
    kind: 'decision',
    severity: 'info',
    title: 'Chose fp8 for the sweep',
    body: 'fp8 halves memory with <0.3% accuracy delta on the eval set.',
    engine: 'codex',
    model: 'gpt-6-astra',
    createdAt: minutesAgo(64),
  }),
  seedNotification({
    id: 'n-thor-4',
    seq: 4,
    source: 'operator',
    sessionId: null,
    sessionSeq: null,
    sessionName: null,
    projectId: null,
    engine: null,
    model: null,
    title: 'Maintenance window tonight at 22:00',
    body: 'Forge on thor restarts for the release; sessions resume automatically.',
    createdAt: minutesAgo(180),
  }),
];

export const MOCK_NOTIFICATION_SINKS: NotificationSinkOption[] = [
  { name: 'telegram', label: 'Telegram', requiresIntegration: true },
  { name: 'push', label: 'Push (iOS)', requiresIntegration: false },
  { name: 'webhook', label: 'Webhook', requiresIntegration: false },
];

export const MOCK_NOTIFICATION_RULES: NotificationRule[] = [
  {
    id: 'rule-critical',
    name: 'Critical to Telegram',
    enabled: true,
    match: { kinds: [], minSeverity: 'critical', sources: [], projectIds: [], sessionIds: [] },
    sink: 'telegram',
    integrationConnectionId: 'telegram-main',
    config: {},
    quietHours: {
      start: '22:00',
      end: '07:00',
      timezone: 'Europe/London',
      allowMinSeverity: 'critical',
    },
  },
];

export const MOCK_NOTIFICATION_DELIVERIES: Record<string, NotificationDelivery[]> = {
  'n-horde-4': [
    {
      id: 'd-1',
      ruleId: 'rule-critical',
      sink: 'telegram',
      status: 'delivered',
      attempts: 1,
      lastError: null,
      deliveredAt: minutesAgo(8),
      nextAttemptAt: null,
      updatedAt: minutesAgo(8),
    },
  ],
};

function matchesServerFilter(
  notification: SessionNotification,
  filter: NotificationServerFilter,
  read: boolean,
): boolean {
  if (filter.kinds.length > 0 && !filter.kinds.includes(notification.kind)) return false;
  if (filter.minSeverity && severityRank(notification.severity) < severityRank(filter.minSeverity))
    return false;
  if (filter.sources.length > 0 && !filter.sources.includes(notification.source)) return false;
  if (filter.sessionId && notification.sessionId !== filter.sessionId) return false;
  if (filter.projectId && notification.projectId !== filter.projectId) return false;
  return !(filter.unreadOnly && read);
}

export function createMockNotificationFeed(
  options: MockNotificationFeedOptions = {},
): MockNotificationFeed {
  let rows = [...(options.seed ?? MOCK_NOTIFICATIONS)].sort(compareNewestFirst);
  let rules = [...(options.rules ?? MOCK_NOTIFICATION_RULES)];
  const sinks = options.sinks ?? MOCK_NOTIFICATION_SINKS;
  const deliveries = options.deliveries ?? MOCK_NOTIFICATION_DELIVERIES;
  const readThrough: Record<string, number> = { ...(options.readThrough ?? {}) };
  const revisions: Record<string, number> = {};
  const subscribers = new Set<NotificationFeedSubscriber>();
  let nextRuleId = 1;

  function commit(notification: SessionNotification): void {
    rows = [...rows, notification].sort(compareNewestFirst);
  }

  function isRead(notification: SessionNotification): boolean {
    return notification.seq <= (readThrough[instanceKey(notification.instanceId)] ?? 0);
  }

  function withRead(notification: SessionNotification): SessionNotification {
    return { ...notification, read: isRead(notification) };
  }

  function readState(): NotificationReadState {
    const instances: Record<string, InstanceReadState> = {};
    for (const row of rows) {
      const key = instanceKey(row.instanceId);
      const state = (instances[key] ??= {
        readThroughSeq: readThrough[key] ?? 0,
        revision: revisions[key] ?? 0,
        unreadCount: 0,
        headSeq: 0,
      });
      state.headSeq = Math.max(state.headSeq, row.seq);
      if (!isRead(row)) state.unreadCount += 1;
    }
    const unreadCount = Object.values(instances).reduce((sum, state) => sum + state.unreadCount, 0);
    return { unreadCount, instances };
  }

  return {
    async list(filter, cursor) {
      const matching = rows.filter((row) => matchesServerFilter(row, filter, isRead(row)));
      const offset = cursor?.before ? Number(cursor.before) : 0;
      const limit = cursor?.limit ?? NOTIFICATION_PAGE_SIZE;
      const items = matching.slice(offset, offset + limit).map(withRead);
      const end = offset + items.length;
      return {
        items,
        nextBefore: end < matching.length ? String(end) : null,
        nextAfter: encodeAfter(seqWatermarks(rows)),
        unreadCount: readState().unreadCount,
        unavailableInstances: [...(options.unavailableInstances ?? [])],
        instanceHeads: seqWatermarks(rows),
      };
    },

    async listSince(filter, after, gapOptions) {
      const limit = gapOptions?.limit ?? NOTIFICATION_GAP_PAGE_SIZE;
      const marks = after.cursor ? decodeAfter(after.cursor) : after.watermarks;
      // Like the facade: only the nodes the cursor names are read, and each
      // node caps its own page at `limit`.
      const perNode = new Map<string, SessionNotification[]>();
      for (const row of rows) {
        const key = instanceKey(row.instanceId);
        const mark = marks[key];
        if (mark === undefined || row.seq <= mark) continue;
        if (!matchesServerFilter(row, filter, isRead(row))) continue;
        perNode.set(key, [...(perNode.get(key) ?? []), row]);
      }
      let hasMore = false;
      const items: SessionNotification[] = [];
      for (const nodeRows of perNode.values()) {
        const ascending = nodeRows.sort((a, b) => a.seq - b.seq);
        hasMore ||= ascending.length > limit;
        items.push(...ascending.slice(0, limit).map(withRead));
      }
      items.sort((a, b) => a.seq - b.seq);
      return { items, hasMore, nextAfter: encodeAfter(maxSeqMaps(marks, seqWatermarks(items))) };
    },

    async listForSession(sessionId, after, sessionOptions) {
      return rows
        .filter(
          (row) =>
            row.sessionId === sessionId &&
            row.seq > (after ?? 0) &&
            (!sessionOptions?.instanceId || row.instanceId === sessionOptions.instanceId),
        )
        .sort((a, b) => a.seq - b.seq)
        .slice(0, sessionOptions?.limit ?? NOTIFICATION_PAGE_SIZE)
        .map(withRead);
    },

    async getReadState() {
      return readState();
    },

    async markRead(through, expected) {
      for (const [key, seq] of Object.entries(through)) {
        if ((expected.instances[key]?.revision ?? 0) !== (revisions[key] ?? 0))
          throw new NotificationReadStateConflictError();
        if (seq > (readThrough[key] ?? 0)) {
          readThrough[key] = seq;
          revisions[key] = (revisions[key] ?? 0) + 1;
        }
      }
      return readState();
    },

    subscribe(subscriber) {
      subscribers.add(subscriber);
      subscriber.onStatus?.('open');
      return () => {
        subscribers.delete(subscriber);
      };
    },

    emit(notification) {
      commit(notification);
      for (const subscriber of Array.from(subscribers)) subscriber.onNotification(notification);
    },

    commit,

    setStatus(status) {
      for (const subscriber of Array.from(subscribers)) subscriber.onStatus?.(status);
    },

    async listRules() {
      return rules.map((rule) => ({ ...rule }));
    },

    async createRule(draft) {
      const rule = { ...draft, id: `rule-${nextRuleId++}` };
      rules = [...rules, rule];
      return rule;
    },

    async updateRule(id, draft) {
      const rule = { ...draft, id };
      rules = rules.map((existing) => (existing.id === id ? rule : existing));
      return rule;
    },

    async deleteRule(id) {
      rules = rules.filter((rule) => rule.id !== id);
    },

    async sinks() {
      return sinks.map((sink) => ({ ...sink }));
    },

    async deliveries(notificationId) {
      return (deliveries[notificationId] ?? []).map((delivery) => ({ ...delivery }));
    },
  };
}
