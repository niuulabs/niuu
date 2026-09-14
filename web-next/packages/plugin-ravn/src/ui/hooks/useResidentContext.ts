/**
 * Optional reads of the Valkyrie side of a resident.
 *
 * These services belong to another plugin and are not always registered — a
 * host can mount Ravn on its own. Each hook therefore goes through
 * `useOptionalService` and stays idle when the service is absent, the same way
 * `useOptionalPersonas` does. The query keys match the Valkyrie plugin's own so
 * the two share one cache instead of fetching twice.
 */

import { useQuery } from '@tanstack/react-query';
import { useOptionalService } from '@niuulabs/plugin-sdk';
import type {
  PendingReviewView,
  RealmView,
  ValkyrieActionView,
  ValkyrieResidentView,
} from '../../domain/residentBoard';

/** The slice of the Valkyrie dashboard this board reads. */
interface ValkyrieDashboardView {
  valkyries: ValkyrieResidentView[];
  actions?: ValkyrieActionView[];
}

interface ValkyrieDashboardService {
  getDashboard(): Promise<ValkyrieDashboardView>;
}

interface ValkyrieReviewService {
  listReviews(filters: { status: 'pending' }): Promise<PendingReviewView[]>;
}

interface RealmDirectoryService {
  listRealms(): Promise<RealmView[]>;
}

const DASHBOARD_REFRESH_MS = 10_000;

/** Shared with the Valkyrie plugin's `useReviewList` so the query key matches. */
const PENDING_FILTER = { status: 'pending' } as const;

const EMPTY_DASHBOARD: ValkyrieDashboardView = { valkyries: [], actions: [] };

export function useOptionalValkyrieDashboard() {
  const service = useOptionalService<ValkyrieDashboardService>('valkyrie');
  return useQuery({
    queryKey: ['valkyrie', 'dashboard'],
    queryFn: () => service?.getDashboard() ?? Promise.resolve(EMPTY_DASHBOARD),
    enabled: Boolean(service),
    refetchInterval: DASHBOARD_REFRESH_MS,
  });
}

export function useOptionalPendingReviews() {
  const service = useOptionalService<ValkyrieReviewService>('valkyrie.reviews');
  return useQuery({
    queryKey: ['valkyrie', 'reviews', 'list', PENDING_FILTER],
    queryFn: () => service?.listReviews(PENDING_FILTER) ?? Promise.resolve([]),
    enabled: Boolean(service),
    refetchInterval: DASHBOARD_REFRESH_MS,
  });
}

export function useOptionalRealms() {
  const service = useOptionalService<RealmDirectoryService>('valkyrie.realms');
  return useQuery({
    queryKey: ['valkyrie', 'realms'],
    queryFn: () => service?.listRealms() ?? Promise.resolve([]),
    enabled: Boolean(service),
  });
}
