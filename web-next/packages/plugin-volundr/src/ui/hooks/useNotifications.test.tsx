import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import {
  DEFAULT_NOTIFICATION_FILTER,
  NotificationReadStateConflictError,
  emptyRuleDraft,
  serverFilterOf,
  type NotificationFilter,
  type SessionNotification,
} from '../../domain/notifications';
import type { INotificationFeed } from '../../ports/INotificationFeed';
import {
  createMockNotificationFeed,
  type MockNotificationFeed,
} from '../../adapters/notifications.mock';
import {
  GAP_FILL_MAX_PAGES,
  NOTIFICATIONS_SERVICE,
  needsFirstPageReload,
  notificationKeys,
  prependNotifications,
  readEverythingThrough,
  useDeleteNotificationRule,
  useMarkNotificationsRead,
  useNotificationDeliveries,
  useNotificationReadState,
  useNotificationRules,
  useNotifications,
  useNotificationSinks,
  useSaveNotificationRule,
  useUnreadNotificationCount,
  withNextAfter,
} from './useNotifications';

let seq = 0;
function row(overrides: Partial<SessionNotification> = {}): SessionNotification {
  seq += 1;
  return {
    id: `n-${seq}`,
    instanceId: 'thor',
    seq,
    sessionId: 'sess-1',
    sessionSeq: null,
    sessionName: 'forge-api',
    ownerId: 'u',
    projectId: null,
    kind: 'milestone',
    severity: 'info',
    source: 'agent',
    title: `Notification ${seq}`,
    body: '',
    links: [],
    engine: null,
    model: null,
    correlationId: null,
    createdAt: new Date(Date.UTC(2026, 8, 23, 10, 0, seq)).toISOString(),
    read: false,
    ...overrides,
  };
}

function seeded(count: number, overrides: Partial<SessionNotification> = {}) {
  return Array.from({ length: count }, () => row(overrides));
}

function setup(
  feed: INotificationFeed | undefined,
  client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  }),
) {
  const services = feed ? { [NOTIFICATIONS_SERVICE]: feed } : {};
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <ServicesProvider services={services}>{children}</ServicesProvider>
    </QueryClientProvider>
  );
  return { wrapper, client };
}

/** A mock whose pages are two rows long, to exercise cursor paging. */
function pagedFeed(rows: SessionNotification[]): MockNotificationFeed {
  const mock = createMockNotificationFeed({ seed: rows, rules: [], deliveries: {} });
  return { ...mock, list: (filter, cursor) => mock.list(filter, { ...cursor, limit: 2 }) };
}

const ALL: NotificationFilter = DEFAULT_NOTIFICATION_FILTER;

