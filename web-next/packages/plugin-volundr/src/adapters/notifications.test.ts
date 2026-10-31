import { describe, expect, it, vi } from 'vitest';
import { ApiClientError } from '@niuulabs/query';
import {
  DEFAULT_NOTIFICATION_FILTER,
  NotificationReadStateConflictError,
  serverFilterOf,
  type NotificationReadState,
  type NotificationServerFilter,
} from '../domain/notifications';
import type { ForgeStreamListener } from '../ports/IForgeEventStream';
import { buildNotificationFeedHttpAdapter, type NotificationHttpClient } from './notifications';
import {
  afterCursor,
  encodeInstanceCursor,
  feedQuery,
  instanceHeads,
  normalizeDelivery,
  normalizeNotification,
  normalizeReadState,
  normalizeRule,
  normalizeSink,
  readStatePutBody,
  ruleDraftToWire,
  withInstance,
  type NotificationWire,
} from './notificationWire';

const ANY: NotificationServerFilter = serverFilterOf(DEFAULT_NOTIFICATION_FILTER);

const wireRow: NotificationWire = {
  id: 'n-1',
  seq: 12,
  session_id: 'sess-1',
  session_seq: 34,
  session_name: 'forge-api',
  owner_id: 'user-1',
  tenant_id: 't',
  project_id: 'proj-1',
  kind: 'milestone',
  severity: 'success',
  source: 'agent',
  title: 'Tests pass',
  body: 'All **green**',
  links: [{ label: 'PR', url: 'https://x.test/pr/1', kind: 'pr', file_id: null }],
  engine: 'claude',
  model: 'opus',
  correlation_id: null,
  created_at: '2026-09-23T10:00:00Z',
  read: true,
  instance_id: 'thor',
  instance_name: 'Thor',
  instance_slug: 'thor',
};

function decodeCursor(cursor: string): unknown {
  const base64 = cursor.replace(/-/g, '+').replace(/_/g, '/');
  const binary = atob(base64 + '='.repeat((4 - (base64.length % 4)) % 4));
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
  return JSON.parse(new TextDecoder().decode(bytes));
}

function fakeClient(responses: Record<string, unknown> = {}) {
  const calls: Array<{ method: string; path: string; body?: unknown }> = [];
  const respond = (method: string) =>
    vi.fn(async (path: string, body?: unknown) => {
      calls.push({ method, path, body });
      const key = Object.keys(responses).find((prefix) => `${method} ${path}`.startsWith(prefix));
      const value = key ? responses[key] : undefined;
      if (value instanceof Error) throw value;
      return value;
    });
  const client: NotificationHttpClient = {
    get: respond('GET') as NotificationHttpClient['get'],
    post: respond('POST') as NotificationHttpClient['post'],
    put: respond('PUT') as NotificationHttpClient['put'],
    delete: respond('DELETE') as NotificationHttpClient['delete'],
  };
  return { client, calls };
}

