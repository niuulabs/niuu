import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IMimirService } from '@niuulabs/plugin-mimir';
import type { IBudgetStream } from '@niuulabs/plugin-ravn';
import type { ITrackerBrowserService } from '@niuulabs/plugin-ting';
import type { IValkyrieService } from '@niuulabs/plugin-valkyrie';
import {
  useRealmTrustGrants,
  useRealms,
  useReviewList,
  useValkyrieDashboard,
} from '@niuulabs/plugin-valkyrie';
import {
  buildRealmView,
  reviewsForRealm,
  residentForRealm,
  sessionsForRealm,
} from '../domain/join';
import { mountNameFor } from '../domain/realm';
import { useRavens, useVolundrSessions } from './useRealmsHome';

/** Everything the realm page shows, joined by the naming conventions. */
export function useRealmView(slug: string) {
  const realms = useRealms();
  const realm = useMemo(
    () => realms.data?.find((entry) => entry.slug === slug) ?? null,
    [realms.data, slug],
  );
  const dashboard = useValkyrieDashboard();
  const ravens = useRavens();
  const grants = useRealmTrustGrants(slug, realm !== null);
  const reviews = useReviewList({});
  const sessions = useVolundrSessions();
  const valkyrie = useService<IValkyrieService>('valkyrie');
  const tracker = useService<ITrackerBrowserService>('ting.tracker');
  const budget = useService<IBudgetStream>('ravn.budget');
  const mimir = useService<IMimirService>('mimir');

  const view = useMemo(
    () =>
      realm
        ? buildRealmView({
            realm,
            dashboard: dashboard.data,
            ravens: ravens.data,
            grants: grants.data,
            reviews: reviews.data,
            sessions: sessions.data,
          })
        : null,
    [dashboard.data, grants.data, ravens.data, realm, reviews.data, sessions.data],
  );

  const resident = residentForRealm(dashboard.data, slug);
  const environmentId = resident?.environmentId ?? null;

  const decisions = useQuery({
    queryKey: ['valkyrie', 'decisions', environmentId],
    queryFn: () => valkyrie.listDecisions({ environmentId: environmentId ?? undefined, limit: 30 }),
    enabled: environmentId !== null,
    refetchInterval: 15_000,
  });

  const boardId = view?.binding?.trackerBoard ?? '';
  const intake = useQuery({
    queryKey: ['ting', 'tracker', 'issues', boardId],
    queryFn: () => tracker.listIssues(boardId),
    enabled: boardId.length > 0,
    refetchInterval: 60_000,
  });

  const bugBoardId = view?.binding?.bugBoard ?? '';
  const findings = useQuery({
    queryKey: ['ting', 'tracker', 'issues', bugBoardId],
    queryFn: () => tracker.listIssues(bugBoardId),
    enabled: bugBoardId.length > 0,
    refetchInterval: 60_000,
  });

  const ravnId = view?.ravnId ?? null;
  const budgetState = useQuery({
    queryKey: ['ravn', 'budget', ravnId],
    queryFn: () => budget.getBudget(ravnId as string),
    enabled: ravnId !== null,
  });

  const mountName = view?.binding ? mountNameFor(slug) : null;
  const mounts = useQuery({
    queryKey: ['mimir', 'mounts'],
    queryFn: () => mimir.mounts.listMounts(),
  });
  const mount = mounts.data?.find((entry) => entry.name === mountName) ?? null;

  return {
    realm,
    view,
    resident,
    environment: dashboard.data?.environments.find((entry) => entry.id === environmentId) ?? null,
    realmSessions: sessionsForRealm(sessions.data, slug),
    realmReviews: reviewsForRealm(reviews.data, resident),
    decisions: decisions.data?.items ?? [],
    intake: intake.data ?? [],
    findings: findings.data ?? [],
    budget: budgetState.data ?? null,
    mount,
    mountName,
    isLoading: realms.isLoading,
    notFound: !realms.isLoading && realms.data !== undefined && realm === null,
    error: realms.error ?? grants.error ?? null,
  };
}
