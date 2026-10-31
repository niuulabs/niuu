import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { REALMS_QUERY_KEY, createSeedRealms, type RealmDetail } from '@niuulabs/plugin-valkyrie';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { createCallLog, fakeRealmService } from '../testing/fakes';
import { defaultServices } from '../testing/renderRealms';
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
    const services = defaultServices(log, { 'valkyrie.realms': realmService });
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
});
