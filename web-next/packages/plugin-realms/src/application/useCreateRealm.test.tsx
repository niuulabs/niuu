import { renderHook, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import type { ReactNode } from 'react';
import { describe, expect, it } from 'vitest';
import { BALANCED_TRUST, EMPTY_DRAFT, type RealmDraft } from '../domain/realm';
import {
  createCallLog,
  fakeMimir,
  fakePersonas,
  fakeRealmService,
  fakeResidents,
  fakeTracker,
  fakeTriggers,
  fakeVolundr,
} from '../testing/fakes';
import { RECIPE_STEPS, useCreateRealm } from './useCreateRealm';

const draft: RealmDraft = {
  ...EMPTY_DRAFT,
  slug: 'lexi-api',
  name: 'Lexi API',
  charter: 'Keep it shippable. Ask before deploying.',
  repo: 'niuulabs/lexi-api',
  branch: 'dev',
  trackerBoard: 'board-1',
  integrationIds: ['int-1'],
  mountTarget: 'ymir',
  profileId: 'profile-1',
  instanceId: 'inst-1',
  model: 'claude-fable-5',
  trust: BALANCED_TRUST,
};

function wrap(services: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={services}>{children}</ServicesProvider>
      </QueryClientProvider>
    );
  };
}

function services(log: ReturnType<typeof createCallLog>, overrides: Record<string, unknown> = {}) {
  const residents = fakeResidents(log);
  return {
    'valkyrie.realms': fakeRealmService(log),
    mimir: fakeMimir(log),
    'ravn.personas': fakePersonas(log),
    'ravn.triggers': fakeTriggers(log),
    'ravn.residents': residents,
    'ravn.ravens': residents,
    'ting.tracker': fakeTracker(log),
    volundr: fakeVolundr(log),
    ...overrides,
  };
}

