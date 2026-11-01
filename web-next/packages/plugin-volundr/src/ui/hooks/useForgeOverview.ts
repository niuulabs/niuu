import { useCallback } from 'react';
import { useQueries, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IVolundrService, SessionReadOptions } from '../../ports/IVolundrService';
import type { IClusterAdapter } from '../../ports/IClusterAdapter';
import type { ISessionStore } from '../../ports/ISessionStore';
import { mergeForgeStats } from '../../domain/forgeStats';
import { readForgeSource } from './readForgeSource';

const OVERVIEW_REFRESH_MS = 30_000;
type Source = { id: string; name: string };

function useOverviewRead<T>(
  kind: string,
  read: (options: SessionReadOptions) => Promise<T>,
  sources: Source[] | undefined,
  federated: boolean,
  timeoutMs?: number,
) {
  const combine = useCallback(
    (results: UseQueryResult<T, Error>[]) => ({
      data: results.flatMap((result) => (result.data === undefined ? [] : [result.data])),
      states: results.map((result, i) => ({
        id: `${kind}:${sources![i]!.id}`,
        name: `${sources![i]!.name} ${kind}`,
        loading: result.isPending,
        error: result.error?.message ?? null,
        stale: result.isError && result.data !== undefined,
      })),
    }),
    [kind, sources],
  );
  const scoped = useQueries({
    queries: (sources ?? []).map((source) => ({
      queryKey: ['volundr', 'overview', kind, source.id],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        readForgeSource(
          (sourceSignal) => read({ instanceId: source.id, signal: sourceSignal }),
          signal,
          timeoutMs,
        ),
      retry: false,
      refetchInterval: OVERVIEW_REFRESH_MS,
    })),
    combine,
  });
  const single = useQuery({
    queryKey: ['volundr', 'overview', kind],
    queryFn: ({ signal }) =>
      readForgeSource((sourceSignal) => read({ signal: sourceSignal }), signal, timeoutMs),
    enabled: !federated,
    retry: false,
    refetchInterval: OVERVIEW_REFRESH_MS,
  });
  if (federated) return scoped;
  return {
    data: single.data === undefined ? [] : [single.data],
    states: [
      {
        id: kind,
        name: `Forge ${kind}`,
        loading: single.isPending,
        error: single.error?.message ?? null,
        stale: single.isError && single.data !== undefined,
      },
    ],
  };
}

/** Metrics and resource reads progress independently, including within a single host. */
export function useForgeOverview() {
  const service = useService<IVolundrService>('volundr');
  const adapter = useService<IClusterAdapter>('clusterAdapter');
  const store = useService<ISessionStore>('sessionStore');
  const cache = useQueryClient();
  const federated = Boolean(store.listSources);
  const discovery = useQuery({
    queryKey: ['volundr', 'targets', 'session-sources'],
    queryFn: () => store.listSources!(),
    enabled: federated,
    refetchInterval: OVERVIEW_REFRESH_MS,
  });
  const metrics = useOverviewRead(
    'metrics',
    (options) => service.getStats(options),
    discovery.data,
    federated,
    store.listRequestTimeoutMs,
  );
  const resources = useOverviewRead(
    'resources',
    (options) => adapter.getClusters(options),
    discovery.data,
    federated,
    store.listRequestTimeoutMs,
  );
  return {
    stats:
      metrics.data.length || (federated && discovery.data?.length === 0)
        ? mergeForgeStats(metrics.data)
        : undefined,
    clusters: resources.data.flat(),
    states: [
      ...(federated && (discovery.isPending || discovery.error)
        ? [
            {
              id: 'registry',
              name: 'Forge registry',
              loading: discovery.isPending,
              error: discovery.error?.message ?? null,
              stale: discovery.data !== undefined,
            },
          ]
        : []),
      ...metrics.states,
      ...resources.states,
    ],
    refetch: () => cache.invalidateQueries({ queryKey: ['volundr', 'overview'] }),
  };
}
