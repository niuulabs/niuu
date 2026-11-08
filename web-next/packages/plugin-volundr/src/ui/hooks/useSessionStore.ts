import { useCallback, useEffect } from 'react';
import { readForgeSource } from './readForgeSource';
import {
  useQueries,
  useQuery,
  useQueryClient,
  type QueryClient,
  type QueryKey,
  type UseQueryResult,
} from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type {
  ISessionStore,
  SessionChangeReader,
  SessionFilters,
  SessionInventoryChanges,
} from '../../ports/ISessionStore';
import type { Session } from '../../domain/session';

const LIVE_REFETCH_MS = 5_000;
const SOURCE_REFETCH_MS = 30_000;
const ARCHIVE_REFETCH_MS = 60_000;
const FULL_READ_CURSOR = '0';

interface InventoryCursor {
  cursor: string;
  fullReadAt: number;
}

/** Per cache, the cursor each source inventory last read up to. */
const inventoryCursors = new WeakMap<QueryClient, Map<string, InventoryCursor>>();

/** Cached rows, replaced by changed ones, in the host's order; null if an id is unknown. */
function mergeChanges(rows: Session[], changes: SessionInventoryChanges): Session[] | null {
  const byId = new Map(rows.map((row) => [row.id, row]));
  for (const session of changes.sessions) byId.set(session.id, session);
  const merged: Session[] = [];
  for (const id of changes.ids) {
    const row = byId.get(id);
    // Listed but neither cached nor sent: it became visible without a write.
    if (!row) return null;
    merged.push(row);
  }
  return merged;
}

/**
 * Reads one host's inventory incrementally: after a full read, only sessions written
 * since the last cursor, merged onto the cached rows. The host's ids decide
 * membership, so deleted, archived and restored sessions leave or join exactly.
 */
async function readInventoryChanges(
  cache: QueryClient,
  queryKey: QueryKey,
  reader: SessionChangeReader,
  source: { instanceId: string; archivedOnly: boolean },
  signal: AbortSignal,
): Promise<Session[]> {
  let cursors = inventoryCursors.get(cache);
  if (!cursors) inventoryCursors.set(cache, (cursors = new Map()));
  const key = JSON.stringify(queryKey);
  const rows = cache.getQueryData<Session[]>(queryKey);
  const last = cursors.get(key);
  const now = Date.now();
  if (rows && last && now - last.fullReadAt < reader.fullResyncMs) {
    const changes = await reader.read(last.cursor, source, signal);
    const merged = mergeChanges(rows, changes);
    if (merged) {
      cursors.set(key, { cursor: changes.cursor, fullReadAt: last.fullReadAt });
      return merged;
    }
  }
  const full = await reader.read(FULL_READ_CURSOR, source, signal);
  cursors.set(key, { cursor: full.cursor, fullReadAt: now });
  return full.sessions;
}

export interface SessionSourceState {
  id: string;
  name: string;
  archived: boolean;
  loading: boolean;
  error: string | null;
  stale: boolean;
}

