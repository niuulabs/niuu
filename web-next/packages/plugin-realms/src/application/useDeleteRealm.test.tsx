import { renderHook, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import type { Ravn } from '@niuulabs/plugin-ravn';
import { createSeedRealms } from '@niuulabs/plugin-valkyrie';
import type { ReactNode } from 'react';
import { describe, expect, it } from 'vitest';
import {
  createCallLog,
  fakeMimir,
  fakePersonas,
  fakeRealmService,
  fakeResidents,
} from '../testing/fakes';
import { TEARDOWN_STEPS, useDeleteRealm } from './useDeleteRealm';

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

async function services(log: ReturnType<typeof createCallLog>) {
  const realms = fakeRealmService(log, createSeedRealms());
  const personas = fakePersonas(log);
  await personas.createPersona({ name: 'realm-valhalla' } as never);
  const mimir = fakeMimir(log);
  await mimir.mounts.upsertRoutingRule({
    id: 'realm-valhalla',
    prefix: 'realms/valhalla/',
    mountName: 'realm-valhalla',
    priority: 10,
    active: true,
  });
  const residents = fakeResidents(log);
  const ravn = await residents.deploy({
    name: 'valhalla',
    profileId: 'p',
    instanceId: 'i',
    personaName: 'realm-valhalla',
  });
  return {
    ravn,
    services: {
      'valkyrie.realms': realms,
      'ravn.residents': residents,
      'ravn.personas': personas,
      mimir,
    },
  };
}

describe('useDeleteRealm', () => {
  it('runs the create recipe backwards and ends with the realm record', async () => {
    const log = createCallLog();
    const { ravn, services: fakes } = await services(log);
    log.calls.length = 0;
    const { result } = renderHook(() => useDeleteRealm('valhalla'), { wrapper: wrap(fakes) });

    await act(async () => {
      await result.current.run(ravn as Ravn);
    });

    expect(log.calls).toEqual([
      'deleteResident:valhalla',
      'deletePersona:realm-valhalla',
      'deleteRoutingRule:realm-valhalla',
      'deleteRealm:valhalla',
    ]);
    expect(result.current.error).toBeNull();
    expect(result.current.running).toBe(false);
  });

  it('tolerates parts that are already gone but not a missing realm', async () => {
    const log = createCallLog();
    const { services: fakes } = await services(log);
    await fakes['ravn.personas'].deletePersona('realm-valhalla');
    await fakes.mimir.mounts.deleteRoutingRule('realm-valhalla');
    log.calls.length = 0;
    const { result } = renderHook(() => useDeleteRealm('valhalla'), { wrapper: wrap(fakes) });

    await act(async () => {
      await result.current.run(null);
    });
    expect(log.calls).toEqual([
      'deletePersona:realm-valhalla',
      'deleteRoutingRule:realm-valhalla',
      'deleteRealm:valhalla',
    ]);

    const ghost = renderHook(() => useDeleteRealm('ghost'), { wrapper: wrap(fakes) });
    await act(async () => {
      await expect(ghost.result.current.run(null)).rejects.toThrow(/not found/);
    });
    expect(ghost.result.current.failedStep).toBe('realm');
    expect(ghost.result.current.error).toMatch(/Realm not found/);
  });

  it('lists four steps ending with the realm', () => {
    expect(TEARDOWN_STEPS.map((step) => step.id)).toEqual([
      'resident',
      'persona',
      'routing',
      'realm',
    ]);
  });
});
