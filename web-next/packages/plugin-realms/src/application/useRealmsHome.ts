import { useQueries, useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import { useReviews, useSagas, type ReviewItem as WorkReviewItem } from '@niuulabs/plugin-ting';
import type { IVolundrService } from '@niuulabs/plugin-volundr';
import type { RealmSummary } from '../domain/governance';
import {
  activeSessionCount,
  bindingFromGrants,
  waitingOnBoard,
  workProjectForBoard,
  workspaceSessions,
} from '../domain/join';
import type { RealmBinding } from '../domain/realm';
import type { IRealmGovernanceService } from '../ports';
import { REALMS_QUERY_KEY, REALM_GOVERNANCE_SERVICE_KEY, useRealms } from './useRealmGovernance';

export const SESSIONS_QUERY_KEY = ['volundr', 'sessions'] as const;

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
  binding: RealmBinding | null;
  boardName: string | null;
  waitingTickets: WorkReviewItem[];
  runningSessions: number;
  totalSessions: number;
}

export interface WaitingTicket {
  item: WorkReviewItem;
  card: RealmHomeCard;
}

export function needsYouCount(card: RealmHomeCard): number {
  return card.waitingTickets.length;
}

export function ticketsOf(cards: RealmHomeCard[]): WaitingTicket[] {
  return cards.flatMap((card) => card.waitingTickets.map((item) => ({ item, card })));
}

/** Realms that need you first, then the busy ones, then by name. */
export function orderCards(cards: RealmHomeCard[]): RealmHomeCard[] {
  return [...cards].sort(
    (a, b) =>
      needsYouCount(b) - needsYouCount(a) ||
      b.runningSessions - a.runningSessions ||
      a.realm.name.localeCompare(b.realm.name),
  );
}

/** One card's worth of data per realm, joined over the objects that already exist. */
export function useRealmsHome() {
  const realms = useRealms();
  const sagas = useSagas();
  const workReviews = useReviews();
  const sessions = useVolundrSessions();
  const governance = useService<IRealmGovernanceService>(REALM_GOVERNANCE_SERVICE_KEY);
  const grants = useQueries({
    queries: (realms.data ?? []).map((realm) => ({
      queryKey: [...REALMS_QUERY_KEY, realm.slug, 'trust-grants'],
      queryFn: () => governance.listTrustGrants(realm.slug),
      staleTime: 60_000,
    })),
  });
  // A dozen realms at most: joining them is cheaper than memoising over the
  // per-render array useQueries hands back.
  const cards: RealmHomeCard[] = (realms.data ?? []).map((realm, index) => {
    const binding = bindingFromGrants(grants[index]?.data);
    const workProject = workProjectForBoard(sagas.data, binding?.trackerBoard);
    const realmSessions = workspaceSessions(sessions.data, workProject?.id);
    return {
      realm,
      binding,
      boardName: workProject?.name ?? null,
      waitingTickets: waitingOnBoard(workReviews.data?.items, workProject?.id),
      runningSessions: activeSessionCount(realmSessions),
      totalSessions: realmSessions.length,
    };
  });

  const sessionsRunning = cards.reduce((sum, card) => sum + card.runningSessions, 0);

  return {
    cards,
    waitingTickets: ticketsOf(cards),
    needsYouError: workReviews.error ?? sagas.error ?? null,
    sessionsRunning,
    isLoading: realms.isLoading,
    error: realms.error ?? null,
    refetch: realms.refetch,
  };
}
