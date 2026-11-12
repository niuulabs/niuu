import { renderHook, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createSeedRealms } from '../adapters/mock';
import type { ReactNode } from 'react';
import { describe, expect, it } from 'vitest';
import { createCallLog, fakeRealmService } from '../testing/fakes';
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

function services(log: ReturnType<typeof createCallLog>) {
  return { 'realms.governance': fakeRealmService(log, createSeedRealms()) };
}

describe('useDeleteRealm', () => {
  it('deletes the realm record', async () => {
    const log = createCallLog();
    const { result } = renderHook(() => useDeleteRealm('valhalla'), {
      wrapper: wrap(services(log)),
    });

    await act(async () => {
      await result.current.run();
    });

    expect(log.calls).toEqual(['deleteRealm:valhalla']);
    expect(result.current.error).toBeNull();
    expect(result.current.running).toBe(false);
  });

  it('fails on a missing realm and names the step', async () => {
    const log = createCallLog();
    const ghost = renderHook(() => useDeleteRealm('ghost'), { wrapper: wrap(services(log)) });
    await act(async () => {
      await expect(ghost.result.current.run()).rejects.toThrow(/not found/);
    });
    expect(ghost.result.current.failedStep).toBe('realm');
    expect(ghost.result.current.error).toMatch(/Realm not found/);
  });

  it('lists one step, the realm', () => {
    expect(TEARDOWN_STEPS.map((step) => step.id)).toEqual(['realm']);
  });
});
