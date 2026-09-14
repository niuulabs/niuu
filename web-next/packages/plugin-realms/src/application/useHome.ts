import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useSessionList } from '@niuulabs/plugin-volundr';
import { useOptionalService, type AppIdentity, type IIdentityService } from '@niuulabs/plugin-sdk';
import type { ITrackerBrowserService } from '@niuulabs/plugin-ting';

/** The chips are decoration around the three choices; five minutes stale is fine. */
const CHIP_STALE_MS = 300_000;

/** Narrow views of services this package does not otherwise depend on. */
interface ModelCatalogService {
  getModelCatalog(): Promise<Record<string, { provider: string }>>;
}
interface RepoCatalogService {
  getRepos(): Promise<unknown[]>;
}

/** Domain sessions, on the same cache entry the sessions page polls. */
export function useDomainSessions() {
  return useSessionList();
}

export interface HomeStatus {
  models: number;
  providers: number;
  repos: number;
  /** null when no tracker is wired: the chip is left out rather than guessed at. */
  boards: number | null;
  host: string;
  identity: AppIdentity | null;
}

/**
 * What the status chips in the top-right corner say. Each source is optional in an
 * embedded host: a service that is not wired leaves its chip out, it does not invent
 * a number.
 */
export function useHomeStatus(): HomeStatus {
  const bifrost = useOptionalService<ModelCatalogService>('bifrost');
  const repoCatalog = useOptionalService<RepoCatalogService>('niuu.repos');
  const tracker = useOptionalService<ITrackerBrowserService>('ting.tracker');
  const identityService = useOptionalService<IIdentityService>('identity');

  const catalog = useQuery({
    queryKey: ['bifrost', 'model-catalog'],
    queryFn: () => bifrost!.getModelCatalog(),
    enabled: bifrost !== undefined,
    staleTime: CHIP_STALE_MS,
    placeholderData: keepPreviousData,
  });
  const repos = useQuery({
    queryKey: ['niuu', 'repos'],
    queryFn: () => repoCatalog!.getRepos(),
    enabled: repoCatalog !== undefined,
    staleTime: CHIP_STALE_MS,
    placeholderData: keepPreviousData,
  });
  const boards = useQuery({
    queryKey: ['ting', 'tracker', 'projects'],
    queryFn: () => tracker!.listProjects(),
    enabled: tracker !== undefined,
    staleTime: CHIP_STALE_MS,
    placeholderData: keepPreviousData,
  });
  const identity = useQuery({
    queryKey: ['identity'],
    queryFn: () => identityService!.getIdentity(),
    enabled: identityService !== undefined,
    staleTime: CHIP_STALE_MS,
  });

  const models = Object.values(catalog.data ?? {});
  return {
    models: models.length,
    providers: new Set(models.map((model) => model.provider)).size,
    repos: (repos.data ?? []).length,
    boards: tracker === undefined ? null : (boards.data ?? []).length,
    host: typeof window === 'undefined' ? '' : window.location.host,
    identity: identity.data ?? null,
  };
}
