import { useQueries, useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IRavenStream, Ravn } from '@niuulabs/plugin-ravn';
import {
  useRealms,
  useReviewList,
  useValkyrieDashboard,
  type EnvironmentSummary,
  type IRealmGovernanceService,
  type RealmSummary,
  type ReviewItem,
  type ValkyrieResident,
} from '@niuulabs/plugin-valkyrie';
import type { IVolundrService } from '@niuulabs/plugin-volundr';
import {
  activeSessionCount,
  bindingFromGrants,
  ravnForRealm,
  residentForRealm,
  reviewsForRealm,
  sessionsForRealm,
} from '../domain/join';
import type { RealmBinding } from '../domain/realm';

export const RAVENS_QUERY_KEY = ['ravn', 'ravens'] as const;
export const SESSIONS_QUERY_KEY = ['volundr', 'sessions'] as const;

export function useRavens() {
  const ravens = useService<IRavenStream>('ravn.ravens');
  return useQuery({ queryKey: RAVENS_QUERY_KEY, queryFn: () => ravens.listRavens() });
}

export function useVolundrSessions() {
  const volundr = useService<IVolundrService>('volundr');
  return useQuery({
    queryKey: SESSIONS_QUERY_KEY,
    queryFn: () => volundr.getSessions(),
    refetchInterval: 15_000,
  });
}

export interface RealmHomeCard {
  realm: RealmSummary;
  resident: ValkyrieResident | null;
  ravn: Ravn | null;
  environment: EnvironmentSummary | null;
  binding: RealmBinding | null;
  pendingReviews: ReviewItem[];
  runningSessions: number;
  totalSessions: number;
}

/** Realms that need you first, then the awake ones, then by name. */
export function orderCards(cards: RealmHomeCard[]): RealmHomeCard[] {
  const rank = (card: RealmHomeCard) =>
    card.resident?.wakefulness === 'wakeful' ? 0 : card.resident ? 1 : card.ravn ? 2 : 3;
  return [...cards].sort(
    (a, b) =>
      b.pendingReviews.length - a.pendingReviews.length ||
      rank(a) - rank(b) ||
      a.realm.name.localeCompare(b.realm.name),
  );
}

/** One card's worth of data per realm, joined over the objects that already exist. */
export function useRealmsHome() {
  const realms = useRealms();
  const dashboard = useValkyrieDashboard();
  const ravens = useRavens();
  const reviews = useReviewList({ status: 'pending' });
  const sessions = useVolundrSessions();
  const governance = useService<IRealmGovernanceService>('valkyrie.realms');
  const grants = useQueries({
    queries: (realms.data ?? []).map((realm) => ({
      queryKey: ['valkyrie', 'realms', realm.slug, 'trust-grants'],
      queryFn: () => governance.listTrustGrants(realm.slug),
      staleTime: 60_000,
    })),
  });
  // A dozen realms at most: joining them is cheaper than memoising over the
  // per-render array useQueries hands back.
  const cards: RealmHomeCard[] = (realms.data ?? []).map((realm, index) => {
    const resident = residentForRealm(dashboard.data, realm.slug);
    const ravn = ravnForRealm(ravens.data, realm.slug);
    const realmSessions = sessionsForRealm(sessions.data, realm.slug);
    const pending = reviewsForRealm(reviews.data, resident);
    return {
      realm,
      resident,
      ravn,
      environment:
        dashboard.data?.environments.find((entry) => entry.id === resident?.environmentId) ??
        null,
      binding: bindingFromGrants(grants[index]?.data),
      pendingReviews: pending,
      runningSessions: activeSessionCount(realmSessions),
      totalSessions: realmSessions.length,
    };
  });

  const residentsOnline = cards.filter(
    (card) => card.resident && card.resident.status !== 'offline',
  ).length;
  const sessionsRunning = cards.reduce((sum, card) => sum + card.runningSessions, 0);

  return {
    cards,
    pendingReviews: reviews.data ?? [],
    residentsOnline,
    sessionsRunning,
    isLoading: realms.isLoading,
    error: realms.error ?? dashboard.error ?? null,
    refetch: realms.refetch,
  };
}
