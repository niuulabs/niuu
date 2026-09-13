import { useCallback, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IMimirService } from '@niuulabs/plugin-mimir';
import type {
  IPersonaStore,
  IResidentControl,
  ITriggerStore,
  PersonaCreateRequest,
} from '@niuulabs/plugin-ravn';
import type { ITrackerBrowserService } from '@niuulabs/plugin-ting';
import type { IRealmGovernanceService, RealmSummary } from '@niuulabs/plugin-valkyrie';
import { REALMS_QUERY_KEY } from '@niuulabs/plugin-valkyrie';
import type { IVolundrService } from '@niuulabs/plugin-volundr';
import {
  ACTION_CLASSES,
  TRUST_LEVEL_FOR,
  charterPagePathFor,
  deploymentNameFor,
  mountNameFor,
  personaNameFor,
  requiredDraftError,
  residentNameFor,
  routingPrefixFor,
  routingRuleIdFor,
  type RealmDraft,
} from '../domain/realm';
import { templateById } from '../domain/templates';

/** One step of the create recipe, in the order it runs. */
export interface RecipeStep {
  id: string;
  label: string;
  /** Where the partial state is visible if this step fails. */
  advancedPath: string;
}

export const RECIPE_STEPS: RecipeStep[] = [
  { id: 'validate', label: 'check the draft', advancedPath: '/realms' },
  { id: 'connections', label: 'test connections', advancedPath: '/settings/integrations' },
  { id: 'realm', label: 'create the realm', advancedPath: '/valkyrie' },
  { id: 'memory', label: 'create realm memory', advancedPath: '/mimir/registry' },
  { id: 'routing', label: 'route memory writes', advancedPath: '/mimir/registry' },
  { id: 'persona', label: 'write the persona', advancedPath: '/ravn/personas' },
  { id: 'trust', label: 'grant trust', advancedPath: '/valkyrie' },
  { id: 'jobs', label: 'schedule standing jobs', advancedPath: '/ravn' },
  { id: 'board', label: 'import the tracker board', advancedPath: '/ting/sagas' },
  { id: 'resident', label: 'start the resident', advancedPath: '/ravn/ravens' },
  { id: 'charter', label: 'write the charter to memory', advancedPath: '/mimir/pages' },
];

export type StepState = 'todo' | 'running' | 'done' | 'failed';

export interface RecipeProgress {
  states: Record<string, StepState>;
  error: string | null;
  failedStep: RecipeStep | null;
  realm: RealmSummary | null;
}

const IDLE: RecipeProgress = {
  states: Object.fromEntries(RECIPE_STEPS.map((step) => [step.id, 'todo'])) as Record<
    string,
    StepState
  >,
  error: null,
  failedStep: null,
  realm: null,
};

const MOUNT_DISCOVERY_DEADLINE_MS = 60_000;
const MOUNT_DISCOVERY_INTERVAL_MS = 3_000;

function errorText(error: unknown): string {
  if (error instanceof Error) {
    const status = (error as { status?: unknown }).status;
    const detail = (error as { detail?: unknown }).detail;
    if (typeof status === 'number') {
      const reason =
        status === 403
          ? 'the platform refused this account for that route'
          : status === 401
            ? 'not signed in'
            : status === 404
              ? 'the route does not exist on this stack'
              : `HTTP ${status}`;
      const extra =
        typeof detail === 'string' && detail && detail !== 'Unknown error' ? ` (${detail})` : '';
      return `${reason}${extra}`;
    }
    return error.message;
  }
  return String(error);
}

/**
 * Creates a realm from a draft by calling, in order, the endpoints that already exist.
 * Stops at the first failure; nothing is retried or rolled back, and the failing step
 * says where the partial state can be seen.
 */
