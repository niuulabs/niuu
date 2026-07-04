import { renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import {
  createMockRealmGovernanceService,
  createSeedRealms,
  createSeedToolWorkflows,
} from '../adapters/mock';
import { wrapWithValkyrie } from '../testing/wrapWithValkyrie';
import {
  useCreateTrustGrant,
  useRealms,
  useRealmTrustGrants,
  useToolWorkflows,
} from './useRealmGovernance';

describe('useRealmGovernance hooks', () => {
  it('lists realms through the injected service', async () => {
    const { result } = renderHook(() => useRealms(), { wrapper: wrapWithValkyrie() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(createSeedRealms());
  });

  it('lists a realm trust grants and the Ting workflows', async () => {
    const wrapper = wrapWithValkyrie();
    const grants = renderHook(() => useRealmTrustGrants('asgard'), { wrapper });
    const workflows = renderHook(() => useToolWorkflows(), { wrapper });

    await waitFor(() => expect(grants.result.current.isSuccess).toBe(true));
    await waitFor(() => expect(workflows.result.current.isSuccess).toBe(true));

    expect(grants.result.current.data?.some((grant) => grant.action_class === 'build')).toBe(true);
    expect(workflows.result.current.data).toEqual(createSeedToolWorkflows());
  });

  it('creates a grant and refreshes the realm grant list', async () => {
    const service = createMockRealmGovernanceService();
    const wrapper = wrapWithValkyrie({ 'valkyrie.realms': service });
    const grants = renderHook(() => useRealmTrustGrants('midgard'), { wrapper });
    await waitFor(() => expect(grants.result.current.isSuccess).toBe(true));
    expect(grants.result.current.data).toHaveLength(0);

    const create = renderHook(() => useCreateTrustGrant('midgard'), { wrapper });
    create.result.current.mutate({
      action_class: 'build',
      target: '*',
      level: 2,
      limits: { workflow: 'valkyrie-tool-forge' },
    });

    await waitFor(() => expect(create.result.current.isSuccess).toBe(true));
    await waitFor(() => expect(grants.result.current.data).toHaveLength(1));
  });

  it('surfaces load and save failures', async () => {
    const broken = {
      listRealms: () => Promise.reject(new Error('realms offline')),
      listTrustGrants: () => Promise.reject(new Error('grants offline')),
      createTrustGrant: () => Promise.reject(new Error('grant refused')),
      listWorkflows: () => Promise.reject(new Error('workflows offline')),
    };
    const wrapper = wrapWithValkyrie({ 'valkyrie.realms': broken });

    const realms = renderHook(() => useRealms(), { wrapper });
    await waitFor(() => expect(realms.result.current.isError).toBe(true));

    const create = renderHook(() => useCreateTrustGrant('asgard'), { wrapper });
    create.result.current.mutate({ action_class: 'build', target: '*', level: 0, limits: {} });
    await waitFor(() => expect(create.result.current.isError).toBe(true));
  });
});
