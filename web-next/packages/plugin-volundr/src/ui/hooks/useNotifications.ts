import { useEffect, useMemo, useState } from 'react';
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
  type QueryClient,
  type QueryKey,
} from '@tanstack/react-query';
import { useOptionalService, useService } from '@niuulabs/plugin-sdk';
import {
  DEFAULT_NOTIFICATION_FILTER,
  NotificationReadStateConflictError,
  advancingReadThrough,
  countLiveNotification,
  headSeqs,
  instanceKey,
  matchesNotificationFilter,
  maxSeqMaps,
  mergeNotifications,
  notificationKey,
  seqWatermarks,
  serverFilterOf,
  type InstanceSeqMap,
  type NotificationFilter,
  type NotificationReadState,
  type NotificationRuleDraft,
  type NotificationServerFilter,
  type SessionNotification,
} from '../../domain/notifications';
import type {
  INotificationFeed,
  NotificationFeedPage,
  NotificationStreamStatus,
} from '../../ports/INotificationFeed';

/** Service key the app registers the INotificationFeed adapter under. */
export const NOTIFICATIONS_SERVICE = 'volundr.notifications';
/** Pages of gap-fill before a reconnect reloads the feed from the top instead. */
export const GAP_FILL_MAX_PAGES = 5;

const ROOT = ['volundr', 'notifications'] as const;

export const notificationKeys = {
  all: ROOT,
  readState: [...ROOT, 'read-state'] as const,
  feed: (filter: NotificationServerFilter) => [...ROOT, 'feed', filter] as const,
  rules: (instanceId: string | null) => [...ROOT, 'rules', instanceId ?? ''] as const,
  sinks: (instanceId: string | null) => [...ROOT, 'sinks', instanceId ?? ''] as const,
  deliveries: (notification: SessionNotification) =>
    [...ROOT, 'deliveries', notificationKey(notification)] as const,
};

type FeedData = InfiniteData<NotificationFeedPage, string | null>;

/**
 * Subscribe without letting a missing or unavailable backend break the UI: the
 * demo/unavailable service proxies throw on use.
 */
function safeSubscribe(
  feed: INotificationFeed,
  subscriber: Parameters<INotificationFeed['subscribe']>[0],
): () => void {
  try {
    return feed.subscribe(subscriber);
  } catch {
    return () => {};
  }
}

/** Put rows into the cached feed: dedupe on (instance, id), keep newest first. */
export function prependNotifications(
  data: FeedData | undefined,
  incoming: readonly SessionNotification[],
): FeedData | undefined {
  if (!data || incoming.length === 0) return data;
  const known = new Set(data.pages.flatMap((page) => page.items.map(notificationKey)));
  const fresh = incoming.filter((notification) => !known.has(notificationKey(notification)));
  if (fresh.length === 0) return data;
  const [first, ...rest] = data.pages;
  const firstPage: NotificationFeedPage = first ?? {
    items: [],
    nextBefore: null,
    nextAfter: null,
    unreadCount: null,
    unavailableInstances: [],
    instanceHeads: null,
  };
  return {
    ...data,
    pages: [{ ...firstPage, items: mergeNotifications(firstPage.items, fresh) }, ...rest],
  };
}

/** Remember where the next gap-fill resumes (kept on the newest page). */
export function withNextAfter(
  data: FeedData | undefined,
  cursor: string | null,
): FeedData | undefined {
  const first = data?.pages[0];
  if (!data || !first || !cursor || first.nextAfter === cursor) return data;
  return { ...data, pages: [{ ...first, nextAfter: cursor }, ...data.pages.slice(1)] };
}

function belongsToServerFilter(
  notification: SessionNotification,
  filter: NotificationServerFilter,
): boolean {
  // Live rows are new, so they are unread; the unread filter cannot exclude them.
  return matchesNotificationFilter(notification, {
    ...DEFAULT_NOTIFICATION_FILTER,
    ...filter,
    unreadOnly: false,
  });
}

// ── Read state and the unread badge ─────────────────────────────────────────

export function useNotificationReadState() {
  const feed = useOptionalService<INotificationFeed>(NOTIFICATIONS_SERVICE);
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: notificationKeys.readState,
    queryFn: () => feed!.getReadState(),
    enabled: Boolean(feed),
  });

  useEffect(() => {
    if (!feed) return;
    // The first `open` is the connection this subscriber joined; every later one
    // is a reconnect (clean or after an error) that may have missed rows.
    let opened = false;
    return safeSubscribe(feed, {
      onNotification: (notification) => {
        queryClient.setQueryData<NotificationReadState>(notificationKeys.readState, (state) =>
          state ? countLiveNotification(state, notification) : state,
        );
      },
      onStatus: (status) => {
        if (status !== 'open') return;
        if (!opened) {
          opened = true;
          return;
        }
        void queryClient.invalidateQueries({ queryKey: notificationKeys.readState });
      },
    });
  }, [feed, queryClient]);

  return query;
}