describe('useNotifications', () => {
  it('pages the feed newest first with the next_before cursor', async () => {
    const rows = seeded(5);
    const feed = pagedFeed(rows);
    const { wrapper } = setup(feed);
    const { result } = renderHook(() => useNotifications(ALL), { wrapper });
    expect(result.current.isLoading).toBe(true);
    await waitFor(() => expect(result.current.notifications).toHaveLength(2));
    expect(result.current.notifications.map((item) => item.seq)).toEqual([5, 4]);
    expect(result.current.hasMore).toBe(true);
    act(() => result.current.loadMore());
    await waitFor(() => expect(result.current.notifications).toHaveLength(4));
    act(() => result.current.loadMore());
    await waitFor(() => expect(result.current.notifications).toHaveLength(5));
    expect(result.current.hasMore).toBe(false);
    expect(result.current.live).toBe(true);
  });

  it('prepends live rows once and ignores rows outside the server filter', async () => {
    const feed = createMockNotificationFeed({ seed: seeded(2) });
    const { wrapper } = setup(feed);
    const filter: NotificationFilter = { ...ALL, kinds: ['milestone', 'error'] };
    const { result } = renderHook(() => useNotifications(filter), { wrapper });
    await waitFor(() => expect(result.current.notifications).toHaveLength(2));

    const fresh = row({ kind: 'error' });
    act(() => {
      feed.emit(fresh);
      feed.emit(fresh);
      feed.emit(row({ kind: 'decision' }));
    });
    await waitFor(() => expect(result.current.notifications[0]?.id).toBe(fresh.id));
    expect(result.current.notifications).toHaveLength(3);
    await waitFor(() => expect(result.current.unreadCount).toBe(4));
  });

  it('applies client-side host, text and unread filters to loaded rows', async () => {
    const rows = [
      row({ title: 'Deploy done', instanceId: 'thor' }),
      row({ title: 'Bench crashed', instanceId: 'horde' }),
    ];
    const feed = createMockNotificationFeed({ seed: rows, readThrough: { thor: rows[0]!.seq } });
    const { wrapper } = setup(feed);
    const { result, rerender } = renderHook(({ filter }) => useNotifications(filter), {
      wrapper,
      initialProps: { filter: { ...ALL, instanceId: 'horde' } as NotificationFilter },
    });
    await waitFor(() => expect(result.current.loaded).toHaveLength(2));
    expect(result.current.notifications.map((item) => item.title)).toEqual(['Bench crashed']);
    rerender({ filter: { ...ALL, query: 'deploy' } });
    await waitFor(() =>
      expect(result.current.notifications.map((item) => item.title)).toEqual(['Deploy done']),
    );
  });

  it('gap-fills rows committed while the stream was down', async () => {
    const feed = createMockNotificationFeed({ seed: seeded(2) });
    const listSince = vi.spyOn(feed, 'listSince');
    const { wrapper } = setup(feed);
    const { result } = renderHook(() => useNotifications(ALL), { wrapper });
    await waitFor(() => expect(result.current.notifications).toHaveLength(2));
    await waitFor(() => expect(result.current.unreadCount).toBe(2));

    act(() => feed.setStatus('error'));
    expect(result.current.live).toBe(false);
    const missed = row();
    feed.commit(missed);
    act(() => feed.setStatus('open'));

    await waitFor(() => expect(result.current.notifications[0]?.id).toBe(missed.id));
    // It resumes from the server's opaque next_after, with watermarks as the fallback.
    expect(listSince).toHaveBeenLastCalledWith(expect.anything(), {
      cursor: expect.stringMatching(/^mock-after:/),
      watermarks: { thor: missed.seq - 1 },
    });
    expect(result.current.live).toBe(true);
    // The read state is re-read after a reconnect, so the badge catches up too.
    await waitFor(() => expect(result.current.unreadCount).toBe(3));
  });

  it('reloads from the top when a gap is larger than it will page through', async () => {
    const base = createMockNotificationFeed({ seed: seeded(1) });
    const listSince = vi.fn(async () => ({ items: [row()], hasMore: true, nextAfter: null }));
    const feed = { ...base, listSince };
    const list = vi.spyOn(feed, 'list');
    const { wrapper } = setup(feed);
    const { result } = renderHook(() => useNotifications(ALL), { wrapper });
    await waitFor(() => expect(result.current.notifications).toHaveLength(1));
    const listsBefore = list.mock.calls.length;
    act(() => base.setStatus('open'));
    await waitFor(() => expect(listSince).toHaveBeenCalledTimes(GAP_FILL_MAX_PAGES));
    await waitFor(() => expect(list.mock.calls.length).toBeGreaterThan(listsBefore));
  });

  it('reports load errors', async () => {
    const feed = createMockNotificationFeed();
    const failing = { ...feed, list: vi.fn(async () => Promise.reject(new Error('Forge down'))) };
    const { wrapper } = setup(failing);
    const { result } = renderHook(() => useNotifications(ALL), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect((result.current.error as Error).message).toBe('Forge down');
    act(() => result.current.refetch());
    await waitFor(() => expect(failing.list).toHaveBeenCalledTimes(2));
  });
});

describe('gap-fill cursors', () => {
  it('passes the latest next_after back and keeps it when a gap page has none', async () => {
    const base = createMockNotificationFeed({ seed: seeded(1) });
    const first = row();
    const calls: unknown[] = [];
    const feed: INotificationFeed = {
      ...base,
      list: async (filter, cursor) => ({ ...(await base.list(filter, cursor)), nextAfter: 'A1' }),
      listSince: async (_filter, after) => {
        calls.push(after);
        return calls.length === 1
          ? { items: [first], hasMore: false, nextAfter: 'A2' }
          : { items: [], hasMore: false, nextAfter: null };
      },
    };
    const { wrapper, client } = setup(feed);
    const { result } = renderHook(() => useNotifications(ALL), { wrapper });
    await waitFor(() => expect(result.current.notifications).toHaveLength(1));
    act(() => base.setStatus('open'));
    await waitFor(() => expect(result.current.notifications[0]?.id).toBe(first.id));
    act(() => base.setStatus('open'));
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls.map((after) => (after as { cursor: string | null }).cursor)).toEqual(['A1', 'A2']);
    const cached = client.getQueryData<{ pages: Array<{ nextAfter: string | null }> }>(
      notificationKeys.feed(serverFilterOf(ALL)),
    );
    expect(cached?.pages[0]?.nextAfter).toBe('A2');
  });

  it('keeps an unchanged cursor and an unloaded cache as they are', () => {
    expect(withNextAfter(undefined, 'A')).toBeUndefined();
    const data = {
      pages: [
        {
          items: [],
          nextBefore: null,
          nextAfter: 'A',
          unreadCount: null,
          unavailableInstances: [],
        },
      ],
      pageParams: [null],
    };
    expect(withNextAfter(data, 'A')).toBe(data);
    expect(withNextAfter(data, null)).toBe(data);
    expect(withNextAfter(data, 'B')?.pages[0]?.nextAfter).toBe('B');
  });
});

