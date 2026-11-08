import { useCallback, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IMimirService } from '@niuulabs/plugin-mimir';
import { routingRuleIdFor } from '../domain/realm';
import type { IRealmGovernanceService } from '../ports';
import { REALMS_QUERY_KEY, REALM_GOVERNANCE_SERVICE_KEY } from './useRealmGovernance';

/** The create recipe run backwards: the routing rule, then the realm record. */
export const TEARDOWN_STEPS = [
  { id: 'routing', label: 'drop the memory routing rule' },
  { id: 'realm', label: 'delete the workspace' },
] as const;

export type TeardownStepId = (typeof TEARDOWN_STEPS)[number]['id'];

function isMissing(error: unknown): boolean {
  return (error as { status?: number }).status === 404;
}

/**
 * Deletes a realm and everything the wizard created for it that the platform can
 * delete. The Mímir instance stays: its deployment target offers start, stop and
 * update but no removal, and stopping it breaks the shared mount listing.
 */
export function useDeleteRealm(slug: string) {
  const realms = useService<IRealmGovernanceService>(REALM_GOVERNANCE_SERVICE_KEY);
  const mimir = useService<IMimirService>('mimir');
  const queryClient = useQueryClient();
  const [failedStep, setFailedStep] = useState<TeardownStepId | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  const run = useCallback(async () => {
    setRunning(true);
    setFailedStep(null);
    setError(null);
    const step = async (id: TeardownStepId, work: () => Promise<void>) => {
      try {
        await work();
      } catch (caught) {
        setFailedStep(id);
        setError(caught instanceof Error ? caught.message : String(caught));
        setRunning(false);
        throw caught;
      }
    };
    const tolerateMissing = async (work: () => Promise<unknown>) => {
      try {
        await work();
      } catch (caught) {
        if (!isMissing(caught)) throw caught;
      }
    };

    await step('routing', () =>
      tolerateMissing(() => mimir.mounts.deleteRoutingRule(routingRuleIdFor(slug))),
    );
    await step('realm', () => realms.deleteRealm(slug));

    await queryClient.invalidateQueries({ queryKey: REALMS_QUERY_KEY });
    setRunning(false);
  }, [mimir, queryClient, realms, slug]);

  return { run, running, failedStep, error };
}