/** Live unread count for the Forge "Notifications" tab badge. */
export function useUnreadNotificationCount(): number | undefined {
  return useNotificationReadState().data?.unreadCount;
}

// ── The feed ────────────────────────────────────────────────────────────────

/**
 * Whether a reconnect must refetch the newest page instead of gap-filling.
 * The facade only reads the nodes an `after` cursor names, so a node the first
 * page did not cover — unreachable then, or visible only since — would be
 * skipped; refetching picks up its backlog within the normal page limit.
 */
export function needsFirstPageReload(
  first: NotificationFeedPage | undefined,
  loaded: readonly SessionNotification[],
  readState: NotificationReadState | undefined,
): boolean {
  if (!first) return false;
  if (first.unavailableInstances.length > 0) return true;
  if (!first.instanceHeads) return false;
  const covered = new Set(Object.keys(first.instanceHeads));
  const known = [
    ...loaded.map((notification) => instanceKey(notification.instanceId)),
    ...Object.keys(readState?.instances ?? {}),
  ];
  return known.some((id) => !covered.has(id));
}

async function gapFill(
  feed: INotificationFeed,
  queryClient: QueryClient,
  queryKey: QueryKey,
  filter: NotificationServerFilter,
): Promise<void> {
  const data = queryClient.getQueryData<FeedData>(queryKey);
  // Before the first page lands, that pending read already covers the gap.
  if (!data) return;
  const first = data.pages[0];
  const loaded = data.pages.flatMap((page) => page.items);
  // Capture state now, before a read-state refetch moves the heads.
  const readState = queryClient.getQueryData<NotificationReadState>(notificationKeys.readState);
  if (needsFirstPageReload(first, loaded, readState)) {
    // The read state most likely misses the same nodes.
    await Promise.all([
      queryClient.invalidateQueries({ queryKey }),
      queryClient.invalidateQueries({ queryKey: notificationKeys.readState }),
    ]);
    return;
  }
  // Resume from the server's opaque `next_after` when it gave one. Otherwise
  // name every known node explicitly: the facade skips nodes a cursor omits.
  let cursor: string | null = first?.nextAfter ?? null;
  let watermarks: InstanceSeqMap = maxSeqMaps(
    first?.instanceHeads ?? {},
    seqWatermarks(loaded),
    headSeqs(readState),
  );
  for (let page = 0; page < GAP_FILL_MAX_PAGES; page++) {
    const gap = await feed.listSince(filter, { cursor, watermarks });
    cursor = gap.nextAfter ?? cursor;
    const resumeFrom = cursor;
    queryClient.setQueryData<FeedData>(queryKey, (current) =>
      withNextAfter(prependNotifications(current, gap.items), resumeFrom),
    );
    if (!gap.hasMore) return;
    watermarks = maxSeqMaps(watermarks, seqWatermarks(gap.items));
  }
  // Too far behind to page through: refetch from the newest page instead.
  await queryClient.invalidateQueries({ queryKey });
}

export interface UseNotificationsResult {
  /** Loaded rows that match every filter (server and client side), newest first. */
  notifications: SessionNotification[];
  /** All loaded rows before client-side filters. */
  loaded: SessionNotification[];
  readState: NotificationReadState | undefined;
  unreadCount: number | undefined;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => void;
  hasMore: boolean;
  loadMore: () => void;
  isLoadingMore: boolean;
  /** Whether the live stream is currently connected. */
  live: boolean;
  /** Nodes the newest page could not reach. */
  unavailableInstances: string[];
}

