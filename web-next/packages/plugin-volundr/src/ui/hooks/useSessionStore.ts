import { useCallback, useEffect } from 'react';
import { readForgeSource } from './readForgeSource';
import { useQueries, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { ISessionStore, SessionFilters } from '../../ports/ISessionStore';
import type { Session } from '../../domain/session';

const LIVE_REFETCH_MS = 5_000;
const SOURCE_REFETCH_MS = 30_000;
const ARCHIVE_REFETCH_MS = 60_000;
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
        // Patch existing inventories immediately. Discovery/polling still owns membership,
        // archives and errors, so one host's event cannot clear another host's rows.
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
        for (const session of sessions) {
          const key = ['volundr', 'domain-session', session.id];
          const old = cache.getQueryData<Session>(key);
          if (old && changed(old, session)) cache.setQueryData(key, session);
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
  const combined = useQueries({
    queries: (sources ?? []).flatMap((source) =>
      [false, true].map((archived) => ({
        queryKey: ['volundr', 'domain-sessions', 'source', source.id, archived, filters ?? null],
        queryFn: ({ signal }: { signal: AbortSignal }) =>
          readForgeSource(
            (sourceSignal) =>
              store.listSessions(
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
            name: 'Forge registry',
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

/** Queries a single session by id from ISessionStore. */
export function useSessionDetail(sessionId: string, instanceId?: string) {
  const store = useService<ISessionStore>('sessionStore');
  return useQuery({
    queryKey: ['volundr', 'domain-session', sessionId, instanceId ?? null],
    queryFn: ({ signal }) => store.getSession(sessionId, { instanceId, signal }),
    refetchInterval: LIVE_REFETCH_MS,
  });
}
