import { describe, expect, it } from 'vitest';
import {
  DEFAULT_NOTIFICATION_FILTER,
  NotificationReadStateConflictError,
  advancingReadThrough,
  compareNewestFirst,
  countLiveNotification,
  emptyRuleDraft,
  headSeqs,
  isFilterActive,
  isNotificationRead,
  matchesNotificationFilter,
  maxSeqMaps,
  mergeNotifications,
  notificationExcerpt,
  notificationKey,
  parseNotificationFilter,
  readThroughFrom,
  safeLinkHref,
  seqWatermarks,
  serverFilterOf,
  type NotificationReadState,
  type SessionNotification,
} from './notifications';

function row(overrides: Partial<SessionNotification> = {}): SessionNotification {
  return {
    id: 'n-1',
    instanceId: 'thor',
    seq: 1,
    sessionId: 'sess-1',
    sessionSeq: 10,
    sessionName: 'forge-api',
    ownerId: 'user-1',
    projectId: 'proj-1',
    kind: 'milestone',
    severity: 'info',
    source: 'agent',
    title: 'Tests pass',
    body: 'All **green**',
    links: [],
    engine: 'claude',
    model: 'opus',
    correlationId: null,
    createdAt: '2026-09-23T10:00:00Z',
    read: false,
    ...overrides,
  };
}

const readState: NotificationReadState = {
  unreadCount: 2,
  instances: {
    thor: { readThroughSeq: 3, revision: 4, unreadCount: 2, headSeq: 5 },
    horde: { readThroughSeq: 0, revision: 0, unreadCount: 0, headSeq: 0 },
  },
};

describe('notification identity and ordering', () => {
  it('keys rows by instance and id, since ids are per node', () => {
    expect(notificationKey(row())).toBe('thor:n-1');
    expect(notificationKey(row({ instanceId: null }))).toBe(':n-1');
  });

  it('orders newest first by created_at, then instance, then seq', () => {
    const older = row({ id: 'a', createdAt: '2026-09-23T09:00:00Z' });
    const newer = row({ id: 'b', createdAt: '2026-09-23T11:00:00Z' });
    const tieA = row({ id: 'c', instanceId: 'a-node', seq: 9 });
    const tieB = row({ id: 'd', instanceId: 'z-node', seq: 1 });
    const tieSeq = row({ id: 'e', instanceId: 'z-node', seq: 2 });
    expect([older, newer].sort(compareNewestFirst).map((item) => item.id)).toEqual(['b', 'a']);
    expect([tieA, tieB, tieSeq].sort(compareNewestFirst).map((item) => item.id)).toEqual([
      'e',
      'd',
      'c',
    ]);
  });

  it('merges without duplicates and keeps the existing list when nothing is new', () => {
    const existing = [row({ id: 'a', seq: 1 })];
    expect(mergeNotifications(existing, [row({ id: 'a', seq: 1 })])).toBe(existing);
    const merged = mergeNotifications(existing, [
      row({ id: 'b', seq: 2, createdAt: '2026-09-23T12:00:00Z' }),
      row({ id: 'b', seq: 2, createdAt: '2026-09-23T12:00:00Z' }),
      row({ id: 'a', instanceId: 'horde', createdAt: '2026-09-23T08:00:00Z' }),
    ]);
    expect(merged.map(notificationKey)).toEqual(['thor:b', 'thor:a', 'horde:a']);
  });
});

describe('watermarks and read state', () => {
  const rows = [
    row({ id: 'a', seq: 7 }),
    row({ id: 'b', seq: 2, instanceId: 'horde' }),
    row({ id: 'c', seq: 4 }),
    row({ id: 'd', seq: 1, instanceId: 'horde' }),
  ];

  it('tracks the highest seq per instance', () => {
    expect(seqWatermarks(rows)).toEqual({ thor: 7, horde: 2 });
    expect(maxSeqMaps({ thor: 3 }, { thor: 1, horde: 4 })).toEqual({ thor: 3, horde: 4 });
    expect(headSeqs(readState)).toEqual({ thor: 5, horde: 0 });
    expect(headSeqs(undefined)).toEqual({});
  });

  it('marks read through a row using everything at or below it, per instance', () => {
    expect(readThroughFrom(rows, 2)).toEqual({ thor: 4, horde: 1 });
    expect(readThroughFrom(rows, -1)).toEqual({ thor: 7, horde: 2 });
  });

  it('only sends watermarks that move forward', () => {
    expect(advancingReadThrough({ thor: 3, horde: 2, new: 1 }, readState)).toEqual({
      horde: 2,
      new: 1,
    });
    expect(advancingReadThrough({ thor: 3 }, undefined)).toEqual({ thor: 3 });
  });

  it('computes read from the watermark, falling back to the server flag', () => {
    expect(isNotificationRead(row({ seq: 3 }), readState)).toBe(true);
    expect(isNotificationRead(row({ seq: 4 }), readState)).toBe(false);
    expect(isNotificationRead(row({ instanceId: 'other', read: true }), readState)).toBe(true);
    expect(isNotificationRead(row({ read: false }), undefined)).toBe(false);
  });

  it('counts a live row once, and only when it is unread', () => {
    const counted = countLiveNotification(readState, row({ seq: 6 }));
    expect(counted.unreadCount).toBe(3);
    expect(counted.instances.thor).toMatchObject({ headSeq: 6, unreadCount: 3 });
    expect(countLiveNotification(counted, row({ seq: 6 }))).toBe(counted);
    const unknown = countLiveNotification(readState, row({ instanceId: 'fresh', seq: 1 }));
    expect(unknown.instances.fresh).toEqual({
      readThroughSeq: 0,
      revision: 0,
      unreadCount: 1,
      headSeq: 1,
    });
    const alreadyRead = countLiveNotification(
      {
        unreadCount: 0,
        instances: { thor: { readThroughSeq: 9, revision: 1, unreadCount: 0, headSeq: 2 } },
      },
      row({ seq: 8 }),
    );
    expect(alreadyRead.unreadCount).toBe(0);
    expect(alreadyRead.instances.thor!.headSeq).toBe(8);
  });

  it('names the conflict error for callers', () => {
    expect(new NotificationReadStateConflictError().name).toBe(
      'NotificationReadStateConflictError',
    );
  });
});