describe('useCreateRealm', () => {
  it('runs the recipe in order against the existing endpoints', async () => {
    const log = createCallLog();
    const { result } = renderHook(() => useCreateRealm(), { wrapper: wrap(services(log)) });

    let realm: { slug: string } | undefined;
    await act(async () => {
      realm = await result.current.run(draft);
    });

    expect(realm?.slug).toBe('lexi-api');
    expect(log.calls).toEqual([
      'testIntegration:int-1',
      'createRealm:lexi-api',
      'deployInstance:realm-lexi-api@ymir',
      'upsertRoutingRule:realm-lexi-api->realm-lexi-api',
      'createPersona:realm-lexi-api',
      'createTrustGrant:lexi-api:observe:2',
      'createTrustGrant:lexi-api:draft:2',
      'createTrustGrant:lexi-api:build:2',
      'createTrustGrant:lexi-api:test:2',
      'createTrustGrant:lexi-api:deploy:1',
      'createTrustGrant:lexi-api:spend:1',
      'createTrigger:cron:realm-lexi-api',
      'createTrigger:event:realm-lexi-api',
      'createTrigger:cron:realm-lexi-api',
      'importProject:board-1:niuulabs/lexi-api',
      'listMounts',
      'upsertPage:realms/lexi-api/charter.md@realm-lexi-api',
      'deploy:lexi-api:realm-lexi-api:realm-1',
    ]);
    expect(Object.values(result.current.progress.states).every((state) => state === 'done')).toBe(
      true,
    );
    expect(result.current.progress.error).toBeNull();
  });

  it('scopes event-kind triggers to the draft repo, and cron-kind triggers to none', async () => {
    const log = createCallLog();
    const created: Array<{ kind: string; repo: string }> = [];
    const capturingTriggers = {
      ...fakeTriggers(log),
      async createTrigger(request: { kind: string; repo?: string }) {
        created.push({ kind: request.kind, repo: request.repo ?? '' });
        return fakeTriggers(log).createTrigger(
          request as Parameters<ReturnType<typeof fakeTriggers>['createTrigger']>[0],
        );
      },
    };
    const { result } = renderHook(() => useCreateRealm(), {
      wrapper: wrap(services(log, { 'ravn.triggers': capturingTriggers })),
    });

    await act(async () => {
      await result.current.run(draft);
    });

    expect(created).toEqual([
      { kind: 'cron', repo: '' },
      { kind: 'event', repo: draft.repo },
      { kind: 'cron', repo: '' },
    ]);
  });

  it('stops at the first failing step and says which one', async () => {
    const log = createCallLog();
    const { result } = renderHook(() => useCreateRealm(), {
      wrapper: wrap(services(log, { volundr: fakeVolundr(log, { failIntegration: 'int-1' }) })),
    });

    await act(async () => {
      await expect(result.current.run(draft)).rejects.toThrow(/token expired/);
    });

    expect(log.calls).toEqual(['testIntegration:int-1']);
    expect(result.current.progress.failedStep?.id).toBe('connections');
    expect(result.current.progress.states.connections).toBe('failed');
    expect(result.current.progress.states.realm).toBe('todo');
  });

  it('does not undo earlier steps when a later one fails', async () => {
    const log = createCallLog();
    const residents = fakeResidents(log, { failDeploy: true });
    const { result } = renderHook(() => useCreateRealm(), {
      wrapper: wrap(services(log, { 'ravn.residents': residents, 'ravn.ravens': residents })),
    });

    await act(async () => {
      await expect(result.current.run(draft)).rejects.toThrow(/not enabled/);
    });

    expect(log.calls).toContain('createRealm:lexi-api');
    expect(log.calls.at(-1)).toBe('deploy:lexi-api:realm-lexi-api:realm-1');
    expect(result.current.progress.failedStep?.id).toBe('resident');
    expect(result.current.progress.failedStep?.advancedPath).toBe('/ravn/ravens');
  });

  it('fails the jobs step when trigger execution is disabled for this deployment', async () => {
    const log = createCallLog();
    const { result } = renderHook(() => useCreateRealm(), {
      wrapper: wrap(
        services(log, { 'ravn.triggers': fakeTriggers(log, { executionEnabled: false }) }),
      ),
    });

    await act(async () => {
      await expect(result.current.run(draft)).rejects.toThrow(/nothing executes them/);
    });

    // The trigger was still created (nothing is rolled back) — only the
    // recipe step is reported as failed, so an operator can see the
    // half-finished state via the 'jobs' step's advancedPath.
    expect(log.calls).toContain('createTrigger:cron:realm-lexi-api');
    expect(result.current.progress.failedStep?.id).toBe('jobs');
    expect(result.current.progress.failedStep?.advancedPath).toBe('/ravn');
  });

  it('surfaces a store-unavailable (503) trigger create as the jobs step failing clearly', async () => {
    // The Ravn API's trigger_store/budget_ledger are opt-in
    // (ravn.config.TriggerStoreConfig) — with neither configured, POST
    // /triggers returns 503 with a remedy in `detail`. No special-casing is
    // needed here: the generic step() error handler already turns any
    // ApiClientError-shaped rejection (status + detail) into readable text.
    const log = createCallLog();
    const unavailable = {
      ...fakeTriggers(log),
      async createTrigger(request: { kind: string; personaName: string }) {
        log.calls.push(`createTrigger:${request.kind}:${request.personaName}`);
        const error = new Error('API request failed: 503') as Error & {
          status: number;
          detail: string;
        };
        error.status = 503;
        error.detail = 'Ravn trigger persistence is unavailable';
        throw error;
      },
    };
    const { result } = renderHook(() => useCreateRealm(), {
      wrapper: wrap(services(log, { 'ravn.triggers': unavailable })),
    });

    await act(async () => {
      await expect(result.current.run(draft)).rejects.toThrow(/API request failed: 503/);
    });

    expect(result.current.progress.failedStep?.id).toBe('jobs');
    // errorText() turns the ApiClientError-shaped rejection (status + detail)
    // into the readable "HTTP 503 (<remedy>)" the UI actually displays.
    expect(result.current.progress.error).toBe(
      'HTTP 503 (Ravn trigger persistence is unavailable)',
    );
  });

  it('refuses a draft that is missing a required field before calling anything', async () => {
    const log = createCallLog();
    const { result } = renderHook(() => useCreateRealm(), { wrapper: wrap(services(log)) });
    await act(async () => {
      await expect(result.current.run({ ...draft, repo: '' })).rejects.toThrow(/repository/);
    });
    expect(log.calls).toEqual([]);
    expect(result.current.progress.failedStep?.id).toBe('validate');
  });

  it('fails loudly when realm memory never appears', async () => {
    const log = createCallLog();
    const { result } = renderHook(() => useCreateRealm(), {
      wrapper: wrap(services(log, { mimir: fakeMimir(log, { mountAppears: false }) })),
    });
    const originalNow = Date.now;
    let now = originalNow();
    Date.now = () => (now += 30_000);
    try {
      await act(async () => {
        await expect(result.current.run(draft)).rejects.toThrow(/did not appear/);
      });
    } finally {
      Date.now = originalNow;
    }
    expect(result.current.progress.failedStep?.id).toBe('charter');
  });

  it('lists eleven steps, validate first and resident last', () => {
    expect(RECIPE_STEPS[0]?.id).toBe('validate');
    expect(RECIPE_STEPS.at(-1)?.id).toBe('resident');
    expect(RECIPE_STEPS).toHaveLength(11);
  });

  it('writes the charter to memory before starting the resident', () => {
    const charterIndex = RECIPE_STEPS.findIndex((step) => step.id === 'charter');
    const residentIndex = RECIPE_STEPS.findIndex((step) => step.id === 'resident');
    // The resident resolves its charter from realm memory at startup and
    // exits if that page is configured but missing (see
    // resident_runtime_wiring.py's _resolve_environment_charter), and the
    // local controller has no restart policy — so charter must be written
    // first.
    expect(charterIndex).toBeGreaterThanOrEqual(0);
    expect(residentIndex).toBeGreaterThan(charterIndex);
  });
});