describe('reconnects that must refetch the newest page', () => {
  it('refetches instead of gap-filling when the first load missed a host', async () => {
    const feed = createMockNotificationFeed({
      seed: seeded(1),
      unavailableInstances: ['horde-2'],
    });
    const listSince = vi.spyOn(feed, 'listSince');
    const list = vi.spyOn(feed, 'list');
    const { wrapper } = setup(feed);
    const { result } = renderHook(() => useNotifications(ALL), { wrapper });
    await waitFor(() => expect(result.current.unavailableInstances).toEqual(['horde-2']));
    const before = list.mock.calls.length;
    act(() => feed.setStatus('open'));
    await waitFor(() => expect(list.mock.calls.length).toBeGreaterThan(before));
    expect(listSince).not.toHaveBeenCalled();
  });

  it('refetches when a live row comes from a host the first page did not cover', async () => {
    const feed = createMockNotificationFeed({ seed: seeded(1) });
    const listSince = vi.spyOn(feed, 'listSince');
    const list = vi.spyOn(feed, 'list');
    const { wrapper } = setup(feed);
    const { result } = renderHook(() => useNotifications(ALL), { wrapper });
    await waitFor(() => expect(result.current.notifications).toHaveLength(1));
    act(() => feed.emit(row({ instanceId: 'horde-new' })));
    await waitFor(() => expect(result.current.notifications).toHaveLength(2));
    const before = list.mock.calls.length;
    act(() => feed.setStatus('open'));
    await waitFor(() => expect(list.mock.calls.length).toBeGreaterThan(before));
    expect(listSince).not.toHaveBeenCalled();
  });

  it('decides from the first page what it covered', () => {
    const page = {
      items: [],
      nextBefore: null,
      nextAfter: null,
      unreadCount: null,
      unavailableInstances: [],
      instanceHeads: { thor: 3, empty: 0 },
    };
    const thorRow = row({ instanceId: 'thor' });
    expect(needsFirstPageReload(undefined, [], undefined)).toBe(false);
    expect(needsFirstPageReload(page, [thorRow], undefined)).toBe(false);
    expect(
      needsFirstPageReload({ ...page, instanceHeads: null }, [row({ instanceId: 'x' })], undefined),
    ).toBe(false);
    expect(
      needsFirstPageReload(page, [thorRow], {
        unreadCount: 0,
        instances: {
          late: { readThroughSeq: 0, revision: 0, unreadCount: 0, headSeq: 2 },
        },
      }),
    ).toBe(true);
    expect(needsFirstPageReload({ ...page, unavailableInstances: ['x'] }, [], undefined)).toBe(
      true,
    );
  });

  it('names every covered node in the fallback cursor, including empty ones', async () => {
    const base = createMockNotificationFeed({ seed: seeded(1) });
    const listSince = vi.spyOn(base, 'listSince');
    const feed: INotificationFeed = {
      ...base,
      list: async (filter, cursor) => ({
        ...(await base.list(filter, cursor)),
        nextAfter: null,
        instanceHeads: { thor: 0, quiet: 0 },
      }),
    };
    const { wrapper } = setup(feed);
    const { result } = renderHook(() => useNotifications(ALL), { wrapper });
    await waitFor(() => expect(result.current.notifications).toHaveLength(1));
    act(() => base.setStatus('open'));
    await waitFor(() => expect(listSince).toHaveBeenCalled());
    expect(listSince.mock.calls[0]![1]).toEqual({
      cursor: null,
      watermarks: { thor: result.current.loaded[0]!.seq, quiet: 0 },
    });
  });
});

