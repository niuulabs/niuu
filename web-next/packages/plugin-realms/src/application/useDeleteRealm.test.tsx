import { renderHook, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createSeedRealms } from '../adapters/mock';
import type { ReactNode } from 'react';
import { describe, expect, it } from 'vitest';
import { createCallLog, fakeMimir, fakeRealmService } from '../testing/fakes';
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
  const mimir = fakeMimir(log);
  await mimir.mounts.upsertRoutingRule({
    id: 'realm-valhalla',
    prefix: 'realms/valhalla/',
    mountName: 'realm-valhalla',
    priority: 10,
    active: true,
  });
  return { 'realms.governance': realms, mimir };
}

describe('useDeleteRealm', () => {
  it('runs the create recipe backwards and ends with the realm record', async () => {
    const log = createCallLog();
    const fakes = await services(log);
    log.calls.length = 0;
    const { result } = renderHook(() => useDeleteRealm('valhalla'), { wrapper: wrap(fakes) });

    await act(async () => {
      await result.current.run();
    });

    expect(log.calls).toEqual(['deleteRoutingRule:realm-valhalla', 'deleteRealm:valhalla']);
    expect(result.current.error).toBeNull();
    expect(result.current.running).toBe(false);
  });

  it('tolerates a routing rule that is already gone but not a missing realm', async () => {
    const log = createCallLog();
    const fakes = await services(log);
    await fakes.mimir.mounts.deleteRoutingRule('realm-valhalla');
    log.calls.length = 0;
    const { result } = renderHook(() => useDeleteRealm('valhalla'), { wrapper: wrap(fakes) });

    await act(async () => {
      await result.current.run();
    });
    expect(log.calls).toEqual(['deleteRoutingRule:realm-valhalla', 'deleteRealm:valhalla']);

    const ghost = renderHook(() => useDeleteRealm('ghost'), { wrapper: wrap(fakes) });
    await act(async () => {
      await expect(ghost.result.current.run()).rejects.toThrow(/not found/);
    });
    expect(ghost.result.current.failedStep).toBe('realm');
    expect(ghost.result.current.error).toMatch(/Realm not found/);
  });

  it('lists two steps ending with the realm', () => {
    expect(TEARDOWN_STEPS.map((step) => step.id)).toEqual(['routing', 'realm']);
  });
});