export function useNotifications(filter: NotificationFilter): UseNotificationsResult {
  const feed = useService<INotificationFeed>(NOTIFICATIONS_SERVICE);
  const queryClient = useQueryClient();
  const serverFilterKey = JSON.stringify(serverFilterOf(filter));
  const serverFilter = useMemo(
    () => JSON.parse(serverFilterKey) as NotificationServerFilter,
    [serverFilterKey],
  );
  const queryKey = useMemo(() => notificationKeys.feed(serverFilter), [serverFilter]);
  const readStateQuery = useNotificationReadState();
  const [status, setStatus] = useState<NotificationStreamStatus | null>(null);

  const query = useInfiniteQuery({
    queryKey,
    queryFn: ({ pageParam }) => feed.list(serverFilter, { before: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.nextBefore,
  });

  useEffect(() => {
    return safeSubscribe(feed, {
      onNotification: (notification) => {
        if (!belongsToServerFilter(notification, serverFilter)) return;
        queryClient.setQueryData<FeedData>(queryKey, (data) =>
          prependNotifications(data, [notification]),
        );
      },
      onStatus: (next) => {
        setStatus(next);
        if (next === 'open')
          void gapFill(feed, queryClient, queryKey, serverFilter).catch(() => undefined);
      },
    });
  }, [feed, queryClient, queryKey, serverFilter]);

  const loaded = useMemo(() => query.data?.pages.flatMap((page) => page.items) ?? [], [query.data]);
  const readState = readStateQuery.data;
  const notifications = useMemo(
    () =>
      loaded.filter((notification) => matchesNotificationFilter(notification, filter, readState)),
    [loaded, filter, readState],
  );

  return {
    notifications,
    loaded,
    readState,
    unreadCount: readState?.unreadCount,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    refetch: () => void query.refetch(),
    hasMore: Boolean(query.hasNextPage),
    loadMore: () => void query.fetchNextPage(),
    isLoadingMore: query.isFetchingNextPage,
    live: status === 'open',
    unavailableInstances: query.data?.pages[0]?.unavailableInstances ?? [],
  };
}

// ── Mark read ───────────────────────────────────────────────────────────────

/**
 * Move read watermarks forward. A revision conflict (another reader marked
 * first) re-reads the state and retries once; the watermark never moves back.
 */
export function useMarkNotificationsRead() {
  const feed = useService<INotificationFeed>(NOTIFICATIONS_SERVICE);
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (through: InstanceSeqMap) => {
      const attempt = async (retry: boolean): Promise<NotificationReadState> => {
        const current =
          queryClient.getQueryData<NotificationReadState>(notificationKeys.readState) ??
          (await feed.getReadState());
        const advancing = advancingReadThrough(through, current);
        if (Object.keys(advancing).length === 0) return current;
        try {
          return await feed.markRead(advancing, current);
        } catch (error) {
          if (!retry || !(error instanceof NotificationReadStateConflictError)) throw error;
          queryClient.setQueryData(notificationKeys.readState, await feed.getReadState());
          return attempt(false);
        }
      };
      return attempt(true);
    },
    onSuccess: (state) => {
      queryClient.setQueryData(notificationKeys.readState, state);
    },
  });
}

/** Watermarks that mark everything currently known as read. */
export function readEverythingThrough(
  readState: NotificationReadState | undefined,
  loaded: readonly SessionNotification[],
): InstanceSeqMap {
  return maxSeqMaps(headSeqs(readState), seqWatermarks(loaded));
}

// ── Delivery rules, sinks and delivery status ─────────────────────────────────

export function useNotificationRules(instanceId: string | null) {
  const feed = useService<INotificationFeed>(NOTIFICATIONS_SERVICE);
  return useQuery({
    queryKey: notificationKeys.rules(instanceId),
    queryFn: () => feed.listRules({ instanceId }),
  });
}

export function useNotificationSinks(instanceId: string | null) {
  const feed = useService<INotificationFeed>(NOTIFICATIONS_SERVICE);
  return useQuery({
    queryKey: notificationKeys.sinks(instanceId),
    queryFn: () => feed.sinks({ instanceId }),
  });
}

export function useSaveNotificationRule(instanceId: string | null) {
  const feed = useService<INotificationFeed>(NOTIFICATIONS_SERVICE);
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, draft }: { id: string | null; draft: NotificationRuleDraft }) =>
      id ? feed.updateRule(id, draft, { instanceId }) : feed.createRule(draft, { instanceId }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: notificationKeys.rules(instanceId) }),
  });
}

export function useDeleteNotificationRule(instanceId: string | null) {
  const feed = useService<INotificationFeed>(NOTIFICATIONS_SERVICE);
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => feed.deleteRule(id, { instanceId }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: notificationKeys.rules(instanceId) }),
  });
}

export function useNotificationDeliveries(notification: SessionNotification, enabled: boolean) {
  const feed = useService<INotificationFeed>(NOTIFICATIONS_SERVICE);
  return useQuery({
    queryKey: notificationKeys.deliveries(notification),
    queryFn: () => feed.deliveries(notification.id, { instanceId: notification.instanceId }),
    enabled,
  });
}