export function useCreateRealm() {
  const realms = useService<IRealmGovernanceService>('valkyrie.realms');
  const mimir = useService<IMimirService>('mimir');
  const personas = useService<IPersonaStore>('ravn.personas');
  const triggers = useService<ITriggerStore>('ravn.triggers');
  const residents = useService<IResidentControl>('ravn.residents');
  const tracker = useService<ITrackerBrowserService>('ting.tracker');
  const volundr = useService<IVolundrService>('volundr');
  const queryClient = useQueryClient();
  const [progress, setProgress] = useState<RecipeProgress>(IDLE);

  const run = useCallback(
    async (draft: RealmDraft): Promise<RealmSummary> => {
      const template = templateById(draft.templateId);
      const personaName = personaNameFor(draft.slug);
      const mountName = mountNameFor(draft.slug, draft.mountTarget);
      let states = { ...IDLE.states };
      setProgress({ ...IDLE, states });

      const mark = (id: string, state: StepState) => {
        states = { ...states, [id]: state };
        setProgress((current) => ({ ...current, states }));
      };

      const step = async <T>(id: string, work: () => Promise<T>): Promise<T> => {
        mark(id, 'running');
        try {
          const result = await work();
          mark(id, 'done');
          return result;
        } catch (error) {
          mark(id, 'failed');
          const failedStep = RECIPE_STEPS.find((entry) => entry.id === id) ?? null;
          setProgress((current) => ({ ...current, error: errorText(error), failedStep }));
          throw error;
        }
      };

      await step('validate', async () => {
        const problem = requiredDraftError(draft, template.needsBoard);
        if (problem) throw new Error(problem);
      });

      await step('connections', async () => {
        for (const id of draft.integrationIds) {
          const result = await volundr.testIntegration(id);
          if (!result.success) {
            throw new Error(`Integration ${id} failed its test: ${result.error ?? 'no detail'}`);
          }
        }
      });

      const realm = await step('realm', () =>
        realms.createRealm({
          slug: draft.slug,
          name: draft.name.trim(),
          sleipnir_domain: 'code',
          instance_id: draft.instanceId || null,
          autonomy_profile: 'balanced',
        }),
      );
      setProgress((current) => ({ ...current, realm }));

      await step('memory', async () => {
        if (!mimir.mounts.deployInstance) {
          throw new Error('This Mímir has no deployment target; set one in Mímir › Registry.');
        }
        await mimir.mounts.deployInstance({
          name: deploymentNameFor(draft.slug),
          backend: 'mimir',
          target: draft.mountTarget,
        });
      });

      await step('routing', () =>
        mimir.mounts.upsertRoutingRule({
          id: routingRuleIdFor(draft.slug),
          prefix: routingPrefixFor(draft.slug),
          mountName,
          priority: 10,
          active: true,
        }),
      );

      await step('persona', () => {
        const request: PersonaCreateRequest = {
          ...template.persona,
          name: personaName,
          summary: template.name,
          description: draft.charter.trim(),
          systemPromptTemplate: template.prompt(draft.charter, draft.repo, draft.trackerBoard),
        };
        return personas.createPersona(request);
      });

      await step('trust', async () => {
        await realms.createTrustGrant(draft.slug, {
          action_class: 'observe',
          target: draft.repo,
          level: 2,
          limits: {
            template: template.id,
            repo: draft.repo,
            branch: draft.branch,
            tracker_board: draft.trackerBoard,
            bug_board: draft.bugBoard,
            mount_target: draft.mountTarget,
          },
        });
        for (const actionClass of ACTION_CLASSES) {
          if (actionClass === 'observe') continue;
          const level = TRUST_LEVEL_FOR[draft.trust[actionClass]];
          if (level === null) continue;
          await realms.createTrustGrant(draft.slug, {
            action_class: actionClass,
            target: '*',
            level,
            limits: {},
          });
        }
      });

      await step('jobs', async () => {
        for (const job of template.jobs) {
          await triggers.createTrigger({
            kind: job.kind,
            personaName,
            spec: job.spec,
            enabled: true,
          });
        }
      });

      await step('board', async () => {
        if (!template.needsBoard || !draft.trackerBoard) return;
        await tracker.importProject(
          draft.trackerBoard,
          [draft.repo],
          draft.branch || undefined,
          draft.instanceId || null,
        );
      });

      await step('resident', () =>
        residents.deploy({
          name: residentNameFor(draft.slug),
          profileId: draft.profileId,
          instanceId: draft.instanceId,
          personaName,
          model: draft.model || undefined,
        }),
      );

      await step('charter', async () => {
        const deadline = Date.now() + MOUNT_DISCOVERY_DEADLINE_MS;
        while (Date.now() < deadline) {
          const mounts = await mimir.mounts.listMounts();
          if (mounts.some((mount) => mount.name === mountName)) {
            await mimir.pages.upsertPage(
              charterPagePathFor(draft.slug),
              draft.charter.trim(),
              mountName,
            );
            return;
          }
          await new Promise((resolve) => setTimeout(resolve, MOUNT_DISCOVERY_INTERVAL_MS));
        }
        throw new Error(
          `Realm memory ${mountName} did not appear within 60 s; the deployment is still starting. Check Mímir › Registry and write the charter from Settings once it is up.`,
        );
      });

      await queryClient.invalidateQueries({ queryKey: REALMS_QUERY_KEY });
      await queryClient.invalidateQueries({ queryKey: ['ravn'] });
      return realm;
    },
    [mimir, personas, queryClient, realms, residents, tracker, triggers, volundr],
  );

  const reset = useCallback(() => setProgress(IDLE), []);

  return { run, reset, progress };
}