describe('prependNotifications', () => {
  it('leaves an unloaded cache alone and fills an empty first page', () => {
    expect(prependNotifications(undefined, [row()])).toBeUndefined();
    const empty = { pages: [], pageParams: [] };
    expect(prependNotifications(empty, [])).toBe(empty);
    const filled = prependNotifications(empty, [row({ id: 'x' })]);
    expect(filled?.pages[0]?.items.map((item) => item.id)).toEqual(['x']);
  });
});

describe('unread count and mark read', () => {
  it('re-reads the count after a clean reconnect, not on the first open', async () => {
    const feed = createMockNotificationFeed({ seed: seeded(1) });
    const getReadState = vi.spyOn(feed, 'getReadState');
    const { wrapper } = setup(feed);
    const { result } = renderHook(() => useUnreadNotificationCount(), { wrapper });
    await waitFor(() => expect(result.current).toBe(1));
    expect(getReadState).toHaveBeenCalledTimes(1);
    feed.commit(row());
    act(() => feed.setStatus('open'));
    await waitFor(() => expect(result.current).toBe(2));
    expect(getReadState).toHaveBeenCalledTimes(2);
  });

  it('reads the unread count and follows live rows', async () => {
    const feed = createMockNotificationFeed({ seed: seeded(3) });
    const { wrapper } = setup(feed);
    const { result } = renderHook(() => useUnreadNotificationCount(), { wrapper });
    await waitFor(() => expect(result.current).toBe(3));
    act(() => feed.emit(row()));
    await waitFor(() => expect(result.current).toBe(4));
  });

  it('shows no count without a notifications service or with an unavailable one', async () => {
    const { wrapper } = setup(undefined);
    const { result } = renderHook(() => useUnreadNotificationCount(), { wrapper });
    expect(result.current).toBeUndefined();

    const unavailable = new Proxy({} as INotificationFeed, {
      get: () => () => {
        throw new Error('unavailable');
      },
    });
    const { wrapper: broken } = setup(unavailable);
    const { result: brokenResult } = renderHook(() => useNotificationReadState(), {
      wrapper: broken,
    });
    await waitFor(() => expect(brokenResult.current.isError).toBe(true));
  });

  it('marks everything read and clears the count', async () => {
    const rows = seeded(3);
    const feed = createMockNotificationFeed({ seed: rows });
    const { wrapper } = setup(feed);
    const { result } = renderHook(
      () => ({ feed: useNotifications(ALL), mark: useMarkNotificationsRead() }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.feed.unreadCount).toBe(3));
    await act(async () => {
      await result.current.mark.mutateAsync(
        readEverythingThrough(result.current.feed.readState, result.current.feed.loaded),
      );
    });
    await waitFor(() => expect(result.current.feed.unreadCount).toBe(0));
    // Nothing left to advance: no second write.
    const markRead = vi.spyOn(feed, 'markRead');
    await act(async () => {
      await result.current.mark.mutateAsync({ thor: 1 });
    });
    expect(markRead).not.toHaveBeenCalled();
  });

  it('re-reads the state and retries once after a revision conflict', async () => {
    const feed = createMockNotificationFeed({ seed: seeded(2) });
    const markRead = vi
      .spyOn(feed, 'markRead')
      .mockRejectedValueOnce(new NotificationReadStateConflictError());
    const getReadState = vi.spyOn(feed, 'getReadState');
    const { wrapper } = setup(feed);
    const { result } = renderHook(() => useMarkNotificationsRead(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ thor: 99 });
    });
    expect(markRead).toHaveBeenCalledTimes(2);
    expect(getReadState).toHaveBeenCalledTimes(2);
  });

  it('surfaces a second conflict and other failures', async () => {
    const feed = createMockNotificationFeed({ seed: seeded(1) });
    vi.spyOn(feed, 'markRead').mockRejectedValue(new NotificationReadStateConflictError());
    const { wrapper } = setup(feed);
    const { result } = renderHook(() => useMarkNotificationsRead(), { wrapper });
    await act(async () => {
      await expect(result.current.mutateAsync({ thor: 99 })).rejects.toBeInstanceOf(
        NotificationReadStateConflictError,
      );
    });

    const other = createMockNotificationFeed({ seed: seeded(1) });
    vi.spyOn(other, 'markRead').mockRejectedValue(new Error('boom'));
    const { wrapper: otherWrapper } = setup(other);
    const { result: otherResult } = renderHook(() => useMarkNotificationsRead(), {
      wrapper: otherWrapper,
    });
    await act(async () => {
      await expect(otherResult.current.mutateAsync({ thor: 99 })).rejects.toThrow('boom');
    });
  });
});

