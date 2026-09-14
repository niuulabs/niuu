import { useCallback, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IMimirService } from '@niuulabs/plugin-mimir';
import type { IPersonaStore, IResidentControl, Ravn } from '@niuulabs/plugin-ravn';
import type { IRealmGovernanceService } from '@niuulabs/plugin-valkyrie';
import { REALMS_QUERY_KEY } from '@niuulabs/plugin-valkyrie';
import { personaNameFor, routingRuleIdFor } from '../domain/realm';
import { RAVENS_QUERY_KEY } from './useRealmsHome';

/** The create recipe run backwards: resident, persona, routing rule, then the realm record. */
export const TEARDOWN_STEPS = [
  { id: 'resident', label: 'remove the resident' },
  { id: 'persona', label: 'delete the persona' },
  { id: 'routing', label: 'drop the memory routing rule' },
  { id: 'realm', label: 'delete the realm' },
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
  const realms = useService<IRealmGovernanceService>('valkyrie.realms');
  const residents = useService<IResidentControl>('ravn.residents');
  const personas = useService<IPersonaStore>('ravn.personas');
  const mimir = useService<IMimirService>('mimir');
  const queryClient = useQueryClient();
  const [failedStep, setFailedStep] = useState<TeardownStepId | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  const run = useCallback(
    async (ravn: Ravn | null) => {
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

      await step('resident', async () => {
        if (ravn) await residents.delete(ravn);
      });
      await step('persona', () =>
        tolerateMissing(() => personas.deletePersona(personaNameFor(slug))),
      );
      await step('routing', () =>
        tolerateMissing(() => mimir.mounts.deleteRoutingRule(routingRuleIdFor(slug))),
      );
      await step('realm', () => realms.deleteRealm(slug));

      await queryClient.invalidateQueries({ queryKey: REALMS_QUERY_KEY });
      await queryClient.invalidateQueries({ queryKey: RAVENS_QUERY_KEY });
      setRunning(false);
    },
    [mimir, personas, queryClient, realms, residents, slug],
  );

  return { run, running, failedStep, error };
}