/** Each registered Forge owns a query: slow hosts and archives never gate healthy sessions. */
export function useSessionList(filters?: SessionFilters) {
  const store = useService<ISessionStore>('sessionStore');
  const cache = useQueryClient();
  const federated = Boolean(store.listSources);
  useEffect(
    () =>
      store.subscribe((sessions) => {
        // Patch existing inventories immediately. Polling (incremental where the store
        // can) still owns membership, archives and errors, so one host's event cannot
        // clear another host's rows.
        const updates = new Map(sessions.map((session) => [session.id, session]));
        const changed = (row: Session, update: Session | undefined): update is Session =>
          Boolean(
            update &&
            update.clusterId === row.clusterId &&
            JSON.stringify(update) !== JSON.stringify(row),
          );
        for (const [key, rows] of cache.getQueriesData<Session[]>({
          queryKey: ['volundr', 'domain-sessions'],
        })) {
          if (!rows?.some((row) => changed(row, updates.get(row.id)))) continue;
          // No-op writes reset polling deadlines and clear source errors. Only the
          // inventory whose actual data changed may be touched by this event.
          cache.setQueryData(
            key,
            rows.map((row) => {
              const update = updates.get(row.id);
              return changed(row, update) ? update : row;
            }),
          );
        }
        // Detail queries carry the instance as a fourth key part, so match by prefix.
        for (const session of sessions) {
          for (const [key, old] of cache.getQueriesData<Session>({
            queryKey: ['volundr', 'domain-session', session.id],
          })) {
            if (old && changed(old, session)) cache.setQueryData(key, session);
          }
        }
      }),
    [store, cache],
  );
  const discovery = useQuery({
    queryKey: ['volundr', 'targets', 'session-sources'],
    queryFn: () => store.listSources!(),
    enabled: federated,
    refetchInterval: SOURCE_REFETCH_MS,
  });
  const single = useQuery({
    queryKey: ['volundr', 'domain-sessions', filters ?? null],
    queryFn: ({ signal }) => store.listSessions(filters, signal),
    enabled: !federated,
    refetchInterval: LIVE_REFETCH_MS,
  });
  const sources = discovery.data;
  const combine = useCallback(
    (results: UseQueryResult<Session[], Error>[]) => {
      const rows = new Map<string, Session>();
      const states: SessionSourceState[] = [];
      for (const [i, result] of results.entries()) {
        const source = sources![Math.floor(i / 2)]!;
        for (const session of result.data ?? []) {
          if (i % 2 === 0 || !rows.has(session.id)) rows.set(session.id, session);
        }
        states.push({
          id: source.id,
          name: source.name,
          archived: i % 2 === 1,
          loading: result.isPending && result.isFetching,
          error: result.error?.message ?? null,
          stale: result.isError && result.data !== undefined,
        });
      }
      return {
        data:
          results.some((result, i) => i % 2 === 0 && result.data !== undefined) ||
          rows.size > 0 ||
          sources?.length === 0
            ? [...rows.values()]
            : undefined,
        sources: states,
        fetching: results.some((result) => result.isFetching),
        pending: results.some((result) => result.isPending && result.isFetching),
        error: results.find((result) => result.error)?.error ?? null,
      };
    },
    [sources],
  );
  // Client-side filters would hide rows the host still lists, so they read in full.
  const changeReader = filters ? undefined : store.sessionChanges;
  const combined = useQueries({
    queries: (sources ?? []).flatMap((source) =>
      [false, true].map((archived) => ({
        queryKey: ['volundr', 'domain-sessions', 'source', source.id, archived, filters ?? null],
        queryFn: ({ signal, queryKey }: { signal: AbortSignal; queryKey: QueryKey }) =>
          readForgeSource(
            (sourceSignal) =>
              changeReader
                ? readInventoryChanges(
                    cache,
                    queryKey,
                    changeReader,
                    { instanceId: source.id, archivedOnly: archived },
                    sourceSignal,
                  )
                : store.listSessions(
                    { ...filters, instanceId: source.id, archivedOnly: archived },
                    sourceSignal,
                  ),
            signal,
            store.listRequestTimeoutMs,
          ),
        retry: false,
        refetchInterval: (query: { state: { status: string } }) =>
          query.state.status === 'error'
            ? SOURCE_REFETCH_MS
            : archived
              ? ARCHIVE_REFETCH_MS
              : LIVE_REFETCH_MS,
      })),
    ),
    combine,
  });
  const refetch = async () => {
    if (!federated) return single.refetch();
    // Invalidate the source prefix to refresh all mounted readers, preserving their independent states.
    await Promise.all([
      discovery.refetch(),
      cache.invalidateQueries({ queryKey: ['volundr', 'domain-sessions', 'source'] }),
    ]);
    return undefined;
  };
  if (!federated) return { ...single, sources: [] as SessionSourceState[] };
  const error = discovery.error ?? combined.error;
  return {
    data: combined.data,
    isLoading: combined.data === undefined && (discovery.isLoading || combined.pending),
    isFetching: discovery.isFetching || combined.fetching,
    isError: Boolean(error) && combined.data === undefined,
    error,
    sources: discovery.error
      ? [
          {
            id: 'registry',
            name: 'Host registry',
            archived: false,
            loading: false,
            error: discovery.error.message,
            stale: discovery.data !== undefined,
          },
          ...combined.sources,
        ]
      : combined.sources,
    refetch,
  };
}

/** States that change only through an action, which refreshes the session itself. */
const SETTLED_STATES: ReadonlySet<Session['state']> = new Set(['terminated', 'archived', 'failed']);

/** Queries a single session by id from ISessionStore; a settled session is not polled. */
export function useSessionDetail(sessionId: string, instanceId?: string) {
  const store = useService<ISessionStore>('sessionStore');
  const cache = useQueryClient();
  // Pushed activity (working, idle, needs you) lands at once, not on the next poll; the
  // session page can be open without the list that otherwise applies these events.
  useEffect(
    () =>
      store.subscribe((sessions) => {
        const update = sessions.find((session) => session.id === sessionId);
        if (!update) return;
        const key = ['volundr', 'domain-session', sessionId, instanceId ?? null];
        const old = cache.getQueryData<Session>(key);
        if (
          old &&
          update.clusterId === old.clusterId &&
          JSON.stringify(update) !== JSON.stringify(old)
        )
          cache.setQueryData(key, update);
      }),
    [store, cache, sessionId, instanceId],
  );
  return useQuery({
    queryKey: ['volundr', 'domain-session', sessionId, instanceId ?? null],
    queryFn: ({ signal }) => store.getSession(sessionId, { instanceId, signal }),
    refetchInterval: (query) =>
      query.state.data && SETTLED_STATES.has(query.state.data.state) ? false : LIVE_REFETCH_MS,
  });
}