describe('delivery rule hooks', () => {
  it('lists, creates, updates and deletes rules and refreshes the list', async () => {
    const feed = createMockNotificationFeed({ rules: [] });
    const { wrapper, client } = setup(feed);
    const { result } = renderHook(
      () => ({
        rules: useNotificationRules(null),
        sinks: useNotificationSinks(null),
        save: useSaveNotificationRule(null),
        remove: useDeleteNotificationRule(null),
      }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.rules.data).toEqual([]));
    expect(result.current.sinks.data?.map((sink) => sink.name)).toContain('telegram');

    await act(async () => {
      await result.current.save.mutateAsync({
        id: null,
        draft: { ...emptyRuleDraft('push'), name: 'Everything' },
      });
    });
    await waitFor(() => expect(result.current.rules.data).toHaveLength(1));
    const id = result.current.rules.data![0]!.id;
    await act(async () => {
      await result.current.save.mutateAsync({
        id,
        draft: { ...emptyRuleDraft('push'), name: 'Renamed' },
      });
    });
    await waitFor(() => expect(result.current.rules.data![0]!.name).toBe('Renamed'));
    await act(async () => {
      await result.current.remove.mutateAsync(id);
    });
    await waitFor(() => expect(result.current.rules.data).toEqual([]));
    expect(client.getQueryData(notificationKeys.rules(null))).toEqual([]);
  });

  it('loads deliveries only when asked, on the owning instance', async () => {
    const feed = createMockNotificationFeed();
    const deliveries = vi.spyOn(feed, 'deliveries');
    const { wrapper } = setup(feed);
    const target = row({ id: 'n-horde-4', instanceId: 'horde-1' });
    const { result, rerender } = renderHook(
      ({ enabled }) => useNotificationDeliveries(target, enabled),
      { wrapper, initialProps: { enabled: false } },
    );
    expect(deliveries).not.toHaveBeenCalled();
    rerender({ enabled: true });
    await waitFor(() => expect(result.current.data).toHaveLength(1));
    expect(deliveries).toHaveBeenCalledWith('n-horde-4', { instanceId: 'horde-1' });
  });
});
