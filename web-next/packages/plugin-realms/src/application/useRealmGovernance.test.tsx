import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createMockRealmGovernanceService, createSeedRealms } from '../adapters/mock';
import {
  REALM_GOVERNANCE_SERVICE_KEY,
  useCreateTrustGrant,
  useRealms,
  useRealmTrustGrants,
} from './useRealmGovernance';

function wrapWith(service: unknown = createMockRealmGovernanceService()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={{ [REALM_GOVERNANCE_SERVICE_KEY]: service }}>
          {children}
        </ServicesProvider>
      </QueryClientProvider>
    );
  };
}

describe('useRealmGovernance hooks', () => {
  it('lists realms through the injected service', async () => {
    const { result } = renderHook(() => useRealms(), { wrapper: wrapWith() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(createSeedRealms());
  });

  it('lists a realm trust grants', async () => {
    const grants = renderHook(() => useRealmTrustGrants('valhalla'), { wrapper: wrapWith() });
    await waitFor(() => expect(grants.result.current.isSuccess).toBe(true));
    expect(grants.result.current.data?.some((grant) => grant.action_class === 'build')).toBe(true);
  });

  it('creates a grant and refreshes the realm grant list', async () => {
    const wrapper = wrapWith(createMockRealmGovernanceService());
    const grants = renderHook(() => useRealmTrustGrants('host-jozef'), { wrapper });
    await waitFor(() => expect(grants.result.current.isSuccess).toBe(true));
    expect(grants.result.current.data).toHaveLength(0);

    const create = renderHook(() => useCreateTrustGrant('host-jozef'), { wrapper });
    create.result.current.mutate({ action_class: 'build', target: '*', level: 2, limits: {} });

    await waitFor(() => expect(create.result.current.isSuccess).toBe(true));
    await waitFor(() => expect(grants.result.current.data).toHaveLength(1));
  });

  it('never fetches grants for an empty slug (no selection yet)', () => {
    const service = createMockRealmGovernanceService();
    const listSpy = vi.spyOn(service, 'listTrustGrants');

    const grants = renderHook(() => useRealmTrustGrants(''), { wrapper: wrapWith(service) });

    expect(grants.result.current.fetchStatus).toBe('idle');
    expect(listSpy).not.toHaveBeenCalled();
  });

  it('stays idle when the caller knows the realm does not exist', async () => {
    const service = createMockRealmGovernanceService();
    const listSpy = vi.spyOn(service, 'listTrustGrants');
    const wrapper = wrapWith(service);

    const gated = renderHook(() => useRealmTrustGrants('ghost-realm', false), { wrapper });
    expect(gated.result.current.fetchStatus).toBe('idle');
    expect(listSpy).not.toHaveBeenCalled();

    const open = renderHook(() => useRealmTrustGrants('valhalla', true), { wrapper });
    await waitFor(() => expect(open.result.current.isSuccess).toBe(true));
    expect(listSpy).toHaveBeenCalledWith('valhalla');
    expect(listSpy).not.toHaveBeenCalledWith('ghost-realm');
  });

  it('surfaces load and save failures', async () => {
    const broken = {
      listRealms: () => Promise.reject(new Error('realms offline')),
      listTrustGrants: () => Promise.reject(new Error('grants offline')),
      createTrustGrant: () => Promise.reject(new Error('grant refused')),
    };
    const wrapper = wrapWith(broken);

    const realms = renderHook(() => useRealms(), { wrapper });
    await waitFor(() => expect(realms.result.current.isError).toBe(true));

    const create = renderHook(() => useCreateTrustGrant('valhalla'), { wrapper });
    create.result.current.mutate({ action_class: 'build', target: '*', level: 0, limits: {} });
    await waitFor(() => expect(create.result.current.isError).toBe(true));
  });
});
