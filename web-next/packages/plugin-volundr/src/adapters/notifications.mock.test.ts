import { describe, expect, it, vi } from 'vitest';
import {
  DEFAULT_NOTIFICATION_FILTER,
  NotificationReadStateConflictError,
  emptyRuleDraft,
  serverFilterOf,
} from '../domain/notifications';
import { MOCK_NOTIFICATIONS, createMockNotificationFeed } from './notifications.mock';

const ANY = serverFilterOf(DEFAULT_NOTIFICATION_FILTER);

describe('createMockNotificationFeed', () => {
  it('pages newest first with an opaque cursor and applies server filters', async () => {
    const feed = createMockNotificationFeed();
    const first = await feed.list(ANY, { limit: 4 });
    expect(first.items.map((item) => item.id)).toEqual([
      'n-thor-7',
      'n-horde-4',
      'n-thor-6',
      'n-thor-5',
    ]);
    expect(first.nextBefore).toBe('4');
    expect(first.unreadCount).toBe(6);
    const second = await feed.list(ANY, { before: first.nextBefore, limit: 4 });
    expect(second.items).toHaveLength(2);
    expect(second.nextBefore).toBeNull();

    const filtered = await feed.list({
      ...ANY,
      kinds: ['error', 'decision'],
      minSeverity: 'critical',
      sources: ['agent'],
      projectId: 'proj-bench',
      sessionId: 'sess-bench',
    });
    expect(filtered.items.map((item) => item.id)).toEqual(['n-horde-4']);
  });

  it('gap-fills from its own next_after or watermarks, reading only the nodes named', async () => {
    const feed = createMockNotificationFeed();
    const page = await feed.list(ANY);
    expect(page.nextAfter).toMatch(/^mock-after:/);
    expect(page.unavailableInstances).toEqual([]);
    expect(await feed.listSince(ANY, { cursor: page.nextAfter, watermarks: {} })).toMatchObject({
      items: [],
      hasMore: false,
    });
    const fromMarks = await feed.listSince(ANY, { cursor: null, watermarks: { thor: 5 } });
    // horde-1 is not named, so it is skipped.
    expect(fromMarks.items.map((item) => `${item.instanceId}:${item.seq}`)).toEqual([
      'thor:6',
      'thor:7',
    ]);
    // Seq 0 reads a node from the start; each node caps its own page.
    const capped = await feed.listSince(
      ANY,
      { cursor: null, watermarks: { thor: 0, 'horde-1': 0 } },
      { limit: 2 },
    );
    expect(capped.hasMore).toBe(true);
    expect(capped.items.map((item) => `${item.instanceId}:${item.seq}`)).toEqual([
      'horde-1:3',
      'thor:4',
      'horde-1:4',
      'thor:5',
    ]);
    const rest = await feed.listSince(ANY, { cursor: capped.nextAfter, watermarks: {} });
    expect(rest.items.map((item) => item.seq)).toEqual([6, 7]);
    expect(rest.hasMore).toBe(false);
  });

  it('reports configured unreachable nodes', async () => {
    const feed = createMockNotificationFeed({ unavailableInstances: ['horde-2'] });
    expect((await feed.list(ANY)).unavailableInstances).toEqual(['horde-2']);
  });

  it('reads one session ascending', async () => {
    const feed = createMockNotificationFeed();
    expect(
      (await feed.listForSession('sess-bench', 3, { instanceId: 'horde-1' })).map(
        (item) => item.seq,
      ),
    ).toEqual([4]);
    expect(await feed.listForSession('sess-bench', null, { instanceId: 'thor' })).toEqual([]);
    expect(await feed.listForSession('sess-forge-api')).toHaveLength(3);
  });

  it('keeps a forward-only read watermark with revisions', async () => {
    const feed = createMockNotificationFeed({ readThrough: { thor: 5 } });
    const state = await feed.getReadState();
    expect(state.instances.thor).toMatchObject({ readThroughSeq: 5, unreadCount: 2, headSeq: 7 });
    expect((await feed.list({ ...ANY, unreadOnly: true })).items).toHaveLength(4);

    const next = await feed.markRead({ thor: 7, 'horde-1': 1 }, state);
    expect(next.instances.thor).toMatchObject({ readThroughSeq: 7, revision: 1, unreadCount: 0 });
    await expect(feed.markRead({ thor: 9 }, state)).rejects.toBeInstanceOf(
      NotificationReadStateConflictError,
    );
    const unchanged = await feed.markRead({ thor: 3 }, next);
    expect(unchanged.instances.thor!.readThroughSeq).toBe(7);
  });

  it('pushes live rows and status to subscribers until they leave', () => {
    const feed = createMockNotificationFeed({ seed: [] });
    const onNotification = vi.fn();
    const onStatus = vi.fn();
    const stop = feed.subscribe({ onNotification, onStatus });
    expect(onStatus).toHaveBeenCalledWith('open');
    feed.emit(MOCK_NOTIFICATIONS[0]!);
    feed.setStatus('error');
    stop();
    feed.emit(MOCK_NOTIFICATIONS[1]!);
    expect(onNotification).toHaveBeenCalledTimes(1);
    expect(onStatus).toHaveBeenLastCalledWith('error');
  });

  it('manages rules, sinks and deliveries in memory', async () => {
    const feed = createMockNotificationFeed();
    const created = await feed.createRule({ ...emptyRuleDraft('push'), name: 'All' });
    expect((await feed.listRules()).map((rule) => rule.id)).toContain(created.id);
    await feed.updateRule(created.id, { ...emptyRuleDraft('push'), name: 'Renamed' });
    expect((await feed.listRules()).find((rule) => rule.id === created.id)?.name).toBe('Renamed');
    await feed.deleteRule(created.id);
    expect((await feed.listRules()).map((rule) => rule.id)).not.toContain(created.id);
    expect((await feed.sinks()).map((sink) => sink.name)).toEqual(['telegram', 'push', 'webhook']);
    expect(await feed.deliveries('n-horde-4')).toHaveLength(1);
    expect(await feed.deliveries('unknown')).toEqual([]);
  });
});