describe('notification wire format', () => {
  it('normalizes the NotificationResponse, including the facade instance tag', () => {
    expect(normalizeNotification(wireRow)).toEqual({
      id: 'n-1',
      instanceId: 'thor',
      instanceName: 'Thor',
      seq: 12,
      sessionId: 'sess-1',
      sessionSeq: 34,
      sessionName: 'forge-api',
      ownerId: 'user-1',
      projectId: 'proj-1',
      kind: 'milestone',
      severity: 'success',
      source: 'agent',
      title: 'Tests pass',
      body: 'All **green**',
      links: [{ label: 'PR', url: 'https://x.test/pr/1', kind: 'pr', fileId: null }],
      engine: 'claude',
      model: 'opus',
      correlationId: null,
      createdAt: '2026-09-23T10:00:00Z',
      read: true,
    });
  });

  it('degrades unknown values from a newer Forge', () => {
    expect(
      normalizeNotification({
        id: 'x',
        seq: 'nope' as never,
        kind: 'party',
        severity: 'loud',
        source: 'robot',
        title: 7 as never,
        created_at: 1 as never,
      }),
    ).toMatchObject({
      seq: 0,
      kind: 'info',
      severity: 'info',
      source: 'system',
      title: '',
      body: '',
      createdAt: '',
      read: false,
      instanceId: null,
      sessionSeq: null,
      ownerId: '',
    });
  });

  it('builds feed queries with every server filter and always fans out', () => {
    const url = new URL(
      feedQuery(
        {
          kinds: ['error', 'milestone'],
          minSeverity: 'warning',
          sources: ['agent'],
          sessionId: 'sess-1',
          projectId: 'proj-1',
          unreadOnly: true,
        },
        { before: 'CURSOR', limit: 50 },
      ),
      'http://x',
    );
    expect(url.pathname).toBe('/notifications');
    expect(Object.fromEntries(url.searchParams)).toEqual({
      all_instances: 'true',
      limit: '50',
      before: 'CURSOR',
      kind: 'error,milestone',
      min_severity: 'warning',
      source: 'agent',
      session_id: 'sess-1',
      project_id: 'proj-1',
      unread: 'true',
    });
    expect(feedQuery(ANY, { after: 'A', limit: 200 })).toBe(
      '/notifications?all_instances=true&limit=200&after=A',
    );
  });

  it('encodes per-instance cursors as unpadded base64url JSON', () => {
    const cursor = encodeInstanceCursor({ thor: 12, hørde: 3 });
    expect(cursor).not.toMatch(/[+/=]/);
    expect(decodeCursor(cursor)).toEqual({ thor: 12, hørde: 3 });
    expect(afterCursor({})).toBeNull();
    expect(afterCursor({ '': 9 })).toBe('9');
    expect(decodeCursor(afterCursor({ '': 9, thor: 4 })!)).toEqual({ thor: 4 });
  });

  it('reads both the facade and single-Forge read-state shapes', () => {
    expect(
      normalizeReadState({
        unread_count: 5,
        instances: {
          thor: { read_through_seq: 3, revision: 2, unread_count: 4, head_seq: 7 },
          horde: { unread_count: 1 },
        },
      }),
    ).toEqual({
      unreadCount: 5,
      instances: {
        thor: { readThroughSeq: 3, revision: 2, unreadCount: 4, headSeq: 7 },
        horde: { readThroughSeq: 0, revision: 0, unreadCount: 1, headSeq: 0 },
      },
    });
    expect(
      normalizeReadState({ instances: { a: { unread_count: 2 }, b: { unread_count: 3 } } }),
    ).toMatchObject({ unreadCount: 5 });
    expect(
      normalizeReadState({ read_through_seq: 1, revision: 1, unread_count: 2, head_seq: 3 }),
    ).toEqual({
      unreadCount: 2,
      instances: { '': { readThroughSeq: 1, revision: 1, unreadCount: 2, headSeq: 3 } },
    });
    expect(normalizeReadState({ unread_count: 1, instance_id: 'thor' }).instances).toHaveProperty(
      'thor',
    );
  });

  it('writes read state per instance at the facade and plainly for one Forge', () => {
    const expected: NotificationReadState = {
      unreadCount: 3,
      instances: {
        thor: { readThroughSeq: 1, revision: 4, unreadCount: 2, headSeq: 9 },
        '': { readThroughSeq: 0, revision: 2, unreadCount: 1, headSeq: 5 },
      },
    };
    expect(readStatePutBody({ thor: 9, horde: 2 }, expected)).toEqual({
      path: '/notifications/read-state?all_instances=true',
      body: {
        instances: {
          thor: { read_through_seq: 9, expected_revision: 4 },
          horde: { read_through_seq: 2, expected_revision: 0 },
        },
      },
    });
    expect(readStatePutBody({ '': 5 }, expected)).toEqual({
      path: '/notifications/read-state',
      body: { read_through_seq: 5, expected_revision: 2 },
    });
    expect(readStatePutBody({ '': 5 }, { unreadCount: 0, instances: {} }).body).toEqual({
      read_through_seq: 5,
      expected_revision: 0,
    });
  });

  it('round-trips delivery rules', () => {
    const rule = normalizeRule({
      id: 'r1',
      name: 'Critical',
      match: { kinds: ['error', 'bogus'], min_severity: 'critical', sources: ['agent', 'x'] },
      sink: 'telegram',
      integration_connection_id: 'conn',
      config: { chat: 1 },
      quiet_hours: { start: '22:00', end: '07:00', timezone: 'Europe/London' },
    });
    expect(rule).toEqual({
      id: 'r1',
      name: 'Critical',
      enabled: true,
      match: {
        kinds: ['error'],
        minSeverity: 'critical',
        sources: ['agent'],
        projectIds: [],
        sessionIds: [],
      },
      sink: 'telegram',
      integrationConnectionId: 'conn',
      config: { chat: 1 },
      quietHours: {
        start: '22:00',
        end: '07:00',
        timezone: 'Europe/London',
        allowMinSeverity: 'critical',
      },
    });
    expect(ruleDraftToWire({ ...rule, name: '  Critical  ' })).toEqual({
      name: 'Critical',
      enabled: true,
      match: {
        kinds: ['error'],
        min_severity: 'critical',
        sources: ['agent'],
        project_ids: [],
        session_ids: [],
      },
      sink: 'telegram',
      integration_connection_id: 'conn',
      config: { chat: 1 },
      quiet_hours: {
        start: '22:00',
        end: '07:00',
        timezone: 'Europe/London',
        allow_min_severity: 'critical',
      },
    });
    expect(
      normalizeRule({ id: 'r2', name: 'x', sink: 'push', enabled: false, config: null as never }),
    ).toMatchObject({
      enabled: false,
      config: {},
      quietHours: null,
      match: { minSeverity: 'info' },
    });
    expect(ruleDraftToWire({ ...rule, quietHours: null }).quiet_hours).toBeNull();
  });

  it('reads every covered node from the per-node summary, including empty ones', () => {
    expect(instanceHeads(undefined)).toBeNull();
    expect(instanceHeads({ thor: { head_seq: 9 }, empty: {}, odd: null as never })).toEqual({
      thor: 9,
      empty: 0,
      odd: 0,
    });
  });

  it('normalizes sinks and deliveries', () => {
    expect(normalizeSink({ name: 'webhook' })).toEqual({
      name: 'webhook',
      label: 'webhook',
      requiresIntegration: false,
    });
    expect(normalizeSink({ name: 'integration', requires_integration: true })).toEqual({
      name: 'integration',
      label: 'Messaging integration',
      requiresIntegration: true,
    });
    expect(
      normalizeDelivery({
        id: 'd1',
        rule_id: 'r1',
        sink: 'telegram',
        status: 'mystery',
        created_at: '2026-09-23T10:00:00Z',
      }),
    ).toEqual({
      id: 'd1',
      ruleId: 'r1',
      sink: 'telegram',
      status: 'pending',
      attempts: 0,
      lastError: null,
      deliveredAt: null,
      nextAttemptAt: null,
      updatedAt: '2026-09-23T10:00:00Z',
    });
  });

  it('routes single-node calls with instance_id', () => {
    expect(withInstance('/notifications/rules', null)).toBe('/notifications/rules');
    expect(withInstance('/notifications/rules', 'thor')).toBe(
      '/notifications/rules?instance_id=thor',
    );
    expect(withInstance('/x?a=1', 'a b')).toBe('/x?a=1&instance_id=a%20b');
  });
});

