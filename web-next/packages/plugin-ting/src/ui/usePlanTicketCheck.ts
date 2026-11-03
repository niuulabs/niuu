import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { ExtractedStructure, ITingService, ITrackerBrowserService } from '../ports';
import { checkPlanTickets, isSameRepository } from '../domain/planTicketCheck';

/** Compares a plan draft with itself and with the tickets of projects on the same repository. */
export function usePlanTicketCheck(repo: string | undefined, structure: ExtractedStructure | null) {
  const ting = useService<ITingService>('ting');
  const tracker = useService<ITrackerBrowserService>('ting.tracker');
  const existingTickets = useQuery({
    queryKey: ['ting', 'plan-ticket-check', repo],
    enabled: Boolean(repo && structure),
    queryFn: async () => {
      const sagas = await ting.getSagas();
      const projects = sagas.filter((saga) =>
        saga.repos.some((sagaRepo) => isSameRepository(sagaRepo, repo as string)),
      );
      const tickets = await Promise.all(
        projects.map((saga) =>
          tracker.listIssues(saga.trackerId, undefined, saga.trackerConnectionId),
        ),
      );
      return tickets.flat();
    },
  });

  const flags = useMemo(
    () => checkPlanTickets(structure?.structure?.phases ?? [], existingTickets.data ?? []),
    [existingTickets.data, structure],
  );
  const error = existingTickets.error
    ? `Could not compare with existing tickets: ${existingTickets.error.message}`
    : null;

  return { flags, error };
}
