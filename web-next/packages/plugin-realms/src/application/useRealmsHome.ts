import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IRavenStream } from '@niuulabs/plugin-ravn';
import { useRealms, useReviewList, useValkyrieDashboard } from '@niuulabs/plugin-valkyrie';
import type { IVolundrService } from '@niuulabs/plugin-volundr';
import {
  activeSessionCount,
  ravnForRealm,
  residentForRealm,
  reviewsForRealm,
  sessionsForRealm,
} from '../domain/join';

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

/** One card's worth of data per realm, joined over the objects that already exist. */
export function useRealmsHome() {
  const realms = useRealms();
  const dashboard = useValkyrieDashboard();
  const ravens = useRavens();
  const reviews = useReviewList({ status: 'pending' });
  const sessions = useVolundrSessions();

  const cards = useMemo(() => {
    return (realms.data ?? []).map((realm) => {
      const resident = residentForRealm(dashboard.data, realm.slug);
      const ravn = ravnForRealm(ravens.data, realm.slug);
      const realmSessions = sessionsForRealm(sessions.data, realm.slug);
      const pending = reviewsForRealm(reviews.data, resident);
      return {
        realm,
        resident,
        ravn,
        pendingReviews: pending,
        runningSessions: activeSessionCount(realmSessions),
        totalSessions: realmSessions.length,
      };
    });
  }, [dashboard.data, ravens.data, realms.data, reviews.data, sessions.data]);

  return {
    cards,
    pendingReviews: reviews.data ?? [],
    isLoading: realms.isLoading,
    error: realms.error ?? dashboard.error ?? null,
    refetch: realms.refetch,
  };
}