describe('buildNotificationFeedHttpAdapter', () => {
  it('lists a page from the facade', async () => {
    const { client, calls } = fakeClient({
      'GET /notifications?': {
        items: [wireRow],
        next_before: 'NEXT',
        next_after: 'AFTER',
        head_seq: null,
        read_through_seq: null,
        unread_count: 4,
        instances: { thor: { head_seq: 12, read_through_seq: 3, unread_count: 4 } },
      },
    });
    const page = await buildNotificationFeedHttpAdapter(client).list(ANY, { before: 'PREV' });
    expect(calls[0]!.path).toBe('/notifications?all_instances=true&limit=50&before=PREV');
    expect(page).toMatchObject({
      nextBefore: 'NEXT',
      nextAfter: 'AFTER',
      unreadCount: 4,
      unavailableInstances: [],
      instanceHeads: { thor: 12 },
    });
    expect(page.items[0]).toMatchObject({ instanceId: 'thor', instanceName: 'Thor' });

    const { client: plain } = fakeClient({ 'GET /notifications?': { items: [], next_before: 7 } });
    expect(await buildNotificationFeedHttpAdapter(plain).list(ANY)).toEqual({
      items: [],
      nextBefore: '7',
      nextAfter: null,
      unreadCount: null,
      unavailableInstances: [],
      instanceHeads: null,
    });
    const { client: bare } = fakeClient({ 'GET /notifications?': {} });
    expect((await buildNotificationFeedHttpAdapter(bare).list(ANY, { limit: 5 })).items).toEqual(
      [],
    );
  });

  it('reports nodes the fan-out could not reach from the response header', async () => {
    const { client } = fakeClient();
    const getWithHeaders = vi.fn(async () => ({
      data: { items: [{ ...wireRow, instance_name: null }], next_before: null },
      headers: new Headers({ 'X-Forge-Unavailable-Instances': 'horde-1, horde-2,' }),
    }));
    const page = await buildNotificationFeedHttpAdapter({ ...client, getWithHeaders }).list(ANY);
    expect(getWithHeaders).toHaveBeenCalledWith('/notifications?all_instances=true&limit=50');
    expect(page.unavailableInstances).toEqual(['horde-1', 'horde-2']);
    // The slug stands in when the facade has no display name.
    expect(page.items[0]!.instanceName).toBe('thor');
    getWithHeaders.mockResolvedValueOnce({ data: { items: [] } as never, headers: new Headers() });
    expect(
      (await buildNotificationFeedHttpAdapter({ ...client, getWithHeaders }).list(ANY))
        .unavailableInstances,
    ).toEqual([]);
  });

  it('gap-fills from the server cursor, or else from per-instance watermarks', async () => {
    const { client, calls } = fakeClient({
      'GET /notifications?': { items: [wireRow], next_after: 'NEXT-AFTER' },
    });
    const adapter = buildNotificationFeedHttpAdapter(client);
    const fromCursor = await adapter.listSince(ANY, { cursor: 'SERVER', watermarks: { thor: 1 } });
    expect(new URL(calls[0]!.path, 'http://x').searchParams.get('after')).toBe('SERVER');
    expect(fromCursor).toEqual({
      items: [normalizeNotification(wireRow)],
      hasMore: false,
      nextAfter: 'NEXT-AFTER',
    });
    const gap = await adapter.listSince(
      ANY,
      { cursor: null, watermarks: { thor: 11 } },
      { limit: 1 },
    );
    const url = new URL(calls[1]!.path, 'http://x');
    expect(decodeCursor(url.searchParams.get('after')!)).toEqual({ thor: 11 });
    expect(url.searchParams.get('limit')).toBe('1');
    expect(gap.hasMore).toBe(true);
    expect(await adapter.listSince(ANY, { cursor: null, watermarks: {} })).toEqual({
      items: [],
      hasMore: false,
      nextAfter: null,
    });
    expect(calls).toHaveLength(2);
    const { client: empty } = fakeClient({ 'GET /notifications?': {} });
    expect(
      await buildNotificationFeedHttpAdapter(empty).listSince(ANY, {
        cursor: null,
        watermarks: { thor: 1 },
      }),
    ).toEqual({ items: [], hasMore: false, nextAfter: null });
  });

  it('reads one session ascending on its owning instance', async () => {
    const { client, calls } = fakeClient({
      'GET /sessions/sess%201/notifications': [wireRow],
    });
    const adapter = buildNotificationFeedHttpAdapter(client);
    expect(
      await adapter.listForSession('sess 1', 4, { instanceId: 'thor', limit: 10 }),
    ).toHaveLength(1);
    expect(calls[0]!.path).toBe(
      '/sessions/sess%201/notifications?after=4&limit=10&instance_id=thor',
    );
    const { client: wrapped, calls: wrappedCalls } = fakeClient({
      'GET /sessions/s/notifications': { items: [wireRow] },
    });
    expect(await buildNotificationFeedHttpAdapter(wrapped).listForSession('s')).toHaveLength(1);
    expect(wrappedCalls[0]!.path).toBe('/sessions/s/notifications?limit=50');
  });

  it('reads and writes read state, mapping 409 to a conflict', async () => {
    const state = { unread_count: 1, instances: { thor: { read_through_seq: 2, revision: 3 } } };
    const { client, calls } = fakeClient({
      'GET /notifications/read-state': state,
      'PUT /notifications/read-state': {
        unread_count: 0,
        instances: { thor: { read_through_seq: 9, revision: 4 } },
      },
    });
    const adapter = buildNotificationFeedHttpAdapter(client);
    const current = await adapter.getReadState();
    expect(calls[0]!.path).toBe('/notifications/read-state?all_instances=true');
    const next = await adapter.markRead({ thor: 9 }, current);
    expect(calls[1]).toEqual({
      method: 'PUT',
      path: '/notifications/read-state?all_instances=true',
      body: { instances: { thor: { read_through_seq: 9, expected_revision: 3 } } },
    });
    expect(next.instances.thor!.readThroughSeq).toBe(9);
    expect(await adapter.markRead({}, current)).toBe(current);
    expect(calls).toHaveLength(2);

    const { client: conflicted } = fakeClient({
      'PUT /notifications/read-state': new ApiClientError('conflict', 409),
    });
    await expect(
      buildNotificationFeedHttpAdapter(conflicted).markRead({ thor: 9 }, current),
    ).rejects.toBeInstanceOf(NotificationReadStateConflictError);
    const { client: broken } = fakeClient({
      'PUT /notifications/read-state': new ApiClientError('boom', 500),
    });
    await expect(
      buildNotificationFeedHttpAdapter(broken).markRead({ thor: 9 }, current),
    ).rejects.toThrow('boom');
  });

  it('manages rules, sinks and deliveries on the selected instance', async () => {
    const ruleWire = { id: 'r1', name: 'Critical', sink: 'telegram' };
    const { client, calls } = fakeClient({
      'GET /notifications/rules': [ruleWire],
      'POST /notifications/rules': ruleWire,
      'PUT /notifications/rules/r1': { ...ruleWire, name: 'Renamed' },
      'DELETE /notifications/rules/r1': undefined,
      'GET /notifications/sinks': {
        sinks: [{ name: 'telegram', label: 'Telegram', requires_integration: true }],
      },
      'GET /notifications/n-1/deliveries': [
        { id: 'd', rule_id: 'r1', sink: 'telegram', status: 'delivered', attempts: 1 },
      ],
    });
    const adapter = buildNotificationFeedHttpAdapter(client);
    expect(await adapter.listRules({ instanceId: 'horde' })).toHaveLength(1);
    const draft = normalizeRule(ruleWire);
    await adapter.createRule(draft);
    expect((await adapter.updateRule('r1', draft, { instanceId: 'horde' })).name).toBe('Renamed');
    await adapter.deleteRule('r1');
    expect(await adapter.sinks()).toEqual([
      { name: 'telegram', label: 'Telegram', requiresIntegration: true },
    ]);
    expect(await adapter.deliveries('n-1', { instanceId: 'thor' })).toMatchObject([
      { status: 'delivered', attempts: 1 },
    ]);
    expect(calls.map((call) => `${call.method} ${call.path}`)).toEqual([
      'GET /notifications/rules?instance_id=horde',
      'POST /notifications/rules',
      'PUT /notifications/rules/r1?instance_id=horde',
      'DELETE /notifications/rules/r1',
      'GET /notifications/sinks',
      'GET /notifications/n-1/deliveries?instance_id=thor',
    ]);
    expect(calls[1]!.body).toMatchObject({ name: 'Critical', sink: 'telegram' });
    const { client: odd } = fakeClient({
      'GET /notifications/rules': { unexpected: true },
      'GET /notifications/n-2/deliveries': { items: [] },
    });
    expect(await buildNotificationFeedHttpAdapter(odd).listRules()).toEqual([]);
    expect(await buildNotificationFeedHttpAdapter(odd).deliveries('n-2')).toEqual([]);
  });

  it('delivers session_notification events from the shared stream', () => {
    let listener: ForgeStreamListener | undefined;
    const unsubscribe = vi.fn();
    const stream = {
      subscribeForgeStream: vi.fn((next: ForgeStreamListener) => {
        listener = next;
        return unsubscribe;
      }),
    };
    const { client } = fakeClient();
    const adapter = buildNotificationFeedHttpAdapter(client, { stream });
    const onNotification = vi.fn();
    const onStatus = vi.fn();
    const stop = adapter.subscribe({ onNotification, onStatus });

    listener!.onEvent({ type: 'session_updated', data: wireRow });
    listener!.onEvent({ type: 'session_notification', data: { id: 'malformed' } });
    const { read: _read, ...live } = wireRow;
    listener!.onEvent({ type: 'session_notification', data: live });
    listener!.onStatus?.('open');

    expect(onNotification).toHaveBeenCalledTimes(1);
    expect(onNotification.mock.calls[0]![0]).toMatchObject({ id: 'n-1', read: false });
    expect(onStatus).toHaveBeenCalledWith('open');
    stop();
    expect(unsubscribe).toHaveBeenCalled();
  });

  it('has no live updates without a stream', () => {
    const { client } = fakeClient();
    const stop = buildNotificationFeedHttpAdapter(client).subscribe({ onNotification: vi.fn() });
    expect(stop).toBeTypeOf('function');
    stop();
  });
});
