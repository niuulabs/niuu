import { useEffect, useMemo } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IMimirService } from '@niuulabs/plugin-mimir';
import { useReviews, useSagas, type ITrackerBrowserService } from '@niuulabs/plugin-ting';
import type { RealmSummary } from '../domain/governance';
import { buildRealmView, waitingOnBoard, workProjectForBoard } from '../domain/join';
import { mountNameFor } from '../domain/realm';
import type { IRealmGovernanceService } from '../ports';
import {
  REALMS_QUERY_KEY,
  REALM_GOVERNANCE_SERVICE_KEY,
  useRealmTrustGrants,
  useRealms,
} from './useRealmGovernance';
import { useVolundrSessions } from './useRealmsHome';

/** Everything the realm page shows, joined by the naming conventions. */
export function useRealmView(slug: string) {
  const queryClient = useQueryClient();
  const realms = useRealms();
  const realm = useMemo(
    () => realms.data?.find((entry) => entry.slug === slug) ?? null,
    [realms.data, slug],
  );
  const hasRealm = realm !== null;
  const governance = useService<IRealmGovernanceService>(REALM_GOVERNANCE_SERVICE_KEY);
  const grants = useRealmTrustGrants(slug, realm !== null);

  useEffect(() => {
    if (!hasRealm) return;
    return governance.subscribeRealmDetail(slug, (detail) => {
      queryClient.setQueryData<RealmSummary[]>(REALMS_QUERY_KEY, (current) =>
        current?.map((entry) => (entry.slug === slug ? detail.realm : entry)),
      );
      queryClient.setQueryData([...REALMS_QUERY_KEY, slug, 'trust-grants'], detail.trust_grants);
    });
  }, [hasRealm, queryClient, slug, governance]);

  const sessions = useVolundrSessions();
  const tracker = useService<ITrackerBrowserService>('ting.tracker');
  const mimir = useService<IMimirService>('mimir');

  const view = useMemo(
    () => (realm ? buildRealmView({ realm, grants: grants.data }) : null),
    [grants.data, realm],
  );

  // With two tracker connections a project id alone is ambiguous, so name the connection.
  const sagas = useSagas();
  const connectionFor = (projectId: string) =>
    sagas.data?.find((saga) => saga.trackerId === projectId)?.trackerConnectionId || undefined;

  const workReviews = useReviews();
  const boardId = view?.binding?.trackerBoard ?? '';
  const intake = useQuery({
    queryKey: ['ting', 'tracker', 'issues', boardId],
    queryFn: () => tracker.listIssues(boardId, undefined, connectionFor(boardId)),
    enabled: boardId.length > 0 && !sagas.isLoading,
    refetchInterval: 60_000,
  });

  const bugBoardId = view?.binding?.bugBoard ?? '';
  const findings = useQuery({
    queryKey: ['ting', 'tracker', 'issues', bugBoardId],
    queryFn: () => tracker.listIssues(bugBoardId, undefined, connectionFor(bugBoardId)),
    enabled: bugBoardId.length > 0 && !sagas.isLoading,
    refetchInterval: 60_000,
  });

  // Only a workspace created with the recipe has a memory mount of its own.
  const mountName = view && !view.adopted ? mountNameFor(slug) : null;
  const mounts = useQuery({
    queryKey: ['mimir', 'mounts'],
    queryFn: () => mimir.mounts.listMounts(),
  });
  const mount = mounts.data?.find((entry) => entry.name === mountName) ?? null;

  return {
    realm,
    view,
    allSessions: sessions.data,
    waitingTickets: waitingOnBoard(
      workReviews.data?.items,
      workProjectForBoard(sagas.data, boardId)?.id,
    ),
    intake: intake.data ?? [],
    findings: findings.data ?? [],
    ticketsError: intake.error ?? findings.error ?? null,
    mount,
    mountName,
    isLoading: realms.isLoading,
    notFound: !realms.isLoading && realms.data !== undefined && realm === null,
    error: realms.error ?? grants.error ?? null,
  };
}
