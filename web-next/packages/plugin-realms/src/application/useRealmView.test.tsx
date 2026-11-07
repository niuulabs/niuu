import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { createSeedRealms } from '../adapters/mock';
import type { RealmDetail, RealmTrustGrant } from '../domain/governance';
import { createCallLog, fakeRealmService } from '../testing/fakes';
import { defaultServices } from '../testing/renderRealms';
import { REALMS_QUERY_KEY, REALM_GOVERNANCE_SERVICE_KEY } from './useRealmGovernance';
import { useRealmView } from './useRealmView';

function wrapper(client: QueryClient, services: Record<string, unknown>) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={services}>{children}</ServicesProvider>
      </QueryClientProvider>
    );
  };
}

describe('useRealmView', () => {
  it('applies realm-detail snapshots and disposes its stream subscription', async () => {
    const [realm] = createSeedRealms();
    if (!realm) throw new Error('realm seed is required for this test');
    const log = createCallLog();
    const unsubscribe = vi.fn();
    let listener: ((detail: RealmDetail) => void) | undefined;
    const realmService = {
      ...fakeRealmService(log, [realm]),
      subscribeRealmDetail: vi.fn((_slug: string, onDetail: (detail: RealmDetail) => void) => {
        listener = onDetail;
        return unsubscribe;
      }),
    };
    const services = defaultServices(log, { [REALM_GOVERNANCE_SERVICE_KEY]: realmService });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const hook = renderHook(() => useRealmView(realm.slug), {
      wrapper: wrapper(client, services),
    });

    await waitFor(() =>
      expect(realmService.subscribeRealmDetail).toHaveBeenCalledWith(
        realm.slug,
        expect.any(Function),
      ),
    );

    const detail: RealmDetail = {
      realm: { ...realm, name: 'Updated realm' },
      trust_grants: [
        {
          id: 'grant-1',
          realm_id: realm.id,
          action_class: 'build',
          target: '*',
          level: 2,
          limits: {},
          granted_by: null,
          granted_at: '2026-10-18T00:00:00Z',
        },
      ],
      capabilities: [],
    };
    act(() => listener?.(detail));

    expect(client.getQueryData(REALMS_QUERY_KEY)).toEqual([detail.realm]);
    expect(client.getQueryData([...REALMS_QUERY_KEY, realm.slug, 'trust-grants'])).toEqual(
      detail.trust_grants,
    );

    hook.unmount();
    expect(unsubscribe).toHaveBeenCalledTimes(1);
  });

  async function renderWithGrants(grants: RealmTrustGrant[]) {
    const [realm] = createSeedRealms();
    if (!realm) throw new Error('realm seed is required for this test');
    const log = createCallLog();
    const realmService = {
      ...fakeRealmService(log, [realm]),
      listTrustGrants: async () => grants,
    };
    const services = defaultServices(log, { [REALM_GOVERNANCE_SERVICE_KEY]: realmService });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return renderHook(() => useRealmView(realm.slug), { wrapper: wrapper(client, services) });
  }

  it('has no memory mount of its own when no memory target was named', async () => {
    const hook = await renderWithGrants([]);

    await waitFor(() => expect(hook.result.current.view).not.toBeNull());
    expect(hook.result.current.mountName).toBeNull();
  });

  it('keeps a created workspace on the memory made with it', async () => {
    const hook = await renderWithGrants([
      {
        id: 'grant-observe',
        realm_id: 'r1',
        action_class: 'observe',
        target: 'github.com/niuulabs/niuu',
        level: 1,
        // The create recipe always names where the workspace memory is made.
        limits: { tracker_board: 'board-1', mount_target: 'shared' },
        granted_by: null,
        granted_at: '2026-10-18T00:00:00Z',
      },
    ]);

    await waitFor(() => expect(hook.result.current.mountName).not.toBeNull());
  });
});