describe('filters', () => {
  it('applies every filter to a row', () => {
    const base = DEFAULT_NOTIFICATION_FILTER;
    const item = row({ severity: 'warning', kind: 'attention', source: 'system' });
    expect(matchesNotificationFilter(item, base)).toBe(true);
    expect(matchesNotificationFilter(item, { ...base, kinds: ['error'] })).toBe(false);
    expect(matchesNotificationFilter(item, { ...base, kinds: ['attention'] })).toBe(true);
    expect(matchesNotificationFilter(item, { ...base, minSeverity: 'critical' })).toBe(false);
    expect(matchesNotificationFilter(item, { ...base, minSeverity: 'success' })).toBe(true);
    expect(matchesNotificationFilter(item, { ...base, sources: ['agent'] })).toBe(false);
    expect(matchesNotificationFilter(item, { ...base, sessionId: 'other' })).toBe(false);
    expect(matchesNotificationFilter(item, { ...base, projectId: 'other' })).toBe(false);
    expect(matchesNotificationFilter(item, { ...base, instanceId: 'horde' })).toBe(false);
    expect(matchesNotificationFilter(item, { ...base, instanceId: 'thor' })).toBe(true);
    expect(matchesNotificationFilter(item, { ...base, query: 'GREEN' })).toBe(true);
    expect(matchesNotificationFilter(item, { ...base, query: 'forge-api' })).toBe(true);
    expect(matchesNotificationFilter(item, { ...base, query: 'nothing' })).toBe(false);
    expect(
      matchesNotificationFilter(row({ seq: 2 }), { ...base, unreadOnly: true }, readState),
    ).toBe(false);
    expect(
      matchesNotificationFilter(row({ seq: 9 }), { ...base, unreadOnly: true }, readState),
    ).toBe(true);
  });

  it('separates server filters with a stable order', () => {
    expect(
      serverFilterOf({
        ...DEFAULT_NOTIFICATION_FILTER,
        kinds: ['milestone', 'error'],
        sources: ['system', 'agent'],
        query: 'x',
        instanceId: 'thor',
      }),
    ).toEqual({
      kinds: ['error', 'milestone'],
      minSeverity: null,
      sources: ['agent', 'system'],
      sessionId: null,
      projectId: null,
      unreadOnly: false,
    });
  });

  it('knows when any filter is active', () => {
    expect(isFilterActive(DEFAULT_NOTIFICATION_FILTER)).toBe(false);
    expect(isFilterActive({ ...DEFAULT_NOTIFICATION_FILTER, query: '  ' })).toBe(false);
    expect(isFilterActive({ ...DEFAULT_NOTIFICATION_FILTER, unreadOnly: true })).toBe(true);
    expect(isFilterActive({ ...DEFAULT_NOTIFICATION_FILTER, instanceId: '' })).toBe(true);
  });

  it('reads persisted filters defensively', () => {
    expect(parseNotificationFilter('')).toBe(DEFAULT_NOTIFICATION_FILTER);
    expect(parseNotificationFilter('{oops')).toBe(DEFAULT_NOTIFICATION_FILTER);
    expect(parseNotificationFilter('[]')).toBe(DEFAULT_NOTIFICATION_FILTER);
    expect(
      parseNotificationFilter(
        JSON.stringify({
          kinds: ['error', 'bogus'],
          minSeverity: 'loud',
          sources: ['operator', 7],
          sessionId: 's',
          projectId: '',
          unreadOnly: 'yes',
          instanceId: 'thor',
          query: 'deploy',
        }),
      ),
    ).toEqual({
      kinds: ['error'],
      minSeverity: null,
      sources: ['operator'],
      sessionId: 's',
      projectId: null,
      unreadOnly: false,
      instanceId: 'thor',
      query: 'deploy',
    });
    expect(
      parseNotificationFilter(JSON.stringify({ minSeverity: 'warning', kinds: 'x' })),
    ).toMatchObject({
      minSeverity: 'warning',
      kinds: [],
    });
  });
});

describe('presentation helpers', () => {
  it('builds a plain excerpt from markdown', () => {
    expect(notificationExcerpt('**Bold** [link](https://x.test) `code`\n\n> quote', 100)).toBe(
      'Bold link code quote',
    );
    expect(notificationExcerpt('```\nblock\n```\nafter', 100)).toBe('after');
    expect(notificationExcerpt('abcdefghij', 5)).toBe('abcd…');
  });

  it('only allows in-app and web links', () => {
    expect(safeLinkHref('/volundr/session/1')).toBe('/volundr/session/1');
    expect(safeLinkHref('//evil.test')).toBeNull();
    expect(safeLinkHref('https://ok.test')).toBe('https://ok.test');
    expect(safeLinkHref('mailto:a@b.test')).toBe('mailto:a@b.test');
    expect(safeLinkHref('javascript:alert(1)')).toBeNull();
    expect(safeLinkHref('not a url')).toBeNull();
    expect(safeLinkHref(null)).toBeNull();
  });

  it('starts new rules permissive with no external target', () => {
    expect(emptyRuleDraft('push')).toMatchObject({
      enabled: true,
      sink: 'push',
      match: { kinds: [], minSeverity: 'info' },
      quietHours: null,
    });
  });
});
